"""SEO agent: a Claude tool-use loop that audits and improves site/, then writes a pull-request report.

Usage: python -m seo_agent [--report seo-report.md] [--title-file seo-title.txt] [--dry-run]

Environment:
  ANTHROPIC_API_KEY          API key (required unless --dry-run)
  SEO_AGENT_MODEL            model id (default: claude-opus-5)
  SEO_AGENT_EFFORT           low | medium | high | xhigh | max (default: high)
  SEO_AGENT_INSTRUCTIONS     optional extra focus for this run
  SEO_AGENT_WEB_SEARCH       set to 0 to disable the web search tool
  SEO_AGENT_EXTRA_HOSTS      extra comma-separated hosts fetch_live_url may reach
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from site_tools.audit import AuditResult
from site_tools.build import ROOT, BuildError, load_site_url
from seo_agent.workspace import SiteWorkspace, ToolError, extra_hosts_from_env

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "high"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 32_000
RETRY_MAX_TOKENS = 64_000
MAX_TURNS = 40
MAX_REPORT_REJECTIONS = 2
WEB_SEARCHES_PER_REQUEST = 3
INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text(encoding="utf-8")


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        "eager_input_streaming": True,
    }


CLIENT_TOOLS = [
    _tool("list_site_files", "List every file in site/ with its size in bytes.", {}, []),
    _tool(
        "read_site_file",
        "Read a text file in site/. Output lines are prefixed with a line number and a tab; the prefix is not part "
        "of the file. Use start_line/end_line to read part of a large file.",
        {
            "path": {"type": "string", "description": "Path relative to site/, e.g. 'index.html'."},
            "start_line": {"type": "integer", "description": "First line to read (1-based)."},
            "end_line": {"type": "integer", "description": "Last line to read (inclusive)."},
        },
        ["path"],
    ),
    _tool(
        "edit_site_file",
        "Replace one exact, unique occurrence of old_string with new_string in a text file in site/. old_string must "
        "match the file byte for byte (no line-number prefixes) and occur exactly once; include surrounding text to "
        "make it unique.",
        {
            "path": {"type": "string", "description": "Path relative to site/."},
            "old_string": {"type": "string", "description": "Exact text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
        },
        ["path", "old_string", "new_string"],
    ),
    _tool(
        "create_site_file",
        "Create a new text file in site/ (.html, .txt, .xml, .json, .svg, .webmanifest). Cannot overwrite existing "
        "files and cannot create sitemap.xml or robots.txt (the build generates them).",
        {
            "path": {"type": "string", "description": "Path relative to site/."},
            "content": {"type": "string", "description": "Full file content."},
        },
        ["path", "content"],
    ),
    _tool(
        "run_seo_audit",
        "Build the current site/ source and run the deterministic SEO audit. Returns errors, warnings and notes, "
        "untranslated text, and the protected facts (metrics, contact links, section ids) that must stay unchanged.",
        {},
        [],
    ),
    _tool(
        "fetch_live_url",
        "HTTP GET a URL on the live site (the site's own domain and its www variant, over http or https). Reports "
        "status, redirects, key headers, and for HTML the title, meta description, canonical, robots meta and h1.",
        {"url": {"type": "string", "description": "Absolute URL, e.g. https://example.com/robots.txt"}},
        ["url"],
    ),
    _tool(
        "submit_report",
        "Finish the run. Validates your changes (build, audit, protected facts, Arabic coverage). If validation fails "
        "you get the problems back and must fix them and call this again.",
        {
            "title": {"type": "string", "description": "Pull request title, under 70 characters, starting with 'SEO:'."},
            "summary": {"type": "string", "description": "Pull request description in Markdown."},
        },
        ["title", "summary"],
    ),
]
_PY_TYPES = {"string": str, "integer": int}


class AgentError(Exception):
    pass


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0

    def add(self, usage) -> None:
        self.requests += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += usage.cache_read_input_tokens or 0
        self.cache_write_tokens += usage.cache_creation_input_tokens or 0
        if usage.server_tool_use is not None:
            self.web_searches += usage.server_tool_use.web_search_requests or 0


@dataclass
class Outcome:
    title: str
    summary: str
    changed_files: list[str]
    baseline: AuditResult
    final: AuditResult
    usage: Usage = field(default_factory=Usage)


def validate_input(tool: dict, data) -> str | None:
    """Return an error message if `data` does not match the tool's schema (eager streaming skips server validation)."""
    schema = tool["input_schema"]
    if not isinstance(data, dict):
        return "input must be a JSON object"
    unknown = set(data) - set(schema["properties"])
    if unknown:
        return f"unknown field(s): {sorted(unknown)}"
    for key in schema["required"]:
        if key not in data:
            return f"missing required field {key!r}"
    for key, value in data.items():
        expected = _PY_TYPES[schema["properties"][key]["type"]]
        if not isinstance(value, expected) or isinstance(value, bool):
            return f"field {key!r} must be of type {schema['properties'][key]['type']}"
    return None


def echo_content(content: list) -> list:
    """Assistant content to send back. After a server-side fallback, drop the declined model's
    non-text blocks that precede the last fallback boundary (see the refusal/fallback docs)."""
    boundary = max((i for i, block in enumerate(content) if block.type == "fallback"), default=None)
    if boundary is None:
        return list(content)
    before = content[:boundary]
    answered = {getattr(b, "tool_use_id", None) for b in before if b.type.endswith("_tool_result") and b.type != "tool_result"}
    kept = [
        b for b in before
        if b.type == "text"
        or (b.type == "server_tool_use" and b.id in answered)
        or (b.type.endswith("_tool_result") and b.type != "tool_result")
    ]
    return kept + list(content[boundary + 1:])


def facts_diff(before: dict, after: dict) -> list[str]:
    return [f"{key} changed from {before.get(key)} to {after.get(key)}" for key in before if before.get(key) != after.get(key)]


class SeoAgent:
    def __init__(self, client: anthropic.Anthropic, workspace: SiteWorkspace, *, model: str, effort: str,
                 web_search: bool, extra_instructions: str = "", max_turns: int = MAX_TURNS):
        self.client = client
        self.workspace = workspace
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.extra_instructions = extra_instructions.strip()
        self.usage = Usage()
        self.tools: list[dict] = list(CLIENT_TOOLS)
        if web_search:
            self.tools.append({
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": WEB_SEARCHES_PER_REQUEST,
                "user_location": {"type": "approximate", "city": "Dubai", "country": "AE", "timezone": "Asia/Dubai"},
            })
        self.tools_by_name = {tool["name"]: tool for tool in CLIENT_TOOLS}
        self.baseline = workspace.build_and_audit()
        self.snapshot_before = workspace.snapshot()
        self.rejections = 0

    # ----- prompt -----------------------------------------------------------------------------

    def kickoff(self) -> str:
        history = subprocess.run(
            ["git", "log", "-n", "15", "--format=%cs %s", "--", "site/"],
            cwd=self.workspace.root, capture_output=True, text=True,
        ).stdout.strip() or "(no history available)"
        parts = [
            f"Today is {dt.date.today().isoformat()}. Canonical site URL: {self.workspace.site_url}",
            "Baseline audit of the current source:\n\n" + self.baseline.to_markdown(),
            "Protected facts (must stay unchanged):\n" + json.dumps(self.baseline.facts, ensure_ascii=False),
            "Recent commits touching site/ (newest first):\n" + history,
        ]
        if self.extra_instructions:
            parts.append("Extra focus requested by Ahmed for this run:\n" + self.extra_instructions)
        parts.append("Run your SEO pass now.")
        return "\n\n".join(parts)

    # ----- model calls ------------------------------------------------------------------------

    def _call(self, messages: list, max_tokens: int):
        request = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=INSTRUCTIONS,
            tools=self.tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        for attempt in range(3):
            try:
                with self.client.beta.messages.stream(**request) as stream:
                    return stream.get_final_message()
            except anthropic.APIError:
                raise
            except ValueError as error:  # unparseable eagerly-streamed tool input: re-issue the request
                if attempt == 2:
                    raise AgentError(f"model produced unparseable tool input three times: {error}") from error
        raise AssertionError("unreachable")

    # ----- tool execution ---------------------------------------------------------------------

    def _execute(self, block) -> tuple[dict, Outcome | None]:
        def result(content: str, is_error: bool = False) -> dict:
            return {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}

        tool = self.tools_by_name.get(block.name)
        if tool is None:
            return result(f"Unknown tool {block.name!r}.", True), None
        problem = validate_input(tool, block.input)
        if problem:
            return result(json.dumps({"INVALID_INPUT": problem, "received": block.input}, ensure_ascii=False), True), None

        args = block.input
        ws = self.workspace
        try:
            if block.name == "list_site_files":
                return result(ws.list_files()), None
            if block.name == "read_site_file":
                return result(ws.read_file(args["path"], args.get("start_line"), args.get("end_line"))), None
            if block.name == "edit_site_file":
                return result(ws.edit_file(args["path"], args["old_string"], args["new_string"])), None
            if block.name == "create_site_file":
                return result(ws.create_file(args["path"], args["content"])), None
            if block.name == "fetch_live_url":
                return result(ws.fetch_live(args["url"])), None
            if block.name == "run_seo_audit":
                current = ws.build_and_audit()
                return result(current.to_markdown() + "\nProtected facts: " + json.dumps(current.facts, ensure_ascii=False)), None
            if block.name == "submit_report":
                return self._submit(block, args, result)
        except BuildError as error:
            return result(f"Build failed: {error}", True), None
        except ToolError as error:
            return result(str(error), True), None
        raise AssertionError(f"unhandled tool {block.name}")

    def _submit(self, block, args: dict, result) -> tuple[dict, Outcome | None]:
        problems: list[str] = []
        try:
            final = self.workspace.build_and_audit()
        except BuildError as error:
            final = None
            problems.append(f"The build fails: {error}")
        if final is not None:
            new_errors = [f"{f.check}: {f.message}" for f in final.findings if f.severity == "error"
                          and (f.check, f.message) not in {(b.check, b.message) for b in self.baseline.findings}]
            problems += [f"New audit error — {e}" for e in new_errors]
            problems += [f"Protected fact changed — {d}" for d in facts_diff(self.baseline.facts, final.facts)]
            new_untranslated = [t for t in final.untranslated if t not in self.baseline.untranslated]
            problems += [f"Visible English text without an Arabic dictionary entry — {t}" for t in new_untranslated]
        if not args["title"].strip() or not args["summary"].strip():
            problems.append("title and summary must be non-empty")

        if problems:
            self.rejections += 1
            if self.rejections > MAX_REPORT_REJECTIONS:
                raise AgentError("changes still fail validation after retries:\n" + "\n".join(problems))
            return result("Validation failed. Fix these and call submit_report again:\n- " + "\n- ".join(problems), True), None

        after = self.workspace.snapshot()
        changed = sorted(p for p in set(after) | set(self.snapshot_before) if after.get(p) != self.snapshot_before.get(p))
        outcome = Outcome(args["title"].strip()[:100], args["summary"].strip(), changed, self.baseline, final, self.usage)
        return result("Report accepted."), outcome

    # ----- loop -------------------------------------------------------------------------------

    def run(self) -> Outcome:
        messages: list = [{"role": "user", "content": self.kickoff()}]
        max_tokens = MAX_TOKENS
        nudged = False
        for _ in range(self.max_turns):
            response = self._call(messages, max_tokens)
            self.usage.add(response.usage)

            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                raise AgentError(f"model declined the request (category: {getattr(details, 'category', None)})")
            if response.stop_reason == "max_tokens":
                if max_tokens >= RETRY_MAX_TOKENS:
                    raise AgentError("response hit max_tokens even at the retry limit")
                max_tokens = RETRY_MAX_TOKENS  # re-issue the same request with more room; never run truncated tools
                continue

            content = echo_content(response.content)
            messages.append({"role": "assistant", "content": content})
            if response.stop_reason == "pause_turn":
                continue  # server tool (web search) paused mid-turn; resend to let it continue

            tool_uses = [block for block in content if block.type == "tool_use"]
            if not tool_uses:
                if nudged:
                    raise AgentError("model ended without calling submit_report")
                nudged = True
                messages.append({"role": "user", "content": "Finish by calling submit_report."})
                continue

            results, outcome = [], None
            for block in tool_uses:
                tool_result, done = self._execute(block)
                results.append(tool_result)
                outcome = outcome or done
            if outcome is not None:
                return outcome
            messages.append({"role": "user", "content": results})
        raise AgentError(f"no accepted report after {self.max_turns} turns")


def render_report(outcome: Outcome, model: str) -> str:
    b, f = outcome.baseline.counts(), outcome.final.counts()
    u = outcome.usage
    lines = [
        outcome.summary,
        "",
        "---",
        "",
        "| Audit | Before | After |",
        "|---|---|---|",
        f"| Errors | {b['error']} | {f['error']} |",
        f"| Warnings | {b['warning']} | {f['warning']} |",
        f"| Notes | {b['info']} | {f['info']} |",
        "",
        "Changed files: " + (", ".join(f"`site/{p}`" for p in outcome.changed_files) or "none"),
        "",
        f"Run: {model}, {u.requests} API requests, {u.input_tokens:,} input + {u.cache_read_tokens:,} cache-read + "
        f"{u.cache_write_tokens:,} cache-write input tokens, {u.output_tokens:,} output tokens, {u.web_searches} web searches.",
    ]
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    if server and repo and run_id:
        lines.append(f"Workflow run: {server}/{repo}/actions/runs/{run_id}")
    lines += ["", "<details><summary>Full audit after changes</summary>", "", outcome.final.to_markdown(), "</details>"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the SEO agent on site/.")
    parser.add_argument("--report", type=Path, default=ROOT / "seo-report.md", help="where to write the PR body")
    parser.add_argument("--title-file", type=Path, default=ROOT / "seo-title.txt", help="where to write the PR title")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS)
    parser.add_argument("--dry-run", action="store_true", help="print the baseline audit and kickoff prompt; no API call")
    args = parser.parse_args(argv)

    model = os.environ.get("SEO_AGENT_MODEL") or DEFAULT_MODEL
    effort = os.environ.get("SEO_AGENT_EFFORT") or DEFAULT_EFFORT
    try:
        workspace = SiteWorkspace(ROOT, load_site_url(ROOT / "site.config.json"), extra_hosts_from_env())
        agent = SeoAgent(
            anthropic.Anthropic(max_retries=4) if not args.dry_run else None,
            workspace,
            model=model,
            effort=effort,
            web_search=os.environ.get("SEO_AGENT_WEB_SEARCH", "1") != "0",
            extra_instructions=os.environ.get("SEO_AGENT_INSTRUCTIONS", ""),
            max_turns=args.max_turns,
        )
    except BuildError as error:
        print(f"SEO agent stopped: the current site does not build: {error}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(agent.kickoff())
        return 0

    try:
        outcome = agent.run()
    except AgentError as error:
        print(f"SEO agent stopped: {error}", file=sys.stderr)
        return 2
    except anthropic.AuthenticationError:
        print("SEO agent stopped: ANTHROPIC_API_KEY is missing or invalid.", file=sys.stderr)
        return 2
    except anthropic.APIStatusError as error:
        print(f"SEO agent stopped: API error {error.status_code} (request id {error.request_id}): {error.message}",
              file=sys.stderr)
        return 2
    except anthropic.APIConnectionError as error:
        print(f"SEO agent stopped: could not reach the API: {error}", file=sys.stderr)
        return 2

    args.report.write_text(render_report(outcome, model), encoding="utf-8")
    args.title_file.write_text(outcome.title + "\n", encoding="utf-8")
    print(f"{outcome.title}\nChanged files: {', '.join(outcome.changed_files) or 'none'}")
    return 0
