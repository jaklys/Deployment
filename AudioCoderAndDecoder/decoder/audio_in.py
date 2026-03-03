"""
Audio input - WAV file reading and microphone capture.

Dependencies: numpy, sounddevice (optional for live capture)
"""

import wave
import sys
import numpy as np


def read_wav(filename):
    """
    Read a WAV file and return float32 numpy array.

    Args:
        filename: path to WAV file

    Returns:
        (samples, sample_rate) where samples is float32 numpy array in [-1, 1]
    """
    with wave.open(filename, 'rb') as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported sample width: {sample_width}")

    # Convert to mono if stereo
    if n_channels == 2:
        samples = (samples[0::2] + samples[1::2]) / 2.0
    elif n_channels > 2:
        samples = samples.reshape(-1, n_channels).mean(axis=1)

    return samples, sample_rate


def list_input_devices():
    """List available audio input devices."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        print("Available input devices:")
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                print(f"  [{i}] {d['name']} ({d['max_input_channels']}ch, "
                      f"{int(d['default_samplerate'])}Hz)")
    except ImportError:
        print("sounddevice not installed. Install with: pip install sounddevice",
              file=sys.stderr)


def capture_audio(duration_sec, sample_rate=44100, device=None):
    """
    Record audio from input device.

    Args:
        duration_sec: recording duration in seconds
        sample_rate: sample rate in Hz
        device: sounddevice device index (None = default)

    Returns:
        float32 numpy array of samples
    """
    import sounddevice as sd

    frames = int(duration_sec * sample_rate)
    print(f"Recording {duration_sec:.1f}s from device {device or 'default'}...")
    print("Press Ctrl+C to stop early.")

    try:
        audio = sd.rec(frames, samplerate=sample_rate, channels=1,
                       dtype='float32', device=device)
        sd.wait()
    except KeyboardInterrupt:
        sd.stop()
        print("\nRecording stopped early.")

    return audio.flatten()
