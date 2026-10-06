"""Demucs stems must add back up to the mix the board serves, at the level the analysis says.

Found on real tracks (2026-10-05): with Demucs's default --clip-mode rescale, a stem that would
peak above full scale (common on loud masters: stems can peak higher than their sum) is scaled
down as a whole, so playing the stems put the drums ~2 dB under the mix. Skipped when Demucs
isn't installed (it's optional: pip install demucs)."""
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip("demucs")
from handoff_pipeline.audio_io import separate_stems, to_flac  # noqa: E402

SOURCE = ROOT / "tracks" / "Symbiotic Crowd - Frog Prince(124bpm).mp3"   # a loud, mastered real track


@pytest.mark.skipif(not SOURCE.exists(), reason="needs the real track in pipeline/tracks/")
@pytest.mark.parametrize("dc", [0.0, -0.02])
def test_stems_rebuild_the_mix_at_the_recorded_gain(tmp_path, dc):
    """dc: a DC offset in the source (CR0SS - Stars Collide has -0.017). Demucs adds its input's
    mean back to every stem, so the stems summed to 4x the offset (rebuild -11 dB), and muting a
    stem stepped the DC: a thump on every stem toggle. The offset must not reach the stems."""
    full = tmp_path / "full.flac"
    to_flac(SOURCE, full)
    y, sr = sf.read(full)
    mix = tmp_path / "audio" / "mix.flac"
    mix.parent.mkdir()
    sf.write(mix, y[60 * sr:72 * sr] * 0.9 + dc, sr)      # 12 s from a loud section
    paths, gain_db = separate_stems(mix, mix.parent)
    ref = sf.read(mix)[0]
    stems = [sf.read(tmp_path / p)[0] for p in paths.values()]
    assert all(np.abs(s).max() < 0.999 for s in stems), "a stem touches full scale (clipped or rescaled)"
    assert all(np.abs(st.mean(0)).max() < 1e-3 for st in stems), "DC offset in a stem"
    rebuilt = sum(stems) * 10 ** (gain_db / 20)
    ref = ref - ref.mean(0)                                  # the stems carry the music, not the offset
    n = min(len(rebuilt), len(ref))
    resid_db = 10 * np.log10(np.mean((rebuilt[:n] - ref[:n]) ** 2) / np.mean(ref[:n] ** 2))
    assert resid_db < -30, f"stems rebuild the mix only to {resid_db:.1f} dB"
