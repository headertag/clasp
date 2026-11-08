"""Small, local macOS file-picker interface for Clasp.

Tk is imported only when the interface starts; importing the CLI never requires it.
"""
from __future__ import annotations

import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
from typing import Any


class ClaspApp:
    """Own the Tk widgets; a worker communicates exclusively through a queue."""

    def __init__(self, root: Any) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk, self.ttk = tk, ttk
        self.filedialog, self.messagebox = filedialog, messagebox
        self.root = root
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.finished_output: Path | None = None
        self.controls: list[Any] = []
        self.video = tk.StringVar()
        self.audio = tk.StringVar()
        self.output = tk.StringVar()
        self.nudge = tk.StringVar(value="0")
        self.coverage = tk.StringVar(value="Trim to common duration")
        self.status = tk.StringVar(value="Choose two recordings of the same take.")
        self._last_auto_output = ""

        root.title("Clasp")
        root.geometry("760x710")
        root.minsize(660, 660)
        root.configure(background="#f6f4ef")
        root.protocol("WM_DELETE_WINDOW", self._close)

        style = ttk.Style(root)
        style.configure("Page.TFrame", background="#f6f4ef")
        style.configure("Page.TLabel", background="#f6f4ef", foreground="#233c3b", font=("Helvetica Neue", 12))
        style.configure("Title.TLabel", background="#f6f4ef", foreground="#176c63", font=("Helvetica Neue", 36, "bold"))
        style.configure("Section.TLabel", background="#f6f4ef", foreground="#233c3b", font=("Helvetica Neue", 12, "bold"))
        style.configure("Note.TLabel", background="#f6f4ef", foreground="#586a66", font=("Helvetica Neue", 11))
        style.configure("Status.TLabel", background="#e8efea", foreground="#233c3b", font=("Helvetica Neue", 12), padding=14)

        page = ttk.Frame(root, style="Page.TFrame", padding=(30, 20, 30, 18))
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        ttk.Label(page, text="Clasp", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(page, text="Waveform sync for double-system sound.", style="Section.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 10))
        ttk.Label(page, text="Match your camera’s guide audio to a separate WAV recording,\nthen export the video with only the external microphone’s sound.", style="Page.TLabel", justify="left").grid(row=2, column=0, sticky="w", pady=(0, 17))

        self._file_row(page, 3, "1  Camera video", "MTS, M2TS, MP4 or MOV · must contain guide audio", self.video, self._choose_video)
        self._file_row(page, 4, "2  External microphone", "WAV · the same take, with overlapping sound", self.audio, self._choose_audio)
        self._file_row(page, 5, "3  Save synchronized video", "MP4: easy sharing · MOV: lossless ALAC audio", self.output, self._choose_output)

        advanced = ttk.Frame(page, style="Page.TFrame")
        advanced.grid(row=6, column=0, sticky="ew", pady=(7, 12))
        advanced.columnconfigure(1, weight=1)
        ttk.Label(advanced, text="If audio is shorter", style="Page.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 15))
        coverage = ttk.Combobox(advanced, textvariable=self.coverage, state="readonly", values=("Trim to common duration", "Keep video; pad with silence", "Require full audio coverage"), width=31)
        coverage.grid(row=0, column=1, sticky="ew")
        self.coverage_control = coverage
        ttk.Label(advanced, text="Fine adjustment", style="Page.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        fine = ttk.Frame(advanced, style="Page.TFrame")
        fine.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        nudge = ttk.Entry(fine, textvariable=self.nudge, width=9)
        nudge.pack(side="left")
        self.controls.append(nudge)
        ttk.Label(fine, text=" ms   ·   positive delays the sound", style="Note.TLabel").pack(side="left", padx=(7, 0))
        ttk.Label(advanced, text="Trim keeps the aligned range covered by both files and re-encodes the video.", style="Note.TLabel", wraplength=670).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.status_label = ttk.Label(page, textvariable=self.status, style="Status.TLabel", wraplength=635, justify="left")
        self.status_label.grid(row=7, column=0, sticky="ew", pady=(0, 8))
        self.bar = ttk.Progressbar(page, mode="indeterminate")
        self.bar.grid(row=8, column=0, sticky="ew", pady=(0, 13))

        actions = ttk.Frame(page, style="Page.TFrame")
        actions.grid(row=9, column=0, sticky="ew")
        ttk.Button(actions, text="How it works", command=self._about).pack(side="left")
        self.open_button = ttk.Button(actions, text="Open video", command=self._open_output, state="disabled")
        self.open_button.pack(side="right", padx=(10, 0))
        self.sync_button = ttk.Button(actions, text="Synchronize", command=self._start)
        self.sync_button.pack(side="right")
        ttk.Label(page, text="Private by design. Processing stays on this Mac.", style="Note.TLabel").grid(row=10, column=0, sticky="w", pady=(15, 0))
        root.after(100, self._poll)

    def _file_row(self, parent: Any, row: int, title: str, hint: str, variable: Any, command: Any) -> None:
        box = self.ttk.Frame(parent, style="Page.TFrame")
        box.grid(row=row, column=0, sticky="ew", pady=(0, 13))
        box.columnconfigure(0, weight=1)
        self.ttk.Label(box, text=title, style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.ttk.Label(box, text=hint, style="Note.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 5))
        entry = self.ttk.Entry(box, textvariable=variable)
        entry.grid(row=2, column=0, sticky="ew")
        button = self.ttk.Button(box, text="Choose…", command=command)
        button.grid(row=2, column=1, padx=(8, 0))
        self.controls.extend((entry, button))

    def _choose_video(self) -> None:
        path = self.filedialog.askopenfilename(parent=self.root, title="Choose camera video", filetypes=[("Camera video", "*.mts *.MTS *.m2ts *.M2TS *.mp4 *.MP4 *.mov *.MOV"), ("All files", "*")])
        if path:
            self.video.set(path)
            if not self.output.get() or self.output.get() == self._last_auto_output:
                self._last_auto_output = str(Path(path).with_name(Path(path).stem + "-clasp.mp4"))
                self.output.set(self._last_auto_output)

    def _choose_audio(self) -> None:
        path = self.filedialog.askopenfilename(parent=self.root, title="Choose microphone recording", filetypes=[("WAV audio", "*.wav *.WAV"), ("All files", "*")])
        if path:
            self.audio.set(path)

    def _choose_output(self) -> None:
        current = Path(self.output.get()).expanduser() if self.output.get() else None
        path = self.filedialog.asksaveasfilename(parent=self.root, title="Save synchronized video", defaultextension=".mp4", initialfile=current.name if current else "clasp-video.mp4", initialdir=str(current.parent) if current else None, filetypes=[("MP4 · AAC audio", "*.mp4"), ("MOV · lossless ALAC audio", "*.mov")])
        if path:
            self.output.set(path)

    def _start(self) -> None:
        if self.busy:
            return
        try:
            if not all(value.get().strip() for value in (self.video, self.audio, self.output)):
                raise ValueError("Choose a camera video, a WAV recording and an output location first.")
            video, audio, output = (Path(value.get()).expanduser().resolve() for value in (self.video, self.audio, self.output))
            if not video.is_file() or not audio.is_file():
                raise ValueError("Both input recordings must be existing files.")
            if output in (video, audio) or (output.exists() and (output.samefile(video) or output.samefile(audio))):
                raise ValueError("Choose a new output file; Clasp never replaces an input recording.")
            nudge = float(self.nudge.get())
            if not math.isfinite(nudge) or abs(nudge) > 5000:
                raise ValueError("Fine adjustment must be a finite value between -5000 and 5000 ms.")
        except (ValueError, OSError) as exc:
            self.messagebox.showerror("Check your selections", str(exc), parent=self.root)
            return
        sidecar = output.with_suffix(output.suffix + ".sync.json")
        existing = [path for path in (output, sidecar) if path.exists()]
        overwrite = bool(existing)
        if overwrite:
            files = "\n".join(str(path) for path in existing)
            if not self.messagebox.askyesno("Replace existing output?", f"Replace these existing output files?\n\n{files}", parent=self.root):
                return
        self.busy = True
        self.finished_output = None
        self.open_button.configure(state="disabled")
        self.sync_button.configure(state="disabled")
        self.coverage_control.configure(state="disabled")
        for control in self.controls:
            control.configure(state="disabled")
        self.status.set("Listening for the same moments in both recordings…")
        self.bar.start(12)
        mode = self.coverage.get()
        kwargs = {"overwrite": overwrite, "pad_missing": mode == "Keep video; pad with silence", "trim_to_overlap": mode == "Trim to common duration", "nudge_ms": nudge}
        threading.Thread(target=self._worker, args=(video, audio, output, kwargs), daemon=True).start()

    def _worker(self, video: Path, audio: Path, output: Path, kwargs: dict[str, Any]) -> None:
        try:
            from .pipeline import synchronize

            report = synchronize(video, audio, output, progress=lambda message: self.events.put(("progress", str(message))), **kwargs)
        except Exception as exc:
            self.events.put(("error", str(exc) or type(exc).__name__))
        else:
            self.events.put(("done", (output, report)))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(payload)
                elif kind == "done":
                    output, _report = payload
                    self._finish()
                    self.finished_output = output
                    self.open_button.configure(state="normal")
                    self.status.set(f"Ready. Your video now uses the external microphone.\n{output.name}")
                elif kind == "error":
                    self._finish()
                    self.status.set("Export stopped. Review the message and try again.")
                    self.messagebox.showerror("Clasp could not finish", payload, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self) -> None:
        self.busy = False
        self.bar.stop()
        self.bar.configure(value=0)
        self.sync_button.configure(state="normal")
        self.coverage_control.configure(state="readonly")
        for control in self.controls:
            control.configure(state="normal")

    def _open_output(self) -> None:
        if self.finished_output is not None:
            try:
                subprocess.run(["open", str(self.finished_output)], check=True, capture_output=True)
            except (OSError, subprocess.CalledProcessError):
                self.messagebox.showerror("Could not open video", f"You can open the exported file in Finder:\n\n{self.finished_output}", parent=self.root)

    def _about(self) -> None:
        self.messagebox.showinfo("How Clasp works", "Clasp compares the camera’s guide audio with your WAV to estimate the start offset and clock drift. It checks multiple moments and stops when the match is unreliable.\n\nThe exported soundtrack comes only from the WAV. Keep the guide audio when recording; Clasp needs it to find the match.\n\nAudio alignment preserves the camera’s own sound-to-picture relationship. Fine adjustment can correct a known camera delay. Clasp does not analyze or alter faces.\n\nIf the WAV misses part of the video, the default trims to the common duration. You can instead require full coverage or pad with silence. All processing is local.", parent=self.root)

    def _close(self) -> None:
        if self.busy:
            self.messagebox.showinfo("Clasp is working", "Please let this export finish before closing Clasp.", parent=self.root)
        else:
            self.root.destroy()


def main() -> int:
    """Launch the optional Tk interface, with useful installation errors."""
    if sys.platform != "darwin":
        print("Clasp’s file-picker interface is supported on macOS only.", file=sys.stderr)
        return 1
    try:
        import tkinter as tk
    except ImportError:
        print("Clasp’s interface needs Tk. Install the python-tk package matching your Homebrew Python version, or use the Python installer from python.org. The clasp command works without Tk.", file=sys.stderr)
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Clasp could not start the graphical interface: {exc}", file=sys.stderr)
        return 1
    ClaspApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
