"""Turn one source file into a track folder:

    <out>/<track_id>/
        analysis.json      TrackAnalysis (contracts/track_analysis.schema.json)
        waveform.json      3-band display waveform
        audio/mix.flac
        audio/stem_{drums,bass,vocals,other}.flac   (only with --stems)

and keep <out>/index.json in sync.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import librosa
import numpy as np

from . import analyze as A
from .audio_io import (SERVE_SR, copy_provided_stems, file_hash, find_provided_stems, guess_title_artist,
                       separate_stems, slugify, to_flac)

PIPELINE_VERSION = "0.1.0"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "track_analysis.schema.json"
DEFAULT_PHRASE_BARS = 16


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def track_id_for(src: Path) -> str:
    return f"{slugify(src.stem)}-{file_hash(src)}"


def process_track(src: Path, out_root: Path, overrides: dict, with_stems: bool, force: bool,
                  log=print) -> dict:
    tid = track_id_for(src)
    tdir = out_root / tid
    analysis_path = tdir / "analysis.json"
    if analysis_path.exists() and not force:
        existing = json.loads(analysis_path.read_text())
        has_stems = existing["audio"]["stems"] is not None
        wants_stems = with_stems or find_provided_stems(src) is not None
        if existing["meta"]["pipeline_version"] == PIPELINE_VERSION and (has_stems or not wants_stems):
            log(f"  cached  {src.name}")
            return existing

    log(f"  start   {src.name} -> {tid}")
    audio_dir = tdir / "audio"
    mix = audio_dir / "mix.flac"
    to_flac(src, mix)

    stems = None
    provided = find_provided_stems(src)
    if provided:
        log(f"  stems   using provided stems from {provided.name}/")
        stems = copy_provided_stems(provided, audio_dir)
    elif with_stems:
        log("  stems   running Demucs (slow on CPU)...")
        stems = separate_stems(mix, audio_dir)

    # Analyze the exact file we serve.
    y, sr = librosa.load(mix, sr=A.ANALYSIS_SR, mono=True)
    duration = float(len(y) / sr)

    beat = A.track_beats(y, sr, duration, bpm_hint=overrides.get("bpm_hint"),
                         force_grid=overrides.get("force_grid"))
    beats = beat["beats"]
    fdi, db_conf = A.find_downbeat_phase(y, sr, beats, beat["onset_env"])
    if "downbeat_shift" in overrides:
        fdi = (fdi + int(overrides["downbeat_shift"])) % A.BEATS_PER_BAR
    bars = A.build_bars(y, sr, beats, fdi, duration)
    sections = A.label_sections(A.find_sections(y, sr, bars, beats), bars)
    phrase_bars = int(overrides.get("phrase_bars", DEFAULT_PHRASE_BARS))
    phrases = A.build_phrases(bars, sections, phrase_bars, int(overrides.get("phrase_offset_bars", 0)))
    key = A.estimate_key(y, sr)
    wave = A.build_waveform(y, sr)

    title, artist = guess_title_artist(src)
    analysis = {
        "schema_version": 1,
        "id": tid,
        "title": overrides.get("title", title),
        "artist": overrides.get("artist", artist),
        "source": {"filename": src.name, "license": overrides.get("license"), "credit": overrides.get("credit")},
        "audio": {"mix": "audio/mix.flac", "stems": stems},
        "sample_rate": SERVE_SR,
        "duration_s": round(duration, 4),
        "tempo": {
            "bpm": round(beat["bpm"], 3),
            "ibi_cv": round(beat["ibi_cv"], 4),
            "grid_residual_ratio": round(beat["grid_residual_ratio"], 4),
            "beatmatchable": beat["beatmatchable"],
            "grid": beat["grid"],
        },
        "beats": [round(float(t), 4) for t in beats],
        "beats_per_bar": A.BEATS_PER_BAR,
        "first_downbeat_index": int(fdi),
        "downbeat_confidence": round(db_conf, 3),
        "bars": bars,
        "phrase_bars": phrase_bars,
        "phrases": phrases,
        "sections": sections,
        "key": key,
        "waveform": "waveform.json",
        "meta": {
            "pipeline_version": PIPELINE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overrides_applied": overrides,
        },
    }
    jsonschema.validate(analysis, load_schema())   # fail here, not in the browser
    assert np.all(np.diff(analysis["beats"]) > 0), "beats must be strictly increasing"

    (tdir / "waveform.json").write_text(json.dumps(wave, separators=(",", ":")))
    analysis_path.write_text(json.dumps(analysis, indent=1))
    log(f"  done    {analysis['tempo']['bpm']:.2f} BPM ({beat['grid']} grid, "
        f"beatmatchable={beat['beatmatchable']}), key {key['camelot']}, "
        f"downbeat conf {db_conf:.2f}, {len(sections)} sections, stems={'yes' if stems else 'no'}")
    return analysis


def write_index(out_root: Path) -> list[dict]:
    """index.json is rebuilt from whatever track folders exist, so it can't drift."""
    tracks = []
    for p in sorted(out_root.glob("*/analysis.json")):
        a = json.loads(p.read_text())
        tracks.append({
            "id": a["id"], "title": a["title"], "artist": a["artist"],
            "bpm": a["tempo"]["bpm"], "camelot": a["key"]["camelot"],
            "duration_s": a["duration_s"], "has_stems": a["audio"]["stems"] is not None,
            "beatmatchable": a["tempo"]["beatmatchable"],
        })
    (out_root / "index.json").write_text(json.dumps({"schema_version": 1, "tracks": tracks}, indent=1))
    return tracks
