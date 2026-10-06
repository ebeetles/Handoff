"""Section labels, cues and hooks (structure.py) on synthetic per-bar features with a known
arrangement. The demo tracks check the same through the real pipeline (test_pipeline.py)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handoff_pipeline import structure as S  # noqa: E402


def arrangement(parts):
    """parts: (bars, energy, drums, vocals, rising?) -> per-bar arrays and sections."""
    E, drums, vocals, sections, a = [], [], [], [], 0
    for k, (n, e, d, v, rising) in enumerate(parts):
        E += list(np.linspace(e - 0.25, e, n) if rising else np.full(n, e))
        drums += [d] * n
        vocals += [v] * n
        sections.append({"index": k, "start_bar": a, "end_bar": a + n, "start_s": a * 2.0})
        a += n
    E = np.array(E)
    presence = {"drums": np.array(drums), "vocals": np.array(vocals), "bass": np.array(drums), "other": np.full(a, 0.8)}
    return E, presence, sections


def chroma_for(n, seed=0):
    rng = np.random.default_rng(seed)
    return np.abs(rng.normal(size=(12, n))) + 0.1


def test_edm_arrangement_is_labelled_like_a_dj_would():
    E, P, secs = arrangement([(16, 0.3, 0.8, 0.0, False), (16, 0.9, 0.9, 0.0, False), (8, 0.35, 0.05, 0.8, False),
                              (8, 0.6, 0.6, 0.2, True), (16, 0.95, 0.9, 0.7, False), (8, 0.3, 0.8, 0.0, False)])
    C = np.tile(chroma_for(4), (1, 18))[:, :len(E)]
    S.label_sections(secs, E, E, P, C)
    # The drums-only intro filling out into the first peak is a groove (main), not a drop.
    assert [s["label"] for s in secs] == ["intro", "main", "breakdown", "build", "drop", "outro"]
    assert secs[4]["vocals"] == "lots" and secs[1]["vocals"] == "none"
    cues = S.find_cues(secs, E, E, P)
    kinds = [(c["kind"], c["bar"]) for c in cues]
    assert ("drop", 48) in kinds and ("build", 40) in kinds and ("breakdown", 32) in kinds
    assert ("vocal_in", 32) in kinds and ("vocal_out", 64) in kinds


def test_a_repeated_sung_peak_is_a_chorus_and_a_sung_lull_a_verse():
    # verse, chorus, verse, chorus: no builds or breakdowns, so the peaks are choruses, not drops.
    E, P, secs = arrangement([(16, 0.7, 0.8, 0.8, False), (16, 0.9, 0.9, 0.9, False),
                              (16, 0.7, 0.8, 0.8, False), (16, 0.9, 0.9, 0.9, False), (8, 0.7, 0.8, 0.8, False)])
    E[16:18] = 0.75; E[48:50] = 0.75                                     # no jump into the peaks
    C = chroma_for(80, 1)
    C[:, 16:32] = C[:, 48:64] = np.tile(chroma_for(4, 2), (1, 4))       # the choruses share their harmony
    S.label_sections(secs, E, E, P, C)
    assert [s["label"] for s in secs] == ["intro", "chorus", "verse", "chorus", "outro"]
    assert secs[1]["group"] == secs[3]["group"] != secs[2]["group"]


def test_without_stems_labels_fall_back_to_the_low_band():
    E, _, secs = arrangement([(16, 0.3, 0, 0, False), (16, 0.9, 0, 0, False), (8, 0.3, 0, 0, False),
                              (16, 0.9, 0, 0, False), (8, 0.3, 0, 0, False)])
    low = E.copy()
    low[:16] = 0.6                     # the intro has a kick
    low[32:40] = 0.1                   # the breakdown doesn't
    S.label_sections(secs, E, low, None, chroma_for(len(E)))
    assert [s["label"] for s in secs] == ["intro", "main", "breakdown", "drop", "outro"]
    assert all(s["vocals"] == "unknown" for s in secs)
    assert all(c["kind"] not in ("vocal_in", "vocal_out") for c in S.find_cues(secs, E, E, None))


def test_the_most_repeated_sung_phrase_is_the_hook():
    n = 64
    rng = np.random.default_rng(3)
    V = np.abs(rng.normal(size=(12, n))) + 0.05                          # every bar sings something different...
    hook = np.abs(rng.normal(size=(12, 2))) + 0.05
    for s in (16, 24, 40, 48):                                           # ...except one 2-bar line, four times
        V[:, s:s + 2] = hook
    vocals = np.zeros(n)
    vocals[8:56] = 0.9
    P = {"vocals": vocals, "other": np.zeros(n), "drums": np.ones(n), "bass": np.ones(n)}
    hooks = S.find_hooks(V, np.full(n, 0.5), P, vchroma=V)
    assert hooks and hooks[0]["kind"] == "vocal" and hooks[0]["bars"] == 2 and hooks[0]["starts"] == [16, 24, 40, 48]
    assert S.find_hooks(V, np.full(n, 0.5), {**P, "vocals": np.zeros(n)}, vchroma=V) == []


def test_a_vocal_entry_is_one_cue():
    """Morgan Page's vocals fade in over two bars: that's one entry, not a cue at bar 7 and bar 8."""
    n = 32
    v = np.zeros(n)
    v[7] = 0.6
    v[8:20] = 0.9
    P = {"vocals": v, "drums": np.ones(n), "bass": np.ones(n), "other": np.ones(n)}
    secs = [{"index": 0, "start_bar": 0, "end_bar": n, "start_s": 0, "label": "main"}]
    ins = [c["bar"] for c in S.find_cues(secs, np.full(n, 0.5), np.full(n, 0.5), P) if c["kind"] == "vocal_in"]
    assert len(ins) == 1 and ins[0] in (7, 8)
