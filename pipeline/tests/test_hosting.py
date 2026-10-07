"""Hosting (backend/hosting.py): access code, signed library links, CORS, origin guard, rate
limit, and read-only library. A fake R2 store stands in for the bucket; real presigning is
checked against moto (a local S3) when it's installed. No network, no API calls."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))
from backend import app as server  # noqa: E402
from backend.hosting import R2Config, RateLimit, Settings  # noqa: E402
from handoff_pipeline.compose.composer import Composer  # noqa: E402
from handoff_pipeline.compose.concepts import History  # noqa: E402
sys.path.insert(0, str(ROOT / "pipeline" / "tests"))
from test_compose import ENTER, STOP16, XF, fake_reply, plan, track  # noqa: E402

BOARD = "https://handoff-web.onrender.com"
R2 = {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "id", "R2_SECRET_ACCESS_KEY": "secret", "R2_BUCKET": "handoff-library"}


class FakeStore:
    def __init__(self, lib: Path):
        self.lib, self.signed = lib, []

    def sign(self, path, ttl):
        self.signed.append(path)
        return f"https://acct.r2.cloudflarestorage.com/handoff-library/{path}?X-Amz-Expires={ttl}&X-Amz-Signature=x"

    def track_dir(self, tid):
        d = self.lib / tid
        return d if (d / "analysis.json").is_file() else None


def hosted(tmp_path, per_hour=2, send=None):
    env = {**R2, "ACCESS_CODE": "open-sesame", "ALLOWED_ORIGINS": BOARD + "/", "COMPOSE_PER_HOUR": str(per_hour), "DATA_DIR": str(tmp_path / "data")}
    settings = Settings.from_env(env)
    lib = tmp_path / "lib"
    for tid in ("a", "b"):
        (lib / tid).mkdir(parents=True)
        (lib / tid / "analysis.json").write_text("{}")
    send = send or fake_reply([plan([ENTER, XF, STOP16], start=16)])[0]
    store = FakeStore(lib)
    app = server.create_app(lib, Composer(tmp_path / "cache", transport=send), History(tmp_path / "h.jsonl"), settings, store)
    return TestClient(app), store, lib


def test_hosting_needs_a_code_and_complete_r2_settings():
    with pytest.raises(RuntimeError, match="ACCESS_CODE"):
        Settings.from_env(R2)
    with pytest.raises(RuntimeError, match="R2_BUCKET"):
        Settings.from_env({k: v for k, v in R2.items() if k != "R2_BUCKET"})
    local = Settings.from_env({})
    assert not local.hosted and local.code_ok(None)
    assert R2Config.from_env(R2).endpoint == "https://acct.r2.cloudflarestorage.com"


def test_every_call_but_health_needs_the_code(tmp_path):
    client, _, _ = hosted(tmp_path)
    health = client.get("/api/health", headers={"Origin": "https://handoff-web-abc1.onrender.com"})
    assert health.json() == {"ok": True, "hosted": True, "origins": [BOARD]}
    assert health.headers["access-control-allow-origin"] == "*"          # readable even from a wrong address
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/session", headers={"X-Access-Code": "nope"}).status_code == 401
    assert client.get("/api/session", headers={"X-Access-Code": "open-sesame"}).json()["ok"] is True
    assert client.post("/api/library/sign", json={"paths": ["index.json"]}).status_code == 401


def test_signed_links_only_for_library_files(tmp_path):
    client, store, _ = hosted(tmp_path)
    h = {"X-Access-Code": "open-sesame"}
    ok = ["index.json", "transitions.json", "a/analysis.json", "a/waveform.json", "a/audio/stem_drums.flac", "a/audio/mix.flac"]
    r = client.post("/api/library/sign", json={"paths": ok}, headers=h)
    assert r.status_code == 200 and set(r.json()["urls"]) == set(ok) and r.json()["expires_in"] == 6 * 3600
    for bad in ["../secret", "a/../../etc/passwd", "a/audio/mix.mp3", "a/stems.json", "/index.json", "A/analysis.json", ".env"]:
        assert client.post("/api/library/sign", json={"paths": [bad]}, headers=h).status_code == 422, bad
    assert client.post("/api/library/sign", json={"paths": ["index.json"] * 65}, headers=h).status_code == 422
    assert store.signed.count("index.json") == 1


def test_cors_answers_only_the_board(tmp_path):
    client, _, _ = hosted(tmp_path)
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-access-code"}
    ok = client.options("/api/library/sign", headers={"Origin": BOARD, **pre})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == BOARD
    assert "x-access-code" in ok.headers["access-control-allow-headers"].lower()
    other = client.options("/api/library/sign", headers={"Origin": "https://evil.example", **pre})
    assert other.headers.get("access-control-allow-origin") is None


def test_hosted_compose_checks_origin_limits_rate_and_leaves_the_library_alone(tmp_path):
    client, _, lib = hosted(tmp_path, per_hour=2)
    body = dict(schema_version=1, out_track="a", in_track="b", out_start_bar=16, max_bars=16)
    h = {"X-Access-Code": "open-sesame", "Origin": BOARD, "X-Forwarded-For": "203.0.113.7"}
    assert client.post("/api/composer/compose", json=body, headers={**h, "X-Access-Code": ""}).status_code == 401
    assert client.post("/api/composer/compose", json=body, headers={**h, "Origin": "https://evil.example"}).status_code == 403
    with pytest.MonkeyPatch.context() as m:
        m.setattr(server, "load_track", lambda p: track(p.name))
        first = client.post("/api/composer/compose", json=body, headers=h)
        assert first.status_code == 200, first.text
        assert first.json()["pairs"][0]["candidates"][0]["recipe"]["anchor"]["out_start_bar"] == 16
        assert client.post("/api/composer/compose", json=body, headers=h).status_code == 200
        limited = client.post("/api/composer/compose", json=body, headers=h)
        assert limited.status_code == 429 and "limit of 2" in limited.json()["detail"]
        other_visitor = client.post("/api/composer/compose", json=body, headers={**h, "X-Forwarded-For": "198.51.100.2"})
        assert other_visitor.status_code == 200
    assert not (lib / "transitions.json").exists()
    assert client.post("/api/composer/compose", json={**body, "in_track": "missing"}, headers=h).status_code == 404


def test_rate_limit_window():
    r = RateLimit(2)
    assert r.take("k", 0) == 0 and r.take("k", 10) == 0 and r.take("k", 20) == pytest.approx(3580)
    assert r.take("k", 3600) == 0


def test_real_presigned_links_and_analysis_download(tmp_path):
    """boto3 against moto: a link that works without credentials, for exactly one object."""
    moto = pytest.importorskip("moto")
    import boto3
    import urllib.request
    from moto.server import ThreadedMotoServer
    from backend.hosting import R2Library
    srv = ThreadedMotoServer(port=0)
    srv.start()
    try:
        endpoint = f"http://127.0.0.1:{srv._server.server_port}"
        cfg = R2Config("acct", "id", "secret", "handoff-library", endpoint)
        s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id="id", aws_secret_access_key="secret", region_name="us-east-1")
        s3.create_bucket(Bucket="handoff-library")
        s3.put_object(Bucket="handoff-library", Key="a/analysis.json", Body=json.dumps({"id": "a"}).encode())
        store = R2Library(cfg, tmp_path / "cache")
        url = store.sign("a/analysis.json", 60)
        assert "X-Amz-Signature=" in url and "/handoff-library/a/analysis.json" in url
        assert json.loads(urllib.request.urlopen(url).read()) == {"id": "a"}
        d = store.track_dir("a")
        assert d and json.loads((d / "analysis.json").read_text()) == {"id": "a"}
        assert store.track_dir("missing") is None
    finally:
        srv.stop()
