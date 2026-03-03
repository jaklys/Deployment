"""
Loopback Test - End-to-end encode/decode verification.

Tests the full pipeline: folder -> tar.gz -> OFDM -> WAV -> decode -> folder
without any audio hardware (pure WAV file round-trip).
"""

import hashlib
import os
import shutil
import sys
import tempfile
import time

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from encoder import encoder as enc
from encoder import protocol

# Decoder uses numpy
import numpy as np
from decoder import decoder as dec
from decoder import audio_in, ofdm_fast


def create_test_folder(path):
    """Create a test folder with various file types."""
    os.makedirs(path, exist_ok=True)

    # Text file
    with open(os.path.join(path, "hello.txt"), "w") as f:
        f.write("Hello OFDM World!\n")
        f.write("This is a test of the audio data transfer system.\n")

    # Binary file (some random-ish bytes)
    with open(os.path.join(path, "binary.dat"), "wb") as f:
        # Deterministic pseudo-random data
        data = bytes([(i * 137 + 42) & 0xFF for i in range(500)])
        f.write(data)

    # Nested directory
    nested = os.path.join(path, "subdir")
    os.makedirs(nested, exist_ok=True)
    with open(os.path.join(nested, "nested.txt"), "w") as f:
        f.write("File in subdirectory\n")

    # Empty file
    with open(os.path.join(path, "empty.txt"), "w") as f:
        pass

    # Python source file
    with open(os.path.join(path, "example.py"), "w") as f:
        f.write('#!/usr/bin/env python3\n')
        f.write('"""Example Python file."""\n')
        f.write('\ndef hello():\n')
        f.write('    print("Hello from transferred code!")\n')
        f.write('\nif __name__ == "__main__":\n')
        f.write('    hello()\n')


def hash_directory(path):
    """Compute SHA256 for every file in a directory tree."""
    hashes = {}
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, path)
            with open(fp, "rb") as fh:
                hashes[rel] = hashlib.sha256(fh.read()).hexdigest()
    return hashes


def test_profile(profile_name, test_folder, tmpdir, add_noise_db=None):
    """
    Test one profile: encode -> WAV -> decode -> verify.

    Args:
        profile_name: modulation profile to test
        test_folder: path to test input folder
        tmpdir: temporary directory for WAV and output
        add_noise_db: if set, add Gaussian noise at this SNR (dB)

    Returns:
        dict with test results
    """
    wav_path = os.path.join(tmpdir, f"test_{profile_name}.wav")
    out_dir = os.path.join(tmpdir, f"output_{profile_name}")

    result = {
        "profile": profile_name,
        "noise_db": add_noise_db,
        "encode_ok": False,
        "decode_ok": False,
        "files_match": False,
        "encode_time": 0,
        "decode_time": 0,
        "wav_duration": 0,
        "data_rate": 0,
    }

    # Encode
    print(f"\n{'='*60}")
    print(f"Testing profile: {profile_name}" +
          (f" (noise: {add_noise_db} dB SNR)" if add_noise_db else ""))
    print(f"{'='*60}")

    try:
        t0 = time.time()
        enc.encode(test_folder, wav_path, profile_name)
        result["encode_time"] = time.time() - t0
        result["encode_ok"] = True
    except Exception as e:
        print(f"  ENCODE FAILED: {e}")
        import traceback
        traceback.print_exc()
        return result

    # Add noise if requested
    if add_noise_db is not None:
        signal, sr = audio_in.read_wav(wav_path)
        # Compute signal power (only non-silent parts)
        nonsilent = np.abs(signal) > 0.001
        if np.any(nonsilent):
            signal_power = np.mean(signal[nonsilent] ** 2)
        else:
            signal_power = np.mean(signal ** 2)

        noise_power = signal_power / (10 ** (add_noise_db / 10))
        noise = np.random.normal(0, np.sqrt(noise_power), len(signal)).astype(np.float32)
        noisy = signal + noise

        # Clip to [-1, 1]
        noisy = np.clip(noisy, -1.0, 1.0)

        # Overwrite WAV with noisy version
        from encoder import audio_out
        audio_out.write_wav(wav_path, noisy.tolist(), sr)
        print(f"  Added Gaussian noise at {add_noise_db} dB SNR")

    # Get WAV info
    signal, sr = audio_in.read_wav(wav_path)
    result["wav_duration"] = len(signal) / sr

    # Decode
    try:
        t0 = time.time()
        success = dec.decode(wav_path, out_dir, profile_name)
        result["decode_time"] = time.time() - t0
        result["decode_ok"] = success
    except Exception as e:
        print(f"  DECODE FAILED: {e}")
        import traceback
        traceback.print_exc()
        return result

    # Verify files match
    if result["decode_ok"]:
        # The extracted folder should be inside out_dir
        src_name = os.path.basename(test_folder)
        extracted_path = os.path.join(out_dir, src_name)

        if os.path.isdir(extracted_path):
            src_hashes = hash_directory(test_folder)
            dst_hashes = hash_directory(extracted_path)

            if src_hashes == dst_hashes:
                result["files_match"] = True
                print(f"\n  FILES MATCH - All {len(src_hashes)} files verified!")
            else:
                print(f"\n  FILES MISMATCH!")
                for f in sorted(set(list(src_hashes.keys()) + list(dst_hashes.keys()))):
                    s = src_hashes.get(f, "MISSING")
                    d = dst_hashes.get(f, "MISSING")
                    status = "OK" if s == d else "DIFFER"
                    print(f"    {status}: {f}")
        else:
            print(f"  Expected extracted folder not found: {extracted_path}")
            # List what was extracted
            if os.path.isdir(out_dir):
                print(f"  Contents of {out_dir}:")
                for item in os.listdir(out_dir):
                    print(f"    {item}")

    # Compute effective data rate
    compressed_size = os.path.getsize(wav_path.replace('.wav', '.wav'))  # use metadata
    if result["wav_duration"] > 0 and result["decode_ok"]:
        result["data_rate"] = result.get("data_bytes", 0) / result["wav_duration"]

    return result


def main():
    print("=" * 60)
    print("OFDM Audio Modem - Loopback Test")
    print("=" * 60)

    # Create temp directories
    tmpdir = tempfile.mkdtemp(prefix="ofdm_test_")
    test_folder = os.path.join(tmpdir, "test_input")

    try:
        # Create test data
        print("\nCreating test data...")
        create_test_folder(test_folder)

        src_hashes = hash_directory(test_folder)
        print(f"Test folder: {test_folder}")
        print(f"Files: {len(src_hashes)}")
        for f, h in sorted(src_hashes.items()):
            size = os.path.getsize(os.path.join(test_folder, f))
            print(f"  {f} ({size} bytes) {h[:12]}...")

        # Test each profile
        results = []

        # Test basic profiles without noise
        for profile in ["safe", "standard", "fast", "turbo"]:
            r = test_profile(profile, test_folder, tmpdir)
            results.append(r)

        # Test with noise (only safe profile to keep it quick)
        for snr_db in [30, 20, 10]:
            noise_dir = tmpdir + f"_noise{snr_db}"
            os.makedirs(noise_dir, exist_ok=True)
            r = test_profile("safe", test_folder, noise_dir,
                             add_noise_db=snr_db)
            results.append(r)

        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"{'Profile':<12} {'Noise':>8} {'Encode':>8} {'Decode':>8} "
              f"{'Match':>8} {'WAV dur':>10} {'Enc time':>10}")
        print("-" * 80)

        for r in results:
            noise_str = f"{r['noise_db']}dB" if r['noise_db'] else "clean"
            enc_str = "OK" if r['encode_ok'] else "FAIL"
            dec_str = "OK" if r['decode_ok'] else "FAIL"
            match_str = "PASS" if r['files_match'] else "FAIL"
            dur_str = f"{r['wav_duration']:.1f}s"
            time_str = f"{r['encode_time']:.1f}s"

            print(f"{r['profile']:<12} {noise_str:>8} {enc_str:>8} {dec_str:>8} "
                  f"{match_str:>8} {dur_str:>10} {time_str:>10}")

        # Overall verdict
        all_clean_pass = all(r['files_match'] for r in results if r['noise_db'] is None)
        print()
        if all_clean_pass:
            print("OVERALL: All clean-channel tests PASSED!")
        else:
            print("OVERALL: Some tests FAILED")

    finally:
        # Cleanup
        print(f"\nCleaning up: {tmpdir}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        # Also clean noise variants
        for snr in [30, 20, 10]:
            p = tmpdir + f"_noise{snr}"
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    main()
