"""Download artist-permitted tracks from Audius into pipeline/tracks/.

    python fetch_audius.py https://audius.co/teixo/tropical-house-bandlab PkglRZ1
    python fetch_audius.py --search "deep house" --genre "Deep House" --limit 5
    python fetch_audius.py --search techno --limit 3 --dry-run
    python fetch_audius.py --search "vocal house" --sort popular --min-plays 2000 --limit 5

Only tracks the artist has marked downloadable (and that aren't download-gated) are
fetched; everything else is skipped and reported. Files are saved as
"Artist - Title.<ext>" (the extension the content node serves: the original upload if the
artist kept it, otherwise MP3). preprocess.py converts them to FLAC and analyzes that FLAC,
so analysis and playback see the same samples.

For each file, overrides.json gets title, artist, license, and credit (artist + Audius URL).
Existing keys are never overwritten, so hand-tuned fixes like downbeat_shift survive.

Set AUDIUS_API_KEY in the environment for higher rate limits (optional; sent as the
x-api-key header, and only to api.audius.co). Get one at https://api.audius.co/plans.

API reference: https://api.audius.co/v1/swagger.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any, Iterator

from handoff_pipeline.audio_io import AUDIO_EXTS

API_BASE = "https://api.audius.co/v1"
API_HOST = urllib.parse.urlparse(API_BASE).hostname
SITE = "https://audius.co"
USER_AGENT = "handoff-fetch-audius/0.1"
SEARCH_PAGE = 50
MAX_SEARCH_PAGES = 10
HERE = Path(__file__).resolve().parent
CONTENT_TYPE_EXTS = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/flac": ".flac", "audio/x-flac": ".flac",
                     "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/aiff": ".aiff",
                     "audio/x-aiff": ".aiff", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
                     "audio/aac": ".aac", "audio/opus": ".opus"}

Track = dict[str, Any]


# ---------------------------------------------------------------- pure helpers (tested offline)

def skip_reason(track: Track) -> str | None:
    """Why this track can't be fetched, or None if it can. `is_downloadable` is the artist's
    switch; `access.download` says whether *we* may download (false when gated behind a
    follow, tip, or purchase). The search API's own filters aren't reliable, so check here."""
    if track.get("is_delete"):
        return "deleted"
    if track.get("is_available") is False:
        return "not available"
    if not track.get("is_downloadable"):
        return "not downloadable (artist hasn't enabled downloads)"
    if track.get("is_download_gated") or not (track.get("access") or {}).get("download", False):
        return "download is gated (follow/tip/purchase required)"
    return None


def quality_reason(track: Track, min_plays: int, min_s: float, max_s: float) -> str | None:
    """Why a fetchable track isn't worth fetching, or None. A plain search returns DJ mixes,
    skits and barely-played uploads; plays are a rough, cheap quality signal. An unknown
    duration passes; an unknown play count fails a play threshold."""
    d = track.get("duration")
    if d is not None and d > max_s:
        return f"{d / 60:.0f} min: a mix, not a track"
    if d is not None and d < min_s:
        return f"{d:.0f} s: too short"
    if min_plays and (track.get("play_count") or 0) < min_plays:
        return f"{track.get('play_count') or 0} plays (< {min_plays})"
    return None


def safe_filename_part(text: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text)
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return s[:120] or "untitled"


def artist_name(track: Track) -> str:
    user = track.get("user") or {}
    return (user.get("name") or user.get("handle") or "Unknown artist").strip()


def track_url(track: Track) -> str:
    return SITE + track["permalink"]


def base_name(track: Track) -> str:
    return f"{safe_filename_part(artist_name(track))} - {safe_filename_part(track.get('title') or '')}"


def pick_extension(content_type: str | None, disposition_filename: str | None, orig_filename: str | None) -> str:
    """Prefer what the server says it sent, then the original upload's name."""
    for name in (disposition_filename, orig_filename):
        if name:
            ext = Path(name).suffix.lower()
            if ext in AUDIO_EXTS:
                return ext
    mime = (content_type or "").split(";")[0].strip().lower()
    return CONTENT_TYPE_EXTS.get(mime, ".mp3")


def override_entry(track: Track) -> dict[str, str]:
    return {
        "title": (track.get("title") or "").strip(),
        "artist": artist_name(track),
        "license": track.get("license") or "Not specified on Audius (artist enabled downloads)",
        "credit": f"{artist_name(track)} — {track_url(track)}",
    }


def merge_overrides(existing: dict[str, dict], filename: str, entry: dict[str, str]) -> list[str]:
    """Add `entry` under `filename`, filling only keys that aren't already there.
    Returns the keys that were added."""
    current = existing.setdefault(filename, {})
    added = [k for k, v in entry.items() if k not in current]
    for k in added:
        current[k] = entry[k]
    return added


def is_url(ref: str) -> bool:
    return "/" in ref or ref.startswith("audius.co")


def track_id_from_location(location: str) -> str | None:
    m = re.search(r"/tracks/([A-Za-z0-9]+)", urllib.parse.urlparse(location).path)
    return m.group(1) if m else None


# ---------------------------------------------------------------- HTTP

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _StripKeyOffHost(urllib.request.HTTPRedirectHandler):
    """Downloads redirect to a content node on another host; don't send our key there."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).hostname != API_HOST:
            new.remove_header("X-api-key")
        return new


class AudiusClient:
    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key
        self._follow = urllib.request.build_opener(_StripKeyOffHost)
        self._nofollow = urllib.request.build_opener(_NoRedirect)

    def _request(self, path: str, params: dict[str, Any] | None = None) -> urllib.request.Request:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        req = urllib.request.Request(f"{API_BASE}{path}" + (f"?{query}" if query else ""))
        req.add_header("User-Agent", USER_AGENT)
        if self.api_key:
            req.add_header("x-api-key", self.api_key)
        return req

    def _json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with self._follow.open(self._request(path, params), timeout=30) as r:
            return json.load(r)

    def get_track(self, track_id: str) -> Track | None:
        try:
            return self._json(f"/tracks/{urllib.parse.quote(track_id)}").get("data")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            raise

    def resolve_track_id(self, url: str) -> str | None:
        """GET /resolve answers 302 -> /v1/tracks/{id}; read the id without following."""
        if not url.startswith("http"):
            url = "https://" + url
        try:
            self._nofollow.open(self._request("/resolve", {"url": url}), timeout=30)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                return track_id_from_location(e.headers.get("Location", ""))
            if e.code in (400, 404):
                return None
            raise
        return None

    def search(self, query: str, genres: list[str], sort: str = "relevant") -> Iterator[Track]:
        # has_downloads=true is the filter that works; only_downloadable did not, when tested.
        for page in range(MAX_SEARCH_PAGES):
            params: dict[str, Any] = {"query": query, "limit": SEARCH_PAGE, "offset": page * SEARCH_PAGE,
                                      "has_downloads": "true", "sort_method": sort}
            if genres:
                params["genre"] = genres
            batch = self._json("/tracks/search", params).get("data") or []
            yield from batch
            if len(batch) < SEARCH_PAGE:
                return

    def download(self, track_id: str, dest_dir: Path, base: str, orig_filename: str | None) -> Path:
        """Stream /tracks/{id}/download to dest_dir/<base><ext>. Writes to a temp file first
        so an interrupted download never leaves a truncated file for preprocess to analyze."""
        with self._follow.open(self._request(f"/tracks/{urllib.parse.quote(track_id)}/download"), timeout=60) as r:
            msg = Message()
            msg["content-disposition"] = r.headers.get("Content-Disposition", "")
            ext = pick_extension(r.headers.get("Content-Type"), msg.get_filename(), orig_filename)
            dest = dest_dir / f"{base}{ext}"
            fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".part-")
            try:
                with os.fdopen(fd, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                os.chmod(tmp, 0o644)   # mkstemp creates 0600
                os.replace(tmp, dest)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return dest


# ---------------------------------------------------------------- CLI

def existing_file(dest_dir: Path, base: str) -> Path | None:
    return next((p for p in dest_dir.glob(f"{glob_escape(base)}.*") if p.suffix.lower() in AUDIO_EXTS), None)


def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", s)


def candidates(client: AudiusClient, args: argparse.Namespace, report: list[tuple[str, str, str]]) -> Iterator[Track]:
    if args.search:
        yield from client.search(args.search, args.genre, args.sort)
        return
    for ref in args.tracks:
        tid = client.resolve_track_id(ref) if is_url(ref) else ref
        track = client.get_track(tid) if tid else None
        if track is None:
            report.append(("not found", ref, "no track with that URL/ID"))
            continue
        yield track


def write_overrides(path: Path, overrides: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(overrides, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracks", nargs="*", help="Audius track URLs or track IDs")
    ap.add_argument("--search", help="search query (instead of URLs/IDs)")
    ap.add_argument("--genre", action="append", default=[], help='genre filter for --search, e.g. "Techno" (repeatable)')
    ap.add_argument("--limit", type=int, default=5, help="max tracks to download (default 5)")
    ap.add_argument("--out", type=Path, default=HERE / "tracks")
    ap.add_argument("--overrides", type=Path, default=HERE / "overrides.json")
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded; write nothing")
    ap.add_argument("--sort", choices=["relevant", "popular", "recent"], default="relevant", help="search order (Audius sort_method)")
    ap.add_argument("--min-plays", type=int, default=0, help="skip tracks with fewer plays")
    ap.add_argument("--min-minutes", type=float, default=1.5, help="skip shorter tracks (skits, snippets)")
    ap.add_argument("--max-minutes", type=float, default=10, help="skip longer tracks (DJ mixes)")
    args = ap.parse_args()
    if bool(args.tracks) == bool(args.search):
        ap.error("give track URLs/IDs or --search, not both (and not neither)")
    if args.genre and not args.search:
        ap.error("--genre only applies to --search")
    if args.limit < 1:
        ap.error("--limit must be at least 1")

    client = AudiusClient(os.environ.get("AUDIUS_API_KEY") or None)
    args.out.mkdir(parents=True, exist_ok=True)
    overrides: dict[str, dict] = json.loads(args.overrides.read_text()) if args.overrides.exists() else {}
    report: list[tuple[str, str, str]] = []   # (status, track, detail)
    fetched = 0

    try:
        for track in candidates(client, args, report):
            if fetched >= args.limit:
                break
            label = f"{artist_name(track)} - {track.get('title', '?')}"
            reason = skip_reason(track) or quality_reason(track, args.min_plays, args.min_minutes * 60, args.max_minutes * 60)
            if reason:
                report.append(("skipped", label, reason))
                continue
            base = base_name(track)
            if args.dry_run:
                report.append(("would get", label, f"{track_url(track)}  [{track.get('license') or 'no license given'}; "
                                                   f"{track.get('play_count') or 0} plays, {(track.get('duration') or 0) / 60:.1f} min, {track.get('genre') or '?'}]"))
                fetched += 1
                continue
            path = existing_file(args.out, base)
            if path:
                status = "exists"
            else:
                path = client.download(track["id"], args.out, base, track.get("orig_filename"))
                status = "downloaded"
            added = merge_overrides(overrides, path.name, override_entry(track))
            write_overrides(args.overrides, overrides)   # after every file, so a crash loses nothing
            detail = f"{path.name} ({path.stat().st_size / 1e6:.1f} MB)"
            if not added:
                detail += "; overrides already set"
            elif len(added) < 4:
                detail += f"; filled overrides: {', '.join(added)}"
            report.append((status, label, detail))
            fetched += 1
    except urllib.error.HTTPError as e:
        report.append(("error", e.url or "", f"HTTP {e.code} {e.reason}"))
    except urllib.error.URLError as e:
        report.append(("error", API_BASE, f"network: {e.reason}"))

    width = max((len(s) for s, _, _ in report), default=0)
    for status, label, detail in report:
        print(f"  {status:<{width}}  {label}\n  {'':<{width}}    {detail}")
    skipped = sum(s in ("skipped", "not found") for s, _, _ in report)
    verb = "would download" if args.dry_run else "fetched"
    print(f"\n{fetched} {verb}, {skipped} skipped" + ("" if args.dry_run else f" -> {args.out}"))
    if args.search and fetched < args.limit:
        print(f"  (only {fetched} usable tracks in the first {MAX_SEARCH_PAGES * SEARCH_PAGE} results)")
    if not args.dry_run and fetched:
        print("Note: 'downloadable' means the artist allows personal downloads. Check each license "
              "before deploying anything publicly (ROADMAP Chunk 11).")
    sys.exit(1 if any(s == "error" for s, _, _ in report) else 0)


if __name__ == "__main__":
    main()
