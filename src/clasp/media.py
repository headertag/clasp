"""Small, shell-free FFmpeg boundary. Source media is always read-only."""
from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

ANALYSIS_RATE = 8000
MAX_DURATION = 1800
VIDEO_SUFFIXES = {".mts", ".m2ts", ".mov", ".mp4"}


class ClaspError(ValueError):
    """An actionable input, alignment, or media error."""


def executable(name: str) -> str:
    found = shutil.which(name)
    # Finder-launched apps often lack Homebrew in PATH.
    if not found:
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = Path(prefix) / name
            if candidate.is_file():
                found = str(candidate)
                break
    if not found:
        raise ClaspError(f"{name} is missing. On macOS, install it with: brew install ffmpeg")
    return found


def run(arguments: list[str], *, timeout: int = 1800) -> bytes:
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaspError(f"Media command failed: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-3000:].strip()
        raise ClaspError(f"{Path(arguments[0]).name} failed: {detail}")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(run([executable("ffprobe"), "-v", "error", "-show_streams",
                           "-show_format", "-of", "json", str(path)]))


def first_stream(info: dict, kind: str) -> dict:
    for stream in info.get("streams", []):
        if stream.get("codec_type") == kind and not stream.get("disposition", {}).get("attached_pic"):
            return stream
    raise ClaspError(f"No {kind} stream found. Camera guide audio is required for waveform sync.")


def duration(info: dict, stream: dict) -> float:
    value = stream.get("duration") or info.get("format", {}).get("duration")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ClaspError("Cannot determine media duration.") from exc
    if not np.isfinite(seconds) or seconds <= 0 or seconds > MAX_DURATION:
        raise ClaspError("Version 0.1 supports continuous recordings up to 30 minutes long.")
    return seconds


def decode_analysis(path: Path, stream: dict) -> tuple[np.ndarray, int]:
    """Use the strongest channel; summing opposite-polarity stereo can erase speech."""
    channels = int(stream.get("channels", 0))
    if channels not in (1, 2):
        raise ClaspError("Version 0.1 supports mono or stereo audio only.")
    raw = run([executable("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path),
               "-map", f"0:{stream['index']}", "-t", str(MAX_DURATION),
               "-ar", str(ANALYSIS_RATE), "-ac", str(channels), "-f", "f32le", "pipe:1"])
    samples = np.frombuffer(raw, dtype="<f4").reshape(-1, channels)
    if len(samples) < ANALYSIS_RATE * 3 or not np.isfinite(samples).all():
        raise ClaspError("Audio must contain at least three seconds of finite samples.")
    energy = np.mean(samples.astype(np.float64) ** 2, axis=0)
    channel = int(np.argmax(energy))
    return samples[:, channel].copy(), channel


def picture_timing(path: Path, stream: dict) -> tuple[float, float, float]:
    """Verify decoded frame cadence, including field-coded AVCHD.

    Interlaced H.264 may have two packets per decoded frame. Container packet
    timestamps alone therefore cannot establish the first displayed picture.
    """
    info = json.loads(run([executable("ffprobe"), "-v", "error", "-select_streams", str(stream["index"]),
                           "-show_frames", "-show_entries", "frame=best_effort_timestamp_time,duration_time",
                           "-of", "json", str(path)]))
    frames = info.get("frames", [])
    try:
        stamps = np.array([float(frame["best_effort_timestamp_time"]) for frame in frames])
        if len(stamps) < 2:
            raise ClaspError("At least two decoded video frames are required.")
        step = float(np.median(np.diff(stamps)))
        tick = float(Fraction(stream.get("time_base", "1/90000")))
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise ClaspError("Cannot establish the video's frame timestamps.") from exc
    tolerance = max(2 * tick, .000002)
    if (len(stamps) < 2 or not np.isfinite(stamps).all() or step <= 0 or
            np.max(np.abs(np.diff(stamps) - step)) > tolerance):
        raise ClaspError("Version 0.1 requires continuous, constant-frame-rate video; variable timestamps or gaps were found.")
    # Check the entire timeline: even a small per-frame rate error accumulates.
    step = float((stamps[-1] - stamps[0]) / (len(stamps) - 1))
    if np.max(np.abs(stamps - (stamps[0] + np.arange(len(stamps)) * step))) > tolerance:
        raise ClaspError("Video timestamps do not follow a continuous constant frame rate.")
    try:
        declared = float(Fraction(stream.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        declared = 0
    if declared > 0 and np.max(np.abs(stamps - (stamps[0] + np.arange(len(stamps)) / declared))) <= tolerance:
        step = 1 / declared
    length = float(stamps[-1] - stamps[0] + step)
    return float(stamps[0]), length, 1 / step
