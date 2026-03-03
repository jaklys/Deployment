"""
OFDM Audio Modem - Protocol Constants & Configuration

This module is the single source of truth for all protocol parameters.
ZERO external dependencies - stdlib only.
An identical copy lives in decoder/protocol.py.
"""

import math

# ─── Signal parameters ────────────────────────────────────────────────
SAMPLE_RATE = 44100          # Hz
FFT_SIZE = 1024              # samples per OFDM symbol (power of 2)
CP_LENGTH = 128              # cyclic prefix length (1/8 of FFT_SIZE)
SYMBOL_SAMPLES = FFT_SIZE + CP_LENGTH  # 1152 total samples per symbol
SYMBOLS_PER_SEC = SAMPLE_RATE / SYMBOL_SAMPLES  # ~38.28

# ─── Frequency allocation ─────────────────────────────────────────────
FREQ_RESOLUTION = SAMPLE_RATE / FFT_SIZE  # ~43.07 Hz per bin
BIN_LOW = 7                  # ~301 Hz  (first usable bin)
BIN_HIGH = 464               # ~19986 Hz (last usable bin)

# Pilot bins - 10 evenly spaced across the band for AGC tracking
# and channel estimation. Known BPSK values on these bins.
PILOT_BINS = [10, 60, 110, 160, 210, 260, 310, 360, 410, 460]
PILOT_AMPLITUDE = 1.0

# Data bins - all usable bins except pilots
# Only bins in [BIN_LOW, BIN_HIGH] range, excluding pilot positions.
# Due to Hermitian symmetry, only bins 1..FFT_SIZE//2-1 carry independent data.
_pilot_set = set(PILOT_BINS)
DATA_BINS = [b for b in range(BIN_LOW, BIN_HIGH + 1) if b not in _pilot_set]
NUM_DATA_BINS = len(DATA_BINS)  # ~448

# ─── Modulation profiles ──────────────────────────────────────────────
PROFILES = {
    "safe": {
        "modulation": "bpsk",
        "bits_per_carrier": 1,
        "description": "BPSK ~1.9 kB/s after FEC - most reliable",
    },
    "standard": {
        "modulation": "qpsk",
        "bits_per_carrier": 2,
        "description": "QPSK ~3.8 kB/s after FEC - recommended default",
    },
    "fast": {
        "modulation": "16qam",
        "bits_per_carrier": 4,
        "description": "16-QAM ~7.5 kB/s after FEC - good cable needed",
    },
    "turbo": {
        "modulation": "64qam",
        "bits_per_carrier": 6,
        "description": "64-QAM ~11.3 kB/s after FEC - excellent SNR only",
    },
}

DEFAULT_PROFILE = "standard"

# ─── FEC parameters (Reed-Solomon) ────────────────────────────────────
RS_N = 255                   # codeword length (bytes)
RS_K = 223                   # message length (bytes)
RS_2T = RS_N - RS_K          # 32 parity bytes
RS_T = RS_2T // 2            # 16 correctable symbol errors
RS_PRIM_POLY = 0x11D         # GF(256) primitive polynomial: x^8+x^4+x^3+x^2+1
RS_FCR = 0                   # first consecutive root

# ─── Frame format ─────────────────────────────────────────────────────
FRAME_MAGIC = 0xAF0D         # 2 bytes - "Audio Freq OFDM Data"
FRAME_HEADER_SIZE = 8        # bytes: [magic 2B][frame_id 2B][total_frames 2B][payload_len 2B]
FRAME_PAYLOAD_MAX = RS_K     # 223 bytes - matches RS message block

# ─── Interleaver ──────────────────────────────────────────────────────
INTERLEAVE_DEPTH = 8         # number of RS codewords interleaved together

# ─── Preamble / Training ─────────────────────────────────────────────
PREAMBLE_SYMBOLS = 2         # Schmidl-Cox preamble (2 symbols)
TRAINING_SYMBOLS = 4         # known pattern for channel estimation
SILENCE_SAMPLES = int(SAMPLE_RATE * 0.5)  # 0.5s silence padding

# Pseudo-random seed for generating known sequences
PN_SEED = 0x42               # fixed seed for reproducible PN sequences

# ─── Constellation maps (Gray-coded) ──────────────────────────────────

def _gray(n):
    """Gray code encode."""
    return n ^ (n >> 1)


# BPSK: 1 bit per symbol
BPSK_MAP = {0: complex(-1.0, 0.0), 1: complex(1.0, 0.0)}

# QPSK: 2 bits per symbol, normalized to unit average power
_qpsk_scale = 1.0 / math.sqrt(2.0)
QPSK_MAP = {}
for _i in range(2):
    for _q in range(2):
        _bits = (_gray(_i) << 1) | _gray(_q)
        _re = (2 * _i - 1) * _qpsk_scale
        _im = (2 * _q - 1) * _qpsk_scale
        QPSK_MAP[_bits] = complex(_re, _im)

# 16-QAM: 4 bits per symbol, Gray-coded, normalized
_qam16_scale = 1.0 / math.sqrt(10.0)
QAM16_MAP = {}
for _i in range(4):
    for _q in range(4):
        _bits = (_gray(_i) << 2) | _gray(_q)
        _re = (2 * _i - 3) * _qam16_scale
        _im = (2 * _q - 3) * _qam16_scale
        QAM16_MAP[_bits] = complex(_re, _im)

# 64-QAM: 6 bits per symbol, Gray-coded, normalized
_qam64_scale = 1.0 / math.sqrt(42.0)
QAM64_MAP = {}
for _i in range(8):
    for _q in range(8):
        _bits = (_gray(_i) << 3) | _gray(_q)
        _re = (2 * _i - 7) * _qam64_scale
        _im = (2 * _q - 7) * _qam64_scale
        QAM64_MAP[_bits] = complex(_re, _im)

# Map profile modulation name to constellation
CONSTELLATION_MAPS = {
    "bpsk": BPSK_MAP,
    "qpsk": QPSK_MAP,
    "16qam": QAM16_MAP,
    "64qam": QAM64_MAP,
}

# Reverse maps for demodulation (constellation point -> bits)
# Built as: for each map, create dict of complex->int
CONSTELLATION_POINTS = {}
for _mod_name, _cmap in CONSTELLATION_MAPS.items():
    CONSTELLATION_POINTS[_mod_name] = {v: k for k, v in _cmap.items()}


def bits_per_symbol(profile_name):
    """Total data bits per OFDM symbol for a given profile."""
    return NUM_DATA_BINS * PROFILES[profile_name]["bits_per_carrier"]


def bytes_per_symbol(profile_name):
    """Total data bytes per OFDM symbol (floored)."""
    return bits_per_symbol(profile_name) // 8


def raw_bitrate(profile_name):
    """Raw data rate in bits/sec (before FEC overhead)."""
    return bits_per_symbol(profile_name) * SYMBOLS_PER_SEC


def effective_byterate(profile_name):
    """Effective byte rate after FEC overhead."""
    fec_efficiency = RS_K / RS_N  # ~0.875
    return raw_bitrate(profile_name) * fec_efficiency / 8


def estimate_duration(data_bytes, profile_name):
    """Estimate total WAV duration in seconds for given data size."""
    rate = effective_byterate(profile_name)
    if rate <= 0:
        return float('inf')
    data_duration = data_bytes / rate
    overhead = (SILENCE_SAMPLES * 2 +
                (PREAMBLE_SYMBOLS + TRAINING_SYMBOLS) * SYMBOL_SAMPLES) / SAMPLE_RATE
    return data_duration + overhead


# ─── PN Sequence Generator ────────────────────────────────────────────

def generate_pn_sequence(length, seed=PN_SEED):
    """Generate a pseudo-random binary sequence (+1/-1) using a simple LFSR.

    Deterministic given the seed - both encoder and decoder produce
    the identical sequence.
    """
    state = seed if seed != 0 else 1
    seq = []
    for _ in range(length):
        bit = state & 1
        # LFSR feedback: taps at bits 7, 5, 4, 3 (maximal length for 8-bit)
        feedback = ((state >> 7) ^ (state >> 5) ^ (state >> 4) ^ (state >> 3)) & 1
        state = ((state << 1) | feedback) & 0xFF
        seq.append(1.0 if bit else -1.0)
    return seq


# Clean up module namespace
del _i, _q, _bits, _re, _im, _qpsk_scale, _qam16_scale, _qam64_scale
del _pilot_set, _mod_name, _cmap
