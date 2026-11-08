"""End-to-end media checks with generated recordings; no personal media needed."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest
from scipy import signal
from scipy.io import wavfile

from clasp.alignment import SyncResult
from clasp.cli import main as cli_main
from clasp import pipeline
from clasp.media import ClaspError, first_stream, probe


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="FFmpeg and ffprobe are required for generated-media integration tests",
)
RATE = 48000


def _run(*args: str) -> bytes:
    return subprocess.run(args, check=True, capture_output=True).stdout


def _wave(seconds: float, seed: int = 41) -> np.ndarray:
    """A distinct, speech-like noise envelope rather than a repeated test tone."""
    rng = np.random.default_rng(seed)
    n = round(seconds * RATE)
    t = np.arange(n) / RATE
    noise = signal.sosfilt(signal.butter(4, [180, 2400], fs=RATE, btype="bandpass", output="sos"), rng.normal(size=n))
    levels = rng.uniform(.1, 1., round(seconds * 7) + 2)
    envelope = np.interp(t, np.linspace(0, seconds, len(levels)), levels)
    return (.18 * noise * envelope).astype(np.float32)


def _write_wav(path: Path, samples: np.ndarray) -> Path:
    wavfile.write(path, RATE, samples.astype(np.float32))
    return path


def _camera(tmp: Path, guide: np.ndarray, *, timestamp: float = 4., guide_shift: float = -.24) -> Path:
    guide_file = _write_wav(tmp / "guide.wav", guide)
    camera = tmp / "camera.mov"
    seconds = len(guide) / RATE
    _run("ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2=size=160x90:rate=25:duration={seconds}",
         "-itsoffset", str(guide_shift), "-i", str(guide_file),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
         "-preset", "ultrafast", "-c:a", "pcm_s16le", "-output_ts_offset", str(timestamp),
         "-t", str(seconds), "-y", str(camera))
    return camera


def _decoded(path: Path) -> np.ndarray:
    return np.frombuffer(_run("ffmpeg", "-v", "error", "-i", str(path),
                            "-map", "0:a:0", "-ac", "1", "-ar", str(RATE),
                            "-f", "f32le", "pipe:1"), dtype="<f4")


def _frame(path: Path, number: int) -> np.ndarray:
    return np.frombuffer(_run("ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
                            "-vf", f"select=eq(n\\,{number}),format=gray", "-frames:v", "1",
                            "-f", "rawvideo", "pipe:1"), dtype=np.uint8).astype(float)


def _known_match(monkeypatch, camera: Path, video_offset: float, rate: float = 1.) -> None:
    info = probe(camera)
    picture, guide = first_stream(info, "video"), first_stream(info, "audio")
    delta = float(picture["start_time"]) - float(guide["start_time"])
    match = SyncResult(video_offset - rate * delta, rate, .99, .1, 8., [])
    monkeypatch.setattr(pipeline, "estimate_sync", lambda *args: match)


@pytest.fixture
def sources(tmp_path):
    guide = _wave(8, 11)
    camera = _camera(tmp_path, guide)
    external = _wave(12, 97)
    mic = _write_wav(tmp_path / "production.wav", external)
    return camera, mic, external


@pytest.mark.parametrize("offset", [.88, -.72])
def test_trim_uses_only_production_audio_and_normalizes_stream_times(sources, tmp_path, monkeypatch, offset):
    camera, mic, external = sources
    _known_match(monkeypatch, camera, offset)
    output = tmp_path / "result.mov"
    report = pipeline.synchronize(camera, mic, output)
    assert report["offset_at_video_start_seconds"] == pytest.approx(offset, abs=1e-9)
    assert report["camera_audio_to_picture_start_seconds"] == pytest.approx(.24, abs=1e-9)
    assert report["export_start_seconds"] == pytest.approx(max(0, -offset), abs=1e-9)
    info = probe(output)
    assert [s["codec_type"] for s in info["streams"]] == ["video", "audio"]
    assert first_stream(info, "video")["start_time"] == "0.000000"
    assert first_stream(info, "audio")["start_time"] == "0.000000"
    assert first_stream(info, "audio")["codec_name"] == "alac"
    actual = _decoded(output)
    start = round((offset + report["export_start_seconds"]) * RATE)
    expected = external[start:start + len(actual)]
    # ALAC export preserves the replacement waveform to quantization accuracy;
    # a camera mix, wrong offset, or AAC track would fail this assertion.
    np.testing.assert_allclose(actual, expected, atol=2e-7, rtol=0)
    assert len(actual) / RATE == pytest.approx(report["duration_seconds"], abs=1 / RATE)
    assert report["video_encoding"] == ("copy" if offset > 0 else "h264")
    first_input_frame = round(report["export_start_seconds"] * 25)
    output_frame_count = round(report["duration_seconds"] * 25)
    for frame_number in (0, output_frame_count - 1):
        actual_frame = _frame(output, frame_number)
        source_frame = _frame(camera, first_input_frame + frame_number)
        assert actual_frame.size == 160 * 90
        # Detect a one-frame selection error by comparing adjacent candidates,
        # rather than mistaking normal CRF17 quantization for a timing failure.
        correct_error = np.mean(np.abs(actual_frame - source_frame))
        assert correct_error < 3
        source_frame_count = round(report["source_video_duration_seconds"] * 25)
        for neighbor in (first_input_frame + frame_number - 1, first_input_frame + frame_number + 1):
            if 0 <= neighbor < source_frame_count:
                neighbor_error = np.mean(np.abs(actual_frame - _frame(camera, neighbor)))
                assert correct_error + .25 < neighbor_error
    sidecar = json.loads(output.with_suffix(".mov.sync.json").read_text())
    assert sidecar["camera_audio_in_output"] is False
    assert sidecar["export_verified"] is True


def test_tail_is_trimmed_to_frame_boundary(sources, tmp_path, monkeypatch):
    camera, mic, external = sources
    _known_match(monkeypatch, camera, 5.13)
    output = tmp_path / "tail.mov"
    report = pipeline.synchronize(camera, mic, output)
    assert report["common_end_seconds"] == pytest.approx(6.87)
    assert report["duration_seconds"] == pytest.approx(6.84)
    actual = _decoded(output)
    start = round(5.13 * RATE)
    np.testing.assert_allclose(actual, external[start:start + len(actual)], atol=2e-7, rtol=0)
    info = probe(output)
    assert float(first_stream(info, "video")["duration"]) == pytest.approx(6.84, abs=.001)


def test_silence_policy_keeps_video_with_silent_uncovered_head(sources, tmp_path, monkeypatch):
    camera, mic, external = sources
    _known_match(monkeypatch, camera, -.72)
    output = tmp_path / "padded.mov"
    report = pipeline.synchronize(camera, mic, output, pad_missing=True)
    actual = _decoded(output)
    head = round(.72 * RATE)
    np.testing.assert_array_equal(actual[:head], 0)
    np.testing.assert_allclose(actual[head:], external[:len(actual) - head], atol=2e-7, rtol=0)
    assert report["video_encoding"] == "copy"
    assert len(actual) == 8 * RATE


def test_silence_policy_has_silent_uncovered_tail(sources, tmp_path, monkeypatch):
    camera, mic, external = sources
    _known_match(monkeypatch, camera, 5.)
    output = tmp_path / "padded-tail.mov"
    pipeline.synchronize(camera, mic, output, pad_missing=True)
    actual = _decoded(output)
    np.testing.assert_allclose(actual[:7 * RATE], external[5 * RATE:], atol=2e-7, rtol=0)
    np.testing.assert_array_equal(actual[7 * RATE:], 0)


def test_error_policy_writes_nothing(sources, tmp_path, monkeypatch):
    camera, mic, _ = sources
    _known_match(monkeypatch, camera, -.72)
    output = tmp_path / "refused.mov"
    with pytest.raises(ClaspError, match="uncovered"):
        pipeline.synchronize(camera, mic, output, trim_to_overlap=False)
    assert not output.exists()
    assert not output.with_suffix(".mov.sync.json").exists()


def test_positive_nudge_delays_production_samples(sources, tmp_path, monkeypatch):
    camera, mic, external = sources
    _known_match(monkeypatch, camera, .88)
    output = tmp_path / "nudge.mov"
    report = pipeline.synchronize(camera, mic, output, nudge_ms=120)
    actual = _decoded(output)
    start = round(.76 * RATE)
    np.testing.assert_allclose(actual, external[start:start + len(actual)], atol=2e-7, rtol=0)
    assert report["offset_at_video_start_seconds"] == pytest.approx(.76)


def test_source_and_existing_outputs_are_protected(sources, tmp_path):
    camera, mic, _ = sources
    original = camera.read_bytes()
    with pytest.raises(ClaspError, match="source recordings"):
        pipeline.synchronize(camera, mic, camera, overwrite=True)
    assert camera.read_bytes() == original
    link = tmp_path / "camera-alias.mov"
    link.symlink_to(camera)
    with pytest.raises(ClaspError, match="source recordings"):
        pipeline.synchronize(camera, mic, link, overwrite=True)
    output = tmp_path / "existing.mov"
    output.write_bytes(b"keep this")
    with pytest.raises(ClaspError, match="already exists"):
        pipeline.synchronize(camera, mic, output)
    assert output.read_bytes() == b"keep this"


def test_cli_analysis_prints_json_without_writing(sources, tmp_path, monkeypatch, capsys):
    camera, mic, _ = sources
    _known_match(monkeypatch, camera, 5.13)
    output = tmp_path / "analysis.mp4"
    status = cli_main(["sync", str(camera), str(mic), "-o", str(output), "--analyze-only"])
    assert status == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["missing_policy"] == "trim"
    assert report["duration_seconds"] == pytest.approx(6.84)
    assert not output.exists()
    assert not output.with_suffix(".mp4.sync.json").exists()


def test_cli_error_policy_exits_with_explanation(sources, tmp_path, monkeypatch, capsys):
    camera, mic, _ = sources
    _known_match(monkeypatch, camera, -.72)
    output = tmp_path / "error.mp4"
    status = cli_main(["sync", str(camera), str(mic), "-o", str(output), "--missing", "error"])
    assert status == 2
    captured = capsys.readouterr()
    assert "uncovered" in captured.err
    assert "--missing trim" in captured.err
    assert "Traceback" not in captured.err
    assert not output.exists()


def test_variable_frame_timestamps_are_rejected(sources, tmp_path):
    camera, mic, _ = sources
    variable = tmp_path / "variable.mov"
    _run("ffmpeg", "-v", "error", "-i", str(camera), "-map", "0:v:0", "-map", "0:a:0",
         "-vf", "select=not(mod(n\\,3))+not(mod(n\\,7))", "-fps_mode", "vfr",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", "-y", str(variable))
    output = tmp_path / "refused-vfr.mov"
    with pytest.raises(ClaspError, match="constant-frame-rate"):
        pipeline.synchronize(variable, mic, output)
    assert not output.exists()


def test_real_alignment_to_aac_export(tmp_path):
    guide = _wave(18, 383)
    camera = _camera(tmp_path, guide, timestamp=0., guide_shift=0.)
    offset = 1.4
    external = np.r_[np.zeros(round(offset * RATE), np.float32), guide * .7, np.zeros(RATE, np.float32)]
    mic = _write_wav(tmp_path / "production.wav", external)
    output = tmp_path / "integrated.mp4"
    report = pipeline.synchronize(camera, mic, output)
    assert report["offset_at_video_start_seconds"] == pytest.approx(offset, abs=.002)
    assert abs(report["drift_ppm"]) < 30
    info = probe(output)
    assert [s["codec_type"] for s in info["streams"]] == ["video", "audio"]
    assert first_stream(info, "audio")["codec_name"] == "aac"
    actual = _decoded(output)[:len(guide)]
    assert np.corrcoef(actual, guide[:len(actual)] * .7)[0, 1] > .98
    assert np.sqrt(np.mean(actual ** 2)) == pytest.approx(np.sqrt(np.mean((guide * .7) ** 2)), rel=.03)


@pytest.mark.parametrize("rate", [1.0015, .9985])
def test_drift_correction_keeps_transients_at_expected_times(tmp_path, monkeypatch, rate):
    seconds = 18
    camera = _camera(tmp_path, _wave(seconds, 12), timestamp=0., guide_shift=0.)
    # Distinct transients expose clock-direction errors independently of the
    # alignment estimator. Broad envelopes avoid measuring carrier phase.
    t = np.arange(round((seconds + 2) * RATE)) / RATE
    offset = .5
    targets = np.array([1., 4., 8., 12., 16.])
    external = np.zeros_like(t)
    for center in offset + rate * targets:
        external += .3 * np.exp(-.5 * ((t - center) / .008) ** 2) * np.sin(2 * np.pi * 800 * t)
    mic = _write_wav(tmp_path / "production.wav", external)
    _known_match(monkeypatch, camera, offset, rate)
    output = tmp_path / "drift.mov"
    pipeline.synchronize(camera, mic, output)
    actual = _decoded(output)
    envelope = np.abs(signal.hilbert(actual))
    for target in targets:
        left, right = round((target - .1) * RATE), round((target + .1) * RATE)
        measured = (left + np.argmax(envelope[left:right])) / RATE
        assert measured == pytest.approx(target, abs=.001)
