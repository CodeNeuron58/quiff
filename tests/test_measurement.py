"""The measuring tools measure the right thing: cut-off replies aren't 'invalid
JSON', the race gives both agents the same effort and budget, and the local
order form serves what its checks expect."""

import html
import json
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import httpx
import pytest

from quiff.models import meta
from quiff.models.meta import (ChatClient, InvalidJSONError, ModelError, TruncatedError,
                               make_client, muse_spark_open)

SPIKES = Path(__file__).resolve().parent.parent / "spikes"
if str(SPIKES) not in sys.path:
    sys.path.insert(0, str(SPIKES))


def client_answering(content, finish_reason="stop"):
    def handler(request):
        return httpx.Response(200, json={
            "model": "muse-test",
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 512},
        })

    client = ChatClient(api_key="k", model="muse-test")
    client._http = httpx.Client(transport=httpx.MockTransport(handler),
                                base_url="https://example.test/v1")
    return client


def test_empty_cut_off_reply_is_truncation_not_invalid_json():
    with pytest.raises(TruncatedError):
        client_answering("", finish_reason="length").structured("p", {"type": "object"})


def test_half_written_json_at_the_budget_is_truncation():
    with pytest.raises(TruncatedError):
        client_answering('{"action": "ty', finish_reason="length").structured("p", {})


def test_finished_but_broken_json_is_invalid_json():
    with pytest.raises(InvalidJSONError):
        client_answering("not json", finish_reason="stop").structured("p", {})


def test_both_are_still_model_errors_for_existing_callers():
    assert issubclass(TruncatedError, ModelError) and issubclass(InvalidJSONError, ModelError)


def test_valid_reply_keeps_its_finish_reason():
    parsed, reply = client_answering('{"row": 4}').structured("p", {})
    assert parsed == {"row": 4} and reply.finish_reason == "stop"


def test_open_client_reads_its_host_from_env(monkeypatch):
    monkeypatch.setattr(meta, "load_env", lambda path=None: None)
    monkeypatch.setenv("MUSE_OPEN_API_KEY", "id.secret")
    monkeypatch.setenv("MUSE_OPEN_BASE_URL", "https://inference.us-west.modal.direct/v1")
    monkeypatch.setenv("MUSE_OPEN_MODEL", "muse-spark-open-816b-a42b")
    client = muse_spark_open()
    assert client.model == "muse-spark-open-816b-a42b"
    assert str(client._http.base_url).startswith("https://inference.us-west.modal.direct/v1")
    assert client._http.headers["Authorization"] == "Bearer id.secret"


def test_open_client_still_accepts_the_baseten_key(monkeypatch):
    monkeypatch.setattr(meta, "load_env", lambda path=None: None)
    for name in ("MUSE_OPEN_API_KEY", "MUSE_OPEN_BASE_URL", "MUSE_OPEN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASETEN_API_KEY", "bt")
    client = muse_spark_open()
    assert "baseten" in str(client._http.base_url)


def test_unknown_model_name_is_refused():
    with pytest.raises(ModelError):
        make_client("gpt")


# ---------------------------------------------------------------- the probe

class ScriptedClient:
    model = "muse-test"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def chat(self, prompt, **kwargs):
        return meta.ModelReply("ok", 100, 5, 1, self.model)

    def structured(self, prompt, schema, **kwargs):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, meta.ModelReply(json.dumps(outcome), 900, 50, 120, self.model, "stop")


def test_probe_counts_cut_offs_apart_from_invalid_json():
    import model_probe

    right = {"action": "type", "row": 4}
    client = ScriptedClient([right, TruncatedError("cut"), InvalidJSONError("bad"),
                             ModelError("HTTP 500")])
    result = model_probe.run(client, ["low"], calls=1, picks_per_effort=4)
    low = result["efforts"]["low"]
    assert low["outcomes"] == {"valid": 1, "cut_off": 1, "invalid_json": 1, "error": 1}
    assert (low["valid_json_pct"], low["cut_off_pct"], low["invalid_json_pct"]) == (25, 25, 25)
    assert low["correct_row_pct"] == 25 and low["correct_of_valid_pct"] == 100


def test_effort_chart_and_table_render():
    import effort_chart

    probe = {"model": "muse-test", "max_tokens": 8192, "efforts": {
        "minimal": {"decision_median_ms": 1500, "correct_row_pct": 100, "valid_json_pct": 100,
                    "cut_off_pct": 0, "invalid_json_pct": 0, "output_tokens_median": 80},
        "high": {"decision_median_ms": 6100, "correct_row_pct": 90, "valid_json_pct": 95,
                 "cut_off_pct": 5, "invalid_json_pct": 0, "output_tokens_median": 900}}}
    svg = effort_chart.chart_svg(probe)
    assert svg.startswith("<svg") and "minimal" in svg and "5% cut off" in svg
    table = effort_chart.table_markdown(probe)
    assert "| minimal | 1.5 s | 100% |" in table and "| high | 6.1 s | 90% |" in table


# ---------------------------------------------------------------- the race

def test_screenshot_agent_gets_quiffs_effort_and_budget():
    import race

    body = race.slow_request_body("muse-test", "goal", "data:image/png;base64,AA")
    assert body["reasoning_effort"] == race.RACE_EFFORT == "minimal"
    assert body["max_tokens"] == race.RACE_MAX_TOKENS


def test_screenshot_agent_gets_quiffs_step_cap():
    # found in the first fair race: the screenshot agent stopped at 12 steps
    # while Quiff's loop may take 25
    import inspect

    import race
    from quiff.loop import MAX_STEPS

    assert inspect.signature(race.slow_run).parameters["max_steps"].default == MAX_STEPS


async def test_quiff_gets_the_same_effort_and_budget(monkeypatch):
    import race

    seen = {}

    async def fake_loop_run(observer, decider, task, mode, rescuer=None, approver=None):
        seen.update(decider=decider, rescuer=rescuer)
        return {"success": False}

    monkeypatch.setattr(race, "_loop_run", fake_loop_run)
    fake_client = object()
    await race.quiff_run(None, type("T", (), {"id": "t", "goal": "g"})(), client=fake_client)
    decider = seen["decider"].inner
    assert decider.client is fake_client
    assert (decider.effort, decider.max_tokens) == (race.RACE_EFFORT, race.RACE_MAX_TOKENS)
    assert seen["rescuer"].effort == "high" and seen["rescuer"].client is fake_client


async def test_auto_approver_says_yes_and_counts():
    import race

    approve = race.auto_approver()
    assert await approve('Clicking "Submit order" submits it for good') is True
    assert len(approve.asked) == 1


def test_local_order_form_serves_what_its_checks_expect():
    import race_local

    server = race_local.QuietServer(("127.0.0.1", 0), race_local.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urlopen(f"{base}/order").read().decode()
        assert "Submit order" in page and 'name="custname"' in page
        body = ("custname=Asha+Rao&custtel=9876543210&custemail=asha%40example.com&size=medium"
                "&topping=cheese&topping=onion&comments=Ring+the+bell")
        reply = urlopen(Request(f"{base}/order", data=body.encode(), method="POST")).read()
        text = html.unescape(reply.decode())
    finally:
        server.shutdown()
    for check in race_local.local_tasks()["order"].checks:
        assert check["contains"] in text, check
