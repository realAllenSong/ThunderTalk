"""Programme loudness (BS.1770) and the output levelling built on it."""

from __future__ import annotations

import numpy as np
import pytest

from thundertalk.core import loudness as L


def _sine(sr, seconds=4.0, hz=997.0, amp=1.0):
    return (amp * np.sin(2 * np.pi * hz * np.arange(int(sr * seconds)) / sr)).astype(np.float32)


@pytest.mark.parametrize("sr", [16000, 22050, 24000, 44100, 48000])
def test_a_full_scale_997_hz_sine_is_minus_three_lufs_at_any_sample_rate(sr):
    assert L.integrated_lufs(_sine(sr), sr) == pytest.approx(-3.01, abs=0.1)        # the standard's reference value


def test_half_the_amplitude_is_six_lu_less():
    sr = 24000
    assert L.integrated_lufs(_sine(sr), sr) - L.integrated_lufs(_sine(sr, amp=0.5), sr) == pytest.approx(6.02, abs=0.02)


def test_silence_around_speech_does_not_change_its_loudness():
    sr = 24000
    x = _sine(sr, amp=0.2)
    padded = np.concatenate([np.zeros(sr * 3, np.float32), x, np.zeros(sr * 3, np.float32)])
    assert L.integrated_lufs(padded, sr) == pytest.approx(L.integrated_lufs(x, sr), abs=0.5)
    assert L.integrated_lufs(padded, sr) > 10 * np.log10(np.mean(padded ** 2)) + 3      # plain RMS would say 4 dB quieter


def test_low_rumble_counts_for_less_than_the_same_level_in_the_voice_band():
    sr = 24000
    assert L.integrated_lufs(_sine(sr, hz=40.0), sr) < L.integrated_lufs(_sine(sr, hz=1000.0), sr) - 6


def test_nothing_to_measure():
    assert L.integrated_lufs(np.zeros(0, np.float32), 24000) == L.SILENT
    assert L.integrated_lufs(np.zeros(24000, np.float32), 24000) == L.SILENT
    y, before = L.normalize(np.zeros(24000, np.float32), 24000)
    assert before == L.SILENT and not y.any()


def test_a_piece_shorter_than_one_block_is_still_measured_at_its_own_level():
    sr = 24000
    assert L.integrated_lufs(_sine(sr, seconds=0.2, amp=0.2), sr) == pytest.approx(L.integrated_lufs(_sine(sr, amp=0.2), sr), abs=1.0)


def test_normalize_reaches_the_target_from_either_side():
    sr = 24000
    for amp in (0.01, 0.6):
        y, before = L.normalize(_sine(sr, amp=amp, hz=300.0), sr)
        assert before == pytest.approx(L.integrated_lufs(_sine(sr, amp=amp, hz=300.0), sr))
        assert L.integrated_lufs(y, sr) == pytest.approx(L.SPEECH_LUFS, abs=0.1)


def test_the_limiter_holds_a_spike_and_leaves_everything_else_alone():
    sr = 24000
    x = _sine(sr, amp=0.3, hz=220.0)
    x[sr] = 1.6                                                    # one stray peak
    y = L.limit(x, sr)
    assert float(np.abs(y).max()) <= L.PEAK_CEILING + 1e-6
    far = np.r_[0:sr - sr // 50, sr + sr // 50:len(x)]             # more than 20 ms from the spike
    assert np.allclose(y[far], x[far], atol=1e-6)
    quiet = _sine(sr, amp=0.3)
    assert L.limit(quiet, sr) is quiet                             # nothing over the ceiling: returned as it is


def test_match_gains_brings_pieces_to_their_median_within_six_db():
    sr = 24000
    pieces = [_sine(sr, 1.0, amp=a) for a in (0.2, 0.1, 0.2, 0.01, 0.2)]
    g = L.match_gains(pieces, sr)
    assert g[0] == pytest.approx(1.0, abs=0.02) and g[1] == pytest.approx(2.0, abs=0.05)
    assert g[3] == pytest.approx(10 ** (6 / 20), abs=0.01)         # 26 dB down: a bad take, raised by the cap only
    assert L.match_gains([np.zeros(sr, np.float32)], sr) == [1.0]
