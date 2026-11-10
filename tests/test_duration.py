"""Regressions for uncapped input analysis and command execution."""
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
import numpy as np
import pytest
from clasp import media, levels

@pytest.mark.parametrize('seconds', [1800.01, 8*3600, 48*3600])
def test_accepts_long_finite_duration(seconds):
    assert media.duration({}, {'duration': str(seconds)}) == seconds

@pytest.mark.parametrize('seconds', [0, -1, float('nan'), float('inf')])
def test_rejects_invalid_duration(seconds):
    with pytest.raises(media.ClaspError, match='finite'):
        media.duration({}, {'duration': str(seconds)})

def test_media_commands_have_no_default_deadline(monkeypatch):
    def long_command(*args, **kwargs):
        if kwargs.get('timeout') is not None:
            raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
        return SimpleNamespace(returncode=0, stdout=b'completed', stderr=b'')
    monkeypatch.setattr(media.subprocess, 'run', long_command)
    assert media.run(['long-export']) == b'completed'


def test_level_scan_has_no_deadline(monkeypatch):
    def long_scan(*args, **kwargs):
        if kwargs.get('timeout') is not None:
            raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
        return SimpleNamespace(returncode=0, stderr=b'[Parsed_astats_1] Peak level dB: -6\n[Parsed_astats_1] Number of NaNs: 0\n[Parsed_astats_1] Number of Infs: 0\n')
    monkeypatch.setattr(levels, 'executable', lambda x: x)
    monkeypatch.setattr(levels.subprocess, 'run', long_scan)
    assert levels.check_levels(Path('long.wav'), {'index': 0}) == -6

@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_analysis_keeps_sound_after_thirty_minutes(tmp_path):
    path = tmp_path / 'past-old-limit.wav'
    expr = r'aevalsrc=if(gte(t\,1801)\,0.2*sin(2*PI*440*t)\,0):s=8000:d=1802'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',expr,'-c:a','pcm_s16le',str(path)],check=True)
    samples, channel = media.decode_analysis(path, {'index':0,'channels':1})
    assert len(samples) == 1802 * media.ANALYSIS_RATE
    assert channel == 0
    assert np.max(np.abs(samples[:1800 * media.ANALYSIS_RATE])) == 0
    assert np.sqrt(np.mean(samples[-4000:]**2)) > .1
