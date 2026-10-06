"""Musical structure from per-bar features: section labels, cues, and hooks (TrackAnalysis v4).

Pure functions on arrays, one value per bar:
  E          overall energy (mean of the three normalized bands, 0..1)
  high       high-band energy (0..1): risers, hats and snare rolls build in it
  presence   per stem (drums, bass, vocals, other) level against the mix, 0..1; None without stems
  chroma     12 x n_bars pitch-class profile; vchroma / ochroma the same for the vocal / other stem

Section labels (one per section from find_sections), by rules a DJ would recognise:
  intro / outro  first / last section, below peak energy
  breakdown      a middle section whose drums drop out (without stems: whose low band does)
  build          below peak, energy rising through it, and the next section is a peak
  drop           a peak after tension or space: a build (section or rising bars), a breakdown, or the
                 beat slamming back after beatless bars. A groove filling out is not a drop.
  chorus         otherwise, a peak section, mostly sung, that sounds like another section of the track
  verse          a sung section below peak energy
  main           everything else (a groove that is just there)
Rough by design: no ground truth for real tracks yet. Spot-check by ear (ROADMAP 1b).

Cues: where things happen, in bars: drop (a drop or chorus hits), build (the rising bars before a
drop), breakdown, vocal_in, vocal_out.

Hooks: the most repeated 2- or 4-bar phrase of the vocal stem ("vocal") and of the "other" stem
(riffs, leads: "instrumental"), with every bar it recurs at. Repetition is cosine similarity of
the bars' pitch-class profiles in sequence.
"""
from __future__ import annotations

import numpy as np

LABELS = ("intro", "verse", "build", "drop", "chorus", "breakdown", "main", "outro")
PEAK = 0.8           # section energy / loudest section's, at or above which a section is a peak
                     # (0.85 demoted every earlier peak of a track with one huge final drop: Stars Collide)
MID = 0.6
NO_DRUMS = 0.3       # drums presence under which a section is a breakdown
SUNG = 0.4           # vocal presence over which a section is sung
RISE = 0.12          # energy gained across a section (second half vs first) that makes a build
JUMP = 0.2           # energy jump into a section (2 bars vs 2 bars) that makes it a drop
HOOK_SIM = {"vocal": 0.95, "instrumental": 0.92}   # cosine similarity at which two phrases count as the same
# (vocal lines over one chord loop all look alike in pitch classes at 0.92: Broey's "hook" recurred 41 times)
HOOK_MIN = 3         # occurrences that make a hook
ON = 0.5             # presence over which a stem is "on" in a bar


def _mean(x: np.ndarray, a: int, b: int) -> float:
    a, b = max(0, a), min(len(x), b)
    return float(x[a:b].mean()) if b > a else 0.0


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=0, keepdims=True) + 1e-9)


def section_features(sections: list[dict], E: np.ndarray, low: np.ndarray, presence: dict | None) -> list[dict]:
    drums = presence["drums"] if presence else low
    out = []
    for k, s in enumerate(sections):
        a, b = s["start_bar"], s["end_bar"]
        half = a + max(1, (b - a) // 2)
        out.append({
            "e": _mean(E, a, b), "drums": _mean(drums, a, b),
            "vocals": None if presence is None else _mean(presence["vocals"], a, b),
            "trend": _mean(E, half, b) - _mean(E, a, half),
            "jump": _mean(E, a, a + 2) - _mean(E, a - 2, a) if k else 0.0,
            "drums_before": _mean(drums, a - 2, a) if k else 1.0,
        })
    return out


def group_sections(sections: list[dict], feats: list[dict], chroma: np.ndarray) -> list[int]:
    """Repetition classes: sections that sound alike (energy, drums, vocals and harmony) share a
    group number, in order of first appearance."""
    prof = [_unit(chroma[:, s["start_bar"]:s["end_bar"]].mean(axis=1)) for s in sections]
    groups: list[int] = []
    for k, f in enumerate(feats):
        g = next((groups[j] for j in range(k) if abs(f["e"] - feats[j]["e"]) < 0.1
                  and abs(f["drums"] - feats[j]["drums"]) < 0.2
                  and (f["vocals"] is None or abs(f["vocals"] - feats[j]["vocals"]) < 0.2)
                  and float(prof[k] @ prof[j]) >= 0.9), None)
        groups.append(max(groups, default=-1) + 1 if g is None else g)
    return groups


def label_sections(sections: list[dict], E: np.ndarray, low: np.ndarray, presence: dict | None,
                   chroma: np.ndarray) -> list[dict]:
    """Adds label, energy_level, vocals and group to each section (in place; returns them)."""
    feats = section_features(sections, E, low, presence)
    groups = group_sections(sections, feats, chroma)
    e_max = max(max(f["e"] for f in feats), 1e-9)
    level = ["high" if f["e"] / e_max >= PEAK else "mid" if f["e"] / e_max >= MID else "low" for f in feats]
    last = len(sections) - 1
    for k, (s, f) in enumerate(zip(sections, feats)):
        prev = sections[k - 1]["label"] if k else None
        repeats = groups.count(groups[k]) > 1
        sung = f["vocals"] is not None and f["vocals"] >= SUNG
        if last == 0:
            label = "main"
        elif k == 0 and level[k] != "high":
            label = "intro"
        elif k == last and level[k] != "high":
            label = "outro"
        elif f["drums"] < (NO_DRUMS if presence else 0.4):
            label = "breakdown"
        elif level[k] != "high" and f["trend"] >= RISE and k < last and level[k + 1] == "high":
            label = "build"
        elif level[k] == "high":
            # A drop is an impact after tension or space: a build (a section, or rising bars just
            # before), a breakdown, or the beat slamming back after beatless bars. More
            # instruments joining a groove (drums-only intro -> full groove) is not a drop.
            space = f["jump"] >= JUMP and f["drums_before"] < (NO_DRUMS if presence else 0.4)
            if prev in ("build", "breakdown") or space or find_builds(E, low if presence is None else E, s["start_bar"]):
                label = "drop"
            elif sung and repeats:              # a song: a sung peak that comes back
                label = "chorus"
            else:
                label = "main"
        elif sung:
            label = "verse"
        else:
            label = "main"
        s.update(label=label, energy_level=level[k], group=groups[k],
                 vocals="unknown" if f["vocals"] is None else "none" if f["vocals"] < 0.15 else "some" if f["vocals"] < 0.6 else "lots")
    return sections


def find_builds(E: np.ndarray, high: np.ndarray, at: int) -> tuple[int, int] | None:
    """The rising stretch that ends at bar `at` (a drop): the longest of 16, 8 or 4 bars whose
    energy (with the high band) never falls from one 2-bar step to the next (by more than 0.03)
    and gains RISE or more in all. Steps, not a straight line: builds often add a layer every
    few bars (Stars Collide's 16-bar build was a 0.33 / 0.65 staircase, correlation 0.56)."""
    x = (E + high) / 2
    for k in (16, 8, 4):
        a = at - k
        if a < 0:
            continue
        steps = x[a:at].reshape(-1, 2).mean(axis=1)
        if np.all(np.diff(steps) >= -0.03) and steps[-1] - steps[0] >= RISE:
            return a, at
    return None


def find_cues(sections: list[dict], E: np.ndarray, high: np.ndarray, presence: dict | None) -> list[dict]:
    cues = []
    for k, s in enumerate(sections):
        a, n = s["start_bar"], s["end_bar"] - s["start_bar"]
        # A drop cue is the moment of impact: a peak entered from something that isn't one (not
        # every section of a long peak, and not the first bar of a track that starts at full tilt).
        entered = k > 0 and sections[k - 1]["label"] not in ("drop", "chorus")
        if s["label"] in ("drop", "chorus") and entered:
            cues.append({"kind": "drop", "bar": a, "bars": n})
            build = next(((p["start_bar"], p["end_bar"]) for p in sections if p["label"] == "build" and p["end_bar"] == a), None) \
                or find_builds(E, high, a)
            if build:
                cues.append({"kind": "build", "bar": build[0], "bars": build[1] - build[0]})
        elif s["label"] == "breakdown":
            cues.append({"kind": "breakdown", "bar": a, "bars": n})
    if presence is not None:
        v = presence["vocals"]
        last = {"vocal_in": -99, "vocal_out": -99}
        for t in range(2, len(v) - 1):
            # The cue is the first bar the vocal is on (in) or off (out), not a bar that a 2-bar
            # average straddles; one cue per entry, not one per bar of it.
            on_now = v[t] >= ON and _mean(v, t, t + 2) >= ON
            off_now = v[t] < 0.2 and _mean(v, t, t + 2) < 0.2
            for kind, hit in (("vocal_in", on_now and _mean(v, t - 2, t) < 0.2),
                              ("vocal_out", off_now and _mean(v, t - 2, t) >= ON)):
                if hit and t - last[kind] >= 4:
                    cues.append({"kind": kind, "bar": t, "bars": 1})
                    last[kind] = t
    seen, out = set(), []
    for c in sorted(cues, key=lambda c: (c["bar"], c["kind"])):
        if (c["kind"], c["bar"]) not in seen:
            seen.add((c["kind"], c["bar"]))
            out.append(c)
    return out


def find_hook(C: np.ndarray, gate: np.ndarray, E: np.ndarray, sim: float, peak: np.ndarray | None = None) -> dict | None:
    """The most repeated 4- or 2-bar phrase of a stem whose presence is `gate`: phrases start on
    the L-bar grid, and the one with the most non-overlapping look-alikes (cosine >= sim, at
    least HOOK_MIN) wins. Phrases in peak sections (`peak`, per bar) come first: that's where
    the hook is sung. Then the longer phrase, then the more energetic."""
    n = C.shape[1]
    Cn = _unit(C)
    best = None
    for L in (4, 2):
        starts = [i for i in range(0, n - L + 1, L) if _mean(gate, i, i + L) >= ON]
        if len(starts) < HOOK_MIN:
            continue
        V = _unit(np.stack([Cn[:, i:i + L].reshape(-1, order="F") for i in starts], axis=1))
        S = V.T @ V
        for a, i in enumerate(starts):
            occ, sims = [], []
            for b, j in enumerate(starts):
                if S[a, b] >= sim and (not occ or j >= occ[-1] + L):
                    occ.append(j)
                    sims.append(S[a, b])
            key = (peak is not None and _mean(peak, i, i + L) >= 0.5, len(occ), L, _mean(E, i, i + L))
            if len(occ) >= HOOK_MIN and (best is None or key > best[0]):
                best = (key, {"bars": L, "starts": occ, "strength": round(float(np.mean(sims)), 3)})
    return best[1] if best else None


def find_hooks(chroma: np.ndarray, E: np.ndarray, presence: dict | None,
               vchroma: np.ndarray | None = None, ochroma: np.ndarray | None = None,
               sections: list[dict] | None = None) -> list[dict]:
    peak = None
    if sections:
        peak = np.zeros(len(E))
        for s in sections:
            if s["label"] in ("drop", "chorus") or s["energy_level"] == "high":
                peak[s["start_bar"]:s["end_bar"]] = 1
    hooks = []
    if presence is not None and vchroma is not None:
        h = find_hook(vchroma, presence["vocals"], E, HOOK_SIM["vocal"], peak)
        if h:
            hooks.append({"kind": "vocal", **h})
    other = ochroma if ochroma is not None else chroma
    gate = presence["other"] if presence is not None else E
    h = find_hook(other, gate, E, HOOK_SIM["instrumental"], peak)
    if h:
        hooks.append({"kind": "instrumental", **h})
    return hooks
