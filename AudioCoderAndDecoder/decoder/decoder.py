"""
OFDM Audio Decoder - Main CLI

Decodes OFDM-modulated audio (from WAV file or live capture)
back to the original file/folder.

Dependencies: numpy, scipy (optional), sounddevice (optional)

Usage:
    python -m decoder.decoder --input signal.wav --output ./received/
    python -m decoder.decoder --device 2 --duration 300 --output ./received/
"""

import argparse
import hashlib
import io
import json
import os
import struct
import sys
import tarfile
import time

import numpy as np

from . import protocol
from . import ofdm_fast
from . import audio_in
from . import fec_fast


def bits_to_bytes(bits):
    """Convert list of 0/1 integers to bytes (MSB first)."""
    result = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j in range(8):
            if i + j < len(bits):
                byte = (byte << 1) | bits[i + j]
            else:
                byte = byte << 1
        result.append(byte)
    return bytes(result)


def deinterleave_codewords(interleaved_bytes, depth):
    """
    Reverse block interleaving to recover individual RS codewords.

    The encoder wrote codewords row-by-row and read column-by-column.
    We reverse: write column-by-column (the interleaved data), read row-by-row.

    Args:
        interleaved_bytes: bytes of interleaved data (depth * RS_N bytes)
        depth: number of codewords interleaved together

    Returns:
        list of bytes objects (each RS_N = 255 bytes)
    """
    width = protocol.RS_N  # 255
    expected_len = depth * width

    data = bytearray(interleaved_bytes)
    if len(data) < expected_len:
        data.extend(b'\x00' * (expected_len - len(data)))

    # Data was written column-by-column: for col in width, for row in depth
    # We read row-by-row to recover codewords
    codewords = []
    for row in range(depth):
        cw = bytearray(width)
        for col in range(width):
            idx = col * depth + row
            cw[col] = data[idx]
        codewords.append(bytes(cw))

    return codewords


def decode_frame(frame_bytes):
    """
    Parse a protocol frame from raw bytes.

    Returns:
        dict with keys: magic, frame_id, total_frames, payload_len, payload
        or None if invalid
    """
    if len(frame_bytes) < protocol.FRAME_HEADER_SIZE:
        return None

    magic, frame_id, total_frames, payload_len = struct.unpack(
        '>HHHH', frame_bytes[:8])

    if magic != protocol.FRAME_MAGIC:
        return None

    payload = frame_bytes[8:8 + payload_len]

    return {
        'magic': magic,
        'frame_id': frame_id,
        'total_frames': total_frames,
        'payload_len': payload_len,
        'payload': payload,
    }


def demodulate_frames(signal, preamble_start, H, data_start, profile_name):
    """
    Demodulate all data symbols and extract frames.

    First frame is always metadata (encoded with BPSK/"safe").
    Remaining frames use the specified profile.

    Args:
        signal: numpy array of audio samples
        preamble_start: detected preamble position
        H: channel estimate
        data_start: sample index where data begins
        profile_name: modulation profile for data frames

    Returns:
        (metadata_dict, data_bytes, stats_dict)
    """
    sym_len = protocol.SYMBOL_SAMPLES

    # Calculate how many symbols fit in remaining signal
    remaining = len(signal) - data_start
    max_symbols = remaining // sym_len

    if max_symbols < 1:
        raise ValueError("Signal too short - no data symbols found")

    print(f"  Max possible symbols: {max_symbols}")

    # RS codeword = RS_N bytes (255); each frame is RS-encoded on the encoder side
    rs_cw_bits = protocol.RS_N * 8  # 2040 bits per RS codeword

    # ── Decode metadata frame first (always BPSK) ──
    meta_bpc = protocol.PROFILES["safe"]["bits_per_carrier"]
    meta_bits_per_sym = protocol.NUM_DATA_BINS * meta_bpc
    meta_sym_count = (rs_cw_bits + meta_bits_per_sym - 1) // meta_bits_per_sym

    print(f"  Decoding metadata ({meta_sym_count} BPSK symbols)...")

    meta_phase_tracker = ofdm_fast.PhaseTracker()
    meta_bits = []
    for sym_idx in range(min(meta_sym_count, max_symbols)):
        sym_start = data_start + sym_idx * sym_len
        bits = ofdm_fast.demodulate_data_symbol(
            signal, sym_start, H, "safe", sym_idx,
            phase_tracker=meta_phase_tracker)
        meta_bits.extend(bits)

    # RS-decode metadata codeword
    meta_cw_bytes = bits_to_bytes(meta_bits[:rs_cw_bits])
    fec_corrected = 0
    try:
        meta_msg, meta_nerr = fec_fast.rs_decode(meta_cw_bytes)
        fec_corrected += meta_nerr
        if meta_nerr > 0:
            print(f"  Metadata: RS corrected {meta_nerr} errors")
    except ValueError as e:
        print(f"  WARNING: Metadata RS decode failed: {e}")
        meta_msg = meta_cw_bytes[:protocol.RS_K]

    meta_frame = decode_frame(meta_msg)
    metadata = None
    if meta_frame and meta_frame['frame_id'] == 0xFFFF:
        try:
            meta_json = meta_frame['payload'].decode('ascii').rstrip('\x00')
            metadata = json.loads(meta_json)
            print(f"  Metadata: {metadata['name']} "
                  f"({metadata['compressed_size']} bytes compressed)")
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
            print(f"  WARNING: Failed to parse metadata: {e}")

    # ── Decode data frames (with deinterleaving) ──
    data_bpc = protocol.PROFILES[profile_name]["bits_per_carrier"]
    data_bits_per_sym = protocol.NUM_DATA_BINS * data_bpc
    depth = protocol.INTERLEAVE_DEPTH

    # Start after metadata symbols
    data_sym_start_idx = meta_sym_count

    # Determine how many data frames to expect
    if metadata:
        compressed_size = metadata['compressed_size']
        payload_per_frame = protocol.FRAME_PAYLOAD_MAX - protocol.FRAME_HEADER_SIZE
        expected_data_frames = (compressed_size + payload_per_frame - 1) // payload_per_frame
    else:
        expected_data_frames = (max_symbols - data_sym_start_idx) * data_bits_per_sym // rs_cw_bits
        expected_data_frames = min(expected_data_frames, 10000)

    # Interleaved batch = depth codewords interleaved together
    batch_bits = depth * rs_cw_bits
    syms_per_batch = (batch_bits + data_bits_per_sym - 1) // data_bits_per_sym
    num_batches = (expected_data_frames + depth - 1) // depth

    print(f"  Expected data frames: {expected_data_frames}")
    print(f"  Interleave depth: {depth}, batches: {num_batches}")
    print(f"  Symbols per batch: {syms_per_batch}")
    print()

    # Collect all data
    all_payload = bytearray()
    frames_ok = 0
    frames_bad = 0
    is_chunked = metadata.get("chunked", False) if metadata else False
    retrain_count = 0
    sym_idx_global = data_sym_start_idx
    chunk_sym_count = 0  # data symbols since last re-training
    data_pilot_idx = 0   # pilot index matches encoder (starts at 0 for data)
    data_phase_tracker = ofdm_fast.PhaseTracker()
    frames_decoded = 0

    for batch_idx in range(num_batches):
        # Check if we need to consume re-training symbols (resync)
        if is_chunked and chunk_sym_count >= protocol.CHUNK_SYMBOLS:
            retrain_start = data_start + sym_idx_global * sym_len
            # Re-estimate channel from re-training symbols
            H = ofdm_fast.estimate_channel_retrain(signal, retrain_start)
            # Skip re-training symbols
            sym_idx_global += protocol.RETRAIN_SYMBOLS
            # Reset phase tracker for fresh tracking in new chunk
            data_phase_tracker = ofdm_fast.PhaseTracker()
            # NOTE: do NOT reset data_pilot_idx - encoder keeps counting
            chunk_sym_count = 0
            retrain_count += 1

        # Demodulate symbols for this batch
        batch_bit_list = []
        for s in range(syms_per_batch):
            if sym_idx_global >= max_symbols:
                break
            sym_start = data_start + sym_idx_global * sym_len
            bits = ofdm_fast.demodulate_data_symbol(
                signal, sym_start, H, profile_name, data_pilot_idx,
                phase_tracker=data_phase_tracker)
            batch_bit_list.extend(bits)
            sym_idx_global += 1
            data_pilot_idx += 1
            chunk_sym_count += 1

        if len(batch_bit_list) < batch_bits:
            batch_bit_list.extend([0] * (batch_bits - len(batch_bit_list)))

        # Convert bits to bytes (interleaved codewords)
        interleaved_bytes = bits_to_bytes(batch_bit_list[:batch_bits])

        # Deinterleave to recover individual RS codewords
        codewords = deinterleave_codewords(interleaved_bytes, depth)

        # RS decode each codeword and extract frames
        frames_in_batch = min(depth, expected_data_frames - batch_idx * depth)
        for cw_idx in range(frames_in_batch):
            rs_ok = False
            try:
                msg_bytes, nerr = fec_fast.rs_decode(codewords[cw_idx])
                fec_corrected += nerr
                rs_ok = True
            except ValueError:
                msg_bytes = codewords[cw_idx][:protocol.RS_K]

            if rs_ok:
                frame = decode_frame(msg_bytes)
                if frame and frame['magic'] == protocol.FRAME_MAGIC:
                    all_payload.extend(frame['payload'])
                    frames_ok += 1
                else:
                    frames_bad += 1
            else:
                # RS decode failed - do NOT trust raw data
                frames_bad += 1

            frames_decoded += 1

        # Progress
        if frames_decoded >= expected_data_frames or (batch_idx + 1) % 5 == 0:
            pct = frames_decoded / max(expected_data_frames, 1) * 100
            sys.stderr.write(f'\r  Decoding: {frames_decoded}/{expected_data_frames} '
                             f'frames ({pct:.0f}%) OK={frames_ok} BAD={frames_bad} '
                             f'FEC={fec_corrected}')
            sys.stderr.flush()

    sys.stderr.write('\n')

    # Trim to expected compressed size
    if metadata:
        all_payload = bytes(all_payload[:metadata['compressed_size']])
    else:
        all_payload = bytes(all_payload)

    stats = {
        'frames_ok': frames_ok,
        'frames_bad': frames_bad,
        'fec_corrected': fec_corrected,
        'total_symbols': sym_idx_global,
        'retrain_count': retrain_count,
        'data_bytes': len(all_payload),
    }

    return metadata, all_payload, stats


def verify_and_extract(data, metadata, output_dir):
    """
    Verify SHA256 and extract tar.gz to output directory.

    Args:
        data: compressed bytes
        metadata: metadata dict (or None)
        output_dir: directory to extract to

    Returns:
        True if successful
    """
    # SHA256 verification
    if metadata and 'sha256' in metadata:
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha == metadata['sha256']:
            print(f"  SHA256 verified: {actual_sha[:16]}...")
        else:
            print(f"  SHA256 MISMATCH!")
            print(f"    Expected: {metadata['sha256'][:32]}...")
            print(f"    Actual:   {actual_sha[:32]}...")
            print("  WARNING: Data may be corrupted. Attempting extraction anyway.")

    # Extract tar.gz
    os.makedirs(output_dir, exist_ok=True)
    try:
        buf = io.BytesIO(data)
        with tarfile.open(fileobj=buf, mode='r:gz') as tf:
            tf.extractall(output_dir)
        print(f"  Extracted to: {os.path.abspath(output_dir)}")
        return True
    except Exception as e:
        print(f"  Extraction FAILED: {e}")

        # Save raw compressed data for debugging
        raw_path = os.path.join(output_dir, "raw_data.tar.gz")
        with open(raw_path, 'wb') as f:
            f.write(data)
        print(f"  Raw data saved to: {raw_path}")
        return False


def decode(input_source, output_dir, profile_name="standard",
           device=None, duration=None):
    """
    Full decoding pipeline: audio -> file/folder.

    Args:
        input_source: WAV file path, or None for live capture
        output_dir: directory to extract received files to
        profile_name: modulation profile
        device: audio device index for live capture
        duration: capture duration in seconds
    """
    print("=== OFDM Audio Decoder ===")
    print(f"Profile: {profile_name}")
    print()

    # Step 1: Get audio signal
    if input_source:
        print(f"Reading WAV: {input_source}")
        signal, sr = audio_in.read_wav(input_source)
        print(f"  Samples: {len(signal):,} ({len(signal)/sr:.1f}s)")
        print(f"  Sample rate: {sr} Hz")
        if sr != protocol.SAMPLE_RATE:
            print(f"  WARNING: Expected {protocol.SAMPLE_RATE} Hz, got {sr} Hz")
    else:
        if duration is None:
            duration = 300  # default 5 minutes
        print(f"Capturing audio for {duration}s...")
        signal = audio_in.capture_audio(duration, protocol.SAMPLE_RATE, device)
        sr = protocol.SAMPLE_RATE
        print(f"  Captured: {len(signal):,} samples")

    print()

    # Step 2: Detect preamble
    print("Detecting preamble...")
    t0 = time.time()
    preamble_start, freq_offset = ofdm_fast.detect_preamble(signal)
    t_detect = time.time() - t0

    if preamble_start is None:
        print("  ERROR: Preamble not detected!")
        print("  Check: Is the audio signal present? Is volume adequate?")
        return False

    print(f"  Preamble found at sample {preamble_start} "
          f"({preamble_start/sr:.3f}s)")
    print(f"  Detection time: {t_detect:.3f}s")
    if freq_offset:
        print(f"  Frequency offset: {freq_offset:.2f} Hz")
    print()

    # Step 3: Channel estimation
    print("Estimating channel...")
    H, data_start = ofdm_fast.estimate_channel(signal, preamble_start)

    # Compute SNR estimate from channel
    active_bins = sorted(set(protocol.DATA_BINS) | set(protocol.PILOT_BINS))
    H_active = H[active_bins]
    H_mag = np.abs(H_active)
    if np.mean(H_mag) > 0:
        snr_est = 20 * np.log10(np.mean(H_mag) / (np.std(H_mag) + 1e-10))
        print(f"  Estimated SNR: {snr_est:.1f} dB")
    print(f"  Data starts at sample {data_start} ({data_start/sr:.3f}s)")
    print()

    # Step 4: Demodulate
    print("Demodulating...")
    t0 = time.time()
    metadata, data, stats = demodulate_frames(
        signal, preamble_start, H, data_start, profile_name)
    t_demod = time.time() - t0

    print()
    print(f"  Demodulation time: {t_demod:.1f}s")
    print(f"  Frames OK: {stats['frames_ok']}, BAD: {stats['frames_bad']}")
    print(f"  FEC corrections: {stats['fec_corrected']} symbols")
    print(f"  Data received: {stats['data_bytes']:,} bytes")
    print()

    if stats['data_bytes'] == 0:
        print("ERROR: No data decoded!")
        return False

    # Step 5: Verify and extract
    print("Verifying and extracting...")
    success = verify_and_extract(data, metadata, output_dir)

    if success:
        print()
        print("=== Decoding Complete ===")
        if metadata:
            print(f"  Original: {metadata.get('name', 'unknown')}")
            print(f"  Files: {metadata.get('file_count', '?')}")
        print(f"  Output: {os.path.abspath(output_dir)}")
    else:
        print()
        print("=== Decoding FAILED ===")
        print("  Try a more conservative profile (e.g., --profile safe)")

    return success


def main():
    parser = argparse.ArgumentParser(
        description="OFDM Audio Decoder - Decode audio signal to files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Decode from WAV file:
    python -m decoder.decoder --input signal.wav --output ./received/

  Decode from microphone (5 min capture):
    python -m decoder.decoder --duration 300 --output ./received/

  List audio devices:
    python -m decoder.decoder --list-devices

  Decode with specific audio device:
    python -m decoder.decoder --device 2 --duration 120 --output ./received/
""")
    parser.add_argument('--input', '-i',
                        help='Input WAV file (omit for live capture)')
    parser.add_argument('--output', '-o', default='./received',
                        help='Output directory (default: ./received)')
    parser.add_argument('--profile', '-p', default=protocol.DEFAULT_PROFILE,
                        choices=list(protocol.PROFILES.keys()),
                        help=f'Modulation profile (default: {protocol.DEFAULT_PROFILE})')
    parser.add_argument('--device', '-d', type=int,
                        help='Audio input device index (for live capture)')
    parser.add_argument('--duration', '-t', type=float,
                        help='Capture duration in seconds (for live capture)')
    parser.add_argument('--list-devices', action='store_true',
                        help='List available audio input devices and exit')

    args = parser.parse_args()

    if args.list_devices:
        audio_in.list_input_devices()
        return

    decode(args.input, args.output, args.profile, args.device, args.duration)


if __name__ == '__main__':
    main()
