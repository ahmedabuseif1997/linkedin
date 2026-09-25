"""Exercises the agent loop against a scripted fake MiniMax (OpenAI-compatible) client: no network, no API key."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletion

from seo_agent.agent import TOOLS, AgentError, SeoAgent, render_report
from seo_agent.workspace import SiteWorkspace
from tests.conftest import SITE_URL

OLD_DESCRIPTION = (
    '<meta name="description" content="Ahmed Abouseif — Dubai executive in HR, strategic operations, '
    'corporate contracts, business development, mobility partnerships &amp; intelligent digital solutions.">'
)
NEW_DESCRIPTION = (
    '<meta name="description" content="Ahmed Abouseif — Dubai executive in HR, strategic operations, '
    'corporate contracts, business development and mobility partnerships.">'
)


def completion(calls=None, content="", finish_reason=None, reasoning=None):
    """A Chat Completions response as MiniMax returns it (reasoning in reasoning_details)."""
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": cid, "type": "function",
             "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
            for cid, name, args in calls
        ]
    if reasoning:
        message["reasoning_details"] = [{"type": "reasoning.text", "text": reasoning}]
    return ChatCompletion.model_validate({
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "MiniMax-M3",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": finish_reason or ("tool_calls" if calls else "stop")}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    })


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(copy.deepcopy(request))
        return self.script.pop(0)


def make_agent(project: Path, script, **kwargs) -> tuple[SeoAgent, FakeClient]:
    client = FakeClient(script)
    agent = SeoAgent(client, SiteWorkspace(project, SITE_URL), model="MiniMax-M3", **kwargs)
    return agent, client


def tool_messages(request) -> list[dict]:
    """Tool results sent at the end of a request, in order."""
    out = []
    for message in reversed(request["messages"]):
        if message["role"] != "tool":
            break
        out.append(message)
    return list(reversed(out))


SUBMIT_NONE = ("t_end", "submit_report", {"title": "SEO: none", "summary": "none"})


def test_happy_path_edits_validates_and_reports(project: Path):
    agent, client = make_agent(project, [
        completion([("t1", "run_seo_audit", {}), ("t2", "read_site_file", {"path": "index.html", "end_line": 12})],
                   reasoning="Start with the audit."),
        completion([("t3", "edit_site_file", {"path": "index.html", "old_string": OLD_DESCRIPTION, "new_string": NEW_DESCRIPTION})]),
        completion([("t4", "submit_report", {"title": "SEO: shorten meta description", "summary": "## Changes\n- Shorter."})]),
    ])
    outcome = agent.run()

    assert outcome.title == "SEO: shorten meta description"
    assert outcome.changed_files == ["index.html"]
    assert NEW_DESCRIPTION in (project / "site" / "index.html").read_text(encoding="utf-8")

    first = client.requests[0]
    assert first["model"] == "MiniMax-M3"
    assert first["extra_body"] == {"reasoning_split": True}
    assert first["tools"] == TOOLS
    assert first["messages"][0]["role"] == "system"
    assert "Canonical site URL: https://example.com/" in first["messages"][1]["content"]

    # the assistant turn goes back complete: tool calls plus MiniMax's reasoning_details
    second = client.requests[1]
    assistant = second["messages"][2]
    assert assistant["role"] == "assistant"
    assert [c["id"] for c in assistant["tool_calls"]] == ["t1", "t2"]
    assert assistant["reasoning_details"] == [{"type": "reasoning.text", "text": "Start with the audit."}]
    results = tool_messages(second)
    assert [r["tool_call_id"] for r in results] == ["t1", "t2"]
    assert not any(r["content"].startswith("ERROR:") for r in results)

    report = render_report(outcome, "MiniMax-M3")
    assert "| Errors | 0 | 0 |" in report and "`site/index.html`" in report and "MiniMax-M3, 3 API requests" in report


def test_changing_a_protected_fact_is_rejected_until_reverted(project: Path):
    fact_old, fact_new = 'data-count="400">400<', 'data-count="500">500<'
    agent, client = make_agent(project, [
        completion([("t1", "edit_site_file", {"path": "index.html", "old_string": fact_old, "new_string": fact_new})]),
        completion([("t2", "submit_report", {"title": "SEO: x", "summary": "x"})]),
        completion([("t3", "edit_site_file", {"path": "index.html", "old_string": fact_new, "new_string": fact_old})]),
        completion([("t4", "submit_report", {"title": "SEO: no changes", "summary": "Nothing to change."})]),
    ])
    outcome = agent.run()

    rejection = tool_messages(client.requests[2])[0]
    assert rejection["content"].startswith("ERROR: Validation failed") and "Protected fact changed" in rejection["content"]
    assert outcome.changed_files == []
    assert agent.rejections == 1


def test_untranslated_new_text_is_rejected(project: Path):
    agent, client = make_agent(project, [
        completion([("t1", "edit_site_file", {"path": "index.html", "old_string": "<h2>Clarity under pressure.</h2>",
                                              "new_string": "<h2>Calm under pressure.</h2>"})]),
        completion([("t2", "submit_report", {"title": "SEO: x", "summary": "x"})]),
        completion([("t3", "submit_report", {"title": "SEO: x", "summary": "x"})]),
        completion([("t4", "submit_report", {"title": "SEO: x", "summary": "x"})]),
    ])
    with pytest.raises(AgentError, match="still fail validation"):
        agent.run()
    assert "without an Arabic dictionary entry" in tool_messages(client.requests[2])[0]["content"]


def test_bad_arguments_and_sandbox_errors_go_back_to_the_model(project: Path):
    agent, client = make_agent(project, [
        completion([("t1", "read_site_file", {"file": "index.html"}),
                    ("t2", "read_site_file", {"path": "../README.md"}),
                    ("t3", "read_site_file", '{"path": "index.html"'),
                    ("t4", "delete_everything", {})]),
        completion([SUBMIT_NONE]),
    ])
    agent.run()
    bad_input, escape, bad_json, unknown = tool_messages(client.requests[1])
    assert bad_input["content"].startswith("ERROR:") and "INVALID_INPUT" in bad_input["content"]
    assert escape["content"].startswith("ERROR:") and "must not contain '..'" in escape["content"]
    assert bad_json["content"].startswith("ERROR:") and "INVALID_JSON" in bad_json["content"]
    assert unknown["content"] == "ERROR: Unknown tool 'delete_everything'."


def test_truncated_reply_stops_without_running_tools(project: Path):
    agent, _ = make_agent(project, [
        completion([("t1", "edit_site_file", {"path": "index.html", "old_string": "<title>", "new_string": "<ti"})],
                   finish_reason="length"),
    ])
    before = (project / "site" / "index.html").read_text(encoding="utf-8")
    with pytest.raises(AgentError, match="cut off"):
        agent.run()
    assert (project / "site" / "index.html").read_text(encoding="utf-8") == before


def test_content_filter_stops_the_run(project: Path):
    agent, _ = make_agent(project, [completion(content="", finish_reason="content_filter")])
    with pytest.raises(AgentError, match="content filter"):
        agent.run()


def test_ending_without_a_report_is_nudged_once(project: Path):
    agent, client = make_agent(project, [
        completion(content="Done."),
        completion([SUBMIT_NONE]),
    ])
    agent.run()
    assert client.requests[1]["messages"][-1] == {"role": "user", "content": "Finish by calling submit_report."}


def test_ending_twice_without_a_report_fails(project: Path):
    agent, _ = make_agent(project, [completion(content="Done."), completion(content="Still done.")])
    with pytest.raises(AgentError, match="without calling submit_report"):
        agent.run()


def test_missing_api_key_fails_fast(monkeypatch, capsys):
    from seo_agent.agent import main

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert main([]) == 2
    assert "MINIMAX_API_KEY is not set" in capsys.readouterr().err
