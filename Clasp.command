#!/bin/bash
set -euo pipefail

CLASP_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ ! -x "$CLASP_ROOT/.venv/bin/clasp-gui" ]; then
    printf 'Clasp needs a one-time setup.\n\n'
    printf 'In Terminal, run:\n  cd "%s"\n  ./scripts/install-macos.sh\n\n' "$CLASP_ROOT"
    read -r -p 'Press Return to close. ' _clasp_reply || true
    exit 1
fi

if ! "$CLASP_ROOT/.venv/bin/clasp-gui"; then
    printf '\nClasp could not start. See the message above and README.md for setup help.\n'
    read -r -p 'Press Return to close. ' _clasp_reply || true
    exit 1
fi
