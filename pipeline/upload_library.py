"""Upload the board's library (web/public/library) to a private Cloudflare R2 bucket.

    R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=handoff-library \\
        python upload_library.py                       # upload what changed
    python upload_library.py --dry-run                 # list what would be uploaded
    python upload_library.py --cors https://handoff-web.onrender.com   # print the CORS rule to paste

The variables can also go in pipeline/.env (gitignored). Only what the board reads is uploaded:
index.json, transitions.json, and each track's analysis.json, waveform.json and audio/*.flac
(about 2.7 GB). Re-runs skip files whose size and MD5 match what's already in the bucket.
The bucket stays private: the backend hands out signed links (backend/hosting.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from backend.hosting import LIBRARY_PATH  # noqa: E402  (the same allow-list the backend signs)

TYPES = {".json": "application/json", ".flac": "audio/flac"}


def load_env() -> dict:
    env = dict(os.environ)
    dotenv = HERE / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            k, _, v = line.strip().partition("=")
            k = k.removeprefix("export ").strip()
            if k.startswith("R2_") and v.strip() and k not in env:
                env[k] = v.strip().strip("'\"")
    return env


def library_files(lib: Path) -> list[Path]:
    return sorted(p for p in lib.rglob("*") if p.is_file() and LIBRARY_PATH.fullmatch(p.relative_to(lib).as_posix()))


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cors_rule(origins: list[str]) -> list[dict]:
    """The R2 CORS policy (dashboard JSON format): the board may GET (and HEAD) library files."""
    return [{"AllowedOrigins": [o.rstrip("/") for o in origins], "AllowedMethods": ["GET", "HEAD"],
             "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600}]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", type=Path, default=HERE.parent / "web" / "public" / "library")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cors", nargs="+", metavar="ORIGIN", help="print the CORS policy JSON for these board origins and exit")
    args = ap.parse_args()
    if args.cors:
        print(json.dumps(cors_rule(args.cors), indent=2))
        return

    env = load_env()
    missing = [k for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET") if not env.get(k)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (environment or pipeline/.env).")
    import boto3
    s3 = boto3.client(service_name="s3", endpoint_url=env.get("R2_ENDPOINT") or f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                      aws_access_key_id=env["R2_ACCESS_KEY_ID"], aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"], region_name="auto")
    bucket = env["R2_BUCKET"]
    # One PUT per file (they're under ~50 MB): a multipart upload's ETag isn't the file's MD5,
    # which would make every re-run upload everything again.
    from boto3.s3.transfer import TransferConfig
    single_part = TransferConfig(multipart_threshold=1 << 30)

    existing: dict[str, tuple[int, str]] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            existing[o["Key"]] = (o["Size"], o["ETag"].strip('"'))

    files = library_files(args.library)
    todo = []
    for p in files:
        key = p.relative_to(args.library).as_posix()
        have = existing.get(key)
        if have and have[0] == p.stat().st_size and have[1] == md5(p):
            continue
        todo.append((p, key))
    size = sum(p.stat().st_size for p, _ in todo)
    print(f"{len(files)} library files; {len(todo)} to upload ({size / 1e9:.2f} GB).")
    for i, (p, key) in enumerate(todo, 1):
        print(f"  [{i}/{len(todo)}] {key} ({p.stat().st_size / 1e6:.1f} MB)", flush=True)
        if not args.dry_run:
            s3.upload_file(str(p), bucket, key, ExtraArgs={"ContentType": TYPES[p.suffix], "CacheControl": "private, max-age=3600"},
                           Config=single_part)
    stale = sorted(set(existing) - {p.relative_to(args.library).as_posix() for p in files})
    if stale:
        print(f"{len(stale)} objects in the bucket aren't in the library any more (left in place): {', '.join(stale[:5])}{' ...' if len(stale) > 5 else ''}")
    print("Done." if not args.dry_run else "Dry run: nothing uploaded.")


if __name__ == "__main__":
    main()
