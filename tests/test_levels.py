"""Exercise real FFmpeg all-channel level checks, including float WAV headroom."""
from __future__ import annotations

import math
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import wavfile

from clasp import levels
from clasp.media import ClaspError


@pytest.fixture
def ffmpeg_available():
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for level integration checks")


def _wav(tmp_path: Path, samples: np.ndarray) -> Path:
    path = tmp_path / "levels.wav"
    wavfile.write(path, 48000, samples)
    return path


def test_finite_float_peak(ffmpeg_available, tmp_path):
    samples = np.zeros(4800, dtype=np.float32)
    samples[1234] = -.5
    peak = levels.check_levels(_wav(tmp_path, samples), {"index": 0})
    assert peak == pytest.approx(20 * math.log10(.5), abs=1e-5)


def test_checks_all_channels_and_whole_file(ffmpeg_available, tmp_path):
    samples = np.zeros((96000, 2), dtype=np.float32)
    samples[:, 0] = .1
    samples[-1, 1] = -2.0
    with pytest.raises(ClaspError, match="Lower WAV gain before export"):
        levels.check_levels(_wav(tmp_path, samples), {"index": 0})


def test_integer_full_scale_is_valid(ffmpeg_available, tmp_path):
    samples = np.array([-32768, 32767] * 2400, dtype=np.int16)
    assert levels.check_levels(_wav(tmp_path, samples), {"index": 0}) == pytest.approx(0)


def test_silence_has_json_safe_peak(ffmpeg_available, tmp_path):
    assert levels.check_levels(_wav(tmp_path, np.zeros(4800, dtype=np.float32)), {"index": 0}) is None


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_nonfinite_source_is_rejected(ffmpeg_available, tmp_path, invalid):
    samples = np.zeros(4800, dtype=np.float32)
    samples[1234] = invalid
    with pytest.raises(ClaspError, match="non-finite"):
        levels.check_levels(_wav(tmp_path, samples), {"index": 0})


def test_missing_summary_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(levels, "executable", lambda _: "/fake/ffmpeg")
    monkeypatch.setattr(levels.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stderr=b"no stats\n"))
    with pytest.raises(ClaspError, match="Could not measure"):
        levels.check_levels(tmp_path / "missing.wav", {"index": 0})
