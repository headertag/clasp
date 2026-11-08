"""Analyze, align, and export two continuous recordings."""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import __version__
from .alignment import estimate_sync
from .levels import check_levels
from .media import (ANALYSIS_RATE, VIDEO_SUFFIXES, ClaspError, decode_analysis,
                    duration, executable, first_stream, picture_timing, probe, run)


def _start(stream: dict) -> float:
    return float(stream.get("start_time", 0) or 0)


def coverage(offset: float, rate: float, audio_duration: float,
             video_duration: float) -> tuple[float, float]:
    return max(0.0, -offset / rate), min(video_duration, (audio_duration - offset) / rate)


def audio_filters(offset: float, rate: float, output_start: float,
                  output_duration: float) -> str:
    """external_time = offset + rate * video_time; positive nudge lowers offset."""
    external_start = offset + rate * output_start
    filters = ["asetpts=PTS-STARTPTS", f"atrim=start={max(0, external_start):.9f}",
               "asetpts=PTS-STARTPTS"]
    if abs(rate - 1.0) > 1e-9:
        # Clock error is a sample-rate error. WSOLA tempo correction moves
        # individual transients, so use deterministic resampling instead.
        filters.extend(["aresample=960000", f"asetrate={round(960000 * rate)}"])
    filters.append("aresample=48000")
    if external_start < 0:
        delay = round(-external_start / rate * 48000)
        filters.append(f"adelay={delay}S:all=1")
    filters.extend(["apad", f"atrim=duration={output_duration:.9f}", "asetpts=PTS-STARTPTS"])
    return ",".join(filters)


def _publish(source: Path, target: Path, overwrite: bool) -> None:
    if overwrite:
        os.replace(source, target)
    else:
        # Atomic no-clobber: another process creating target during export is protected.
        try:
            os.link(source, target)
        except FileExistsError as exc:
            raise ClaspError(f"Output already exists: {target}") from exc
        source.unlink()


def synchronize(video: Path, audio: Path, output: Path, *, overwrite: bool = False,
                pad_missing: bool = False, trim_to_overlap: bool = True,
                nudge_ms: float = 0.0, analyze_only: bool = False,
                progress: Callable[[str], None] | None = None) -> dict:
    say = progress or (lambda message: None)
    video, audio, output = (Path(p).expanduser().resolve() for p in (video, audio, output))
    sidecar = output.with_suffix(output.suffix + ".sync.json")
    if output in (video, audio) or sidecar in (video, audio):
        raise ClaspError("Choose a new output path; source recordings cannot be overwritten.")
    for path in (video, audio):
        if not path.is_file():
            raise ClaspError(f"Input file does not exist: {path}")
    if video.suffix.lower() not in VIDEO_SUFFIXES or audio.suffix.lower() != ".wav":
        raise ClaspError("Use H.264 MTS, M2TS, MOV, or MP4 video and a PCM WAV recording.")
    if output.suffix.lower() not in {".mp4", ".mov"}:
        raise ClaspError("Output must end in .mp4 (AAC) or .mov (lossless ALAC).")
    if not math.isfinite(nudge_ms) or abs(nudge_ms) > 5000:
        raise ClaspError("Manual audio nudge must be a finite value between -5000 and 5000 ms.")
    if pad_missing:
        trim_to_overlap = False
    if not analyze_only:
        for path in (output, sidecar):
            if path.exists() and (not overwrite or path.is_dir()):
                raise ClaspError(f"Output already exists: {path}. Use --overwrite or choose a new name.")
        if not output.parent.is_dir():
            raise ClaspError(f"Output directory does not exist: {output.parent}")

    say("Inspecting picture and production audio…")
    video_info, audio_info = probe(video), probe(audio)
    picture, guide = first_stream(video_info, "video"), first_stream(video_info, "audio")
    production = first_stream(audio_info, "audio")
    if picture.get("codec_name") != "h264":
        raise ClaspError("Version 0.1 supports H.264 picture only.")
    if picture.get("pix_fmt") != "yuv420p" or picture.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
        raise ClaspError("Version 0.1 supports 8-bit SDR H.264 in yuv420p format only.")
    if picture.get("field_order") in {"tb", "bt"}:
        raise ClaspError("Mixed coded/displayed field order is not supported in version 0.1.")
    if not production.get("codec_name", "").startswith("pcm_"):
        raise ClaspError("The WAV must contain uncompressed PCM audio, including float PCM.")
    video_length, audio_length = duration(video_info, picture), duration(audio_info, production)
    duration(video_info, guide)
    picture_start, video_length, fps = picture_timing(video, picture)
    peak_dbfs = check_levels(audio, production)
    say("Matching waveforms across the take and measuring recorder drift…")
    reference, guide_channel = decode_analysis(video, guide)
    external, production_channel = decode_analysis(audio, production)
    result = estimate_sync(reference, external, ANALYSIS_RATE)
    applied_rate = round(result.rate * 960000) / 960000
    # Demuxed camera audio and picture may begin at different timestamps (notably AVCHD).
    guide_delta = picture_start - _start(guide)
    offset = result.offset_seconds + result.rate * guide_delta - nudge_ms / 1000 * applied_rate
    # Decoded length captures WAV sample precision more accurately than rounded metadata.
    audio_length = min(audio_length, len(external) / ANALYSIS_RATE)
    common_start, common_end = coverage(offset, applied_rate, audio_length, video_length)
    if common_end - common_start < 1:
        raise ClaspError("The recordings have less than one second of aligned coverage.")
    missing_head, missing_tail = common_start, max(0, video_length - common_end)
    has_gap = missing_head > 0.001 or missing_tail > 0.001
    if has_gap and not (trim_to_overlap or pad_missing):
        raise ClaspError(f"Production audio leaves {missing_head:.3f}s at the start and "
                         f"{missing_tail:.3f}s at the end uncovered. Use --missing trim or --missing silence.")

    export_start, export_end = 0.0, video_length
    reencode = bool(trim_to_overlap and has_gap)
    if reencode:
        export_start = math.ceil(common_start * fps - 1e-7) / fps
        export_end = math.floor(common_end * fps + 1e-7) / fps
    export_duration = export_end - export_start
    if export_duration < 1:
        raise ClaspError("Aligned coverage is too short after frame-boundary trimming.")
    warnings = list(getattr(result, "warnings", []))
    if has_gap:
        action = "Trimmed to common duration" if trim_to_overlap else "Padded with silence"
        warnings.append(f"{action}: {missing_head:.3f}s uncovered at start, {missing_tail:.3f}s at end.")
    if reencode:
        warnings.append("Picture re-encoded as H.264 for frame-accurate trimming.")
    report = {
        "clasp_version": __version__, "video": video.name, "production_audio": audio.name,
        "output": str(output), "method": "waveform", "alignment": asdict(result),
        "offset_at_video_start_seconds": offset,
        "camera_audio_to_picture_start_seconds": guide_delta,
        "drift_ppm": (result.rate - 1) * 1e6, "confidence": result.confidence,
        "applied_audio_rate": applied_rate,
        "manual_nudge_ms": nudge_ms, "missing_policy": "silence" if pad_missing else "trim" if trim_to_overlap else "error",
        "source_video_duration_seconds": video_length,
        "source_audio_duration_seconds": audio_length,
        "production_sample_peak_dbfs": peak_dbfs,
        "common_start_seconds": common_start, "common_end_seconds": common_end,
        "export_start_seconds": export_start, "duration_seconds": export_duration,
        "uncovered_head_seconds": missing_head, "uncovered_tail_seconds": missing_tail,
        "guide_analysis_channel": guide_channel + 1, "production_analysis_channel": production_channel + 1,
        "video_encoding": "h264" if reencode else "copy",
        "audio_encoding": "alac" if output.suffix.lower() == ".mov" else "aac",
        "camera_audio_in_output": False, "warnings": warnings,
        "note": "Waveform alignment assumes the camera guide is already synchronized to picture. Confidence is a heuristic, not a probability.",
    }
    say(f"Matched: WAV position {offset:.6f}s at picture start; "
        f"drift {report['drift_ppm']:+.1f} ppm; residual {result.residual_ms:.2f} ms.")
    for warning in warnings:
        say(warning)
    if analyze_only:
        return report

    say(f"Exporting {export_duration:.3f}s with production audio only…")
    with tempfile.TemporaryDirectory(prefix=".clasp-", dir=output.parent) as temp:
        rendered = Path(temp) / ("render" + output.suffix.lower())
        report_file = Path(temp) / "report.json"
        filters = f"[1:{production['index']}]{audio_filters(offset, applied_rate, export_start, export_duration)}[a]"
        cmd = [executable("ffmpeg"), "-v", "error", "-nostdin", "-y", "-copyts",
               "-itsoffset", f"{-picture_start:.9f}", "-i", str(video), "-i", str(audio)]
        if reencode:
            filters += (f";[0:{picture['index']}]setpts=PTS-STARTPTS,trim=start={export_start:.9f}:"
                        f"end={export_end:.9f},setpts=PTS-STARTPTS[v]")
        cmd += ["-filter_complex", filters, "-map", "[v]" if reencode else f"0:{picture['index']}", "-map", "[a]"]
        if reencode:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p"]
            if picture.get("field_order") in {"tt", "bb"}:
                field = "tff=1" if picture["field_order"] == "tt" else "bff=1"
                cmd += ["-flags", "+ilme+ildct", "-x264-params", field]
            cmd += ["-fps_mode", "passthrough"]
        else:
            cmd += ["-c:v", "copy"]
        if output.suffix.lower() == ".mov":
            cmd += ["-c:a", "alac", "-sample_fmt", "s32p"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "320k"]
        cmd += ["-t", f"{export_duration:.9f}", "-map_metadata", "-1", "-map_chapters", "-1",
                "-metadata:s:a:0", "title=Production audio (Clasp)", "-disposition:a:0", "default",
                "-avoid_negative_ts", "disabled", "-movflags", "+faststart", "-use_editlist", "1", str(rendered)]
        run(cmd)
        final_info = probe(rendered)
        streams = final_info.get("streams", [])
        if sum(s.get("codec_type") == "audio" for s in streams) != 1 or sum(s.get("codec_type") == "video" for s in streams) != 1:
            raise ClaspError("Export validation failed: expected exactly one picture and one production audio track.")
        out_video, out_audio = first_stream(final_info, "video"), first_stream(final_info, "audio")
        expected_audio = "alac" if output.suffix.lower() == ".mov" else "aac"
        if (out_video.get("codec_name") != "h264" or out_audio.get("codec_name") != expected_audio or
                int(out_audio.get("channels", 0)) != int(production["channels"]) or
                int(out_audio.get("sample_rate", 0)) != 48000):
            raise ClaspError("Export validation failed: unexpected output codecs or audio format.")
        out_picture_start, out_picture_length, _ = picture_timing(rendered, out_video)
        if abs(out_picture_start) > .002 or abs(_start(out_audio)) > .002:
            raise ClaspError("Export validation failed: picture or audio does not start at zero.")
        if (abs(out_picture_length - export_duration) > 1 / fps + .002 or
                abs(duration(final_info, out_audio) - export_duration) > .025):
            raise ClaspError("Export validation failed: output duration differs from the planned common duration.")
        report["export_verified"] = True
        report_file.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        _publish(rendered, output, overwrite)
        _publish(report_file, sidecar, overwrite)
    say(f"Saved {output.name} and {sidecar.name}")
    return report
