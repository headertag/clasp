"""Check source sample peaks before converting floating-point WAV to AAC/ALAC."""
from __future__ import annotations

import math
from pathlib import Path
import re
import subprocess

from .media import ClaspError, executable


def _stat(log: str, label: str) -> float:
    # Only read astats' own summary lines, not arbitrary input metadata.
    values = re.findall(r"^\[[^\]\n]*astats[^\]\n]*\]\s*" + re.escape(label) +
                        r":\s*(\S+)\s*$", log, flags=re.MULTILINE)
    if not values:
        raise ClaspError("Could not measure the WAV's sample peaks with FFmpeg.")
    try:
        return float(values[-1])
    except ValueError as exc:
        raise ClaspError("FFmpeg returned an invalid WAV level measurement.") from exc


def check_levels(audio: Path, stream: dict) -> float | None:
    """Return the full-file, all-channel sample peak in dBFS; silence is ``None``.

    AAC and ALAC cannot retain floating-point samples above full scale. Refuse
    those inputs instead of silently clipping or automatically changing gain.
    This measures sample peaks, not codec or reconstruction true-peak overshoot.
    """
    filters = ("aformat=sample_fmts=dblp,"
               "astats=metadata=0:reset=0:measure_perchannel=none:"
               "measure_overall=Peak_level+Number_of_NaNs+Number_of_Infs")
    command = [executable("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-v", "info",
               "-i", str(audio), "-map", f"0:{stream['index']}", "-af", filters,
               "-c:a", "pcm_f64le", "-f", "null", "-"]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=None, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaspError(f"Could not measure WAV levels: {exc}") from exc
    log = result.stderr.decode("utf-8", errors="replace")
    if result.returncode:
        raise ClaspError(f"FFmpeg could not measure WAV levels: {log[-2000:].strip()}")
    for label in ("Number of NaNs", "Number of Infs"):
        count = _stat(log, label)
        if not math.isfinite(count) or count != 0:
            raise ClaspError("The WAV contains non-finite audio samples. Repair the recording before export.")
    peak = _stat(log, "Peak level dB")
    if peak == -math.inf:
        return None
    if not math.isfinite(peak):
        raise ClaspError("The WAV contains non-finite audio levels. Repair the recording before export.")
    if peak > 0:
        raise ClaspError(f"The WAV peaks at {peak:+.2f} dBFS and would clip in AAC or ALAC. "
                         "Lower WAV gain before export, then retry. Clasp does not change gain automatically.")
    return peak
