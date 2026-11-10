# Long-recording release gate

Main has no imposed input duration or media-command time limit. v0.1.0 stays fixed;
v0.2.0 must wait for real multi-hour verification on Linux. A passing 30-minute
boundary regression proves that decoding no longer truncates, not that all long
recordings align reliably.

## Before releasing v0.2.0

- [ ] Obtain continuous real 2-, 4-, and preferably 8-hour video/WAV pairs.
- [ ] Record Python/FFmpeg versions, RAM peak, free disk, and elapsed time.
- [ ] Run analysis-only, inspect accepted/rejected checkpoints throughout each take,
      and check that a linear clock model remains appropriate.
- [ ] Render each take; verify picture/audio durations, timestamps, and one production
      soundtrack, with no camera audio mixed in.
- [ ] Review lip sync at the beginning, middle, end, and regular intervals; compare
      waveforms independently around those points, including quiet sections.
- [ ] Confirm no truncation at 30 minutes, no command deadline, and acceptable RAM/disk use.
- [ ] Exercise both signs of offset, measured clock drift, and the trim/silence policies.
- [ ] Keep Linux/macOS CI green and document remaining limits before tagging v0.2.0.

The CLI is headless and CPU-based; GPU acceleration is a separate improvement.
Split-camera-file assembly, nonlinear drift correction, and arbitrary changes of
frame rate are not introduced by removing the duration cap. Keep private recordings
and machine-specific reports outside the repository.
