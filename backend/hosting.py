"""Hosting the board for other people (Render + a private Cloudflare R2 bucket).

Locally nothing here is used: the library is web/public/library and the composer only answers
the local board. Hosted (R2_* set), the library lives in a private R2 bucket and nothing is
public: a visitor enters the access code, the backend checks it on every request and hands out
signed links that expire (R2 presigned GET URLs, S3 API domain: they don't work with custom
domains; the bucket needs a CORS rule for the board's origin). Composing is rate-limited per
code and visitor, since it spends the Anthropic key.

Configuration (environment variables):
  ACCESS_CODE          required when hosted; what visitors type in
  ALLOWED_ORIGINS      comma-separated board origins, e.g. https://handoff-web.onrender.com
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
  R2_ENDPOINT          optional override (tests use a local S3 server)
  COMPOSE_PER_HOUR     compositions per code and visitor per hour (default 10)
  SIGNED_LINK_HOURS    how long a signed link works (default 6)
  DATA_DIR             scratch space for caches (default /tmp/handoff; lost on restart)
"""
from __future__ import annotations

import hmac
import json
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

# Library paths a visitor may get a link to: the index, the transitions, and each track's
# analysis, waveform and audio. Nothing else in the bucket, and no way out of it.
LIBRARY_PATH = re.compile(r"^(index\.json|transitions\.json|[a-z0-9-]+/(analysis\.json|waveform\.json|audio/[a-z0-9_]+\.flac))$")
LOCAL_ORIGINS = ("localhost:5173", "127.0.0.1:5173")


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    endpoint: str

    @classmethod
    def from_env(cls, env: dict) -> "R2Config | None":
        keys = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
        if not any(env.get(k) for k in keys):
            return None
        missing = [k for k in keys if not env.get(k)]
        if missing:
            raise RuntimeError(f"Hosting is half-configured: set {', '.join(missing)}")
        endpoint = env.get("R2_ENDPOINT") or f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
        return cls(env["R2_ACCOUNT_ID"], env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"], env["R2_BUCKET"], endpoint)


@dataclass(frozen=True)
class Settings:
    access_code: str | None
    allowed_origins: tuple[str, ...]
    r2: R2Config | None
    data_dir: Path
    compose_per_hour: int
    sign_ttl_s: int

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        r2 = R2Config.from_env(env)
        code = (env.get("ACCESS_CODE") or "").strip() or None
        if r2 and not code:
            raise RuntimeError("Hosted (R2_* set) without ACCESS_CODE: refusing to serve the library and the composer to anyone")
        origins = tuple(o.strip().rstrip("/") for o in (env.get("ALLOWED_ORIGINS") or "").split(",") if o.strip())
        return cls(access_code=code, allowed_origins=origins, r2=r2,
                   data_dir=Path(env.get("DATA_DIR") or "/tmp/handoff"),
                   compose_per_hour=int(env.get("COMPOSE_PER_HOUR") or 10),
                   sign_ttl_s=int(float(env.get("SIGNED_LINK_HOURS") or 6) * 3600))

    @property
    def hosted(self) -> bool:
        return self.r2 is not None

    def code_ok(self, given: str | None) -> bool:
        if not self.access_code:
            return True
        return hmac.compare_digest((given or "").encode(), self.access_code.encode())

    def origin_ok(self, origin: str | None) -> bool:
        """The composer spends the API key: only the board may call it (locally, or the hosted
        board's origins). A missing Origin is a non-browser client, which needs the code anyway."""
        if not origin:
            return True
        netloc = urlparse(origin).netloc
        return netloc in LOCAL_ORIGINS or netloc in {urlparse(o).netloc for o in self.allowed_origins}


class R2Library:
    """The library in a private R2 bucket: signed links for the browser, and track analyses for
    the composer (downloaded once into cache_dir, so load_track can read them)."""

    def __init__(self, cfg: R2Config, cache_dir: Path, client=None) -> None:
        self.cfg, self.cache_dir = cfg, cache_dir
        if client is None:
            import boto3   # only needed when hosted
            client = boto3.client(service_name="s3", endpoint_url=cfg.endpoint, aws_access_key_id=cfg.access_key_id,
                                  aws_secret_access_key=cfg.secret_access_key, region_name="auto")
        self.client = client

    def sign(self, path: str, ttl_s: int) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.cfg.bucket, "Key": path}, ExpiresIn=ttl_s)

    def track_dir(self, track_id: str) -> Path | None:
        """A local folder holding this track's analysis.json, or None if it isn't in the bucket."""
        d = self.cache_dir / track_id
        if (d / "analysis.json").is_file():
            return d
        try:
            body = self.client.get_object(Bucket=self.cfg.bucket, Key=f"{track_id}/analysis.json")["Body"].read()
            json.loads(body)
        except Exception:
            return None
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "analysis.json.tmp"
        tmp.write_bytes(body)
        tmp.replace(d / "analysis.json")
        return d


class RateLimit:
    """At most `per_hour` events per key in any hour."""

    def __init__(self, per_hour: int) -> None:
        self.per_hour = per_hour
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.lock = Lock()

    def take(self, key: str, now: float | None = None) -> float:
        """0 if allowed (and counted), else seconds until the next one is."""
        now = time.time() if now is None else now
        with self.lock:
            q = self.events[key]
            while q and now - q[0] >= 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                return 3600 - (now - q[0])
            q.append(now)
            return 0.0


def client_ip(headers, fallback: str | None) -> str:
    """The visitor's address: Render's proxy puts it first in X-Forwarded-For."""
    fwd = headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (fallback or "unknown")
