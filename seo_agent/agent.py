"""SEO agent: a MiniMax tool-calling loop that audits and improves site/, then writes a pull-request report.

Uses MiniMax's OpenAI-compatible Chat Completions API.

Usage: python -m seo_agent [--report seo-report.md] [--title-file seo-title.txt] [--dry-run]

Environment:
  MINIMAX_API_KEY            API key (required unless --dry-run)
  MINIMAX_BASE_URL           API base URL (default: https://api.minimax.io/v1;
                             mainland China accounts: https://api.minimaxi.com/v1)
  SEO_AGENT_MODEL            model id (default: MiniMax-M3)
  SEO_AGENT_INSTRUCTIONS     optional extra focus for this run
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

import openai

from site_tools.audit import AuditResult
from site_tools.build import ROOT, BuildError, load_site_url
from seo_agent.workspace import SiteWorkspace, ToolError, extra_hosts_from_env

DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_BASE_URL = "https://api.minimax.io/v1"
MAX_TURNS = 40
MAX_REPORT_REJECTIONS = 2
INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text(encoding="utf-8")


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        },
    }


TOOLS = [
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
_SCHEMAS = {tool["function"]["name"]: tool["function"]["parameters"] for tool in TOOLS}
_PY_TYPES = {"string": str, "integer": int}


class AgentError(Exception):
    pass


@dataclass
class Usage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, usage) -> None:
        self.requests += 1
        if usage is not None:
            self.prompt_tokens += usage.prompt_tokens or 0
            self.completion_tokens += usage.completion_tokens or 0


@dataclass
class Outcome:
    title: str
    summary: str
    changed_files: list[str]
    baseline: AuditResult
    final: AuditResult
    usage: Usage = field(default_factory=Usage)


def validate_input(schema: dict, data) -> str | None:
    """Return an error message if `data` does not match the tool's parameter schema."""
    if not isinstance(data, dict):
        return "arguments must be a JSON object"
    unknown = set(data) - set(schema["properties"])
    if unknown:
        return f"unknown argument(s): {sorted(unknown)}"
    for key in schema["required"]:
        if key not in data:
            return f"missing required argument {key!r}"
    for key, value in data.items():
        expected = _PY_TYPES[schema["properties"][key]["type"]]
        if not isinstance(value, expected) or isinstance(value, bool):
            return f"argument {key!r} must be of type {schema['properties'][key]['type']}"
    return None


def assistant_turn(message) -> dict:
    """The assistant message to keep in history. MiniMax requires the complete message, including
    reasoning_details (the model's interleaved thinking), to be sent back on the next request."""
    turn: dict = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        turn["tool_calls"] = [
            {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in message.tool_calls
        ]
    reasoning = getattr(message, "reasoning_details", None)
    if reasoning:
        turn["reasoning_details"] = reasoning
    return turn


def facts_diff(before: dict, after: dict) -> list[str]:
    return [f"{key} changed from {before.get(key)} to {after.get(key)}" for key in before if before.get(key) != after.get(key)]


class SeoAgent:
    def __init__(self, client, workspace: SiteWorkspace, *, model: str, extra_instructions: str = "",
                 max_turns: int = MAX_TURNS):
        self.client = client
        self.workspace = workspace
        self.model = model
        self.max_turns = max_turns
        self.extra_instructions = extra_instructions.strip()
        self.usage = Usage()
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

    # ----- tool execution ---------------------------------------------------------------------

    def _execute(self, call) -> tuple[dict, Outcome | None]:
        def result(content: str, is_error: bool = False) -> dict:
            return {"role": "tool", "tool_call_id": call.id, "content": ("ERROR: " + content) if is_error else content}

        name = call.function.name
        schema = _SCHEMAS.get(name)
        if schema is None:
            return result(f"Unknown tool {name!r}.", True), None
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            return result(json.dumps({"INVALID_JSON": call.function.arguments}, ensure_ascii=False), True), None
        problem = validate_input(schema, args)
        if problem:
            return result(json.dumps({"INVALID_INPUT": problem, "received": args}, ensure_ascii=False), True), None

        ws = self.workspace
        try:
            if name == "list_site_files":
                return result(ws.list_files()), None
            if name == "read_site_file":
                return result(ws.read_file(args["path"], args.get("start_line"), args.get("end_line"))), None
            if name == "edit_site_file":
                return result(ws.edit_file(args["path"], args["old_string"], args["new_string"])), None
            if name == "create_site_file":
                return result(ws.create_file(args["path"], args["content"])), None
            if name == "fetch_live_url":
                return result(ws.fetch_live(args["url"])), None
            if name == "run_seo_audit":
                current = ws.build_and_audit()
                return result(current.to_markdown() + "\nProtected facts: " + json.dumps(current.facts, ensure_ascii=False)), None
            if name == "submit_report":
                return self._submit(args, result)
        except BuildError as error:
            return result(f"Build failed: {error}", True), None
        except ToolError as error:
            return result(str(error), True), None
        raise AssertionError(f"unhandled tool {name}")

    def _submit(self, args: dict, result) -> tuple[dict, Outcome | None]:
        problems: list[str] = []
        try:
            final = self.workspace.build_and_audit()
        except BuildError as error:
            final = None
            problems.append(f"The build fails: {error}")
        if final is not None:
            known = {(b.check, b.message) for b in self.baseline.findings}
            problems += [f"New audit {f.severity} — {f.check}: {f.message}" for f in final.findings
                         if f.severity in ("error", "warning") and f.check != "arabic-translation"
                         and (f.check, f.message) not in known]
            problems += [f"Protected fact changed — {d}" for d in facts_diff(self.baseline.facts, final.facts)]
            problems += [f"Visible English text without an Arabic dictionary entry — {t}"
                         for t in final.untranslated if t not in self.baseline.untranslated]
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
        messages: list[dict] = [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": self.kickoff()},
        ]
        nudged = False
        for _ in range(self.max_turns):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS,
                extra_body={"reasoning_split": True},  # thinking arrives in reasoning_details, not in content
            )
            self.usage.add(response.usage)
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise AgentError("the model's reply was cut off (finish_reason=length); no truncated tool call was run")
            if choice.finish_reason == "content_filter":
                raise AgentError("the model's reply was blocked by the provider's content filter")

            message = choice.message
            messages.append(assistant_turn(message))
            if not message.tool_calls:
                if nudged:
                    raise AgentError("model ended without calling submit_report")
                nudged = True
                messages.append({"role": "user", "content": "Finish by calling submit_report."})
                continue

            outcome = None
            for call in message.tool_calls:
                tool_result, done = self._execute(call)
                messages.append(tool_result)
                outcome = outcome or done
            if outcome is not None:
                return outcome
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
        f"Run: {model}, {u.requests} API requests, {u.prompt_tokens:,} prompt tokens, {u.completion_tokens:,} completion tokens.",
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
    client = None
    if not args.dry_run:
        api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
        if not api_key:
            print("SEO agent stopped: MINIMAX_API_KEY is not set.", file=sys.stderr)
            return 2
        client = openai.OpenAI(api_key=api_key, base_url=os.environ.get("MINIMAX_BASE_URL") or DEFAULT_BASE_URL,
                               max_retries=4)
    try:
        workspace = SiteWorkspace(ROOT, load_site_url(ROOT / "site.config.json"), extra_hosts_from_env())
        agent = SeoAgent(client, workspace, model=model,
                         extra_instructions=os.environ.get("SEO_AGENT_INSTRUCTIONS", ""), max_turns=args.max_turns)
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
    except openai.AuthenticationError:
        print("SEO agent stopped: MINIMAX_API_KEY was rejected by the API.", file=sys.stderr)
        return 2
    except openai.APIStatusError as error:
        print(f"SEO agent stopped: API error {error.status_code}: {error.message}", file=sys.stderr)
        return 2
    except openai.APIConnectionError as error:
        print(f"SEO agent stopped: could not reach the API: {error}", file=sys.stderr)
        return 2

    args.report.write_text(render_report(outcome, model), encoding="utf-8")
    args.title_file.write_text(outcome.title + "\n", encoding="utf-8")
    print(f"{outcome.title}\nChanged files: {', '.join(outcome.changed_files) or 'none'}")
    return 0
