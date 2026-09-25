"""Exercises the agent loop against a scripted fake Claude client (no network, no API key)."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaMessage

from seo_agent.agent import FALLBACK_BETA, RETRY_MAX_TOKENS, AgentError, SeoAgent, echo_content, render_report
from seo_agent.workspace import SiteWorkspace
from tests.conftest import SITE_URL

OLD_DESCRIPTION = (
    '<meta name="description" content="Ahmed Abouseif — Dubai-based executive working across HR, strategic operations, '
    'corporate contracts, business development, mobility partnerships, and intelligent digital solutions.">'
)
NEW_DESCRIPTION = (
    '<meta name="description" content="Ahmed Abouseif — Dubai-based executive in HR, strategic operations, '
    'corporate contracts, business development and mobility partnerships.">'
)


def message(content, stop_reason="tool_use"):
    return BetaMessage.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 50},
    })


def tool_use(tool_id, name, tool_input):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **request):
        self.requests.append(copy.deepcopy(request))
        final = self.script.pop(0)

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return final

        return Stream()


def make_agent(project: Path, script, **kwargs) -> tuple[SeoAgent, FakeClient]:
    client = FakeClient(script)
    agent = SeoAgent(client, SiteWorkspace(project, SITE_URL), model="claude-opus-5", effort="high",
                     web_search=kwargs.pop("web_search", False), **kwargs)
    return agent, client


def tool_results(request) -> list[dict]:
    return request["messages"][-1]["content"]


def test_happy_path_edits_validates_and_reports(project: Path):
    agent, client = make_agent(project, [
        message([tool_use("t1", "run_seo_audit", {}), tool_use("t2", "read_site_file", {"path": "index.html", "end_line": 12})]),
        message([tool_use("t3", "edit_site_file", {"path": "index.html", "old_string": OLD_DESCRIPTION, "new_string": NEW_DESCRIPTION})]),
        message([tool_use("t4", "submit_report", {"title": "SEO: shorten meta description", "summary": "## Changes\n- Shorter description."})]),
    ])
    outcome = agent.run()

    assert outcome.title == "SEO: shorten meta description"
    assert outcome.changed_files == ["index.html"]
    assert outcome.baseline.count("warning") == 1 and outcome.final.count("warning") == 0
    assert NEW_DESCRIPTION in (project / "site" / "index.html").read_text(encoding="utf-8")

    first = client.requests[0]
    assert first["model"] == "claude-opus-5" and first["fallbacks"] == "default" and first["betas"] == [FALLBACK_BETA]
    assert first["thinking"] == {"type": "adaptive"} and first["output_config"] == {"effort": "high"}
    assert all(t.get("eager_input_streaming") for t in first["tools"])
    assert "Canonical site URL: https://example.com/" in first["messages"][0]["content"]
    # both parallel tool results go back in a single user message, in order
    assert [r["tool_use_id"] for r in tool_results(client.requests[1])] == ["t1", "t2"]
    assert not any(r["is_error"] for r in tool_results(client.requests[1]))

    report = render_report(outcome, "claude-opus-5")
    assert "| Warnings | 1 | 0 |" in report and "`site/index.html`" in report


def test_changing_a_protected_fact_is_rejected_until_reverted(project: Path):
    fact_old, fact_new = 'data-count="400">400<', 'data-count="500">500<'
    agent, client = make_agent(project, [
        message([tool_use("t1", "edit_site_file", {"path": "index.html", "old_string": fact_old, "new_string": fact_new})]),
        message([tool_use("t2", "submit_report", {"title": "SEO: x", "summary": "x"})]),
        message([tool_use("t3", "edit_site_file", {"path": "index.html", "old_string": fact_new, "new_string": fact_old})]),
        message([tool_use("t4", "submit_report", {"title": "SEO: no changes", "summary": "Nothing to change."})]),
    ])
    outcome = agent.run()

    rejection = tool_results(client.requests[2])[0]
    assert rejection["is_error"] and "Protected fact changed" in rejection["content"]
    assert outcome.changed_files == []
    assert agent.rejections == 1


def test_untranslated_new_text_is_rejected(project: Path):
    agent, client = make_agent(project, [
        message([tool_use("t1", "edit_site_file", {"path": "index.html", "old_string": "<h2>Clarity under pressure.</h2>",
                                                   "new_string": "<h2>Calm under pressure.</h2>"})]),
        message([tool_use("t2", "submit_report", {"title": "SEO: x", "summary": "x"})]),
        message([tool_use("t3", "submit_report", {"title": "SEO: x", "summary": "x"})]),
        message([tool_use("t4", "submit_report", {"title": "SEO: x", "summary": "x"})]),
    ])
    with pytest.raises(AgentError, match="still fail validation"):
        agent.run()
    assert "without an Arabic dictionary entry" in tool_results(client.requests[2])[0]["content"]


def test_invalid_tool_input_and_sandbox_errors_go_back_to_the_model(project: Path):
    agent, client = make_agent(project, [
        message([tool_use("t1", "read_site_file", {"file": "index.html"}),
                 tool_use("t2", "read_site_file", {"path": "../README.md"})]),
        message([tool_use("t3", "submit_report", {"title": "SEO: none", "summary": "none"})]),
    ])
    agent.run()
    bad_input, escape = tool_results(client.requests[1])
    assert bad_input["is_error"] and "INVALID_INPUT" in bad_input["content"]
    assert escape["is_error"] and "must not contain '..'" in escape["content"]


def test_max_tokens_retries_without_running_truncated_tools(project: Path):
    agent, client = make_agent(project, [
        message([tool_use("t1", "edit_site_file", {"path": "index.html", "old_string": "<title>", "new_string": "<ti"})],
                stop_reason="max_tokens"),
        message([tool_use("t2", "submit_report", {"title": "SEO: none", "summary": "none"})]),
    ])
    outcome = agent.run()
    assert client.requests[1]["max_tokens"] == RETRY_MAX_TOKENS
    assert len(client.requests[1]["messages"]) == 1  # the truncated turn was not appended
    assert outcome.changed_files == []


def test_refusal_stops_the_run(project: Path):
    agent, _ = make_agent(project, [message([], stop_reason="refusal")])
    with pytest.raises(AgentError, match="declined"):
        agent.run()


def test_ending_without_a_report_is_nudged_once(project: Path):
    agent, client = make_agent(project, [
        message([{"type": "text", "text": "Done."}], stop_reason="end_turn"),
        message([tool_use("t1", "submit_report", {"title": "SEO: none", "summary": "none"})]),
    ])
    agent.run()
    assert client.requests[1]["messages"][-1] == {"role": "user", "content": "Finish by calling submit_report."}


def test_web_search_tool_is_declared_when_enabled(project: Path):
    agent, client = make_agent(project, [message([tool_use("t1", "submit_report", {"title": "SEO: none", "summary": "none"})])],
                               web_search=True)
    agent.run()
    search = [t for t in client.requests[0]["tools"] if t.get("type") == "web_search_20260209"]
    assert search and search[0]["max_uses"] == 3 and "eager_input_streaming" not in search[0]


def test_echo_content_drops_declined_blocks_before_the_fallback_boundary():
    content = message([
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "partial"},
        tool_use("t0", "list_site_files", {}),
        {"type": "fallback", "from": {"model": "claude-opus-5"}, "to": {"model": "claude-opus-4-8"},
         "trigger": {"type": "refusal", "category": None}},
        tool_use("t1", "list_site_files", {}),
    ]).content
    assert [(b.type, getattr(b, "id", None)) for b in echo_content(content)] == [("text", None), ("tool_use", "t1")]
