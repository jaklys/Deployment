"""
OFDM Modulation + Pure Python FFT (Cooley-Tukey)

ZERO external dependencies - stdlib only.
Handles: FFT/IFFT, constellation mapping, subcarrier assembly,
cyclic prefix, Schmidl-Cox preamble, training symbols.
"""

import math
from . import protocol


# ─── Pre-computed twiddle factors ─────────────────────────────────────
# Cache twiddle factors for each FFT size to avoid repeated sin/cos calls.
# Key: N (FFT size), Value: list of (cos, sin) tuples for k=0..N//2-1

_twiddle_cache = {}


def _get_twiddles(n):
    """Get or compute twiddle factors W_N^k = exp(-j*2*pi*k/N) for k=0..N//2-1."""
    if n in _twiddle_cache:
        return _twiddle_cache[n]
    half = n // 2
    angle_step = -2.0 * math.pi / n
    twiddles = []
    for k in range(half):
        angle = angle_step * k
        twiddles.append((math.cos(angle), math.sin(angle)))
    _twiddle_cache[n] = twiddles
    return twiddles


def _bit_reverse(x, log2n):
    """Bit-reverse an integer x with log2n bits."""
    result = 0
    for _ in range(log2n):
        result = (result << 1) | (x & 1)
        x >>= 1
    return result


def fft(x):
    """
    Compute FFT of complex sequence x using iterative Cooley-Tukey.

    Input: list of complex numbers (or (real, imag) tuples)
    Output: list of complex numbers
    Length must be a power of 2.
    """
    n = len(x)
    if n == 1:
        return [complex(x[0]) if not isinstance(x[0], complex) else x[0]]

    log2n = int(math.log2(n))
    assert (1 << log2n) == n, f"FFT length must be power of 2, got {n}"

    # Convert to list of complex
    buf = [complex(0)] * n
    for i in range(n):
        ri = _bit_reverse(i, log2n)
        v = x[i]
        buf[ri] = complex(v) if not isinstance(v, complex) else v

    # Butterfly stages
    length = 2
    while length <= n:
        half = length // 2
        twiddles = _get_twiddles(length)
        step = n // length  # twiddle index step for this stage

        for start in range(0, n, length):
            for k in range(half):
                tw_re, tw_im = twiddles[k]
                u = buf[start + k]
                v = buf[start + k + half]
                # Twiddle multiply: tw * v
                tv = complex(tw_re * v.real - tw_im * v.imag,
                             tw_re * v.imag + tw_im * v.real)
                buf[start + k] = u + tv
                buf[start + k + half] = u - tv

        length *= 2

    return buf


def ifft(x):
    """
    Compute IFFT using conjugate trick: IFFT(x) = conj(FFT(conj(x))) / N.

    Input/Output: list of complex numbers.
    """
    n = len(x)
    # Conjugate input
    x_conj = [complex(v.real, -v.imag) for v in x]
    # Forward FFT
    result = fft(x_conj)
    # Conjugate and scale
    inv_n = 1.0 / n
    return [complex(v.real * inv_n, -v.imag * inv_n) for v in result]


# ─── Constellation mapping ────────────────────────────────────────────

def bits_to_symbols(bits, modulation):
    """
    Map a bit sequence to constellation symbols.

    Args:
        bits: list of 0/1 integers
        modulation: "bpsk", "qpsk", "16qam", or "64qam"

    Returns:
        list of complex constellation points
    """
    cmap = protocol.CONSTELLATION_MAPS[modulation]
    bpc = protocol.PROFILES[[p for p in protocol.PROFILES
                              if protocol.PROFILES[p]["modulation"] == modulation][0]]["bits_per_carrier"]

    symbols = []
    for i in range(0, len(bits), bpc):
        chunk = bits[i:i + bpc]
        # Pad if last chunk is short
        while len(chunk) < bpc:
            chunk.append(0)
        # Convert bit list to integer
        val = 0
        for b in chunk:
            val = (val << 1) | b
        symbols.append(cmap[val])

    return symbols


def map_bits_for_profile(bits, profile_name):
    """Map bits to constellation symbols using the specified profile."""
    mod = protocol.PROFILES[profile_name]["modulation"]
    return bits_to_symbols(bits, mod)


# ─── OFDM Symbol Assembly ─────────────────────────────────────────────

def build_ofdm_symbol(data_symbols, pilot_values):
    """
    Build one OFDM time-domain symbol from frequency-domain data.

    Args:
        data_symbols: list of complex values, one per DATA_BIN (len == NUM_DATA_BINS)
        pilot_values: list of complex values, one per PILOT_BIN (len == 10)

    Returns:
        list of float samples (real-valued) with cyclic prefix prepended.
        Length = SYMBOL_SAMPLES (1152)
    """
    n = protocol.FFT_SIZE
    freq = [complex(0)] * n

    # Place data on data subcarriers
    for i, bin_idx in enumerate(protocol.DATA_BINS):
        if i < len(data_symbols):
            freq[bin_idx] = data_symbols[i]

    # Place pilots
    for i, bin_idx in enumerate(protocol.PILOT_BINS):
        if i < len(pilot_values):
            freq[bin_idx] = pilot_values[i]

    # Enforce Hermitian symmetry for real output: X[N-k] = conj(X[k])
    freq[0] = complex(0)
    for k in range(1, n // 2):
        freq[n - k] = complex(freq[k].real, -freq[k].imag)
    freq[n // 2] = complex(freq[n // 2].real, 0)  # Nyquist bin must be real

    # IFFT to get time domain
    time_domain = ifft(freq)

    # Extract real parts (imaginary should be ~0 due to Hermitian symmetry)
    real_samples = [s.real for s in time_domain]

    # Add cyclic prefix (last CP_LENGTH samples prepended)
    cp = real_samples[-protocol.CP_LENGTH:]
    return cp + real_samples


def get_pilot_values(symbol_index):
    """
    Get pilot values for a given symbol index.
    Uses a known BPSK pattern derived from PN sequence.
    Symbol index varies the pattern for tracking purposes.
    """
    pn = protocol.generate_pn_sequence(len(protocol.PILOT_BINS),
                                        seed=(protocol.PN_SEED + symbol_index) & 0xFF)
    return [complex(v * protocol.PILOT_AMPLITUDE, 0) for v in pn]


# ─── Preamble Generation (Schmidl-Cox) ────────────────────────────────

def generate_preamble():
    """
    Generate Schmidl-Cox preamble (2 OFDM symbols).

    Symbol 1: Only even-indexed subcarriers in the usable band carry
              a known PN sequence. Odd subcarriers are zero.
              -> Time domain has two identical halves (periodicity N/2)
              -> Receiver detects via autocorrelation.

    Symbol 2: All subcarriers (data + pilot positions) carry a known
              PN sequence. Used for fine timing and frequency offset estimation.

    Returns:
        list of float samples (2 symbols worth, with CP)
    """
    n = protocol.FFT_SIZE
    samples = []

    # ── Symbol 1: even bins only ──
    # Collect all active bins (data + pilot) in the usable band
    all_active_bins = sorted(set(protocol.DATA_BINS) | set(protocol.PILOT_BINS))
    even_bins = [b for b in all_active_bins if b % 2 == 0]

    pn1 = protocol.generate_pn_sequence(len(even_bins), seed=0xA1)

    freq1 = [complex(0)] * n
    for i, bin_idx in enumerate(even_bins):
        freq1[bin_idx] = complex(pn1[i], 0)

    # Hermitian symmetry
    freq1[0] = complex(0)
    for k in range(1, n // 2):
        freq1[n - k] = complex(freq1[k].real, -freq1[k].imag)
    freq1[n // 2] = complex(freq1[n // 2].real, 0)

    td1 = ifft(freq1)
    real1 = [s.real for s in td1]
    cp1 = real1[-protocol.CP_LENGTH:]
    samples.extend(cp1 + real1)

    # ── Symbol 2: all bins ──
    pn2 = protocol.generate_pn_sequence(len(all_active_bins), seed=0xB2)

    freq2 = [complex(0)] * n
    for i, bin_idx in enumerate(all_active_bins):
        freq2[bin_idx] = complex(pn2[i], 0)

    # Hermitian symmetry
    freq2[0] = complex(0)
    for k in range(1, n // 2):
        freq2[n - k] = complex(freq2[k].real, -freq2[k].imag)
    freq2[n // 2] = complex(freq2[n // 2].real, 0)

    td2 = ifft(freq2)
    real2 = [s.real for s in td2]
    cp2 = real2[-protocol.CP_LENGTH:]
    samples.extend(cp2 + real2)

    return samples


def generate_training_symbols():
    """
    Generate training symbols (TRAINING_SYMBOLS count).

    All data + pilot subcarriers carry known BPSK values.
    The receiver uses these to estimate channel H(k) per subcarrier.

    Returns:
        list of float samples, and
        list of frequency-domain vectors (for decoder reference)
    """
    n = protocol.FFT_SIZE
    all_active_bins = sorted(set(protocol.DATA_BINS) | set(protocol.PILOT_BINS))

    samples = []
    freq_refs = []  # store freq domain for decoder's channel estimation

    for sym_idx in range(protocol.TRAINING_SYMBOLS):
        pn = protocol.generate_pn_sequence(len(all_active_bins),
                                            seed=(0xC0 + sym_idx) & 0xFF)

        freq = [complex(0)] * n
        for i, bin_idx in enumerate(all_active_bins):
            freq[bin_idx] = complex(pn[i], 0)

        # Hermitian symmetry
        freq[0] = complex(0)
        for k in range(1, n // 2):
            freq[n - k] = complex(freq[k].real, -freq[k].imag)
        freq[n // 2] = complex(freq[n // 2].real, 0)

        freq_refs.append(list(freq))

        td = ifft(freq)
        real_td = [s.real for s in td]
        cp = real_td[-protocol.CP_LENGTH:]
        samples.extend(cp + real_td)

    return samples, freq_refs


def generate_retrain_symbols(retrain_index=0):
    """
    Generate periodic re-training symbols (RETRAIN_SYMBOLS count).

    Same structure as initial training symbols but with a seed offset
    based on retrain_index, so the decoder can verify alignment.

    Args:
        retrain_index: which re-training block this is (0-based)

    Returns:
        list of float samples
    """
    n = protocol.FFT_SIZE
    all_active_bins = sorted(set(protocol.DATA_BINS) | set(protocol.PILOT_BINS))

    samples = []

    for sym_idx in range(protocol.RETRAIN_SYMBOLS):
        # Use same seed formula as initial training (0xC0 + sym_idx)
        # so the decoder can use the same channel estimation code
        pn = protocol.generate_pn_sequence(len(all_active_bins),
                                            seed=(0xC0 + sym_idx) & 0xFF)

        freq = [complex(0)] * n
        for i, bin_idx in enumerate(all_active_bins):
            freq[bin_idx] = complex(pn[i], 0)

        # Hermitian symmetry
        freq[0] = complex(0)
        for k in range(1, n // 2):
            freq[n - k] = complex(freq[k].real, -freq[k].imag)
        freq[n // 2] = complex(freq[n // 2].real, 0)

        td = ifft(freq)
        real_td = [s.real for s in td]
        cp = real_td[-protocol.CP_LENGTH:]
        samples.extend(cp + real_td)

    return samples


# ─── Normalization ─────────────────────────────────────────────────────

def normalize_samples(samples, peak=0.9):
    """
    Normalize audio samples to [-peak, peak] range.

    Args:
        samples: list of float
        peak: maximum absolute value (0.9 leaves headroom to prevent clipping)

    Returns:
        list of float, normalized
    """
    max_val = max(abs(s) for s in samples) if samples else 1.0
    if max_val == 0:
        return samples
    scale = peak / max_val
    return [s * scale for s in samples]
