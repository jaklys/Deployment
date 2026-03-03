"""
WAV file writing and Windows audio playback.

ZERO external dependencies - stdlib only.
Uses: wave, struct, array modules.
"""

import wave
import array
import sys
import os


def samples_to_pcm16(samples, peak_amplitude=0.9):
    """
    Convert float samples [-1.0, 1.0] to 16-bit signed PCM.

    Args:
        samples: iterable of float values
        peak_amplitude: scale factor (0.9 leaves headroom for clipping prevention)

    Returns:
        array.array('h') of 16-bit signed integers
    """
    scale = 32767.0 * peak_amplitude
    pcm = array.array('h')
    for s in samples:
        clamped = max(-1.0, min(1.0, s))
        pcm.append(int(round(clamped * scale)))
    return pcm


def write_wav(filename, samples, sample_rate=44100, peak_amplitude=0.9):
    """
    Write float samples to a mono 16-bit WAV file.

    Args:
        filename: output WAV file path
        samples: list/iterable of float values in [-1.0, 1.0]
        sample_rate: samples per second (default 44100)
        peak_amplitude: normalization peak (default 0.9)
    """
    pcm = samples_to_pcm16(samples, peak_amplitude)
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def play_wav(filename):
    """
    Play a WAV file on Windows using winsound (blocking).

    Falls back gracefully if winsound is not available (non-Windows).
    """
    try:
        import winsound
        winsound.PlaySound(filename, winsound.SND_FILENAME)
    except ImportError:
        print("winsound not available (non-Windows). WAV saved but cannot play.",
              file=sys.stderr)
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)


def get_wav_duration(filename):
    """Get duration of a WAV file in seconds."""
    with wave.open(filename, 'rb') as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / rate


def print_progress(current, total, prefix="Progress", bar_length=40):
    """Print a simple progress bar to stderr."""
    if total == 0:
        return
    pct = current / total
    filled = int(bar_length * pct)
    bar = '=' * filled + '-' * (bar_length - filled)
    sys.stderr.write(f'\r{prefix}: [{bar}] {pct*100:.1f}% ({current}/{total})')
    if current >= total:
        sys.stderr.write('\n')
    sys.stderr.flush()
