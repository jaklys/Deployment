"""
OFDM Audio Modem - Calibration Signal Generator

Generates a WAV containing known data encoded with all profiles
(safe, standard, fast, turbo) sequentially. Play this on the source
PC; the decoder analyzes the recording to determine channel quality
and recommend the best profile for actual transfers.

ZERO external dependencies - stdlib only.

Usage:
    python -m encoder.calibration --output calibration.wav
    python -m encoder.calibration --output calibration.wav --play
"""

import argparse
import json
import os
import struct
import sys
import time

from . import protocol
from . import ofdm
from . import audio_out
from . import fec


def generate_known_frames(profile_name):
    """Generate one interleave batch (8 frames) of known calibration data."""
    depth = protocol.INTERLEAVE_DEPTH
    payload_size = protocol.FRAME_PAYLOAD_MAX - protocol.FRAME_HEADER_SIZE

    frames = []
    for i in range(depth):
        seed = protocol.calibration_frame_seed(profile_name, i)
        payload = protocol.generate_calibration_data(payload_size, seed=seed)

        header = struct.pack('>HHHH',
                             protocol.FRAME_MAGIC,
                             i, depth, len(payload))

        frame_data = header + payload
        if len(frame_data) < protocol.RS_K:
            frame_data += b'\x00' * (protocol.RS_K - len(frame_data))

        frames.append(frame_data)

    return frames


def data_to_bits(data):
    """Convert bytes to list of 0/1 integers (MSB first)."""
    bits = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def interleave_codewords(codewords):
    """Block interleave RS codewords (row write, column read)."""
    depth = len(codewords)
    width = protocol.RS_N
    padded = []
    for cw in codewords:
        if len(cw) < width:
            cw = cw + b'\x00' * (width - len(cw))
        padded.append(cw)
    result = bytearray()
    for col in range(width):
        for row in range(depth):
            result.append(padded[row][col])
    return bytes(result)


def encode_calibration(output_wav, play=False):
    """Generate calibration WAV with all profiles."""
    profiles = protocol.CALIBRATION_PROFILES

    print("=== OFDM Calibration Signal Generator ===")
    print()

    # Compute symbols per profile
    symbols_per_profile = {}
    for pname in profiles:
        bpc = protocol.PROFILES[pname]["bits_per_carrier"]
        bits_per_sym = protocol.NUM_DATA_BINS * bpc
        batch_bits = protocol.INTERLEAVE_DEPTH * protocol.RS_N * 8
        syms = (batch_bits + bits_per_sym - 1) // bits_per_sym
        symbols_per_profile[pname] = syms
        desc = protocol.PROFILES[pname]["description"]
        print(f"  {pname:10s}: {syms:3d} symbols  ({desc})")
    print()

    total_data_syms = sum(symbols_per_profile.values())

    # Build calibration metadata
    cal_metadata = {
        "type": "calibration",
        "profiles": profiles,
        "symbols_per_profile": symbols_per_profile,
    }

    meta_json = json.dumps(cal_metadata, ensure_ascii=True).encode('ascii')
    meta_header = struct.pack('>HHHH',
                              protocol.FRAME_MAGIC,
                              0xFFFF, 0, len(meta_json))
    meta_frame = meta_header + meta_json
    if len(meta_frame) < protocol.RS_K:
        meta_frame += b'\x00' * (protocol.RS_K - len(meta_frame))

    print("Generating signal...")
    t0 = time.time()

    all_samples = []

    # Leading silence
    all_samples.extend([0.0] * protocol.SILENCE_SAMPLES)

    # Preamble + Training
    all_samples.extend(ofdm.generate_preamble())
    training, _ = ofdm.generate_training_symbols()
    all_samples.extend(training)

    # Metadata (BPSK, single RS codeword)
    meta_cw = fec.rs_encode(meta_frame)
    meta_bits = data_to_bits(meta_cw)
    bpc_safe = protocol.PROFILES["safe"]["bits_per_carrier"]
    bits_per_sym_safe = protocol.NUM_DATA_BINS * bpc_safe

    meta_sym_count = 0
    for bit_offset in range(0, len(meta_bits), bits_per_sym_safe):
        bit_chunk = meta_bits[bit_offset:bit_offset + bits_per_sym_safe]
        while len(bit_chunk) < bits_per_sym_safe:
            bit_chunk.append(0)
        data_symbols = ofdm.map_bits_for_profile(bit_chunk, "safe")
        pilot_vals = ofdm.get_pilot_values(meta_sym_count)
        sym_samples = ofdm.build_ofdm_symbol(data_symbols, pilot_vals)
        all_samples.extend(sym_samples)
        meta_sym_count += 1

    # Data for each profile (1 interleave batch each, pilot index resets per profile)
    for pname in profiles:
        frames = generate_known_frames(pname)
        codewords = [fec.rs_encode(f) for f in frames]
        interleaved = interleave_codewords(codewords)
        bits = data_to_bits(interleaved)

        bpc = protocol.PROFILES[pname]["bits_per_carrier"]
        bits_per_sym = protocol.NUM_DATA_BINS * bpc

        sym_count = 0
        for bit_offset in range(0, len(bits), bits_per_sym):
            bit_chunk = bits[bit_offset:bit_offset + bits_per_sym]
            while len(bit_chunk) < bits_per_sym:
                bit_chunk.append(0)
            data_symbols = ofdm.map_bits_for_profile(bit_chunk, pname)
            pilot_vals = ofdm.get_pilot_values(sym_count)  # reset per profile
            sym_samples = ofdm.build_ofdm_symbol(data_symbols, pilot_vals)
            all_samples.extend(sym_samples)
            sym_count += 1

        print(f"  {pname}: {sym_count} symbols")

    # Trailing silence
    all_samples.extend([0.0] * protocol.SILENCE_SAMPLES)

    t_encode = time.time() - t0

    # Normalize and write
    all_samples = ofdm.normalize_samples(all_samples)
    audio_out.write_wav(output_wav, all_samples, protocol.SAMPLE_RATE)

    wav_duration = len(all_samples) / protocol.SAMPLE_RATE
    wav_size = os.path.getsize(output_wav)

    print()
    print("=== Calibration WAV Ready ===")
    print(f"  File: {output_wav}")
    print(f"  Size: {wav_size:,} bytes ({wav_size/1024:.1f} KB)")
    print(f"  Duration: {wav_duration:.1f}s")
    print(f"  Encoding time: {t_encode:.1f}s")
    print(f"  Metadata: {meta_sym_count} BPSK symbols")
    print(f"  Data: {total_data_syms} symbols across {len(profiles)} profiles")
    print()
    print("Play this WAV on the source PC while running:")
    print("  python -m decoder.calibration --duration <seconds>")

    if play:
        print("\nPlaying...")
        audio_out.play_wav(output_wav)


def main():
    parser = argparse.ArgumentParser(
        description="Generate OFDM calibration signal")
    parser.add_argument('--output', '-o', default='calibration.wav',
                        help='Output WAV file (default: calibration.wav)')
    parser.add_argument('--play', action='store_true',
                        help='Play WAV after generating')

    args = parser.parse_args()
    encode_calibration(args.output, args.play)


if __name__ == '__main__':
    main()
