# Contributing

Clasp starts small: one continuous camera take, one external WAV, and macOS.
Changes should make that workflow more reliable before expanding format support.

## Development

Install FFmpeg, Python 3.11 or newer, and tkinter if working on the GUI. From
the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest
```

Use synthetic signals and generated media for tests. Never commit personal
recordings, temporary renders, or synchronization reports. Regression tests
should check the resulting timing or audio provenance, not just command text.

## Changes and bug reports

- Describe the input formats, macOS version, Python version, and FFmpeg version.
- Include a small, shareable reproduction or instructions to generate one.
- When sharing a report, inspect it first and remove private filenames or paths.
- Explain how timing was measured, including the reference time origin and lag sign.
- Keep refusal behavior for weak, ambiguous, or unsupported recordings explicit.
- Add coverage for meaningful changes to alignment, timestamps, or export behavior.

The GUI is intentionally small. Keep processing off the UI thread and report
errors without hiding useful context. Scope platform claims to systems actually
tested. Contributions are licensed under the repository's MIT license.
