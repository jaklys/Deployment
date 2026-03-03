"""
OFDM Audio Encoder - Main CLI

Encodes a file or folder into an OFDM-modulated WAV file for
transfer over 3.5mm audio cable.

ZERO external dependencies - stdlib only.

Usage:
    python -m encoder.encoder --input <path> --output signal.wav --profile standard
    python -m encoder.encoder --input <path> --output signal.wav --profile fast --play
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
import zlib

from . import protocol
from . import ofdm
from . import audio_out
from . import fec


def compress_path(input_path):
    """
    Compress a file or folder into tar.gz in memory.

    Returns:
        (compressed_bytes, metadata_dict)
    """
    input_path = os.path.abspath(input_path)

    if os.path.isfile(input_path):
        base_name = os.path.basename(input_path)
        is_dir = False
    elif os.path.isdir(input_path):
        base_name = os.path.basename(input_path.rstrip(os.sep))
        is_dir = True
    else:
        raise FileNotFoundError(f"Input path not found: {input_path}")

    # Count files and total size
    file_count = 0
    total_size = 0
    if is_dir:
        for root, dirs, files in os.walk(input_path):
            for f in files:
                fp = os.path.join(root, f)
                file_count += 1
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
    else:
        file_count = 1
        total_size = os.path.getsize(input_path)

    # Create tar.gz in memory
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz', compresslevel=9) as tf:
        tf.add(input_path, arcname=base_name)
    compressed = buf.getvalue()

    # SHA256 of compressed data
    sha256 = hashlib.sha256(compressed).hexdigest()

    metadata = {
        "name": base_name,
        "is_dir": is_dir,
        "file_count": file_count,
        "original_size": total_size,
        "compressed_size": len(compressed),
        "sha256": sha256,
    }

    return compressed, metadata


def data_to_bits(data):
    """Convert bytes to list of 0/1 integers (MSB first)."""
    bits = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def bits_to_data(bits):
    """Convert list of 0/1 integers back to bytes (MSB first)."""
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


def build_frames(data, metadata):
    """
    Split data into protocol frames.

    Each frame: [magic 2B][frame_id 2B][total_frames 2B][payload_len 2B][payload ≤223B]
    Total frame size = 8 + payload_len ≤ 231 bytes.

    For RS encoding later, the frame (header + payload) is padded to RS_K (223) bytes
    and then RS-encoded to RS_N (255) bytes. For now (without FEC), we just use raw frames.

    Returns:
        list of bytes objects (each is one frame, padded to RS_K bytes)
    """
    payload_max = protocol.FRAME_PAYLOAD_MAX - protocol.FRAME_HEADER_SIZE  # 215 bytes
    total_frames = (len(data) + payload_max - 1) // payload_max

    frames = []
    offset = 0
    frame_id = 0

    while offset < len(data) or frame_id == 0:
        chunk = data[offset:offset + payload_max]
        payload_len = len(chunk)

        # Build frame header
        header = struct.pack('>HHHH',
                             protocol.FRAME_MAGIC,
                             frame_id,
                             total_frames,
                             payload_len)

        frame_data = header + chunk

        # Pad to RS_K bytes (223) for alignment with RS encoding
        if len(frame_data) < protocol.RS_K:
            frame_data += b'\x00' * (protocol.RS_K - len(frame_data))

        frames.append(frame_data)
        offset += payload_max
        frame_id += 1

    return frames


def build_metadata_frame(metadata):
    """
    Build a special metadata frame (frame_id = 0xFFFF) containing JSON metadata.
    Encoded with BPSK for maximum reliability.

    Returns:
        bytes (padded to RS_K)
    """
    meta_json = json.dumps(metadata, ensure_ascii=True).encode('ascii')

    header = struct.pack('>HHHH',
                         protocol.FRAME_MAGIC,
                         0xFFFF,  # special metadata frame ID
                         0,       # not used
                         len(meta_json))

    frame_data = header + meta_json
    if len(frame_data) < protocol.RS_K:
        frame_data += b'\x00' * (protocol.RS_K - len(frame_data))
    elif len(frame_data) > protocol.RS_K:
        raise ValueError(f"Metadata too large for single frame: {len(frame_data)} > {protocol.RS_K}")

    return frame_data


def interleave_codewords(codewords):
    """
    Block interleave a group of RS codewords.

    Writes codeword bytes row-by-row into a matrix, reads column-by-column.
    This spreads consecutive byte errors across different codewords,
    allowing RS to correct burst errors that span multiple OFDM symbols.

    Args:
        codewords: list of bytes objects (each RS_N = 255 bytes)

    Returns:
        bytes - interleaved data (same total length)
    """
    depth = len(codewords)
    width = protocol.RS_N  # 255

    # Pad all codewords to same length
    padded = []
    for cw in codewords:
        if len(cw) < width:
            cw = cw + b'\x00' * (width - len(cw))
        padded.append(cw)

    # Read column-by-column
    result = bytearray()
    for col in range(width):
        for row in range(depth):
            result.append(padded[row][col])

    return bytes(result)


def bits_to_ofdm_symbols(bits, profile_name, sym_offset=0):
    """
    Convert a bit stream to OFDM time-domain samples.

    Args:
        bits: list of 0/1 integers
        profile_name: modulation profile
        sym_offset: starting symbol index for pilot pattern

    Returns:
        (samples, sym_count)
    """
    bpc = protocol.PROFILES[profile_name]["bits_per_carrier"]
    bits_per_sym = protocol.NUM_DATA_BINS * bpc

    samples = []
    sym_count = 0

    for bit_offset in range(0, len(bits), bits_per_sym):
        bit_chunk = bits[bit_offset:bit_offset + bits_per_sym]
        while len(bit_chunk) < bits_per_sym:
            bit_chunk.append(0)

        data_symbols = ofdm.map_bits_for_profile(bit_chunk, profile_name)
        pilot_vals = ofdm.get_pilot_values(sym_offset + sym_count)
        sym_samples = ofdm.build_ofdm_symbol(data_symbols, pilot_vals)
        samples.extend(sym_samples)
        sym_count += 1

    return samples, sym_count


def encode_frame_to_symbols(frame_bytes, profile_name, sym_offset=0):
    """
    Encode a single frame (RS_K bytes) into OFDM symbols.
    Applies RS FEC before modulation. No interleaving.

    Returns:
        (samples, sym_count)
    """
    rs_codeword = fec.rs_encode(frame_bytes)
    bits = data_to_bits(rs_codeword)
    return bits_to_ofdm_symbols(bits, profile_name, sym_offset)


def encode_frames_interleaved(frames, profile_name, sym_offset=0):
    """
    Encode a batch of frames with RS FEC + interleaving.

    Frames are RS-encoded, interleaved at byte level, then modulated.

    Args:
        frames: list of frame bytes (each RS_K = 223 bytes)
        profile_name: modulation profile
        sym_offset: starting symbol index

    Returns:
        (samples, sym_count)
    """
    # RS encode each frame
    codewords = [fec.rs_encode(f) for f in frames]

    # Interleave
    interleaved = interleave_codewords(codewords)

    # Convert to bits and modulate
    bits = data_to_bits(interleaved)
    return bits_to_ofdm_symbols(bits, profile_name, sym_offset)


def encode(input_path, output_wav, profile_name="standard", play=False):
    """
    Full encoding pipeline: file/folder -> OFDM WAV.

    Args:
        input_path: path to file or folder to encode
        output_wav: output WAV file path
        profile_name: modulation profile name
        play: if True, play the WAV after saving
    """
    profile = protocol.PROFILES[profile_name]
    print(f"Profile: {profile_name} ({profile['description']})")
    print()

    # Step 1: Compress
    print("Compressing input...")
    t0 = time.time()
    compressed, metadata = compress_path(input_path)
    t_compress = time.time() - t0

    print(f"  Source: {metadata['name']} ({'directory' if metadata['is_dir'] else 'file'})")
    print(f"  Files: {metadata['file_count']}")
    print(f"  Original size: {metadata['original_size']:,} bytes")
    print(f"  Compressed: {metadata['compressed_size']:,} bytes "
          f"({metadata['compressed_size']/max(metadata['original_size'],1)*100:.1f}%)")
    print(f"  SHA256: {metadata['sha256'][:16]}...")
    print(f"  Compression time: {t_compress:.1f}s")
    print()

    # Step 2: Build frames
    print("Building frames...")
    meta_frame = build_metadata_frame(metadata)
    data_frames = build_frames(compressed, metadata)
    total_frames = 1 + len(data_frames)  # metadata + data
    print(f"  Total frames: {total_frames} (1 metadata + {len(data_frames)} data)")
    print()

    # Step 3: Estimate duration
    est_duration = protocol.estimate_duration(len(compressed), profile_name)
    print(f"  Estimated WAV duration: {est_duration:.1f}s ({est_duration/60:.1f} min)")
    print()

    # Step 4: Generate audio signal
    print("Generating OFDM signal...")
    t0 = time.time()

    all_samples = []

    # Leading silence
    all_samples.extend([0.0] * protocol.SILENCE_SAMPLES)

    # Preamble
    preamble = ofdm.generate_preamble()
    all_samples.extend(preamble)

    # Training symbols
    training, _training_freq_refs = ofdm.generate_training_symbols()
    all_samples.extend(training)

    # Metadata frame (always BPSK, no interleaving - single frame)
    meta_samples, meta_sym_count = encode_frame_to_symbols(meta_frame, "safe")
    all_samples.extend(meta_samples)

    # Data frames - batched with interleaving
    total_data_syms = 0
    depth = protocol.INTERLEAVE_DEPTH
    num_batches = (len(data_frames) + depth - 1) // depth

    for batch_idx in range(num_batches):
        batch_start = batch_idx * depth
        batch_end = min(batch_start + depth, len(data_frames))
        batch = data_frames[batch_start:batch_end]

        # Pad last batch to full depth with zero frames
        while len(batch) < depth:
            batch.append(b'\x00' * protocol.RS_K)

        batch_samples, sym_count = encode_frames_interleaved(
            batch, profile_name, sym_offset=total_data_syms)
        all_samples.extend(batch_samples)
        total_data_syms += sym_count

        frames_done = min(batch_end, len(data_frames))
        audio_out.print_progress(frames_done, len(data_frames), "Encoding")

    # Trailing silence
    all_samples.extend([0.0] * protocol.SILENCE_SAMPLES)

    t_encode = time.time() - t0

    # Step 5: Normalize
    all_samples = ofdm.normalize_samples(all_samples)

    # Step 6: Write WAV
    print(f"\nWriting WAV: {output_wav}")
    audio_out.write_wav(output_wav, all_samples, protocol.SAMPLE_RATE)

    wav_duration = len(all_samples) / protocol.SAMPLE_RATE
    wav_size = os.path.getsize(output_wav)

    print()
    print("=== Encoding Complete ===")
    print(f"  WAV file: {output_wav}")
    print(f"  WAV size: {wav_size:,} bytes ({wav_size/1024/1024:.1f} MB)")
    print(f"  WAV duration: {wav_duration:.1f}s ({wav_duration/60:.1f} min)")
    print(f"  OFDM symbols: {meta_sym_count} (meta) + {total_data_syms} (data)")
    print(f"  Encoding time: {t_encode:.1f}s")
    actual_rate = metadata['compressed_size'] / wav_duration if wav_duration > 0 else 0
    print(f"  Effective data rate: {actual_rate:.0f} B/s ({actual_rate/1024:.1f} kB/s)")
    print()

    # Step 7: Play if requested
    if play:
        print("Playing WAV...")
        audio_out.play_wav(output_wav)
        print("Playback complete.")


def main():
    parser = argparse.ArgumentParser(
        description="OFDM Audio Encoder - Encode files to audio signal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Profiles:
  safe     - BPSK  ~1.9 kB/s  Most reliable
  standard - QPSK  ~3.8 kB/s  Recommended default
  fast     - 16QAM ~7.5 kB/s  Good cable needed
  turbo    - 64QAM ~11 kB/s   Excellent SNR only

Examples:
  python -m encoder.encoder --input myproject/ --output signal.wav
  python -m encoder.encoder --input data.zip --output signal.wav --profile fast --play
""")
    parser.add_argument('--input', '-i', required=True,
                        help='Input file or folder to encode')
    parser.add_argument('--output', '-o', default='signal.wav',
                        help='Output WAV file (default: signal.wav)')
    parser.add_argument('--profile', '-p', default=protocol.DEFAULT_PROFILE,
                        choices=list(protocol.PROFILES.keys()),
                        help=f'Modulation profile (default: {protocol.DEFAULT_PROFILE})')
    parser.add_argument('--play', action='store_true',
                        help='Play WAV file after encoding')

    args = parser.parse_args()
    encode(args.input, args.output, args.profile, args.play)


if __name__ == '__main__':
    main()
