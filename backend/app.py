"""The Handoff backend: the on-demand composer (Chunk 7), and, when hosted, the gatekeeper of a
private library (signed links). Run locally from the repo root:

pipeline/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

Locally it only answers the local board and saves compositions into web/public/library. Hosted
(see backend/hosting.py and docs/DEPLOY.md) every call needs the access code, the library is
read from R2, compositions go back to the browser (which keeps them), and composing is
rate-limited. Only musical analysis is sent to Claude; API keys stay server-side.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal

import jsonschema
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from handoff_pipeline.compose.composer import Composer, load_api_key  # noqa: E402
from handoff_pipeline.compose.facts import load_track  # noqa: E402
from plan_transitions import SCHEMA, TRANSITIONS_VERSION, load_saved, merge_pair, plan_pair  # noqa: E402
from handoff_pipeline.compose.concepts import History  # noqa: E402
from backend.hosting import LIBRARY_PATH, R2Library, RateLimit, Settings, client_ip  # noqa: E402


REFINE_ROUNDS = 2   # draft `candidates` ideas, keep the strongest, revise it up to twice; one transition comes back


class ComposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    out_track: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_-]+$")
    in_track: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_-]+$")
    out_start_bar: int | None = Field(default=None, ge=0)
    min_start_bar: int = Field(default=0, ge=0)
    max_bars: int = Field(default=16, ge=4, le=32)
    candidates: int = Field(default=3, ge=1, le=6)
    brief: str = Field(default="Creative, energetic, track-specific transitions", max_length=500)


class SignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(min_length=1, max_length=64)


def create_app(library: Path = ROOT / "web/public/library", composer: Composer | None = None,
               history: History | None = None, settings: Settings | None = None, store: R2Library | None = None) -> FastAPI:
    settings = settings or Settings(None, (), None, ROOT / "pipeline/cache", 10, 6 * 3600)
    if settings.hosted and store is None:
        store = R2Library(settings.r2, settings.data_dir / "library")
    api = FastAPI(title="Handoff", version="1.1.0")
    if settings.allowed_origins:   # the hosted board calls from another origin
        api.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "X-Access-Code"], max_age=3600)
    cache = settings.data_dir if settings.hosted else ROOT / "pipeline/cache"
    comp = composer or Composer(cache / "compose")
    memory = history or History(cache / "composer_history.jsonl")   # what not to repeat
    lock = Lock()  # bound paid work and serialize the library/cache writer
    limit = RateLimit(settings.compose_per_hour)

    def require_code(request: Request) -> None:
        if not settings.code_ok(request.headers.get("x-access-code")):
            raise HTTPException(401, "Wrong or missing access code")

    @api.get("/api/health")
    def health() -> dict:
        return {"ok": True, "hosted": settings.hosted}

    @api.get("/api/session")
    def session(request: Request) -> dict:
        require_code(request)
        return {"ok": True, "composer": composer is not None or bool(load_api_key())}

    @api.post("/api/library/sign")
    def sign(body: SignRequest, request: Request) -> dict:
        require_code(request)
        if store is None:
            raise HTTPException(404, "This server serves no library (the board reads it locally)")
        bad = [p for p in body.paths if not LIBRARY_PATH.fullmatch(p)]
        if bad:
            raise HTTPException(422, f"Not a library path: {bad[0][:80]}")
        return {"urls": {p: store.sign(p, settings.sign_ttl_s) for p in body.paths}, "expires_in": settings.sign_ttl_s}

    def track_dir(tid: str) -> Path | None:
        if store is not None:
            return store.track_dir(tid)
        p = library / tid
        return p if (p / "analysis.json").is_file() and p.resolve().parent == library.resolve() else None

    @api.post("/api/composer/compose")
    def compose(body: ComposeRequest, request: Request) -> dict:
        # The composer spends the API key: the access code (hosted), the board's own origin, a
        # JSON-only POST, and a rate limit keep other websites and visitors from billing it.
        require_code(request)
        if not settings.origin_ok(request.headers.get("origin")):
            raise HTTPException(403, "Use the Handoff board to compose")
        if body.out_track == body.in_track:
            raise HTTPException(422, "Load two different tracks")
        dirs = [track_dir(tid) for tid in (body.out_track, body.in_track)]
        if any(d is None for d in dirs):
            raise HTTPException(404, "Track is not in this library")
        if composer is None and not load_api_key():
            raise HTTPException(503, "The server has no Anthropic API key (ANTHROPIC_API_KEY)")
        if settings.hosted:
            wait = limit.take(f"{request.headers.get('x-access-code', '')}|{client_ip(request.headers, request.client and request.client.host)}")
            if wait:
                raise HTTPException(429, f"That's the limit of {settings.compose_per_hour} compositions an hour; try again in {int(wait // 60) + 1} min")
        paths = dirs
        if not lock.acquire(blocking=False):
            raise HTTPException(429, "A composition is already running; wait for it to finish")
        try:
            a, b = (load_track(p) for p in paths)
            options = body.model_dump(exclude={"schema_version", "out_track", "in_track", "candidates"})
            pair = plan_pair(a, b, "llm", comp, body.candidates, REFINE_ROUNDS, memory, **options)
            if pair["composer"] and pair["composer"].get("error"):
                raise HTTPException(502, pair["composer"]["error"])
            doc = {"schema_version": TRANSITIONS_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "pairs": [pair]}
            jsonschema.validate(doc, SCHEMA)
            if store is not None:
                return doc   # hosted: the library is read-only; the browser keeps compositions
            # Keep other pairs and previously composed exits, including the rules baseline.
            dest = library / "transitions.json"
            # Pairs saved by an older version are recompiled from their plans (no API calls).
            loaded = {a.id: a, b.id: b}
            def track(tid: str):  # noqa: E306
                if tid not in loaded and (library / tid / "analysis.json").is_file():
                    loaded[tid] = load_track(library / tid)
                return loaded.get(tid)
            merged = load_saved(dest, track)
            merged[(a.id, b.id)] = merge_pair(merged.get((a.id, b.id)), pair)
            saved = {**doc, "pairs": list(merged.values())}
            jsonschema.validate(saved, SCHEMA)
            tmp = dest.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, separators=(",", ":")))
            tmp.replace(dest)
            return doc
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        finally:
            lock.release()

    return api


app = create_app(settings=Settings.from_env())
