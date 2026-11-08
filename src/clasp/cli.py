"""Clasp command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clasp", description="Waveform sync for double-system sound.")
    parser.add_argument("--version", action="version", version=f"Clasp {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    gui = commands.add_parser("gui", help="Open the macOS file-picker interface")
    sync = commands.add_parser("sync", help="Align production WAV to picture and replace camera audio")
    sync.add_argument("video", type=Path)
    sync.add_argument("audio", type=Path)
    sync.add_argument("-o", "--output", type=Path, required=True, help=".mp4 (AAC) or .mov (ALAC)")
    policy = sync.add_mutually_exclusive_group()
    policy.add_argument("--missing", choices=["trim", "silence", "error"], default="trim",
                        help="Missing production audio: trim to common duration (default), pad silence, or stop")
    policy.add_argument("--pad-missing", dest="missing", action="store_const", const="silence", help=argparse.SUPPRESS)
    policy.add_argument("--trim-to-overlap", dest="missing", action="store_const", const="trim", help=argparse.SUPPRESS)
    sync.add_argument("--nudge-ms", type=float, default=0, help="Manual fine adjustment; positive delays audio")
    sync.add_argument("--overwrite", action="store_true")
    sync.add_argument("--analyze-only", action="store_true", help="Print JSON analysis without writing media")
    args = parser.parse_args(argv)
    if args.command == "gui":
        from .gui import main as gui_main
        return gui_main() or 0
    from .pipeline import synchronize
    try:
        report = synchronize(args.video, args.audio, args.output, overwrite=args.overwrite,
                             pad_missing=args.missing == "silence", trim_to_overlap=args.missing == "trim",
                             nudge_ms=args.nudge_ms, analyze_only=args.analyze_only,
                             progress=lambda message: print(message, file=sys.stderr))
        print(json.dumps(report, indent=2, allow_nan=False) if args.analyze_only else str(args.output.resolve()))
    except (ValueError, OSError) as exc:
        print(f"Clasp: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Clasp: cancelled.", file=sys.stderr)
        return 130
    return 0
