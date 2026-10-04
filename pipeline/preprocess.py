"""Preprocess a folder of tracks into the web app's library.

    python preprocess.py --in tracks/ --out ../web/public/library            # fast: analysis only
    python preprocess.py --in tracks/ --out ../web/public/library --stems    # + Demucs stems (slow)

Per-file corrections go in overrides.json, keyed by source filename:
    {"My Track.mp3": {"downbeat_shift": 1, "phrase_offset_bars": 0, "bpm_hint": 174,
                      "force_grid": "regular", "title": "...", "artist": "...",
                      "license": "CC BY 4.0", "credit": "..."}}
"""
import argparse
import json
import sys
from pathlib import Path

from handoff_pipeline.audio_io import AUDIO_EXTS, require_ffmpeg
from handoff_pipeline.build import process_track, write_index


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--stems", action="store_true", help="separate drums/bass/vocals/other with Demucs")
    ap.add_argument("--overrides", type=Path, default=Path("overrides.json"))
    ap.add_argument("--force", action="store_true", help="reprocess even if cached")
    ap.add_argument("--only", help="process only files whose name contains this text")
    args = ap.parse_args()

    require_ffmpeg()
    overrides = json.loads(args.overrides.read_text()) if args.overrides.exists() else {}
    files = sorted(p for p in args.inp.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    if args.only:
        files = [f for f in files if args.only in f.name]
    if not files:
        sys.exit(f"No audio files in {args.inp} (looked for {', '.join(sorted(AUDIO_EXTS))}).")

    args.out.mkdir(parents=True, exist_ok=True)
    failed = []
    for f in files:
        try:
            process_track(f, args.out, overrides.get(f.name, {}), args.stems, args.force)
        except Exception as e:  # keep going; one bad file shouldn't sink the batch
            print(f"  FAILED  {f.name}: {e}")
            failed.append(f.name)
    tracks = write_index(args.out)
    print(f"\n{len(tracks)} tracks in {args.out / 'index.json'}" + (f"; {len(failed)} failed: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
