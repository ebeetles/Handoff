"""File-level audio operations. Everything that shells out to ffmpeg/ffprobe/demucs lives here."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SERVE_SR = 44100
STEM_NAMES = ("drums", "bass", "vocals", "other")
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".aiff", ".aif", ".opus"}


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} not found on PATH. Install it (macOS: brew install ffmpeg) and rerun.")


def file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "track"


def to_flac(src: Path, dst: Path) -> None:
    """Serve lossless audio. MP3/AAC/Opus encoders add priming samples (often ~25 ms)
    that browsers may or may not trim when decoding; that would silently shift the
    beat grid. FLAC has no encoder delay, so analysis and playback see identical samples."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "2",
                    "-ar", str(SERVE_SR), "-sample_fmt", "s16", str(dst)], check=True)


def probe_tags(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=title,artist",
                          "-of", "json", str(path)], capture_output=True, text=True)
    try:
        tags = json.loads(out.stdout).get("format", {}).get("tags", {})
    except json.JSONDecodeError:
        tags = {}
    return {k.lower(): v for k, v in tags.items()}


def guess_title_artist(path: Path) -> tuple[str, str]:
    tags = probe_tags(path)
    if tags.get("title"):
        return tags["title"], tags.get("artist", "Unknown artist")
    stem = path.stem
    if " - " in stem:  # "Artist - Title" filename convention
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip()
    return stem, "Unknown artist"


def find_provided_stems(src: Path) -> Path | None:
    """Pre-separated stems (official stem packs, remix kits, or the demo generator) can sit
    next to the source as '<name>.stems/{drums,bass,vocals,other}.<ext>'. They must be
    sample-aligned with the source file."""
    d = src.parent / f"{src.stem}.stems"
    if not d.is_dir():
        return None
    found = {n: next((p for p in d.iterdir() if p.stem == n and p.suffix.lower() in AUDIO_EXTS), None) for n in STEM_NAMES}
    missing = [n for n, p in found.items() if p is None]
    if missing:
        sys.exit(f"{d} is missing stems: {missing}")
    return d


def copy_provided_stems(stem_dir: Path, out_dir: Path) -> dict[str, str]:
    paths = {}
    for name in STEM_NAMES:
        src = next(p for p in stem_dir.iterdir() if p.stem == name and p.suffix.lower() in AUDIO_EXTS)
        dst = out_dir / f"stem_{name}.flac"
        to_flac(src, dst)
        paths[name] = f"audio/{dst.name}"
    return paths


def separate_stems(mix_flac: Path, out_dir: Path, model: str = "htdemucs") -> dict[str, str]:
    """Run Demucs on the served mix and write FLAC stems next to it.

    Demucs keeps sample alignment with its input, and we feed it the exact file we
    serve, so stems line up sample-for-sample with the mix and the beat grid.
    Slow on CPU (minutes per track); run it once and cache.
    """
    try:
        import demucs  # noqa: F401
    except ImportError:
        sys.exit("--stems needs Demucs: pip install demucs  (first run also downloads model weights)")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "demucs", "-n", model, "-o", tmp, str(mix_flac)], check=True)
        produced = Path(tmp) / model / mix_flac.stem
        paths = {}
        for name in STEM_NAMES:
            wav = produced / f"{name}.wav"
            if not wav.exists():
                sys.exit(f"Demucs did not produce {wav}. Check the model name and Demucs version.")
            dst = out_dir / f"stem_{name}.flac"
            to_flac(wav, dst)
            paths[name] = f"audio/{dst.name}"
    return paths
