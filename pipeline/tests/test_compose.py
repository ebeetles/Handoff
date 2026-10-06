"""Chunk 7: facts -> composer -> compiler -> critic, on synthetic tracks with known content."""
import json
import sys
from pathlib import Path

import jsonschema
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from handoff_pipeline.compose import composer as C  # noqa: E402
from handoff_pipeline.compose.compiler import compile_plan  # noqa: E402
from handoff_pipeline.compose.critic import critique  # noqa: E402
from handoff_pipeline.compose.facts import TrackData, pair_facts, summary  # noqa: E402
from handoff_pipeline.compose.mix import choose_sync  # noqa: E402
from handoff_pipeline.compose.recipes import recipe_errors  # noqa: E402
from handoff_pipeline.compose.rules_composer import compose_rules  # noqa: E402

CONTRACTS = ROOT.parent / "contracts"


def track(tid="a", n=128, bpm=124.0, camelot="8A", stems=False, vocals=None, bass=0.8, steady=True,
          drums=None, music=None) -> TrackData:
    """Intro (bars 0-16, low), main (16-96, high), outro (96-n, low); phrases every 16 bars.
    Stem presence is known when any of vocals/drums/music is given (scalars or per-bar arrays):
    music sets the bass and other stems."""
    sections = [{"index": 0, "start_bar": 0, "end_bar": 16, "start_s": 0, "label": "intro", "energy_level": "low"},
                {"index": 1, "start_bar": 16, "end_bar": 96, "start_s": 0, "label": "main", "energy_level": "high"},
                {"index": 2, "start_bar": 96, "end_bar": n, "start_s": 0, "label": "outro", "energy_level": "low"}]
    phrases = [{"index": i, "start_bar": s, "n_bars": 16, "start_s": 0, "section_index": 0 if s < 16 else 1 if s < 96 else 2}
               for i, s in enumerate(range(0, n, 16))]
    level = np.array([0.4 if b < 16 or b >= 96 else 0.9 for b in range(n)])
    return TrackData(id=tid, title=tid, artist="t", bpm=bpm, camelot=camelot, key="", beatmatchable=steady, has_stems=stems,
                     n_bars=n, phrases=phrases, sections=sections, low=level * bass, mid=level, high=level,
                     rms_db=np.where(level > 0.5, -9.0, -14.0), presence=presence(n, vocals, drums, music))


def presence(n, vocals, drums, music):
    if vocals is None and drums is None and music is None:
        return None
    full = lambda v, d: np.broadcast_to(np.asarray(d if v is None else v, dtype=float), (n,)).copy()  # noqa: E731
    return {"vocals": full(vocals, 0.0), "drums": full(drums, 0.9), "bass": full(music, 0.8), "other": full(music, 0.8)}


def plan(moves, start=96, idea="t", shift=0):
    return {"idea": idea, "rationale": "r", "out_start_bar": start, "moves": moves, "in_key_shift": shift}


ENTER = {"move": "in_enter", "at_bar": 0, "from_bar": 0}
STOP16 = {"move": "out_stop", "at_bar": 16}
XF = {"move": "crossfade", "start_bar": 0, "end_bar": 16, "to": 1, "shape": "ramp"}


def lane(r, deck, ctl):
    return next(l["points"] for l in r["lanes"] if l["deck"] == deck and l["control"] == ctl)


# ---------------------------------------------------------------- mix ports

def test_choose_sync_matches_the_shared_fixture():
    """Same cases the web's chooseSync is tested on (ROADMAP: test both ports against it)."""
    for c in json.loads((CONTRACTS / "fixtures" / "sync_cases.json").read_text())["cases"]:
        got = choose_sync(c["my_bpm"], c["other_bpm"])
        if c["expect"] is None:
            assert got is None, c
        else:
            assert got[0] == c["expect"]["multiplier"] and abs(got[1] - c["expect"]["rate"]) < 1e-5, c


# ---------------------------------------------------------------- facts

def test_summary_is_per_phrase_and_discretized():
    s = summary(track(vocals=0.9))
    assert len(s["phrases"]) == 8 and s["phrases"][0] == {"bar": 0, "bars": 16, "section": "intro", "energy": "mid",
                                                          "trend": "steady", "bass": "light", "vocals": "lots", "drums": "lots"}
    assert summary(track())["phrases"][2]["vocals"] == "unknown"
    pf = pair_facts(track(), track("b", bpm=128))
    assert pf["tempo"]["sync"]["multiplier"] == 1
    assert pf["exit_bars"] == list(range(0, 128, 16))
    assert pf["entry_bars"] == list(range(0, 128, 16))


def test_midtrack_gate_and_volume_compile_to_audio_clock_steps():
    moves = [ENTER, XF, STOP16,
             {"move": "rhythmic_gate", "deck": "out", "start_bar": 8, "end_bar": 10,
              "period_beats": 1, "depth": 0.9},
             {"move": "volume", "deck": "out", "start_bar": 10.25, "end_bar": 16, "to": 0}]
    r, errors = compile_plan(plan(moves, start=16), track(), track("b"), "midtrack")
    assert errors == [] and r["anchor"]["out_start_bar"] == 16
    pts = lane(r, "out", "volume")
    assert [8.125, 0.08] in pts and [10, 0.8] in pts and pts[-1] == [16, 0]
    assert sum(a[0] == b[0] for a, b in zip(pts, pts[1:])) == 16


def test_invalid_model_move_is_rejected_without_crashing():
    for move in [{"move": "laser"}, {"move": "volume", "deck": "in", "start_bar": 0,
                                    "end_bar": 2, "to": float("nan")}]:
        assert compile_plan(plan([ENTER, XF, STOP16, move]), track(), track("b"), "bad")[0] is None


def test_gate_reserves_its_whole_lane_and_rolls_cannot_overlap():
    gate = {"move": "rhythmic_gate", "deck": "out", "start_bar": 4, "end_bar": 8, "period_beats": 2, "depth": 1}
    vol = {"move": "volume", "deck": "out", "start_bar": 5, "end_bar": 6, "to": 0.3}
    assert compile_plan(plan([ENTER, XF, STOP16, gate, vol]), track(), track("b"), "bad")[0] is None
    rolls = [{"move": "loop_roll", "start_bar": 4, "end_bar": 8},
             {"move": "loop_roll", "start_bar": 6, "end_bar": 10}]
    assert compile_plan(plan([ENTER, XF, STOP16, *rolls]), track(), track("b"), "bad")[0] is None


def test_compiler_prepares_incoming_controls_and_requires_audible_finish():
    r, errors = compile_plan(plan([ENTER, XF, STOP16]), track(), track("b", stems=True), "ready")
    assert errors == []
    assert lane(r, "in", "filter") == [[0, 0.5]]
    assert lane(r, "in", "stem.vocals") == [[0, 1]]
    muted = {"move": "volume", "deck": "in", "start_bar": 0, "end_bar": 0, "to": 0}
    assert compile_plan(plan([ENTER, XF, STOP16, muted]), track(), track("b"), "bad")[0] is None


# ---------------------------------------------------------------- compiler

def test_every_move_compiles_into_a_valid_anchored_recipe():
    a, b = track(stems=True), track("b", stems=True)
    r, errs = compile_plan(plan([
        {"move": "in_enter", "at_bar": 4, "from_bar": 16},
        {"move": "stem_drop", "deck": "in", "stems": ["vocals"], "action": "mute", "at_bar": 0},
        {"move": "crossfade", "start_bar": 4, "end_bar": 8, "to": 0.5, "shape": "ramp"},
        {"move": "eq", "deck": "out", "band": "mid", "start_bar": 4, "end_bar": 6, "to": "dip"},
        {"move": "bass_swap", "at_bar": 8},
        {"move": "filter_sweep", "deck": "out", "kind": "highpass", "direction": "close", "start_bar": 8, "end_bar": 12},
        {"move": "stem_drop", "deck": "in", "stems": ["vocals"], "action": "unmute", "at_bar": 12},
        {"move": "loop_roll", "start_bar": 12, "end_bar": 14},
        {"move": "echo_throw", "at_bar": 15, "cut": True},
        {"move": "crossfade", "start_bar": 14, "end_bar": 16, "to": 1, "shape": "ramp"},
        {"move": "out_stop", "at_bar": 16}]), a, b, "c1")
    assert errs == [] and recipe_errors(r) == []
    assert r["anchor"] == {"out_track": "a", "in_track": "b", "out_start_bar": 96, "in_from_bar": 16}
    assert r["requires"] == {"stems": "both", "beatmatchable": True, "max_camelot_distance": None}
    assert lane(r, "in", "stem.vocals") == [[0, 0], [12, 0], [12, 1]]          # muted before B is heard
    assert lane(r, "in", "stem.bass") == [[0, 0], [8, 0], [8, 1]]              # bass swap on stems
    assert lane(r, "out", "stem.bass") == [[0, 1], [8, 1], [8, 0]]
    assert lane(r, "out", "eqMid") == [[0, 0.5], [4, 0.5], [6, 0.3]]
    assert lane(r, "out", "volume") == [[0, 0.8], [15, 0.8], [15, 0]]            # echo throw cut
    assert lane(r, None, "xfader") == [[0, 0], [4, 0], [8, 0.5], [14, 0.5], [16, 1]]
    ev = [(e["at_bar"], e["deck"], e["command"], e.get("beats")) for e in r["events"]]
    assert ev == [(4, "in", "play", None), (12, "out", "loop", 0.5), (13, "out", "loop", 0.25),
                  (13.5, "out", "loop", 0.125), (14, "out", "loop_off", None), (16, "out", "pause", None)]


def test_bass_swap_uses_eq_without_stems_and_cuts_need_no_beatmatch():
    r, _ = compile_plan(plan([ENTER, XF, {"move": "bass_swap", "at_bar": 8}, STOP16]), track(), track("b"), "c")
    assert lane(r, "in", "eqLow") == [[0, 0], [8, 0], [8, 0.5]] and r["requires"]["stems"] == "none"
    r, _ = compile_plan(plan([{"move": "echo_throw", "at_bar": 8, "cut": True}, {"move": "in_enter", "at_bar": 8, "from_bar": 0},
                              {"move": "crossfade", "start_bar": 8, "end_bar": 8, "to": 1, "shape": "cut"},
                              {"move": "out_stop", "at_bar": 12}]), track(), track("b"), "c")
    assert r["requires"]["beatmatchable"] is False


@pytest.mark.parametrize("moves,start,why", [
    ([ENTER, XF, STOP16], 100, "not one of A's phrase starts"),
    ([ENTER, XF, {"move": "out_stop", "at_bar": 40}], 96, "A ends at bar 128"),
    ([{"move": "in_enter", "at_bar": 0, "from_bar": 6}, XF, STOP16], 96, "4-bar grid"),
    ([{"move": "in_enter", "at_bar": 0, "from_bar": 120}, XF, STOP16], 96, "B would run out"),
    ([ENTER, XF, {"move": "stem_drop", "deck": "out", "stems": ["bass"], "action": "mute", "at_bar": 4}, STOP16], 96, "has no stems"),
    ([ENTER, XF, {"move": "eq", "deck": "out", "band": "low", "start_bar": 2, "end_bar": 10, "to": "kill"},
      {"move": "bass_swap", "at_bar": 8}, STOP16], 96, "at once"),
    ([ENTER, {"move": "crossfade", "start_bar": 0, "end_bar": 8, "to": 0.5, "shape": "ramp"}, STOP16], 96, "not fully on B"),
    ([ENTER, STOP16], 96, "never moves to B"),
    ([XF, STOP16], 96, "exactly one in_enter"),
    ([ENTER, XF, STOP16, {"move": "bass_swap", "at_bar": 20}], 96, "after out_stop"),
    ([ENTER, {"move": "crossfade", "start_bar": 0, "end_bar": 16.1, "to": 1, "shape": "ramp"}, STOP16], 96, "quarter bar"),
])
def test_compiler_rejects_with_reasons(moves, start, why):
    r, errs = compile_plan(plan(moves, start), track(), track("b"), "c")
    assert r is None and any(why in e for e in errs), errs


# ---------------------------------------------------------------- critic

def score(moves, a=None, b=None, start=96):
    a, b = a or track(), b or track("b")
    r, errs = compile_plan(plan(moves, start), a, b, "c")
    assert not errs, errs
    return critique(r, a, b, len(moves))


def test_bass_clash_is_caught_and_a_bass_swap_fixes_it():
    hold = [ENTER, {"move": "crossfade", "start_bar": 0, "end_bar": 2, "to": 0.5, "shape": "ramp"},
            {"move": "crossfade", "start_bar": 14, "end_bar": 16, "to": 1, "shape": "ramp"}, STOP16]
    a, b = track(bass=1.0), track("b", bass=1.0)
    a.low[96:], b.low[:16] = 0.9, 0.9                       # heavy bass where they overlap
    muddy = score(hold, a, b)
    swapped = score(hold[:3] + [{"move": "bass_swap", "at_bar": 8}] + hold[3:], a, b)
    assert muddy["measures"]["bass_clash_bars"] > 8 and muddy["worst"] == "bass_clash"
    assert swapped["measures"]["bass_clash_bars"] == 0 and swapped["score"] > muddy["score"]


def test_vocal_clash_needs_known_vocals_and_a_stem_drop_fixes_it():
    moves = [ENTER, {"move": "crossfade", "start_bar": 0, "end_bar": 2, "to": 0.5, "shape": "ramp"},
             {"move": "crossfade", "start_bar": 14, "end_bar": 16, "to": 1, "shape": "ramp"}, STOP16]
    a, b = track(stems=True, vocals=0.9), track("b", stems=True, vocals=0.9)
    assert score(moves, a, b)["measures"]["vocal_clash_bars"] > 8
    dropped = score(moves[:1] + [{"move": "stem_drop", "deck": "out", "stems": ["vocals"], "action": "mute", "at_bar": 0}] + moves[1:], a, b)
    assert dropped["measures"]["vocal_clash_bars"] == 0
    unknown = score(moves)
    assert unknown["measures"]["vocals_known"] is False and any("not checked" in r for r in unknown["reasons"])


def test_key_clash_tempo_and_structure():
    blend = [ENTER, XF, STOP16]
    assert score(blend, b=track("b", camelot="3B"))["measures"]["key_clash_bars"] > 0
    assert score(blend, b=track("b", camelot="9A"))["measures"]["key_clash_bars"] == 0
    bad = score(blend, b=track("b", bpm=174))
    assert bad["valid"] is False and bad["score"] == 0
    cut = [{"move": "echo_throw", "at_bar": 8, "cut": True}, {"move": "in_enter", "at_bar": 8, "from_bar": 0},
           {"move": "crossfade", "start_bar": 8, "end_bar": 8, "to": 1, "shape": "cut"}, {"move": "out_stop", "at_bar": 12}]
    assert score(cut, b=track("b", bpm=174))["valid"] is True            # no overlap: any tempo
    assert "mid_phrase_entry" in score([{"move": "in_enter", "at_bar": 0, "from_bar": 4}, XF, STOP16])["breakdown"]
    assert "calm_exit" in score(blend, start=96)["breakdown"] and "calm_exit" not in score(blend, start=64)["breakdown"]


def test_a_gap_in_the_level_is_a_dip():
    gap = [{"move": "crossfade", "start_bar": 4, "end_bar": 4, "to": 1, "shape": "cut"},
           {"move": "in_enter", "at_bar": 10, "from_bar": 0}, STOP16]
    assert score(gap)["measures"]["dip_bars"] >= 5 and "dip" in score(gap)["breakdown"]


# ---------------------------------------------------------------- composers

def test_rules_composer_candidates_all_compile():
    a, b = track(stems=True), track("b", stems=True, bpm=126)
    cands = compose_rules(a, b)
    assert [c["idea"] for c in cands] == ["Bass swap blend", "Filter handoff", "Loop roll drop", "Drum bridge", "Echo out"]
    for c in cands:
        assert compile_plan(c, a, b, "c")[1] == [], c["idea"]
    assert [c["idea"] for c in compose_rules(a, track("b", bpm=174))] == ["Echo out"]


def fake_reply(candidates, stop="end_turn"):
    calls = []

    def send(params):
        calls.append(params)
        return {"text": json.dumps({"candidates": candidates}), "stop_reason": stop, "model": params["model"],
                "usage": {"input_tokens": 1000, "output_tokens": 500}}
    return send, calls


def test_llm_composer_request_parse_and_cache(tmp_path):
    good = [plan([ENTER, XF, STOP16], idea="Long blend")]
    send, calls = fake_reply(good)
    comp = C.Composer(tmp_path, transport=send)
    cands, meta = comp.compose(track(), track("b"), n=3)
    assert cands == good and meta["error"] is None and meta["cached"] is False and comp.calls == 1
    p = calls[0]
    assert p["model"] == "claude-opus-5-5" and p["fallbacks"] == "default" and p["betas"] == ["server-side-fallback-2026-07-01"]
    assert p["output_config"]["format"]["type"] == "json_schema" and p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert json.loads(p["messages"][0]["content"])["candidates"] == 3
    again = C.Composer(tmp_path, transport=send)                          # a fresh run, warm cache
    assert again.compose(track(), track("b"), n=3)[0] == good and again.calls == 0 and len(calls) == 1


@pytest.mark.parametrize("stop,text,err", [("refusal", None, "declined"), ("max_tokens", None, "cut off"), ("end_turn", "{oops", "unparseable")])
def test_llm_composer_failures_are_reported_not_cached(tmp_path, stop, text, err):
    def send(params):
        return {"text": text or "{}", "stop_reason": stop, "model": params["model"], "usage": {}}
    comp = C.Composer(tmp_path, transport=send)
    cands, meta = comp.compose(track(), track("b"))
    assert cands == [] and err in meta["error"] and not list(tmp_path.glob("*.json"))


def test_no_key_means_no_call(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "load_api_key", lambda: None)
    cands, meta = C.Composer(tmp_path).compose(track(), track("b"))
    assert cands == [] and "no API key" in meta["error"]


def test_plan_output_validates_against_c6(tmp_path):
    sys.path.insert(0, str(ROOT))
    import plan_transitions as P
    send, _ = fake_reply([plan([ENTER, XF, STOP16], idea="LLM blend"), plan([ENTER, STOP16], idea="Broken")])
    pair = P.plan_pair(track(), track("b"), "both", C.Composer(tmp_path, transport=send), 2)
    doc = {"schema_version": P.TRANSITIONS_VERSION, "generated_at": "now", "pairs": [pair]}
    jsonschema.validate(doc, json.loads((CONTRACTS / "transitions.schema.json").read_text()))
    llm = [c for c in pair["candidates"] if c["source"] == "llm"]
    assert [c["recipe"] is not None for c in llm] == [True, False] and "never moves to B" in llm[1]["errors"][0]
    assert pair["best"] is not None and pair["candidates"][pair["best"]]["critic"]["valid"]


def test_shared_fixture_matches_c6():
    """contracts/fixtures/sample_transitions.json is what the web tests read; keep it current."""
    doc = json.loads((CONTRACTS / "fixtures" / "sample_transitions.json").read_text())
    jsonschema.validate(doc, json.loads((CONTRACTS / "transitions.schema.json").read_text()))
    for c in doc["pairs"][0]["candidates"]:
        assert c["recipe"] is None or recipe_errors(c["recipe"]) == []


def test_a_clean_blend_outscores_an_echo_out():
    """Avoiding overlap must not be the safest way to score: a clean bass-swap blend earns credit
    for its clean overlap and beats a plain echo out on the same pair (it used to tie at the cap,
    and any blend flaw lost to the cut)."""
    blend = [ENTER, {"move": "crossfade", "start_bar": 0, "end_bar": 4, "to": 0.5, "shape": "ramp"},
             {"move": "bass_swap", "at_bar": 8}, {"move": "crossfade", "start_bar": 12, "end_bar": 16, "to": 1, "shape": "ramp"}, STOP16]
    echo = [{"move": "echo_throw", "at_bar": 8, "cut": True}, {"move": "in_enter", "at_bar": 8, "from_bar": 0},
            {"move": "crossfade", "start_bar": 8, "end_bar": 8, "to": 1, "shape": "cut"}, {"move": "out_stop", "at_bar": 12}]
    b, e = score(blend), score(echo)
    assert b["measures"]["clean_overlap_bars"] >= 8 and "clean_blend" in b["breakdown"]
    assert e["measures"]["clean_overlap_bars"] == 0
    assert b["score"] > e["score"]


def test_stem_presence_is_measured_against_the_mix(tmp_path):
    """An instrumental track's vocal stem holds only faint bleed. Judged against its own loudest
    bars (the first version) that bleed read as vocals in every phrase (djteixo, San Pacho); judged
    against the mix it's ~45 dB down: no vocals. A real vocal ~6 dB under the mix is voiced. The
    same measure finds beatless bars (a breakdown's drum stem is bleed) for the composer."""
    import soundfile as sf
    from handoff_pipeline.compose.facts import stem_presence
    sr, bar = 11025, 2.0
    t = np.arange(int(16 * bar * sr)) / sr
    music = 0.3 * np.sin(2 * np.pi * 110 * t)
    sung = (t >= 8 * bar) * 0.15 * np.sin(2 * np.pi * 440 * t)          # vocals in the second half
    bleed = 0.002 * np.sin(2 * np.pi * 440 * t)                         # ~ -45 dB under the music
    kick = (t < 8 * bar) * 0.3 * np.sin(2 * np.pi * 60 * t) * (t % 0.5 < 0.1)   # drums in the first half only
    stems = {k: f"audio/stem_{k}.flac" for k in ("drums", "bass", "vocals", "other")}
    analysis = {"bars": [{"start_s": i * bar} for i in range(16)],
                "audio": {"stems": stems, "stems_gain_db": 0.0, "mix": "audio/mix.flac"}}
    for name, voc in (("instrumental", bleed), ("vocal", sung)):
        d = tmp_path / name
        (d / "audio").mkdir(parents=True)
        parts = {"drums": kick, "bass": music, "vocals": voc, "other": np.zeros_like(t)}
        sf.write(d / "audio" / "mix.flac", np.stack([sum(parts.values())] * 2, 1), sr)
        for k, y in parts.items():
            sf.write(d / stems[k], np.stack([y] * 2, 1), sr)
        pres = stem_presence(d, analysis)
        assert pres["drums"][:8].min() > 0.9 and pres["drums"][8:].max() < 0.1, pres["drums"]
        if name == "instrumental":
            assert pres["vocals"].max() < 0.1, pres["vocals"]
        else:
            assert pres["vocals"][:8].max() < 0.1 and pres["vocals"][8:].min() > 0.9, pres["vocals"]
        assert stem_presence(d, analysis)["drums"].tolist() == pres["drums"].tolist()   # cached


# ---------------------------------------------------------------- across tempos and keys (moves v3)

from handoff_pipeline.compose.mix import key_relation, pitch_offset, ride_sync, tempo_rate, tonal_clash, transpose_camelot  # noqa: E402

RIDE = {"move": "tempo_ride", "start_bar": 0, "end_bar": 8}


def test_pitch_follows_tempo_without_key_lock():
    """The board changes tempo by playback rate, so a locked deck is transposed: 6% is a semitone.
    Locked, B's key is heard shifted by the tempo ratio (12*log2), whichever deck moved."""
    assert transpose_camelot("8B", 1) == "3B" and transpose_camelot("3B", -1) == "8B" and transpose_camelot("12A", 1) == "7A"
    s = pitch_offset(1.0, 124 / 128)                                       # 128 synced down to 124
    assert abs(s + 0.55) < 0.01 and pitch_offset(128 / 124, 1.0) == pytest.approx(s)
    assert key_relation("8B", "9B", s) == {"in_sounds_as": "9B", "distance": 1, "detune_cents": -55}
    assert tonal_clash("8A", "8A", 0) == 0 and tonal_clash("8A", "9A", 0) == 0
    assert tonal_clash("8B", "8B", pitch_offset(1, 125 / 128)) < 0.05      # 41 cents: fine by ear (zaza <-> EvenFall)
    assert 0.2 < tonal_clash("8B", "8B", pitch_offset(1, 124 / 128)) < 0.5  # 55 cents: sour
    assert tonal_clash("8A", "10B", 0) == 2                                # distance 3


def test_tempo_ride_reaches_twice_the_fader_range():
    m, r_out, r_in = ride_sync(124, 128)
    assert m == 1 and r_out == pytest.approx(128 / 124) and r_in == pytest.approx(1)   # B plays at its own tempo
    m, r_out, r_in = ride_sync(145, 128)                                   # 13% apart: beyond plain sync
    assert m == 1 and r_out == pytest.approx(0.92) and r_in == pytest.approx(145 * 0.92 / 128)
    assert ride_sync(170, 128) is None
    pf = pair_facts(track(bpm=145), track("b", bpm=128, camelot="8A"))
    assert pf["tempo"]["sync"] is None and pf["tempo"]["tempo_ride"]["out_rate_change_pct"] == -8.0


def test_summary_marks_beatless_and_drums_only_windows():
    drums = np.r_[np.zeros(16), np.full(112, 0.9)]
    w = summary(track(drums=drums, music=0.0))["four_bar_windows"]
    assert w[0]["drums"] == 0 and w[8]["drums"] == 0.9 and w[8]["tonal"] == 0
    assert summary(track())["four_bar_windows"][0]["drums"] is None


def test_tempo_ride_compiles_to_a_ramp_on_the_outgoing_tempo():
    a, b = track(bpm=145), track("b", bpm=128)
    moves = [RIDE, {"move": "in_enter", "at_bar": 8, "from_bar": 0},
             {"move": "crossfade", "start_bar": 8, "end_bar": 16, "to": 1, "shape": "ramp"}, STOP16]
    r, errs = compile_plan(plan(moves), a, b, "c")
    assert errs == [] and recipe_errors(r) == []
    pts = lane(r, "out", "tempo")
    assert pts[0] == [0, 0.5] and pts[-1][0] == 8 and tempo_rate(pts[-1][1]) == pytest.approx(0.92, abs=1e-4)
    assert r["requires"]["beatmatchable"] is True                          # the ride makes the overlap lockable
    r, _ = compile_plan(plan(moves[1:]), a, b, "c")
    assert r["requires"]["beatmatchable"] is False                         # without it, it would play unlocked


@pytest.mark.parametrize("moves,why", [
    ([{"move": "tempo_ride", "start_bar": 0, "end_bar": 4}, {"move": "in_enter", "at_bar": 8, "from_bar": 0}, XF, STOP16], "a bar per 1%"),
    ([RIDE, ENTER, XF, STOP16], "at or after the ride ends"),
    ([RIDE, {"move": "tempo_ride", "start_bar": 8, "end_bar": 12}, {"move": "in_enter", "at_bar": 12, "from_bar": 0}, XF, STOP16], "at most one tempo_ride"),
    ([{"move": "loop_roll", "start_bar": 6, "end_bar": 10}, RIDE, {"move": "in_enter", "at_bar": 10, "from_bar": 0}, XF, STOP16], "overlap"),
    ([ENTER, XF, {"move": "brake", "at_bar": 15.75, "beats": 4}, STOP16], "stopped by out_stop"),
    ([ENTER, XF, {"move": "brake", "at_bar": 12, "beats": 4}, {"move": "rhythmic_gate", "deck": "out", "start_bar": 10,
      "end_bar": 14, "period_beats": 2, "depth": 1}, STOP16], "rhythmic_gate overlaps"),
])
def test_ride_and_brake_rules(moves, why):
    r, errs = compile_plan(plan(moves), track(bpm=145), track("b", bpm=128), "c")
    assert r is None and any(why in e for e in errs), errs


def test_a_ride_needs_tempos_it_can_reach():
    moves = [RIDE, {"move": "in_enter", "at_bar": 8, "from_bar": 0}, XF, STOP16]
    r, errs = compile_plan(plan(moves), track(bpm=170), track("b", bpm=128), "c")
    assert r is None and any("can't bring" in e for e in errs)


def test_brake_is_an_exact_event_then_silence():
    r, errs = compile_plan(plan([ENTER, XF, {"move": "brake", "at_bar": 15.5, "beats": 2}, STOP16]), track(), track("b"), "c")
    assert errs == [] and recipe_errors(r) == []
    assert {"at_bar": 15.5, "deck": "out", "command": "brake", "beats": 2} in r["events"]
    assert lane(r, "out", "volume") == [[0, 0.8], [16, 0.8], [16, 0]]


def test_key_clash_counts_pitched_parts_only():
    """Drums have no key: A stripped to drums under B's music doesn't clash at any key distance."""
    blend = [ENTER, XF, STOP16]
    a, b = track(stems=True, music=0.8), track("b", stems=True, camelot="3B", music=0.8)
    assert score(blend, a, b)["measures"]["key_clash_bars"] > 0
    drums_only = [{"move": "stem_drop", "deck": "out", "stems": ["bass", "other", "vocals"], "action": "mute", "at_bar": 0}, *blend]
    assert score(drums_only, a, b)["measures"]["key_clash_bars"] == 0


def test_unlocked_overlap_is_fine_without_two_beats():
    """174 and 124 BPM can't lock. B's beat under A's beatless outro is a legitimate bridge; two
    beats together is a train wreck (invalid). Without stems, any unlocked overlap stays invalid."""
    blend = [ENTER, XF, STOP16]
    beatless_outro = np.r_[np.full(96, 0.9), np.zeros(32)]
    a = track(stems=True, drums=beatless_outro, music=0.1, camelot="8A")
    b = track("b", bpm=174, stems=True, camelot="8A", drums=0.9)
    ok = score(blend, a, b)
    assert ok["valid"] and ok["measures"]["locked"] is False and ok["measures"]["rhythm_clash_bars"] == 0
    wreck = score(blend, track(stems=True, camelot="8A"), b)
    assert not wreck["valid"] and wreck["measures"]["rhythm_clash_bars"] >= 1 and "train wreck" in wreck["reasons"][0]
    assert not score(blend, track(), track("b", bpm=174))["valid"]


def test_saved_pairs_from_an_older_version_are_recompiled_from_their_plans(tmp_path):
    """A contract or critic change mustn't throw away compositions that cost money: older saved
    pairs are rebuilt from their stored plans, without calling the composer again."""
    import plan_transitions as P
    a, b = track(), track("b")
    send, _ = fake_reply([plan([ENTER, XF, STOP16], idea="Paid for")])
    pair = P.plan_pair(a, b, "llm", C.Composer(tmp_path / "cache", transport=send), 1)
    old = json.loads(json.dumps(pair))
    old["candidates"][0]["recipe"]["schema_version"] = 2
    old["candidates"][0]["critic"]["score"] = 1.0
    gone = {**old, "out_track": "gone"}
    path = tmp_path / "transitions.json"
    path.write_text(json.dumps({"schema_version": 2, "generated_at": "x", "pairs": [old, gone]}))
    saved = P.load_saved(path, {"a": a, "b": b}.get)
    assert list(saved) == [("a", "b")]
    c = saved[("a", "b")]["candidates"][0]
    assert c["source"] == "llm" and c["idea"] == "Paid for" and c["recipe"]["schema_version"] == 4
    assert c["critic"] == pair["candidates"][0]["critic"]
    doc = {"schema_version": P.TRANSITIONS_VERSION, "generated_at": "now", "pairs": list(saved.values())}
    jsonschema.validate(doc, json.loads((CONTRACTS / "transitions.schema.json").read_text()))


def test_rerunning_the_planner_keeps_earlier_compositions(tmp_path):
    """Rerunning the free rules planner over the library used to replace each saved pair with
    the new rules-only result, dropping the LLM candidates already paid for."""
    import plan_transitions as P
    a, b = track(), track("b")
    send, _ = fake_reply([plan([ENTER, XF, STOP16], idea="Paid for")])
    paid = P.plan_pair(a, b, "llm", C.Composer(tmp_path, transport=send), 1)
    rules = P.plan_pair(a, b, "rules", None, 4)
    merged = P.merge_pair(paid, rules)
    assert {c["source"] for c in merged["candidates"]} == {"llm", "rules"} and merged["composer"] == paid["composer"]
    assert len(P.merge_pair(merged, rules)["candidates"]) == len(merged["candidates"])     # idempotent
    best = merged["candidates"][merged["best"]]
    assert best["source"] == "llm"          # by ear, a composition beat a higher-scored rules drum bridge
    assert any(c["source"] == "rules" and c["critic"]["score"] > best["critic"]["score"] for c in merged["candidates"] if c["critic"])


def test_a_brake_can_hand_over_on_the_bar_a_stops():
    """Live, Claude's brake plans all landed B on the bar A stops, and were thrown away: B had to
    start strictly before. A hard switch on the stop bar is a legitimate cut, with no overlap."""
    moves = [{"move": "loop_roll", "start_bar": 4, "end_bar": 7}, {"move": "brake", "at_bar": 7.5, "beats": 2},
             {"move": "in_enter", "at_bar": 8, "from_bar": 16}, {"move": "crossfade", "start_bar": 8, "end_bar": 8, "to": 1, "shape": "cut"},
             {"move": "out_stop", "at_bar": 8}]
    a, b = track(bpm=124), track("b", bpm=101.65, steady=False)
    r, errs = compile_plan(plan(moves, start=64), a, b, "c")
    assert errs == [] and r["requires"]["beatmatchable"] is False
    assert critique(r, a, b, len(moves))["valid"]
    late = [*moves[:2], {"move": "in_enter", "at_bar": 9, "from_bar": 16}, *moves[3:]]
    assert any("after out_stop" in e for e in compile_plan(plan(late, start=64), a, b, "c")[1])


# ---------------------------------------------------------------- one transition, refined

def scripted(replies):
    """A fake Claude that answers each call with the next plan in `replies`."""
    calls = []

    def send(params):
        calls.append(params)
        c = replies[min(len(calls), len(replies)) - 1]
        return {"text": json.dumps({"candidates": [c]}), "stop_reason": "end_turn", "model": params["model"],
                "usage": {"input_tokens": 1000, "output_tokens": 500}}
    return send, calls


MUDDY = [ENTER, {"move": "crossfade", "start_bar": 0, "end_bar": 2, "to": 0.5, "shape": "ramp"},
         {"move": "crossfade", "start_bar": 14, "end_bar": 16, "to": 1, "shape": "ramp"}, STOP16]
SWAPPED = MUDDY[:3] + [{"move": "bass_swap", "at_bar": 8}] + MUDDY[3:]


def bassy():
    a, b = track(bass=1.0), track("b", bass=1.0)
    a.low[96:], b.low[:16] = 0.9, 0.9
    return a, b


def test_one_transition_is_drafted_checked_and_revised_until_it_is_good(tmp_path):
    import plan_transitions as P
    a, b = bassy()
    broken = plan([ENTER, STOP16], idea="Draft")                   # never crossfades: doesn't compile
    send, calls = scripted([broken, plan(MUDDY, idea="Muddy"), plan(SWAPPED, idea="Swapped")])
    pair = P.plan_pair(a, b, "llm", C.Composer(tmp_path, transport=send), 1, refine=2)
    assert len(calls) == 3 and [c["idea"] for c in pair["candidates"]] == ["Swapped"]
    second = calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    fb = json.loads(second[2]["content"])["feedback"]
    assert fb["compiled"] is False and any("never moves to B" in e for e in fb["errors"])
    fb = json.loads(calls[2]["messages"][4]["content"])["feedback"]
    assert fb["compiled"] and "bass_clash" in fb["penalties"] and any("(bars " in f for f in fb["findings"])
    meta = pair["composer"]
    assert [r["round"] for r in meta["rounds"]] == [0, 1, 2] and meta["kept_round"] == 2
    assert meta["usage"]["output_tokens"] == 1500 and pair["best"] == 0
    again = C.Composer(tmp_path, transport=send)                   # rerun: every round comes from the cache
    assert P.plan_pair(a, b, "llm", again, 1, refine=2)["candidates"][0]["idea"] == "Swapped" and again.calls == 0


def test_a_good_draft_is_not_revised_and_a_worse_revision_never_wins(tmp_path):
    import plan_transitions as P
    a, b = bassy()
    send, calls = scripted([plan(SWAPPED, idea="Clean")])
    clean = P.plan_pair(a, b, "llm", C.Composer(tmp_path / "1", transport=send), 1, refine=2)
    assert P.good_enough(clean["candidates"][0]) and len(calls) == 1
    send, calls = scripted([plan(MUDDY, idea="Muddy"), plan([ENTER, STOP16], idea="Broken"), plan([XF, STOP16], idea="Worse")])
    pair = P.plan_pair(a, b, "llm", C.Composer(tmp_path / "2", transport=send), 1, refine=2)
    assert len(calls) == 3 and pair["candidates"][0]["idea"] == "Muddy" and pair["composer"]["kept_round"] == 0


def test_unavoidable_penalties_are_flagged_not_chased():
    """Live (Protohype -> Morgan Page, 145 vs 125): 'B is stretched 7%' comes from the tempos.
    Claude was asked to fix it and piled on moves (82.6 -> 66.6)."""
    import plan_transitions as P
    c = {"recipe": {"id": "r"}, "errors": [], "critic": {"valid": True, "score": 88.0, "reasons": ["B is stretched 7%"],
                                                "breakdown": {"stretch": -4.0, "clean_blend": 2.0}}}
    fb = P.feedback(c)
    assert fb["penalties"] == {} and "stretch" in fb["unavoidable"] and P.good_enough(c)
    c["critic"]["breakdown"]["dip"] = -3.0
    assert P.feedback(c)["penalties"] == {"dip": -3.0} and not P.good_enough(c)


# ---------------------------------------------------------------- creativity (concepts, memory, novelty, loop_hold)

import random  # noqa: E402

from handoff_pipeline.compose.concepts import History, draw, feasible, similarity  # noqa: E402


def test_concepts_fit_the_tracks_and_rotate_away_from_recent_work():
    plain = {c["id"] for c in feasible(track(), track("b", bpm=174))}      # no stems, can't lock
    assert "hook_loop" not in plain and "acapella_over" not in plain and {"energy_slam", "turntable_stop"} <= plain
    rich = {c["id"] for c in feasible(track(stems=True, vocals=0.9), track("b", stems=True, vocals=0.9))}
    assert {"acapella_over", "vocal_preview", "layer_by_layer"} <= rich and "hook_loop" not in rich   # no hook detected
    a = track(stems=True, vocals=0.9)
    a.hooks, a.cues = [{"kind": "vocal", "bars": 2, "starts": [16, 24, 40]}], [{"kind": "build", "bar": 88, "bars": 8}]
    b = track("b", stems=True, vocals=0.9)
    b.hooks, b.cues = [{"kind": "vocal", "bars": 4, "starts": [8, 24, 40]}], [{"kind": "drop", "bar": 16, "bars": 16}]
    assert {"hook_loop", "build_handoff", "hook_mashup", "false_drop"} <= {c["id"] for c in feasible(a, b)}
    a, b = track(stems=True, vocals=0.9), track("b", stems=True, vocals=0.9)
    recent = [{"concept": c} for c in ("hook_loop", "false_drop", "energy_slam")]
    for seed in range(20):
        picks = draw(a, b, 3, recent, random.Random(seed))
        assert len({p["id"] for p in picks}) == 3 and not {p["id"] for p in picks} & {"hook_loop", "false_drop", "energy_slam"}


def test_similarity_compares_toolkits_not_the_moves_every_plan_has():
    recent = [{"toolkit": ["echo_throw", "stem_drop"]}]
    assert similarity([ENTER, XF, {"move": "stem_drop"}, {"move": "echo_throw"}, STOP16], recent) == 1
    assert similarity([ENTER, XF, {"move": "loop_hold"}, STOP16], recent) == 0
    assert similarity([ENTER, XF, STOP16], []) == 0


def test_composing_with_memory_draws_concepts_prefers_novelty_and_remembers(tmp_path):
    """The draft that repeats recent work loses to a fresh one scoring a little lower; a
    revision is told only its own concept; the kept transition joins the history."""
    import plan_transitions as P
    a, b = bassy()
    a.has_stems = b.has_stems = True
    hist = History(tmp_path / "h.jsonl")
    for _ in range(2):
        hist.add(("x", "y"), {"idea": "old", "plan": {"moves": SWAPPED}}, "long_eq_blend")
    same = plan(SWAPPED, idea="Same again")                              # clean (no penalties), but a repeat
    fresh = plan(MUDDY[:1] + [{"move": "eq", "deck": "in", "band": "low", "start_bar": 0, "end_bar": 0, "to": "kill"},
                              {"move": "eq", "deck": "in", "band": "low", "start_bar": 8, "end_bar": 8, "to": "flat"},
                              {"move": "eq", "deck": "out", "band": "low", "start_bar": 8, "end_bar": 8, "to": "kill"}] + MUDDY[1:],
                 idea="Fresh")
    sent = []

    def send(params):
        sent.append(params)
        cands = [same, fresh] if len(sent) == 1 else [fresh]
        return {"text": json.dumps({"candidates": cands}), "stop_reason": "end_turn", "model": params["model"], "usage": {}}
    pair = P.plan_pair(a, b, "llm", C.Composer(tmp_path / "c", transport=send), 2, refine=2, history=hist, rng=random.Random(1))
    first = json.loads(sent[0]["messages"][0]["content"])
    assert len(first["concepts"]) == 2 and [w["idea"] for w in first["recent_work"]] == ["old", "old"]
    assert pair["candidates"][0]["idea"] == "Fresh" and "_novelty" not in pair["candidates"][0]
    if len(sent) > 1:
        assert [c["id"] for c in json.loads(sent[1]["messages"][0]["content"])["concepts"]] == [pair["composer"]["kept_concept"]]
    assert hist.recent()[-1]["idea"] == "Fresh" and hist.recent()[-1]["concept"] == pair["composer"]["kept_concept"]


def test_loop_hold_compiles_to_a_held_loop_and_the_critic_cycles_its_bars():
    a, b = track(stems=True), track("b", stems=True)
    a.mid[100:102] = 0.1                                                  # A's bars 100-101 sound different
    moves = [{"move": "loop_hold", "start_bar": 4, "end_bar": 12, "bars": 2}, {"move": "in_enter", "at_bar": 4, "from_bar": 0},
             {"move": "crossfade", "start_bar": 4, "end_bar": 12, "to": 1, "shape": "ramp"}, {"move": "out_stop", "at_bar": 12}]
    r, errs = compile_plan(plan(moves), a, b, "c")
    assert errs == [] and recipe_errors(r) == []
    ev = [(e["at_bar"], e["command"], e.get("beats")) for e in r["events"] if e["deck"] == "out"]
    assert sorted(ev, key=str) == sorted([(4, "loop", 8), (12, "loop_off", None), (12, "pause", None)], key=str)
    assert critique(r, a, b, len(moves))["valid"]
    for bad, why in [({"move": "loop_hold", "start_bar": 4, "end_bar": 5, "bars": 2}, "at least one loop"),
                     ({"move": "loop_hold", "start_bar": 4.5, "end_bar": 12, "bars": 1}, "bar line"),
                     ({"move": "loop_roll", "start_bar": 10, "end_bar": 12}, "overlap")]:
        errs = compile_plan(plan([bad if bad["move"] == "loop_hold" else moves[0], *moves[1:], *([bad] if bad["move"] == "loop_roll" else [])]), a, b, "c")[1]
        assert any(why in e for e in errs), (bad, errs)


def test_claudes_loop_move_compiles_like_the_internal_forms():
    """The output schema has one `loop` move (a 14th move type made the API reject the grammar);
    it compiles exactly like loop_roll / loop_hold, which older saved plans still use."""
    from handoff_pipeline.compose.moves import PLAN_SCHEMA, normalize
    a, b = track(stems=True), track("b", stems=True)
    rest = [{"move": "in_enter", "at_bar": 4, "from_bar": 0}, {"move": "crossfade", "start_bar": 4, "end_bar": 12, "to": 1, "shape": "ramp"},
            {"move": "out_stop", "at_bar": 12}]
    for new, old in [({"move": "loop", "start_bar": 4, "end_bar": 12, "style": "hold_2"}, {"move": "loop_hold", "start_bar": 4, "end_bar": 12, "bars": 2}),
                     ({"move": "loop", "start_bar": 8, "end_bar": 12, "style": "roll"}, {"move": "loop_roll", "start_bar": 8, "end_bar": 12})]:
        c = plan([new, *rest])
        jsonschema.validate({"candidates": [c]}, PLAN_SCHEMA)
        r1, e1 = compile_plan(c, a, b, "x")
        r2, e2 = compile_plan(plan([old, *rest]), a, b, "x")
        assert e1 == e2 == [] and r1["events"] == r2["events"]
        assert c["moves"][0] == new and normalize(c)["moves"][0] == old       # the stored plan is Claude's own
    names = [m["properties"]["move"]["const"] for m in PLAN_SCHEMA["properties"]["candidates"]["items"]["properties"]["moves"]["items"]["anyOf"]]
    assert len(names) <= 13 and "loop_roll" not in names


# ---------------------------------------------------------------- structure (TrackAnalysis v4) for the composer and critic

def test_the_composer_sees_sections_cues_and_hooks():
    t = track(stems=True, vocals=0.9)
    t.cues = [{"kind": "build", "bar": 88, "bars": 8}, {"kind": "drop", "bar": 96, "bars": 1}]
    t.hooks = [{"kind": "vocal", "bars": 2, "starts": [16, 24, 40, 48], "strength": 0.97}]
    s = summary(t)
    assert s["sections"][0] == {"bars": "0-16", "label": "intro", "energy": "low", "vocals": "unknown", "group": None}
    assert s["cues"] == [{"kind": "build", "bar": 88, "bars": 8}, {"kind": "drop", "bar": 96}]
    assert s["hooks"] == [{"kind": "vocal", "bars": 2, "starts": [16, 24, 40, 48]}]


def test_landing_bs_drop_in_full_earns_the_payoff():
    a, b = track(stems=True, drums=0.9, music=0.8), track("b", stems=True, drums=0.9, music=0.8)
    b.cues = [{"kind": "drop", "bar": 16, "bars": 16}]
    moves = [{"move": "in_enter", "at_bar": 0, "from_bar": 8}, {"move": "crossfade", "start_bar": 0, "end_bar": 6, "to": 1, "shape": "ramp"},
             {"move": "out_stop", "at_bar": 12}]
    landed = score(moves, a, b)
    assert "drop_lands" in landed["breakdown"] and any("bar 8" in r for r in landed["reasons"])
    held = score([moves[0], {"move": "stem_drop", "deck": "in", "stems": ["bass"], "action": "mute", "at_bar": 0},
                  {"move": "stem_drop", "deck": "in", "stems": ["bass"], "action": "unmute", "at_bar": 10}, *moves[1:]], a, b)
    assert "drop_lands" not in held["breakdown"]                          # B's bass was held back as its drop hit



# ---------------------------------------------------------------- key lock and key shift

def test_with_key_lock_a_tempo_gap_does_not_detune_and_a_key_shift_fixes_a_clash():
    """The board has key lock: 128 synced to 124 used to play B 55 cents flat (a clash on its
    own); now the keys meet as written. 8A and 10A clash (two steps); B shifted -2 semitones is
    8A... and the critic, compiler and facts agree on it."""
    from handoff_pipeline.compose.mix import best_key_shift, key_shift_options
    blend = [ENTER, XF, STOP16]
    same = score(blend, track(stems=True, music=0.8), track("b", bpm=128, stems=True, music=0.8))
    assert same["measures"]["key_clash_bars"] == 0
    a, b = track(stems=True, music=0.8), track("b", camelot="10A", stems=True, music=0.8)
    assert score(blend, a, b)["measures"]["key_clash_bars"] > 0
    shift = best_key_shift("8A", "10A")
    assert shift == -2 and {"in_key_shift": -2, "in_sounds_as": "8A", "distance": 0} in key_shift_options("8A", "10A")
    r, errs = compile_plan(plan(blend, shift=shift), a, b, "c")
    assert errs == [] and lane(r, "in", "key") == [[0, round(0.5 - 2 / 12, 4)]]      # set before B is heard, and kept
    fixed = critique(r, a, b, 3)
    assert fixed["measures"]["key_clash_bars"] == 0 and fixed["score"] > score(blend, a, b)["score"]
    assert pair_facts(a, b)["key"]["best_in_key_shift"] == -2 and "when_locked" not in pair_facts(a, b)["key"]


def test_a_tempo_ride_no_longer_costs_pitch():
    a, b = track(bpm=145, stems=True, music=0.8), track("b", bpm=128, stems=True)
    ride = [RIDE, {"move": "in_enter", "at_bar": 8, "from_bar": 0},
            {"move": "crossfade", "start_bar": 8, "end_bar": 16, "to": 1, "shape": "ramp"}, STOP16]
    v = score(ride, a, b)
    assert "ride_pitch" not in v["breakdown"] and not any("slides" in r for r in v["reasons"])


def test_older_plans_without_a_key_shift_still_compile():
    c = plan([ENTER, XF, STOP16])
    del c["in_key_shift"]
    r, errs = compile_plan(c, track(), track("b"), "c")
    assert errs == [] and not any(l["control"] == "key" for l in r["lanes"])


def test_the_key_shift_survives_saving_recompiling_and_revising(tmp_path):
    """The planner stored only out_start_bar and moves: a composed key shift was compiled once,
    then lost on recompile, and revisions showed Claude its plan without it."""
    import plan_transitions as P
    a, b = track(stems=True, music=0.8), track("b", camelot="10A", stems=True, music=0.8)
    send, calls = scripted([plan(MUDDY, idea="Shifted", shift=-2), plan(MUDDY, idea="Shifted", shift=-2)])
    pair = P.plan_pair(a, b, "llm", C.Composer(tmp_path, transport=send), 1, refine=1)
    c = pair["candidates"][0]
    assert c["plan"]["in_key_shift"] == -2 and lane(c["recipe"], "in", "key") == [[0, round(0.5 - 2 / 12, 4)]]
    again = P.recompile_pair(a, b, pair)["candidates"][0]
    assert again["recipe"]["lanes"] == c["recipe"]["lanes"]
    if len(calls) > 1:
        assert json.loads(calls[1]["messages"][1]["content"])["candidates"][0]["in_key_shift"] == -2


def test_rerunning_the_rules_replaces_them_instead_of_piling_up():
    """Rules candidates are regenerated on every run; their ids hash their content, so any change
    to the rules (a new field) added a second 'Echo out' next to the first, run after run."""
    import plan_transitions as P
    a, b = track(), track("b")
    old = P.plan_pair(a, b, "rules", None, 4)
    for c in old["candidates"]:
        c["id"] += "_old"                                                 # same ideas, older content
    paid = {**old["candidates"][0], "id": "llm_paid", "source": "llm", "idea": "Paid for"}
    saved = {**old, "candidates": old["candidates"] + [paid]}
    merged = P.merge_pair(saved, P.plan_pair(a, b, "rules", None, 4))
    ideas = [c["idea"] for c in merged["candidates"] if c["source"] == "rules"]
    assert len(ideas) == len(set(ideas)) and "Paid for" in [c["idea"] for c in merged["candidates"]]
