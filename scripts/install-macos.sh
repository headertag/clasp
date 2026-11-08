#!/bin/bash
# Installs Clasp into this checkout's .venv. Does not change system packages.
set -euo pipefail

CLASP_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ "$(uname -s)" != "Darwin" ]; then
    printf 'Clasp currently supports macOS only.\n' >&2
    exit 1
fi

for clasp_tool in ffmpeg ffprobe; do
    if ! command -v "$clasp_tool" >/dev/null 2>&1; then
        printf 'Missing %s. Install prerequisites first:\n' "$clasp_tool" >&2
        printf '  brew install ffmpeg python@3.13 python-tk@3.13\n' >&2
        exit 1
    fi
done

if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [python-executable]\n' "$0" >&2
    exit 1
fi

if [ "$#" -eq 1 ]; then
    CLASP_PYTHON="$1"
elif command -v python3.13 >/dev/null 2>&1; then
    CLASP_PYTHON="$(command -v python3.13)"
elif command -v python3 >/dev/null 2>&1; then
    CLASP_PYTHON="$(command -v python3)"
else
    printf 'Python 3.11 or newer is required. Run: brew install python@3.13 python-tk@3.13\n' >&2
    exit 1
fi

"$CLASP_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required.")'

if [ ! -d "$CLASP_ROOT/.venv" ]; then
    "$CLASP_PYTHON" -m venv "$CLASP_ROOT/.venv"
fi
"$CLASP_ROOT/.venv/bin/python" -m pip install "$CLASP_ROOT"

if ! "$CLASP_ROOT/.venv/bin/python" -c 'import tkinter' >/dev/null 2>&1; then
    printf '\nClasp CLI is installed; the GUI needs tkinter for your Python version.\n'
    printf 'For Homebrew Python 3.13: brew install python-tk@3.13\n'
else
    printf '\nReady. Double-click Clasp.command to open the file picker.\n'
fi
printf 'CLI: "%s/.venv/bin/clasp" --help\n' "$CLASP_ROOT"
