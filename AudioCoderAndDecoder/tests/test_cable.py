"""
Cable Test - Interactive test tool for physical 3.5mm audio cable.

Steps:
  1. Play test tones and measure input level -> set volume
  2. Send short test sequence -> measure BER
  3. Auto-find optimal profile (max speed with BER < 10^-6)
  4. Recommend settings for production transfer

Requirements: numpy, scipy, sounddevice (same as decoder)

Usage:
    python -m tests.test_cable
    python -m tests.test_cable --output-device 3 --input-device 5
"""

import argparse
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from encoder import encoder as enc
from encoder import protocol
from encoder import audio_out as enc_audio
from decoder import decoder as dec
from decoder import audio_in
from decoder import ofdm_fast


def step1_level_check(input_device=None, output_device=None):
    """Play test tones and measure input level."""
    import sounddevice as sd

    print("=" * 60)
    print("STEP 1: Audio Level Check")
    print("=" * 60)
    print()
    print("Connect 3.5mm cable: PC headphone out -> PC line-in")
    print("Set output volume to ~50%")
    print()
    input("Press Enter when ready...")

    sr = protocol.SAMPLE_RATE
    duration = 3.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    # Generate test tone (1kHz sine)
    tone = 0.5 * np.sin(2 * np.pi * 1000 * t).astype(np.float32)

    print(f"\nPlaying 1 kHz tone for {duration}s...")
    print("Recording simultaneously...")

    # Play and record simultaneously
    recorded = sd.playrec(
        tone.reshape(-1, 1),
        samplerate=sr,
        channels=1,
        input_mapping=[1],
        output_mapping=[1],
        device=(input_device, output_device),
        dtype='float32'
    )
    sd.wait()
    recorded = recorded.flatten()

    # Measure levels
    peak = np.max(np.abs(recorded))
    rms = np.sqrt(np.mean(recorded ** 2))
    peak_db = 20 * np.log10(peak + 1e-10)
    rms_db = 20 * np.log10(rms + 1e-10)

    print(f"\n  Input peak level: {peak:.4f} ({peak_db:.1f} dBFS)")
    print(f"  Input RMS level:  {rms:.4f} ({rms_db:.1f} dBFS)")

    if peak < 0.01:
        print("\n  WARNING: Very low signal! Check cable connection and volume.")
        print("  Recommended: Increase output volume.")
        return False
    elif peak > 0.95:
        print("\n  WARNING: Signal is clipping! Reduce output volume.")
        return False
    elif peak < 0.1:
        print("\n  Signal is weak. Try increasing output volume to 70-80%.")
    else:
        print("\n  Signal level looks good!")

    return True


def step2_ber_test(profile_name, input_device=None, output_device=None):
    """Send short test and measure BER."""
    import sounddevice as sd

    print()
    print("=" * 60)
    print(f"STEP 2: BER Test (profile: {profile_name})")
    print("=" * 60)

    # Create small test data
    test_data = bytes(range(256)) * 4  # 1024 bytes, known pattern

    tmpdir = tempfile.mkdtemp(prefix="cable_test_")
    test_file = os.path.join(tmpdir, "test_data.bin")
    wav_file = os.path.join(tmpdir, "test_signal.wav")

    with open(test_file, "wb") as f:
        f.write(test_data)

    # Encode
    print("\nEncoding test data...")
    enc.encode(test_file, wav_file, profile_name)

    # Read WAV
    signal, sr = audio_in.read_wav(wav_file)
    duration = len(signal) / sr + 2.0  # add margin

    # Play and record
    print(f"\nPlaying and recording ({duration:.1f}s)...")
    signal_padded = np.zeros(int(duration * sr), dtype=np.float32)
    signal_padded[:len(signal)] = signal.astype(np.float32)

    recorded = sd.playrec(
        signal_padded.reshape(-1, 1),
        samplerate=sr,
        channels=1,
        input_mapping=[1],
        output_mapping=[1],
        device=(input_device, output_device),
        dtype='float32'
    )
    sd.wait()
    recorded = recorded.flatten()

    # Save recorded WAV for analysis
    rec_wav = os.path.join(tmpdir, "recorded.wav")
    enc_audio.write_wav(rec_wav, recorded.tolist(), sr)

    # Decode
    print("\nDecoding...")
    out_dir = os.path.join(tmpdir, "output")
    success = dec.decode(rec_wav, out_dir, profile_name)

    if success:
        # Compare
        received_file = os.path.join(out_dir, "test_data.bin")
        if os.path.exists(received_file):
            with open(received_file, "rb") as f:
                received = f.read()

            # Calculate BER
            errors = 0
            total_bits = min(len(test_data), len(received)) * 8
            for i in range(min(len(test_data), len(received))):
                diff = test_data[i] ^ received[i]
                errors += bin(diff).count('1')

            ber = errors / total_bits if total_bits > 0 else 1.0
            print(f"\n  Bit Error Rate: {ber:.2e}")
            print(f"  Bit errors: {errors} / {total_bits}")

            if ber == 0:
                print("  PERFECT - Zero errors!")
            elif ber < 1e-6:
                print("  EXCELLENT - Below 10^-6 BER")
            elif ber < 1e-3:
                print("  OK - Some errors, FEC should handle this")
            else:
                print("  POOR - Too many errors, try lower profile")

            return ber
        else:
            print("  Output file not found!")
            return 1.0
    else:
        print("  Decode failed!")
        return 1.0


def step3_find_optimal(input_device=None, output_device=None):
    """Auto-find best profile."""
    print()
    print("=" * 60)
    print("STEP 3: Finding Optimal Profile")
    print("=" * 60)

    results = {}
    for profile_name in ["turbo", "fast", "standard", "safe"]:
        print(f"\n--- Testing {profile_name} ---")
        ber = step2_ber_test(profile_name, input_device, output_device)
        results[profile_name] = ber

        if ber == 0:
            print(f"\n  {profile_name}: PERFECT! This is the optimal profile.")
            return profile_name, results

    # Find best
    for name in ["turbo", "fast", "standard", "safe"]:
        if results.get(name, 1.0) < 1e-6:
            return name, results

    return "safe", results


def step4_recommendation(optimal_profile, results):
    """Print final recommendations."""
    print()
    print("=" * 60)
    print("STEP 4: Recommendations")
    print("=" * 60)
    print()

    print("Test results:")
    for name in ["turbo", "fast", "standard", "safe"]:
        if name in results:
            ber = results[name]
            rate = protocol.effective_byterate(name)
            status = "OK" if ber < 1e-6 else "FAIL"
            print(f"  {name:<10} BER={ber:.2e}  {status}  ({rate/1024:.1f} kB/s)")

    print()
    print(f"Recommended profile: {optimal_profile}")
    rate = protocol.effective_byterate(optimal_profile)
    print(f"Expected throughput: {rate/1024:.1f} kB/s")
    print()
    print("To transfer files:")
    print(f"  Sender:   python -m encoder.encoder -i <folder> -o signal.wav -p {optimal_profile} --play")
    print(f"  Receiver: python -m decoder.decoder --duration 300 -o ./received/ -p {optimal_profile}")


def main():
    parser = argparse.ArgumentParser(
        description="Cable test - find optimal transfer settings",
    )
    parser.add_argument('--input-device', '-id', type=int, default=None,
                        help='Audio input device index')
    parser.add_argument('--output-device', '-od', type=int, default=None,
                        help='Audio output device index')
    parser.add_argument('--list-devices', action='store_true',
                        help='List audio devices and exit')
    parser.add_argument('--step', type=int, choices=[1, 2, 3],
                        help='Run specific step only')
    parser.add_argument('--profile', '-p', default='standard',
                        choices=list(protocol.PROFILES.keys()),
                        help='Profile for step 2 (default: standard)')

    args = parser.parse_args()

    if args.list_devices:
        audio_in.list_input_devices()
        return

    if args.step == 1:
        step1_level_check(args.input_device, args.output_device)
    elif args.step == 2:
        step2_ber_test(args.profile, args.input_device, args.output_device)
    elif args.step == 3:
        step3_find_optimal(args.input_device, args.output_device)
    else:
        # Full test sequence
        ok = step1_level_check(args.input_device, args.output_device)
        if not ok:
            print("\nFix audio levels before continuing.")
            return

        optimal, results = step3_find_optimal(args.input_device, args.output_device)
        step4_recommendation(optimal, results)


if __name__ == "__main__":
    main()
