"""Local HTTP boundary and live-composer constraints, with no paid/network calls."""
import json
import sys
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import app as server
from handoff_pipeline.compose.concepts import History  # noqa: E402
from handoff_pipeline.compose.composer import Composer
from plan_transitions import plan_pair
from test_compose import ENTER, XF, STOP16, fake_reply, plan, track


def test_constraints_reach_the_model_and_change_the_cache(tmp_path):
    send, calls = fake_reply([plan([ENTER, XF, STOP16], start=16)])
    composer = Composer(tmp_path, transport=send)
    a, b = track(), track("b")
    options = dict(out_start_bar=16, min_start_bar=8, max_bars=16, brief="A vocal tease and a big reveal")
    composer.compose(a, b, 4, **options)
    payload = json.loads(calls[0]["messages"][0]["content"])
    assert payload["pair"]["exit_bars"] == [16] and payload["pair"]["entry_bars"][-1] == 112
    assert payload["brief"] == options["brief"] and payload["constraints"]["max_bars"] == 16
    assert payload["moves_version"] == 5 and payload["out"]["four_bar_windows"]
    composer.compose(a, b, 4, **options)
    assert len(calls) == 1
    composer.compose(a, b, 4, **{**options, "brief": "Percussion relay"})
    assert len(calls) == 2


def test_plan_cannot_ignore_requested_exit_or_length(tmp_path):
    send, _ = fake_reply([plan([ENTER, XF, STOP16], start=64)])
    pair = plan_pair(track(), track("b"), "llm", Composer(tmp_path, transport=send), 1,
                     out_start_bar=16, max_bars=8)
    assert pair["best"] is None and pair["candidates"][0]["recipe"] is None
    assert len(pair["candidates"][0]["errors"]) == 2


def test_transport_errors_are_redacted_and_not_cached(tmp_path):
    def broken(_):
        raise RuntimeError("secret-key-and-private-body")
    cands, meta = Composer(tmp_path, transport=broken).compose(track(), track("b"))
    assert not cands and "RuntimeError" in meta["error"] and "secret" not in meta["error"]
    assert not list(tmp_path.glob("*.json"))


def test_http_compose_validates_and_preserves_other_exits(tmp_path, monkeypatch):
    for tid in ("a", "b"):
        (tmp_path / tid).mkdir()
        (tmp_path / tid / "analysis.json").write_text("{}")
    monkeypatch.setattr(server, "load_track", lambda p: track(p.name))
    send, calls = fake_reply([plan([ENTER, XF, STOP16], start=16)])
    history = History(tmp_path / "history.jsonl")
    client = TestClient(server.create_app(tmp_path, Composer(tmp_path / "cache", transport=send), history))
    body = dict(schema_version=1, out_track="a", in_track="b", out_start_bar=16, max_bars=16)
    r = client.post("/api/composer/compose", json=body)
    assert r.status_code == 200, r.text
    doc = r.json()
    jsonschema.validate(doc, server.SCHEMA)
    assert doc["pairs"][0]["best"] == 0
    assert doc["pairs"][0]["candidates"][0]["recipe"]["anchor"]["out_start_bar"] == 16
    first = json.loads(calls[0]["messages"][0]["content"])
    assert len(first["concepts"]) == 3 and first["recent_work"] == []
    # Composing again is a fresh attempt, told what it just did (not a cache hit).
    r = client.post("/api/composer/compose", json=body)
    assert r.status_code == 200 and len(calls) == 2
    assert [w["idea"] for w in json.loads(calls[1]["messages"][0]["content"])["recent_work"]] == ["t"]
    assert len(history.recent()) == 2
    assert len(json.loads((tmp_path / "transitions.json").read_text())["pairs"][0]["candidates"]) == 1   # same plan, same id
    assert client.post("/api/composer/compose", json={**body, "out_track": "../a"}).status_code == 422
    assert client.post("/api/composer/compose", json={**body, "candidates": 100}).status_code == 422
    assert client.post("/api/composer/compose", json={**body, "in_track": "missing"}).status_code == 404
    assert client.post("/api/composer/compose", json={**body, "out_start_bar": 3}).status_code == 422
    assert client.post("/api/composer/compose", json=body, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    assert len(calls) == 2


def test_request_schema_matches_server():
    schema = json.loads((server.ROOT / "contracts/composer_request.schema.json").read_text())
    assert schema == server.ComposeRequest.model_json_schema()


def test_stream_request_id_comes_from_the_stream_not_the_message(monkeypatch):
    from types import SimpleNamespace
    from contextlib import nullcontext
    import anthropic
    from handoff_pipeline.compose.composer import anthropic_transport
    # Streaming messages do not carry the non-streaming response's _request_id attribute.
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"candidates":[]}')],
                              stop_reason="end_turn", model="test", usage=SimpleNamespace(to_dict=lambda: {}))
    stream = SimpleNamespace(get_final_message=lambda: message, request_id="req_test")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_: SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **_: nullcontext(stream)))))
    assert anthropic_transport("test-key")({})["request_id"] == "req_test"
