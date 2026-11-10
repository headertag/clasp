# Clasp

**Waveform sync for double-system sound.**

Clasp aligns a separate WAV recording to your camera's reference audio, corrects
measurable clock drift, and exports a video containing only the external microphone.
The command line runs locally on Linux and macOS. The optional file-picker
interface is currently supported on macOS.

Keep recording reference audio on the camera:
Clasp uses it to find the match, then leaves it out of the finished file.

## Quick start

Install [Homebrew](https://brew.sh/) if needed, then run:

```sh
brew install ffmpeg python@3.13 python-tk@3.13
git clone https://github.com/headertag/clasp.git
cd clasp
./scripts/install-macos.sh
```

Double-click **Clasp.command**, choose the camera video and external WAV, choose
where to save, and click **Synchronize**. The first setup downloads Python dependencies;
processing your recordings does not require an internet connection.

For an existing Python installation, pass its executable to the installer:

```sh
./scripts/install-macos.sh /path/to/python3
```

Python 3.11 or newer and FFmpeg/ffprobe must be installed. The file-picker interface
also needs tkinter for that Python installation. Clasp is distributed as source,
not a signed, standalone macOS app.

## Linux / SSH setup

On Ubuntu/Debian, install FFmpeg and Python 3.11 or newer:

```sh
sudo apt-get install ffmpeg python3-venv
git clone https://github.com/headertag/clasp.git
cd clasp
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/clasp sync camera.MTS microphone.wav -o finished.mp4 --analyze-only
```

No display, Tk, or GPU is required for the CLI. Rendering currently uses CPU
FFmpeg/libx264; an NVIDIA GPU is not automatically used.

## Duration and release status

Main removes the input-duration cap, the analysis decoder's 30-minute cutoff,
and fixed media-command execution deadlines. A healthy long export can run until
completion. Interrupt with Ctrl-C if needed; existing source files are read-only.

There is **no imposed time limit**, but available RAM, disk, and processing time
still matter. Analysis currently holds decoded audio and frame timestamps in
memory, and alignment uses up to 41 checkpoints with a single linear drift model.
Very long takes or changing recorder drift may need further improvements. This
change does not claim that arbitrary multi-hour recordings have been validated.

The stable `v0.1.0` tag remains unchanged. Main reports `0.2.0.dev0`; **do not cut
v0.2.0 until real multi-hour recordings pass Linux alignment and export checks**.
See [the long-recording validation checklist](docs/long-recording-validation.md).

## Command line

Activate the local environment once per Terminal session:

```sh
source .venv/bin/activate
clasp sync camera.MTS microphone.wav -o finished.mp4
```

Choose a MOV output to keep the replacement audio lossless:

```sh
clasp sync camera.MTS microphone.wav -o finished.mov
```

| Option | Behavior |
| --- | --- |
| `--analyze-only` | Measure the alignment without rendering the video. |
| `--missing trim` | **Default.** Trim to common duration: export the aligned interval covered by both inputs. |
| `--missing silence` | Keep the whole video and use silence where the WAV has no coverage. |
| `--missing error` | Stop if the WAV does not cover the whole video. |
| `--nudge-ms 12` | Delay the replacement sound by another 12 milliseconds. Negative values advance it. |
| `--overwrite` | Replace an existing output after checking that it is not an input. |

Clasp writes a synchronization report beside the output as
`finished.mp4.sync.json` (or `finished.mov.sync.json`). The report records the
measured alignment and export decisions. Treat reports as private if their
filenames or paths identify your recordings.

If the WAV starts late or ends early, Clasp trims to the common duration by
default. This re-encodes the video only when a trim is needed. Select
`--missing silence` to retain the full video, or `--missing error` to stop and
inspect the coverage before exporting. Clasp never fills a gap with camera
audio. Existing outputs are protected unless you use `--overwrite`.

## Supported on main (v0.2 development)

| Input or output | Scope |
| --- | --- |
| Camera video | Constant-frame-rate, 8-bit SDR H.264 (`yuv420p`) in `.mts`, `.m2ts`, `.mp4`, or `.mov` |
| Camera reference audio | Mono or stereo; shared sound from the same take is required |
| External mic | PCM WAV, including floating-point WAV; mono or stereo |
| Takes | Continuous recordings; no imposed duration limit |
| MP4 export | H.264 video and AAC audio at 320 kb/s |
| MOV export | H.264 video and lossless ALAC audio |
| Platform | Linux and macOS CLI; macOS file-picker interface |

Clasp uses the first video track that is not cover art and the first audio
track. The video is copied without
re-encoding when its full duration is covered. Resampling or drift
correction changes audio samples; “lossless ALAC” describes the export codec,
not a promise that corrected samples equal the original WAV bit for bit.

Clasp preserves the recording's gain. Floating-point WAVs with sample peaks
above 0 dBFS are rejected because AAC and ALAC cannot retain those values;
lower the WAV's gain before retrying. Non-finite audio samples are also rejected.

## How the alignment works

The camera and external mic heard the same event. Clasp compares those audio
waveforms, looks for consistent matches at multiple positions, and estimates
the starting offset and relative clock rate. It checks decoded picture
timestamps and accounts for the camera's audio/video start times before placing
the replacement track. Clock drift is corrected by resampling the original
production audio. Weak or
ambiguous matches are rejected instead of silently producing a guess.

This is audio-based synchronization, not facial analysis or generated lip
movement. It assumes the camera's own audio is in sync with its picture. A
camera's internal delay, acoustic travel time between microphones, cuts,
dropouts, or recordings of different takes can prevent an exact result.
Review the finished video; use the small manual nudge if you need to account
for a known device delay. No tool can promise perfect alignment for every pair.

For best results, record reference audio, keep both devices rolling, and include
speech or a distinct clap. Separate files spanning edits or repeated identical
material need a more involved workflow than this first release supports.
Variable-frame-rate video, timestamp gaps, HDR, and 10-bit picture are outside
the first release's scope and are rejected.

Clasp builds on [SciPy's correlation tools](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.correlate.html)
and [FFmpeg's audio filters](https://ffmpeg.org/ffmpeg-filters.html).

## Development

```sh
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest
```

The Linux and macOS CI workflow runs the test suite with Python 3.11 and 3.13. The
repository contains no personal recordings. See [CONTRIBUTING.md](CONTRIBUTING.md)
for development and bug-report guidance.

## License

[MIT](LICENSE). FFmpeg, NumPy, SciPy, Python, and Tcl/Tk have their own licenses.
