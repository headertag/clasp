"""Timing-ground-truth tests; no personal recordings are checked into the repo."""
from __future__ import annotations

import numpy as np
import pytest
from scipy import signal

from clasp.alignment import AlignmentError, estimate_sync


FS = 4000


def speech_like(seconds=36., seed=17):
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds * FS)) / FS
    carrier = signal.sosfilt(signal.butter(3, [130, 1450], fs=FS,
                                           btype="bandpass", output="sos"),
                             rng.standard_normal(len(t)))
    controls = rng.uniform(.04, 1, round(seconds * 5) + 2)
    envelope = np.interp(t, np.arange(len(controls)) / 5, controls) ** 2
    return carrier * envelope


def recorder_pair(offset, rate=1., seconds=36., seed=17, noise=.01, invert=False):
    reference = speech_like(seconds, seed)
    t = np.arange(len(reference)) / FS
    duration = max(5, offset + rate * seconds + 1)
    external_times = np.arange(round(duration * FS)) / FS
    external = np.interp((external_times - offset) / rate, t, reference, left=0, right=0)
    rng = np.random.default_rng(seed + 1)
    external = external * (-.27 if invert else .27)
    external += noise * np.std(external) * rng.standard_normal(len(external))
    # A secondary microphone has its own EQ / level, not identical samples.
    external = signal.sosfiltfilt(signal.butter(2, 1200, fs=FS, output="sos"), external)
    return reference, external


@pytest.mark.parametrize("offset", [-3.427, 0., 8.731])
def test_signed_offset_and_loudness(offset):
    reference, external = recorder_pair(offset)
    result = estimate_sync(reference, external, FS)
    assert result.offset_seconds == pytest.approx(offset, abs=.001)
    assert result.rate == 1.
    assert result.confidence > .8
    assert result.residual_ms < 1.
    assert sum(a["accepted"] for a in result.anchors) >= 3


@pytest.mark.parametrize("rate", [.9996, 1.0007])
def test_linear_clock_drift(rate):
    reference, external = recorder_pair(4.236, rate=rate, seconds=60.)
    result = estimate_sync(reference, external, FS)
    # Check the predicted timing at both ends, not only the slope's units.
    for t in [0., 30., 58.]:
        assert result.offset_seconds + result.rate * t == pytest.approx(4.236 + rate * t, abs=.0015)
    assert abs(result.rate - 1.) > .0002
    assert result.residual_ms < 1.


def test_inverted_polarity_and_noisy_microphone():
    reference, external = recorder_pair(2.371, invert=True, noise=.2)
    result = estimate_sync(reference, external, FS)
    assert result.offset_seconds == pytest.approx(2.371, abs=.001)


def test_unrelated_recordings_are_not_forced():
    with pytest.raises(AlignmentError):
        estimate_sync(speech_like(seed=2), speech_like(seed=9), FS)


@pytest.mark.parametrize("which", ["reference", "external"])
def test_silent_recording_rejected(which):
    reference, external = recorder_pair(1.)
    if which == "reference":
        reference[:] = 0
    else:
        external[:] = 0
    with pytest.raises(AlignmentError, match="silent"):
        estimate_sync(reference, external, FS)


def test_repeated_take_is_ambiguous():
    reference = speech_like(12.)
    external = np.r_[np.zeros(FS), reference, np.zeros(2 * FS), reference, np.zeros(FS)]
    with pytest.raises(AlignmentError, match="equally likely|Repeated"):
        estimate_sync(reference, external, FS)


def test_periodic_tone_cannot_establish_a_unique_offset():
    t = np.arange(20 * FS) / FS
    reference = np.sin(2 * np.pi * 250 * t)
    external = np.sin(2 * np.pi * 250 * (t - .217))
    with pytest.raises(AlignmentError):
        estimate_sync(reference, external, FS)


def test_too_short_and_nonfinite_input():
    with pytest.raises(AlignmentError, match="four seconds"):
        estimate_sync(np.ones(FS), np.ones(FS), FS)
    reference = speech_like()
    external = reference.copy()
    external[4] = np.nan
    with pytest.raises(AlignmentError, match="non-finite"):
        estimate_sync(reference, external, FS)


def test_input_arrays_are_not_modified():
    reference, external = recorder_pair(1.7)
    original_ref, original_ext = reference.copy(), external.copy()
    estimate_sync(reference, external, FS)
    np.testing.assert_array_equal(reference, original_ref)
    np.testing.assert_array_equal(external, original_ext)


@pytest.mark.parametrize("cut_fraction", [.5, .7])
def test_abrupt_timeline_edit_is_rejected(cut_fraction):
    reference = speech_like(60.)
    cut = round(len(reference) * cut_fraction)
    # Insert 180 ms halfway (or near the end): no single offset/rate is valid.
    external = np.r_[np.zeros(2 * FS), reference[:cut], np.zeros(round(.18 * FS)), reference[cut:]]
    with pytest.raises(AlignmentError, match="timing|consistent|disagree"):
        estimate_sync(reference, external, FS)
