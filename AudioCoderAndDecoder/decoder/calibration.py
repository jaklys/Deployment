"""
OFDM Audio Modem - Calibration Signal Analyzer

Decodes a calibration WAV recording and reports channel quality
metrics for each modulation profile. Recommends the best profile.

Usage:
    python -m decoder.calibration --input recording.wav
    python -m decoder.calibration --device 1 --duration 15
"""

import argparse
import json
import struct
import sys
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
    """Reverse block interleaving to recover individual RS codewords."""
    width = protocol.RS_N
    expected_len = depth * width
    data = bytearray(interleaved_bytes)
    if len(data) < expected_len:
        data.extend(b'\\x00' * (expected_len - len(data)))
    codewords = []
    for row in range(depth):
        cw = bytearray(width)
        for col in range(width):
            idx = col * depth + row
            cw[col] = data[idx]
        codewords.append(bytes(cw))
    return codewords


def decode_frame(frame_bytes):
    """Parse a protocol frame from raw bytes."""
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


def analyze_channel(H):
    """Analyze channel frequency response and find dead bins."""
    active_bins = sorted(set(protocol.DATA_BINS) | set(protocol.PILOT_BINS))
    H_mag = np.abs(H[active_bins])
    H_db = 20 * np.log10(H_mag + 1e-10)

    median_db = np.median(H_db)

    # Dead bins: >15 dB below median
    dead_threshold = median_db - 15
    dead_bins = []
    for i, bin_idx in enumerate(active_bins):
        if H_db[i] < dead_threshold:
            freq = bin_idx * protocol.FREQ_RESOLUTION
            dead_bins.append((bin_idx, freq, H_db[i]))

    # SNR from channel flatness
    snr_est = 20 * np.log10(np.mean(H_mag) / (np.std(H_mag) + 1e-10))

    # Frequency response summary (band edges)
    n_bins = len(active_bins)
    low_band_db = np.mean(H_db[:n_bins // 4])
    mid_band_db = np.mean(H_db[n_bins // 4:3 * n_bins // 4])
    high_band_db = np.mean(H_db[3 * n_bins // 4:])

    return {
        'active_bins': active_bins,
        'H_db': H_db,
        'median_db': median_db,
        'dead_bins': dead_bins,
        'snr_est': snr_est,
        'low_band_db': low_band_db,
        'mid_band_db': mid_band_db,
        'high_band_db': high_band_db,
    }


def calibrate(input_source, device=None, duration=None):
    """Full calibration pipeline."""
    print("=" * 60)
    print("  OFDM Audio Modem - Calibration")
    print("=" * 60)
    print()

    # Step 1: Get audio signal
    if input_source:
        print(f"Reading WAV: {input_source}")
        signal, sr = audio_in.read_wav(input_source)
        print(f"  Samples: {len(signal):,} ({len(signal)/sr:.1f}s at {sr} Hz)")
    else:
        if duration is None:
            duration = 15
        print(f"Capturing audio for {duration}s...")
        signal = audio_in.capture_audio(duration, protocol.SAMPLE_RATE, device)
        sr = protocol.SAMPLE_RATE
        print(f"  Captured: {len(signal):,} samples ({len(signal)/sr:.1f}s)")
    print()

    # Step 2: Detect preamble
    preamble_start, freq_offset = ofdm_fast.detect_preamble(signal)
    if preamble_start is None:
        print("ERROR: No preamble detected!")
        print("  - Check that the calibration WAV is playing")
        print("  - Check cable connection and volume level")
        return False

    print(f"Preamble detected at {preamble_start/sr:.3f}s")

    # Step 3: Channel estimation
    H, data_start = ofdm_fast.estimate_channel(signal, preamble_start)

    # Step 4: Channel analysis
    ch = analyze_channel(H)
    print()
    print("--- Channel Response ---")
    print(f"  Low band  (300-5000 Hz):   {ch['low_band_db']:.1f} dB")
    print(f"  Mid band  (5000-15000 Hz): {ch['mid_band_db']:.1f} dB")
    print(f"  High band (15000-20000 Hz):{ch['high_band_db']:.1f} dB")
    print(f"  Channel flatness (SNR):     {ch['snr_est']:.1f} dB")

    if ch['dead_bins']:
        print(f"  Dead bins: {len(ch['dead_bins'])}", end="")
        for bin_idx, freq, db in ch['dead_bins'][:3]:
            print(f"  [{bin_idx}={freq:.0f}Hz {db:.0f}dB]", end="")
        if len(ch['dead_bins']) > 3:
            print(f" +{len(ch['dead_bins'])-3} more", end="")
        print()
    else:
        print("  Dead bins: none")
    print()

    # Step 5: Decode metadata
    sym_len = protocol.SYMBOL_SAMPLES
    remaining = len(signal) - data_start
    max_symbols = remaining // sym_len

    rs_cw_bits = protocol.RS_N * 8
    meta_bpc = protocol.PROFILES["safe"]["bits_per_carrier"]
    meta_bits_per_sym = protocol.NUM_DATA_BINS * meta_bpc
    meta_sym_count = (rs_cw_bits + meta_bits_per_sym - 1) // meta_bits_per_sym

    print("Decoding calibration metadata...")
    meta_phase_tracker = ofdm_fast.PhaseTracker()
    meta_bits = []
    for sym_idx in range(min(meta_sym_count, max_symbols)):
        sym_start = data_start + sym_idx * sym_len
        bits = ofdm_fast.demodulate_data_symbol(
            signal, sym_start, H, "safe", sym_idx,
            phase_tracker=meta_phase_tracker)
        meta_bits.extend(bits)

    meta_cw_bytes = bits_to_bytes(meta_bits[:rs_cw_bits])
    try:
        meta_msg, meta_nerr = fec_fast.rs_decode(meta_cw_bytes)
        if meta_nerr > 0:
            print(f"  Metadata RS: {meta_nerr} corrections")
    except ValueError:
        print("  ERROR: Metadata RS decode failed!")
        print("  The signal may be too noisy or not a calibration signal.")
        return False

    meta_frame = decode_frame(meta_msg)
    if not meta_frame or meta_frame['frame_id'] != 0xFFFF:
        print("  ERROR: Invalid metadata frame!")
        return False

    try:
        cal_meta = json.loads(meta_frame['payload'].decode('ascii').rstrip('\x00'))
    except Exception as e:
        print(f"  ERROR: Failed to parse metadata: {e}")
        return False

    if cal_meta.get('type') != 'calibration':
        print("  ERROR: Not a calibration signal (type={})".format(
            cal_meta.get('type', '?')))
        return False

    profiles = cal_meta['profiles']
    symbols_per_profile = cal_meta['symbols_per_profile']
    print(f"  Calibration signal OK: {len(profiles)} profiles")
    print()

    # Step 6: Decode each profile
    print("--- Per-Profile Results ---")
    print()
    print(f"  {'Profile':<10s}  {'Frames':>8s}  {'FEC corr':>9s}  {'Max/CW':>7s}  "
          f"{'Payload':>8s}  {'Status':>8s}")
    print("  " + "-" * 62)

    results = {}
    sym_idx_global = meta_sym_count
    depth = protocol.INTERLEAVE_DEPTH

    for pname in profiles:
        n_syms = symbols_per_profile[pname]

        # Demodulate with fresh PhaseTracker per profile
        phase_tracker = ofdm_fast.PhaseTracker()
        batch_bits = []
        for s in range(n_syms):
            if sym_idx_global >= max_symbols:
                break
            sym_start = data_start + sym_idx_global * sym_len
            bits = ofdm_fast.demodulate_data_symbol(
                signal, sym_start, H, pname, s,
                phase_tracker=phase_tracker)
            batch_bits.extend(bits)
            sym_idx_global += 1

        # Deinterleave + RS decode
        batch_bit_count = depth * rs_cw_bits
        if len(batch_bits) < batch_bit_count:
            batch_bits.extend([0] * (batch_bit_count - len(batch_bits)))

        interleaved_bytes = bits_to_bytes(batch_bits[:batch_bit_count])
        codewords = deinterleave_codewords(interleaved_bytes, depth)

        # RS decode each codeword and compare against known data
        frames_ok = 0
        frames_bad = 0
        total_corrections = 0
        max_corrections = 0
        byte_errors = 0
        payload_size = protocol.FRAME_PAYLOAD_MAX - protocol.FRAME_HEADER_SIZE

        for cw_idx in range(depth):
            rs_ok = False
            try:
                msg_bytes, nerr = fec_fast.rs_decode(codewords[cw_idx])
                total_corrections += nerr
                max_corrections = max(max_corrections, nerr)
                rs_ok = True
            except ValueError:
                pass

            # Generate expected payload
            seed = protocol.calibration_frame_seed(pname, cw_idx)
            expected_payload = protocol.generate_calibration_data(
                payload_size, seed=seed)

            if rs_ok:
                frame = decode_frame(msg_bytes)
                if frame and frame['magic'] == protocol.FRAME_MAGIC:
                    frames_ok += 1
                    # Compare payload
                    actual = frame['payload']
                    for k in range(min(len(actual), len(expected_payload))):
                        if actual[k] != expected_payload[k]:
                            byte_errors += 1
                else:
                    frames_bad += 1
            else:
                frames_bad += 1

        # Determine status
        rs_margin = protocol.RS_T - max_corrections  # how many more errors RS could handle
        if frames_ok == depth and byte_errors == 0:
            if rs_margin >= 8:
                status = "GOOD"
            elif rs_margin >= 4:
                status = "OK"
            else:
                status = "TIGHT"
        elif frames_ok > depth // 2:
            status = "WEAK"
        else:
            status = "FAIL"

        results[pname] = {
            'frames_ok': frames_ok,
            'frames_bad': frames_bad,
            'total_corrections': total_corrections,
            'max_corrections': max_corrections,
            'byte_errors': byte_errors,
            'rs_margin': rs_margin,
            'status': status,
        }

        marker = {'GOOD': '+', 'OK': '+', 'TIGHT': '~', 'WEAK': '!', 'FAIL': 'X'}
        print(f"  [{marker[status]}] {pname:<10s}  {frames_ok}/{depth:>5d}  "
              f"{total_corrections:>9d}  {max_corrections:>7d}  "
              f"{byte_errors:>8d}  {status:>8s}")

    # Step 7: Recommendation
    print()
    print("--- Recommendation ---")

    # Find fastest profile with GOOD or OK status
    recommended = None
    for pname in reversed(profiles):
        r = results[pname]
        if r['status'] in ('GOOD', 'OK'):
            recommended = pname
            break

    if recommended is None:
        # Fall back to TIGHT
        for pname in reversed(profiles):
            if results[pname]['status'] == 'TIGHT':
                recommended = pname
                break

    if recommended is None:
        for pname in profiles:
            if results[pname]['status'] == 'WEAK':
                recommended = pname
                print(f"  WARNING: Best available profile '{pname}' has frame losses!")
                break

    if recommended is None:
        print("  ERROR: All profiles failed!")
        print("  Check: cable, volume, audio device settings")
        print("  Try disabling Windows AGC (Sound > Input > Advanced)")
        return False

    desc = protocol.PROFILES[recommended]["description"]
    rate = protocol.effective_byterate(recommended)
    r = results[recommended]

    print(f"  Profile:  {recommended}")
    print(f"  Rate:     {desc}")
    print(f"  Margin:   RS can handle {r['rs_margin']} more errors/codeword")
    print()
    print(f"  Use this profile for transfers:")
    print(f"    Encoder: python -m encoder.encoder -i <path> -o signal.wav -p {recommended}")
    print(f"    Decoder: python -m decoder.decoder -i signal.wav -o ./received/ -p {recommended}")
    print()

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Analyze OFDM calibration signal")
    parser.add_argument('--input', '-i',
                        help='Input WAV file (omit for live capture)')
    parser.add_argument('--device', '-d', type=int,
                        help='Audio input device index')
    parser.add_argument('--duration', '-t', type=float, default=15,
                        help='Capture duration in seconds (default: 15)')
    parser.add_argument('--list-devices', action='store_true',
                        help='List audio input devices and exit')

    args = parser.parse_args()

    if args.list_devices:
        audio_in.list_input_devices()
        return

    calibrate(args.input, args.device, args.duration)


if __name__ == '__main__':
    main()
