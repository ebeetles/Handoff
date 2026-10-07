"""The hosted setup, end to end on this machine: a local S3 server (moto) stands in for the
private R2 bucket, upload_library.py fills it with a 4-track subset, the backend runs in hosted
mode (access code, signed links, CORS), and the board is served with VITE_API_URL set.

Checks: the gate rejects a wrong code and accepts the right one; the library, credits, analysis
and audio all come through signed links from the bucket (none from the site); tracks load and
play; the transitions list loads; the code is remembered on reload. With --compose (one real,
paid composition, ~$0.15-0.30): compose in the browser, reload, and the composition is still there.

    pipeline/.venv/bin/python web/e2e/hosted.py [--compose]
Needs: pip install "moto[server]" boto3 (in pipeline/.venv). Starts and stops its own servers.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / "pipeline/.venv/bin/python")
LIB = ROOT / "web/public/library"
TRACKS = ["demo-amber-room", "demo-teal-signal", "morgan-page", "fortu-mendoza"]
CODE = "test-code-123"
WEB, API = 5197, 8011
COMPOSE = "--compose" in sys.argv


def wait_http(url, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{url} didn't come up")


def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok:
        raise SystemExit(1)


def main():
    from moto.server import ThreadedMotoServer
    import boto3
    sys.path.insert(0, str(ROOT / "pipeline"))
    from handoff_pipeline.build import write_index

    tmp = Path(tempfile.mkdtemp(prefix="handoff-hosted-"))
    procs = []
    moto = ThreadedMotoServer(port=0)
    moto.start()
    try:
        endpoint = f"http://127.0.0.1:{moto._server.server_port}"
        r2 = {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "id", "R2_SECRET_ACCESS_KEY": "secret", "R2_BUCKET": "handoff-library", "R2_ENDPOINT": endpoint}
        boto3.client("s3", endpoint_url=endpoint, aws_access_key_id="id", aws_secret_access_key="secret",
                     region_name="us-east-1").create_bucket(Bucket="handoff-library")
        # A 4-track library: the tracks, their index, and the transitions between them.
        sub = tmp / "library"
        ids = [next(d.name for d in LIB.iterdir() if d.name.startswith(t)) for t in TRACKS]
        for tid in ids:
            shutil.copytree(LIB / tid, sub / tid)
        write_index(sub)
        doc = json.loads((LIB / "transitions.json").read_text())
        doc["pairs"] = [p for p in doc["pairs"] if p["out_track"] in ids and p["in_track"] in ids]
        (sub / "transitions.json").write_text(json.dumps(doc))
        up = subprocess.run([PY, str(ROOT / "pipeline/upload_library.py"), "--library", str(sub)], env={**os.environ, **r2},
                            capture_output=True, text=True)
        check(up.returncode == 0, f"upload_library.py filled the bucket ({up.stdout.strip().splitlines()[0] if up.stdout else up.stderr[-200:]})")
        again = subprocess.run([PY, str(ROOT / "pipeline/upload_library.py"), "--library", str(sub)], env={**os.environ, **r2},
                               capture_output=True, text=True)
        check("0 to upload" in again.stdout, "a second upload skips unchanged files")

        board = f"http://localhost:{WEB}"
        api_env = {**os.environ, **r2, "ACCESS_CODE": CODE, "ALLOWED_ORIGINS": f"{board},http://127.0.0.1:{WEB}", "DATA_DIR": str(tmp / "data")}
        procs.append(subprocess.Popen([PY, "-m", "uvicorn", "backend.app:app", "--port", str(API)], cwd=ROOT, env=api_env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen(["npx", "vite", "--port", str(WEB), "--strictPort"], cwd=ROOT / "web",
                                      env={**os.environ, "VITE_API_URL": f"http://127.0.0.1:{API}"},
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_http(f"http://127.0.0.1:{API}/api/health")
        wait_http(board)

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-audio-output"])
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            errors, audio_hosts = [], set()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: audio_hosts.add(r.url.split("/")[2]) if r.url.split("?")[0].endswith(".flac") else None)
            page.goto(board)
            page.get_by_label("Access code").fill("wrong")
            page.get_by_role("button", name="Open the board").click()
            page.wait_for_selector("text=didn't work", timeout=20000)
            check(True, "a wrong code is refused")
            page.get_by_label("Access code").fill(CODE)
            page.get_by_role("button", name="Open the board").click()
            page.wait_for_selector(".board", timeout=30000)
            check(True, "the right code opens the board")
            page.get_by_role("button", name="Library", exact=True).click()
            page.wait_for_selector(".lib-row", timeout=20000)
            rows = page.locator(".lib-row").count()
            credit = page.locator(".lib-credit").first.inner_text() if page.locator(".lib-credit").count() else ""
            check(rows == 4 and "All rights reserved" in page.inner_text(".library"), f"the library index comes from the bucket ({rows} tracks, credits shown: {credit!r})")
            for title, deck in [("The Longest Road", "A"), ("Crypto", "B")]:
                if not page.locator(".library").count():
                    page.get_by_role("button", name="Library", exact=True).click()
                page.locator(".lib-row", has_text=title).get_by_text(f"Load to {deck}").click()
                page.wait_for_function(f"() => window.__handoff.engine.snapshot('{deck}').loaded", timeout=90000)
            check(audio_hosts == {f"127.0.0.1:{moto._server.server_port}"}, f"all audio came from the bucket through signed links ({sorted(audio_hosts)})")
            page.evaluate("() => window.__handoff.bus.dispatch({type: 'togglePlay', deck: 'A'})")
            page.wait_for_timeout(1500)
            level = page.evaluate("() => window.__handoff.engine.snapshot('A').level")
            check(level > 0.01, f"deck A plays (level {level:.2f})")
            composed = page.evaluate("""() => { const e = window.__handoff.engine;
              return window.__handoff.transitions.composedFor(e.snapshot('A').trackId, e.snapshot('B').trackId).length; }""")
            check(composed > 0, f"the transitions list loads from the bucket ({composed} for this pair)")
            before = composed
            if COMPOSE:
                page.evaluate("() => window.__handoff.bus.dispatch({type: 'pause', deck: 'A'})")
                page.get_by_role("button", name="Compose with AI", exact=True).click()
                page.get_by_role("button", name="Compose transition").click()
                page.wait_for_selector("dialog", state="detached", timeout=420000)
                after = page.evaluate("""() => { const e = window.__handoff.engine;
                  return window.__handoff.transitions.composedFor(e.snapshot('A').trackId, e.snapshot('B').trackId).filter(c => c.source === 'llm').length; }""")
                check(after >= 1, f"composed through the hosted backend ({after} AI transitions for the pair)")
            page.reload()
            page.wait_for_selector(".board", timeout=30000)
            check(page.locator(".gate").count() == 0, "the code is remembered on reload")
            for title, deck in [("The Longest Road", "A"), ("Crypto", "B")]:
                page.get_by_role("button", name="Library", exact=True).click()
                page.locator(".lib-row", has_text=title).get_by_text(f"Load to {deck}").click()
                page.wait_for_function(f"() => window.__handoff.engine.snapshot('{deck}').loaded", timeout=90000)
            kept = page.evaluate("""() => { const e = window.__handoff.engine;
              return window.__handoff.transitions.composedFor(e.snapshot('A').trackId, e.snapshot('B').trackId).length; }""")
            check(kept >= before + (1 if COMPOSE else 0), f"after a reload the pair still has its transitions ({kept})")
            check(not errors, f"no page errors {errors}")
            browser.close()
    finally:
        for pr in procs:
            pr.terminate()
        moto.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
