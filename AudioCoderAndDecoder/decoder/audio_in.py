"""
Audio input - WAV file reading and microphone capture.

Dependencies: numpy, scipy (optional for resampling), sounddevice (optional for live capture)
"""

import wave
import sys
import numpy as np

from . import protocol


def _resample(samples, orig_rate, target_rate):
    """Resample audio using polyphase filtering (high quality)."""
    if orig_rate == target_rate:
        return samples
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(orig_rate), int(target_rate))
    up = int(target_rate) // g
    down = int(orig_rate) // g
    return resample_poly(samples, up, down).astype(np.float32)


def read_wav(filename):
    """
    Read a WAV file and return float32 numpy array at protocol sample rate.

    Args:
        filename: path to WAV file

    Returns:
        (samples, sample_rate) where samples is float32 numpy array in [-1, 1]
        sample_rate is always protocol.SAMPLE_RATE (resampled if needed)
    """
    with wave.open(filename, 'rb') as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 3:
        # 24-bit PCM: unpack 3 bytes per sample (little-endian signed)
        raw_bytes = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        samples = (raw_bytes[:, 0].astype(np.int32)
                   | (raw_bytes[:, 1].astype(np.int32) << 8)
                   | (raw_bytes[:, 2].astype(np.int32) << 16))
        samples[samples >= 0x800000] -= 0x1000000
        samples = samples.astype(np.float32) / 8388608.0
    elif sample_width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported sample width: {sample_width}")

    # Convert to mono if stereo
    if n_channels == 2:
        samples = (samples[0::2] + samples[1::2]) / 2.0
    elif n_channels > 2:
        samples = samples.reshape(-1, n_channels).mean(axis=1)

    # Resample to protocol sample rate if needed
    if sample_rate != protocol.SAMPLE_RATE:
        print(f"  Resampling {sample_rate} Hz -> {protocol.SAMPLE_RATE} Hz")
        samples = _resample(samples, sample_rate, protocol.SAMPLE_RATE)
        sample_rate = protocol.SAMPLE_RATE

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
