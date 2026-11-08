"""Estimate a recorder's offset and clock drift against camera scratch audio.

The model is ``external_time = offset_seconds + rate * reference_time``.
Times refer to each decoded stream's first sample, not container timestamps.
Only a shared recording can establish sync; this module never invents a match.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal


class AlignmentError(ValueError):
    """The available audio cannot establish a sufficiently reliable alignment."""


@dataclass(frozen=True)
class SyncResult:
    offset_seconds: float
    rate: float
    confidence: float
    residual_ms: float
    overlap_seconds: float
    anchors: list[dict[str, float | bool]]
    warnings: list[str] = field(default_factory=list)


_MAX_DRIFT = 0.002  # 2,000 ppm: reject implausible / nonlinear clock fits.


def _prepare(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    samples = np.asarray(samples)
    if samples.ndim != 1:
        raise AlignmentError("Alignment requires a single analysis channel per recording.")
    if len(samples) < 4 * sample_rate:
        raise AlignmentError("Each recording needs at least four seconds of audio.")
    if not np.all(np.isfinite(samples)):
        raise AlignmentError("The audio contains non-finite sample values.")
    peak = float(np.max(np.abs(samples)))
    if peak < 1e-9:
        raise AlignmentError("A recording is silent; shared scratch audio is required.")
    samples = np.asarray(samples / peak, dtype=np.float32)
    sos = signal.butter(4, [100, min(3400, sample_rate * 0.43)],
                        btype="bandpass", fs=sample_rate, output="sos")
    samples = signal.sosfiltfilt(sos, samples).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if rms < 1e-5:
        raise AlignmentError("A recording has no useful audible signal to align.")
    return samples


def _envelope(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, float]:
    # Log-energy at approximately 100 Hz tolerates level/EQ differences and
    # retains consonants and syllables without allocating a full audio FFT.
    block = max(1, round(sample_rate / 100))
    frames = samples[:len(samples) // block * block].reshape(-1, block)
    energy = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    floor = max(float(np.quantile(energy, .25)) * .1, float(energy.max()) * 1e-5)
    envelope = np.log(np.maximum(energy, floor))
    rate = sample_rate / block
    highpass = signal.butter(2, .3, btype="highpass", fs=rate, output="sos")
    envelope = signal.sosfiltfilt(highpass, envelope)
    return envelope, rate


def _coarse_offset(reference: np.ndarray, external: np.ndarray,
                   sample_rate: int) -> tuple[float, float, float]:
    ref, rate = _envelope(reference, sample_rate)
    ext, _ = _envelope(external, sample_rate)
    nr, ne = len(ref), len(ext)
    correlation = signal.correlate(ext, ref, mode="full", method="fft")
    lags = signal.correlation_lags(ne, nr)
    r0 = np.maximum(0, -lags)
    r1 = np.minimum(nr, ne - lags)
    e0 = np.maximum(0, lags)
    e1 = np.minimum(ne, nr + lags)
    count = r1 - r0

    def interval_sums(values: np.ndarray, start: np.ndarray, end: np.ndarray):
        total = np.r_[0., np.cumsum(values, dtype=np.float64)]
        square = np.r_[0., np.cumsum(values * values, dtype=np.float64)]
        return total[end] - total[start], square[end] - square[start]

    rs, rr = interval_sums(ref, r0, r1)
    es, ee = interval_sums(ext, e0, e1)
    covariance = correlation - rs * es / count
    denominator = np.sqrt(np.maximum(rr - rs * rs / count, 0) *
                          np.maximum(ee - es * es / count, 0))
    scores = np.divide(covariance, denominator, out=np.zeros_like(covariance),
                       where=denominator > 1e-12)
    minimum_overlap = max(3 * rate, min(12 * rate, .4 * min(nr, ne)))
    scores[count < minimum_overlap] = -1
    best = int(np.argmax(scores))
    best_score = float(scores[best])
    distant = np.abs(lags - lags[best]) > .75 * rate
    second_score = max(0., float(np.max(scores[distant], initial=0)))
    uniqueness = best_score / max(second_score, 1e-6)
    if best_score < .20:
        raise AlignmentError("No shared audio was found with enough confidence. "
                             "Use the original camera audio and the matching mic take.")
    if uniqueness < 1.12:
        raise AlignmentError("Several different alignments look equally likely. "
                             "Repeated audio needs a more distinctive shared section.")
    return float(lags[best] / rate), best_score, uniqueness


def _local_match(reference: np.ndarray, external: np.ndarray, sample_rate: int,
                 start: float, length: float, offset: float, radius: float,
                 rate: float = 1.) -> dict[str, float | bool] | None:
    begin = round(start * sample_rate)
    size = min(round(length * sample_rate), len(reference) - begin)
    template = reference[begin:begin + size].astype(np.float64)
    if size < sample_rate or float(np.dot(template, template)) < 1e-10:
        return None
    # Resampling the short template, rather than the recording, lets the second
    # pass resolve drift without smearing its local correlation peak.
    if rate != 1.:
        source_positions = np.arange(round(size * rate)) / rate
        template = np.interp(source_positions, np.arange(size), template)
    low = max(0, int(np.floor((offset + rate * start - radius) * sample_rate)))
    high = min(len(external), int(np.ceil((offset + rate * (start + length) + radius) * sample_rate)))
    segment = external[low:high].astype(np.float64)
    size = len(template)
    if len(segment) < size + 3:
        return None
    template -= template.mean()
    numerator = signal.correlate(segment, template, mode="valid", method="fft")
    sums = np.r_[0., np.cumsum(segment)]
    squares = np.r_[0., np.cumsum(segment * segment)]
    power = squares[size:] - squares[:-size] - (sums[size:] - sums[:-size]) ** 2 / size
    denominator = np.sqrt(np.maximum(power, 0) * np.dot(template, template))
    scores = np.divide(np.abs(numerator), denominator, out=np.zeros_like(numerator),
                       where=denominator > 1e-12)
    peak = int(np.argmax(scores))
    score = float(scores[peak])
    other = scores.copy()
    exclusion = max(1, round(.04 * sample_rate))
    other[max(0, peak - exclusion):peak + exclusion + 1] = 0
    distinctness = score / max(float(other.max(initial=0)), 1e-6)
    if score < .15 or distinctness < 1.15 or peak == 0 or peak == len(scores) - 1:
        return None
    # Parabolic sub-sample interpolation of the absolute correlation peak.
    left, middle, right = scores[peak - 1:peak + 2]
    curvature = left - 2 * middle + right
    fraction = float(np.clip(.5 * (left - right) / curvature, -.5, .5)) if curvature < -1e-12 else 0.
    reference_time = begin / sample_rate + length / 2
    external_time = (low + peak + fraction) / sample_rate + rate * length / 2
    return {"reference_seconds": float(reference_time),
            "external_seconds": float(external_time),
            "offset_seconds": float(external_time - reference_time),
            "correlation": float(np.clip(score, 0, 1)),
            "distinctness": float(distinctness), "accepted": False}


def _fit(anchors: list[dict[str, float | bool]], overlap: float):
    if len(anchors) < 3:
        raise AlignmentError("Too few distinct shared audio sections were found. "
                             "Silence, background noise or repetitive tones cannot establish sync.")
    x = np.array([a["reference_seconds"] for a in anchors], dtype=float)
    y = np.array([a["offset_seconds"] for a in anchors], dtype=float)
    weights = np.array([a["correlation"] for a in anchors], dtype=float) ** 2
    tolerance = .003
    candidates = [(float(value), 0.) for value in y]
    for i in range(len(x)):
        for j in range(i + 1, len(x)):
            if x[j] - x[i] < 2.:
                continue
            slope = (y[j] - y[i]) / (x[j] - x[i])
            if abs(slope) <= _MAX_DRIFT:
                candidates.append((float(y[i] - slope * x[i]), float(slope)))
    best = None
    for intercept, slope in candidates:
        residuals = abs(y - intercept - slope * x)
        accepted = residuals <= tolerance
        if accepted.sum() < 3:
            continue
        score = float(weights[accepted].sum())
        weighted_error = float(np.average(residuals[accepted] ** 2, weights=weights[accepted]))
        key = (score, -weighted_error)
        if best is None or key > best[0]:
            best = (key, accepted)
    if best is None:
        raise AlignmentError("The matching sections disagree on timing. "
                             "Use continuous, unedited recordings of the same take.")
    accepted = best[1]
    span = float(np.ptp(x[accepted]))
    if accepted.sum() < max(3, int(np.ceil(len(anchors) * .5))) or span < min(6., overlap * .30):
        raise AlignmentError("Too little consistent audio supports one timing model. "
                             "The recordings may be edited or may not contain the same take.")
    xa, ya, wa = x[accepted], y[accepted], weights[accepted]
    center = float(np.average(xa, weights=wa))
    mean = float(np.average(ya, weights=wa))
    slope = float(np.sum(wa * (xa - center) * (ya - mean)) / np.sum(wa * (xa - center) ** 2))
    intercept = mean - slope * center
    residual = float(np.sqrt(np.average((ya - intercept - slope * xa) ** 2, weights=wa)))
    # Do not invent a speed change below the observed uncertainty. Small clock
    # fits are commonly just microphone reflections or a different phoneme peak.
    if abs(slope) * span < max(.003, 4 * residual):
        slope = 0.
        intercept = mean
    if abs(slope) > _MAX_DRIFT:
        raise AlignmentError("Clock drift exceeds this version's 2,000 ppm limit.")
    residual = float(np.sqrt(np.average((ya - intercept - slope * xa) ** 2, weights=wa)))
    errors = y - intercept - slope * x
    # A second coherent timing group is evidence of an edit/dropout, rather
    # than an isolated reflection or a voiced-speech correlation side lobe.
    # Reject an obvious step instead of silently syncing only the larger half.
    inconsistent = np.flatnonzero((~accepted) & (np.abs(errors) > .020))
    required = max(3, int(np.ceil(len(anchors) * .15)))
    for index in inconsistent:
        group = inconsistent[np.abs(errors[inconsistent] - errors[index]) <= .003]
        if len(group) >= required and np.ptp(x[group]) >= min(6., overlap * .20):
            raise AlignmentError("Distinct sections require different timing offsets. "
                                 "The recordings may contain an edit, dropout or discontinuity.")
    for a, ok, error in zip(anchors, accepted, errors):
        a["accepted"] = bool(ok)
        a["residual_ms"] = float(error * 1000)
    return intercept, 1. + slope, residual * 1000, accepted, span


def estimate_sync(reference: np.ndarray, external: np.ndarray, sample_rate: int) -> SyncResult:
    """Return a conservative offset/rate fit or raise :class:`AlignmentError`.

    Pass mono analysis samples at a shared sample rate (8 kHz recommended).
    Analysis is separate from rendering: the original external recording is the
    sole sound source for the output. A low-confidence match is never forced.
    ``residual_ms`` describes accepted waveform anchors, not visual lip accuracy.
    """
    if not isinstance(sample_rate, (int, np.integer)) or sample_rate < 2000 or sample_rate > 48000:
        raise AlignmentError("Analysis sample rate must be an integer from 2,000 to 48,000 Hz.")
    ref = _prepare(reference, sample_rate)
    ext = _prepare(external, sample_rate)
    coarse, coarse_score, uniqueness = _coarse_offset(ref, ext, sample_rate)
    duration_ref, duration_ext = len(ref) / sample_rate, len(ext) / sample_rate
    start = max(0., -coarse)
    end = min(duration_ref, duration_ext - coarse)
    overlap = end - start
    if overlap < 4:
        raise AlignmentError("At least four seconds of shared audio are needed.")
    length = min(4., overlap * .32)
    count = min(41, max(9, int(overlap / 3)))
    times = np.linspace(start, end - length, count)
    radius = .30 + _MAX_DRIFT * max(duration_ref, duration_ext)
    anchors = [match for t in times
               if (match := _local_match(ref, ext, sample_rate, float(t), length,
                                         coarse, radius)) is not None]
    offset, rate, residual, accepted, span = _fit(anchors, overlap)
    if rate != 1.:
        # Verify independently across the same timeline after accounting for the
        # fitted rate. A broad search still exposes repeated/ambiguous material.
        refined = [match for t in times
                   if (match := _local_match(ref, ext, sample_rate, float(t), length,
                                             offset, .20, rate)) is not None]
        offset, rate, residual, accepted, span = _fit(refined, overlap)
        anchors = refined
    overlap = max(0., min(duration_ref, (duration_ext - offset) / rate) - max(0., -offset / rate))
    median_score = float(np.median([a["correlation"] for a in anchors if a["accepted"]]))
    fraction = float(np.mean(accepted))
    confidence = float(np.clip(.30 * min(coarse_score / .65, 1) +
                               .30 * min(median_score / .45, 1) +
                               .25 * fraction + .15 * min(span / max(overlap * .6, 1), 1), 0, 1))
    warnings = []
    if fraction < .85:
        warnings.append("Some audio windows disagreed with the timing fit; the reported residual includes accepted windows only.")
    if span < overlap * .5:
        warnings.append("Distinctive matched audio covers less than half of the overlap; review the rest of the clip.")
    return SyncResult(float(offset), float(rate), confidence, float(residual), float(overlap), anchors, warnings)
