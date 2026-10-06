"""Ground-truth regression tests. Generates the synthetic demo tracks (known BPM,
downbeat, key, structure), runs the real pipeline, and checks the results.
Run: cd pipeline && pytest -q      (~1-2 min on a laptop)"""
import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import make_demo_tracks  # noqa: E402
from handoff_pipeline.analyze import camelot  # noqa: E402
from handoff_pipeline.build import load_schema, process_track, write_index  # noqa: E402


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    tracks = tmp_path_factory.mktemp("tracks")
    out = tmp_path_factory.mktemp("lib")
    sys.argv = ["make_demo_tracks.py", "--out", str(tracks)]
    make_demo_tracks.main()
    truth = json.loads((tracks / "ground_truth.json").read_text())
    results = {}
    for name in truth:
        results[name] = process_track(tracks / name, out, {}, with_stems=False, force=True, log=lambda *_: None)
    write_index(out)
    return truth, results, out


def test_camelot_mapping():
    assert camelot(0, "major") == "8B"    # C
    assert camelot(9, "minor") == "8A"    # A minor
    assert camelot(4, "minor") == "9A"    # E minor
    assert camelot(7, "major") == "9B"    # G
    assert camelot(5, "major") == "7B"    # F
    assert camelot(11, "major") == "1B"   # B
    assert camelot(8, "minor") == "1A"    # G# minor


def test_shared_fixture_matches_schema():
    """contracts/fixtures/sample_analysis.json is what the web tests read; keep it current."""
    fixture = json.loads((ROOT.parent / "contracts" / "fixtures" / "sample_analysis.json").read_text())
    jsonschema.validate(fixture, load_schema())


def test_schema_valid(library):
    _, results, _ = library
    schema = load_schema()
    for a in results.values():
        jsonschema.validate(a, schema)


def test_bpm(library):
    truth, results, _ = library
    for name, t in truth.items():
        assert abs(results[name]["tempo"]["bpm"] - t["bpm"]) < 0.1, name
        assert results[name]["tempo"]["beatmatchable"], name
        assert results[name]["tempo"]["max_drift_ms"] < 5.0, name   # perfectly steady: only noise


def test_regular_grid_is_stored_evenly_spaced(library):
    """A regular grid's beats must survive serialization: sync takes the playback rate from
    the local beat interval, so rounding to 0.1 ms (+/-0.02 BPM) drifted synced decks ~0.2 ms
    per second (browser smoke test)."""
    import numpy as np
    _, results, _ = library
    for name, a in results.items():
        if a["tempo"]["grid"] == "regular":
            ibi = np.diff(a["beats"])
            assert np.ptp(ibi) < 4e-6, f"{name}: beat intervals vary by {np.ptp(ibi) * 1e3:.3f} ms"


def test_downbeat_and_grid_phase(library):
    truth, results, _ = library
    for name, t in truth.items():
        a = results[name]
        db = a["beats"][a["first_downbeat_index"]]
        assert abs(db - t["first_downbeat_s"]) < 0.010, f"{name}: downbeat {db:.4f} vs {t['first_downbeat_s']:.4f}"


def test_key_number(library):
    """Relative major/minor share a Camelot number and are mix-compatible; we only
    require the number to match (see estimate_key docstring)."""
    truth, results, _ = library
    for name, t in truth.items():
        assert results[name]["key"]["camelot"][:-1] == t["camelot"][:-1], name


def test_amber_room_structure(library):
    _, results, _ = library
    secs = results["Demo - Amber Room.wav"]["sections"]
    starts = [s["start_bar"] for s in secs]
    assert starts[:5] == [0, 16, 32, 40, 56]
    assert [s["label"] for s in secs[:5]] == ["intro", "main", "break", "main", "outro"]


def test_phrases_cover_bars(library):
    _, results, _ = library
    for a in results.values():
        covered = sum(p["n_bars"] for p in a["phrases"])
        assert covered == len(a["bars"])
        assert all(p["start_bar"] % a["phrase_bars"] == 0 for p in a["phrases"])


def test_index(library):
    _, results, out = library
    idx = json.loads((out / "index.json").read_text())
    assert {t["id"] for t in idx["tracks"]} == {a["id"] for a in results.values()}
