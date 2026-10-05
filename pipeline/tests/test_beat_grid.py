"""Beat-grid ground truth on synthetic tracks built to break naive grid fitting:

- loud offbeat hats (and offbeat bass), so the full-band onset peak is on the "and";
- a beat tracker that slips half a beat several times (seen on Frog Prince), injected by
  monkeypatching librosa's tracker so the test doesn't depend on when it happens to slip;
- a drifting "live" tempo, which must come out non-beatmatchable.

Run: cd pipeline && pytest -q tests/test_beat_grid.py
"""
import sys
from pathlib import Path

import librosa
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import make_demo_tracks as M  # noqa: E402
from handoff_pipeline import analyze as A  # noqa: E402

BPM = 124.0
PERIOD = 60.0 / BPM
LEAD_S = 0.213
N_BEATS = 4 * 96          # 96 bars, ~3 min
SLIPS_S = (15.0, 60.0, 108.0, 150.0)   # tracker toggles on-beat <-> offbeat here


def render(kick_times: np.ndarray, breakdown: tuple[int, int] | None = None) -> np.ndarray:
    """Kick on each time in kick_times; loud closed hat and a bass note half a beat after it.
    `breakdown` = (first_beat, end_beat) replaced by a sustained pad with no onsets."""
    rng = np.random.default_rng(7)
    dur = kick_times[-1] + 2.0
    y = np.zeros(int(dur * M.SR))
    k_snd = M.kick()
    hat = M.noise_burst(int(0.06 * M.SR), 0.02, rng, hp=6000)
    bass = M.tone(M.hz("A", 1), int(0.4 * PERIOD * M.SR), (1, .5, .25), tau=0.12)
    for i, t in enumerate(kick_times):
        if breakdown and breakdown[0] <= i < breakdown[1]:
            continue
        M.add(y, t, k_snd, 0.5)
        off = t + 0.5 * PERIOD
        M.add(y, off, hat, 1.0)
        M.add(y, off, bass, 0.35)
    if breakdown:
        t0, t1 = kick_times[breakdown[0]], kick_times[breakdown[1]]
        pad = M.tone(M.hz("A", 3), int((t1 - t0) * M.SR), (1, .4)) * np.hanning(int((t1 - t0) * M.SR))
        M.add(y, t0, pad, 0.3)
    y = y / np.max(np.abs(y)) * 0.9
    # float32 like librosa.load. With float64 here, later float32 calls to librosa's
    # numba beat tracker (in test_pipeline) warned "invalid value encountered in cast".
    return librosa.resample(y, orig_sr=M.SR, target_sr=A.ANALYSIS_SR).astype(np.float32)


def max_error_ms(beats: np.ndarray, truth: np.ndarray) -> float:
    """Largest distance from any grid beat inside the truth span to its nearest true beat."""
    inside = beats[(beats >= truth[0] - 0.1) & (beats <= truth[-1] + 0.1)]
    j = np.clip(np.searchsorted(truth, inside), 1, len(truth) - 1)
    d = np.minimum(np.abs(inside - truth[j - 1]), np.abs(inside - truth[j]))
    return float(d.max() * 1000)


@pytest.fixture(scope="module")
def steady():
    truth = LEAD_S + PERIOD * np.arange(N_BEATS)
    y = render(truth, breakdown=(4 * 48, 4 * 56))
    return y, truth


def slipping_tracker(truth: np.ndarray, tempo: float):
    """Fake librosa.beat.beat_track: on the beat, then on the offbeat after each slip,
    quantized to HOP frames like the real tracker."""
    region = np.searchsorted(SLIPS_S, truth) % 2          # 0 = on-beat, 1 = offbeat
    times = truth + region * 0.5 * PERIOD
    frames = librosa.time_to_frames(times, sr=A.ANALYSIS_SR, hop_length=A.HOP)

    def fake(*_args, **_kwargs):
        return np.array([tempo]), np.unique(frames)
    return fake


def check_grid(res: dict, truth: np.ndarray, y: np.ndarray) -> None:
    assert res["grid"] == "regular"
    assert res["beatmatchable"]
    assert abs(res["bpm"] - BPM) < 0.01, res["bpm"]
    err = max_error_ms(res["beats"], truth)
    assert err < 5.0, f"grid beat {err:.1f} ms from nearest kick"
    assert res["max_drift_ms"] < 5.0, res["max_drift_ms"]


def test_offbeat_hats_real_tracker(steady):
    """Loud offbeat hats: the grid must sit on the kicks, not the hats."""
    y, truth = steady
    check_grid(A.track_beats(y, A.ANALYSIS_SR, len(y) / A.ANALYSIS_SR), truth, y)


@pytest.mark.parametrize("tracker_bpm", [123.6, 124.0, 124.5])
def test_half_beat_tracker_slips(steady, monkeypatch, tracker_bpm):
    """The tracker slips half a beat 4 times (so its beat indices are wrong after each
    slip) and reports a slightly-off tempo. The fitted grid must still be exact."""
    y, truth = steady
    monkeypatch.setattr(librosa.beat, "beat_track", slipping_tracker(truth, tracker_bpm))
    check_grid(A.track_beats(y, A.ANALYSIS_SR, len(y) / A.ANALYSIS_SR), truth, y)


def test_drifting_tempo_is_not_beatmatchable():
    """A live-style tempo that wanders +/-25 ms around a straight line can't hold sync."""
    i = np.arange(N_BEATS)
    truth = LEAD_S + PERIOD * i + 0.025 * np.sin(2 * np.pi * i / 160)
    y = render(truth)
    res = A.track_beats(y, A.ANALYSIS_SR, len(y) / A.ANALYSIS_SR)
    assert res["max_drift_ms"] > 15.0, res["max_drift_ms"]
    assert not res["beatmatchable"]
    assert res["grid"] == "tracked"
