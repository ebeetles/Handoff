"""On-demand Chunk 7 composer. Run from the repo root:

pipeline/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

Only musical analysis is sent to Claude. API keys and transport stay server-side.
This local development endpoint is not a public, authenticated multi-user service.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import urlparse

import jsonschema
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from handoff_pipeline.compose.composer import Composer, load_api_key  # noqa: E402
from handoff_pipeline.compose.facts import load_track  # noqa: E402
from plan_transitions import SCHEMA, TRANSITIONS_VERSION, load_saved, merge_pair, plan_pair  # noqa: E402
from handoff_pipeline.compose.concepts import History  # noqa: E402


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


def create_app(library: Path = ROOT / "web/public/library", composer: Composer | None = None,
               history: History | None = None) -> FastAPI:
    api = FastAPI(title="Handoff composer", version="1.0.0")
    comp = composer or Composer(ROOT / "pipeline/cache/compose")
    memory = history or History(ROOT / "pipeline/cache/composer_history.jsonl")   # what not to repeat
    lock = Lock()  # bound paid work and serialize the library/cache writer

    @api.post("/api/composer/compose")
    def compose(body: ComposeRequest, request: Request) -> dict:
        origin = request.headers.get("origin")
        # JSON-only POST + no CORS + loopback origin guard prevent another website billing this local key.
        if origin and urlparse(origin).netloc not in ("localhost:5173", "127.0.0.1:5173"):
            raise HTTPException(403, "Use the local Handoff board to compose")
        if body.out_track == body.in_track:
            raise HTTPException(422, "Load two different tracks")
        paths = [library / tid for tid in (body.out_track, body.in_track)]
        if any(not (p / "analysis.json").is_file() or p.resolve().parent != library.resolve() for p in paths):
            raise HTTPException(404, "Track is not in this library")
        if composer is None and not load_api_key():
            raise HTTPException(503, "Set ANTHROPIC_API_KEY in pipeline/.env, then compose again")
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


app = create_app()
