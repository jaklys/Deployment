"""
OFDM Demodulation with numpy/scipy.

Handles: Schmidl-Cox preamble detection, channel estimation,
zero-forcing equalization, pilot-based AGC compensation,
constellation demapping.
"""

import numpy as np
from . import protocol


# ─── Preamble Detection (Schmidl-Cox) ─────────────────────────────────

def detect_preamble(signal, threshold=0.7):
    """
    Detect Schmidl-Cox preamble in the signal.

    The first preamble symbol has data only on even subcarriers,
    so the time domain has two identical halves of length L = FFT_SIZE//2.
    We detect this via sliding-window autocorrelation.

    Args:
        signal: numpy float32 array of audio samples
        threshold: detection threshold for Schmidl-Cox metric (0-1)

    Returns:
        (preamble_start_sample, freq_offset_hz) or (None, None) if not found
    """
    n = protocol.FFT_SIZE
    L = n // 2  # half-symbol length
    cp = protocol.CP_LENGTH

    if len(signal) < 2 * n + cp:
        return None, None

    sig = signal.astype(np.float64)

    # Compute sliding autocorrelation P[n] = sum(conj(r[n+m]) * r[n+m+L], m=0..L-1)
    # For real signal, conj(r) = r, so P[n] = sum(r[n+m] * r[n+m+L])
    max_idx = len(sig) - 2 * L
    if max_idx <= 0:
        return None, None

    # Vectorized computation using cumulative sum
    product = sig[:-L] * sig[L:]
    energy = sig[L:] ** 2

    # Sliding window sums via cumsum
    cum_prod = np.cumsum(product)
    cum_energy = np.cumsum(energy)

    P = np.zeros(max_idx)
    R = np.zeros(max_idx)

    P[0] = cum_prod[L - 1]
    R[0] = cum_energy[L - 1]

    valid_len = min(max_idx, len(cum_prod) - L)
    if valid_len > 1:
        P[1:valid_len] = cum_prod[L:L + valid_len - 1] - cum_prod[:valid_len - 1]
        R[1:valid_len] = cum_energy[L:L + valid_len - 1] - cum_energy[:valid_len - 1]

    # Schmidl-Cox metric M[n] = |P[n]|^2 / R[n]^2
    R_sq = R ** 2
    R_sq[R_sq < 1e-20] = 1e-20  # avoid division by zero
    M = P ** 2 / R_sq

    # Gate: ignore positions where signal energy is too low (noise)
    # R is energy of L samples; minimum RMS ~ 0.005 means R_min = L * 0.005^2
    R_min = L * 0.005 ** 2
    M[R < R_min] = 0.0

    # Find peak above threshold
    above = M > threshold
    if not np.any(above):
        # Try lower threshold
        above = M > threshold * 0.5
        if not np.any(above):
            return None, None

    # Find the first sustained peak (plateau region)
    # The plateau starts when M first exceeds threshold
    indices = np.where(above)[0]
    if len(indices) == 0:
        return None, None

    # The peak of the plateau corresponds to the start of the preamble symbol
    # after the cyclic prefix. We look for the maximum of M around the first detection.
    search_start = max(0, indices[0] - L)
    search_end = min(len(M), indices[0] + 2 * L)
    peak_idx = search_start + np.argmax(M[search_start:search_end])

    # The preamble symbol 1 starts at (peak_idx - CP_LENGTH)
    # because peak_idx points to somewhere in the symbol body
    preamble_start = max(0, peak_idx - cp)

    # Frequency offset estimation from phase of P at peak
    # For real signal: P is real, so freq offset estimation is limited.
    # We use the complex version for better estimation:
    # Recompute P as complex at the detection point
    start = peak_idx
    end = min(start + L, len(sig) - L)
    if end - start < L:
        freq_offset = 0.0
    else:
        p_complex = np.sum(sig[start:start + L] * sig[start + L:start + 2 * L])
        # For real signals, the frequency offset from autocorrelation is limited
        # We can estimate it from the phase of the cross-correlation
        freq_offset = 0.0  # placeholder, real-valued signal limits this

    return preamble_start, freq_offset


# ─── Channel Estimation ───────────────────────────────────────────────

def extract_symbol(signal, symbol_start):
    """
    Extract one OFDM symbol from signal: remove CP, return FFT_SIZE samples.

    Args:
        signal: numpy array
        symbol_start: sample index of symbol start (including CP)

    Returns:
        numpy array of FFT_SIZE complex frequency-domain values (after FFT)
    """
    cp = protocol.CP_LENGTH
    n = protocol.FFT_SIZE

    # Skip CP, take FFT_SIZE samples
    start = symbol_start + cp
    end = start + n

    if end > len(signal):
        # Pad with zeros if signal is too short
        chunk = np.zeros(n)
        available = len(signal) - start
        if available > 0:
            chunk[:available] = signal[start:start + available]
    else:
        chunk = signal[start:end]

    return np.fft.fft(chunk)


def estimate_channel(signal, preamble_start):
    """
    Estimate channel from training symbols.

    After the 2-symbol preamble, there are TRAINING_SYMBOLS training symbols
    with known patterns on all active subcarriers.

    Args:
        signal: numpy array of audio samples
        preamble_start: sample index where preamble starts

    Returns:
        H: numpy array of complex channel estimates (FFT_SIZE elements)
        data_start: sample index where data symbols begin
    """
    sym_len = protocol.SYMBOL_SAMPLES
    n = protocol.FFT_SIZE

    # Skip preamble (2 symbols)
    training_start = preamble_start + protocol.PREAMBLE_SYMBOLS * sym_len

    # Get known training sequences
    all_active_bins = sorted(set(protocol.DATA_BINS) | set(protocol.PILOT_BINS))

    H_sum = np.zeros(n, dtype=complex)
    H_count = np.zeros(n, dtype=float)

    for sym_idx in range(protocol.TRAINING_SYMBOLS):
        sym_start = training_start + sym_idx * sym_len

        # Get received frequency domain
        rx_freq = extract_symbol(signal, sym_start)

        # Generate known training pattern (same as encoder)
        pn = protocol.generate_pn_sequence(len(all_active_bins),
                                            seed=(0xC0 + sym_idx) & 0xFF)

        # Build expected frequency domain
        tx_freq = np.zeros(n, dtype=complex)
        for i, bin_idx in enumerate(all_active_bins):
            tx_freq[bin_idx] = complex(pn[i], 0)

        # Hermitian symmetry (same as encoder)
        for k in range(1, n // 2):
            tx_freq[n - k] = np.conj(tx_freq[k])
        tx_freq[0] = 0
        tx_freq[n // 2] = tx_freq[n // 2].real

        # Channel estimate: H = Rx / Tx (only at active bins)
        for bin_idx in all_active_bins:
            if abs(tx_freq[bin_idx]) > 0.01:
                H_sum[bin_idx] += rx_freq[bin_idx] / tx_freq[bin_idx]
                H_count[bin_idx] += 1

    # Average
    H = np.zeros(n, dtype=complex)
    nonzero = H_count > 0
    H[nonzero] = H_sum[nonzero] / H_count[nonzero]

    # Data starts after preamble + training
    data_start = training_start + protocol.TRAINING_SYMBOLS * sym_len

    return H, data_start


# ─── Equalization and Demapping ────────────────────────────────────────

def equalize_symbol(rx_freq, H):
    """
    Apply zero-forcing equalization: X_hat = Y / H.

    Args:
        rx_freq: numpy array of received frequency-domain values
        H: numpy array of channel estimates

    Returns:
        equalized numpy array
    """
    eq = np.zeros_like(rx_freq)
    nonzero = np.abs(H) > 0.01
    eq[nonzero] = rx_freq[nonzero] / H[nonzero]
    return eq


class PhaseTracker:
    """
    Incremental phase drift tracker for sample clock offset compensation.

    Instead of fitting absolute pilot phases each symbol (which fails when
    accumulated drift exceeds ±π between adjacent pilots), this tracks the
    drift incrementally: each symbol only needs to correct a small delta.
    """

    def __init__(self):
        self.alpha = 0.0  # accumulated common phase offset
        self.beta = 0.0   # accumulated phase slope (rad/bin)
        self.initialized = False

    def correct(self, eq_symbol, pilot_bins, expected_pilot_vals):
        """Apply accumulated + incremental phase correction."""
        if len(pilot_bins) < 2:
            return eq_symbol

        expected = np.array(expected_pilot_vals, dtype=complex)
        expected_mag = np.abs(expected)
        valid = expected_mag > 0.01
        if np.sum(valid) < 2:
            return eq_symbol

        pilot_k = np.array(pilot_bins, dtype=float)[valid]

        if not self.initialized:
            # First symbol: fit from scratch with unwrapping
            received = eq_symbol[pilot_bins]
            ratios = received[valid] / expected[valid]
            phase_errors = np.unwrap(np.angle(ratios))
            self.beta, self.alpha = np.polyfit(pilot_k, phase_errors, 1)
            self.initialized = True
        else:
            # Apply current estimate, measure residual, update
            all_bins = np.arange(len(eq_symbol))
            pre_correction = np.exp(-1j * (self.alpha + self.beta * all_bins))
            corrected = eq_symbol * pre_correction

            received = corrected[pilot_bins]
            ratios = received[valid] / expected[valid]
            residual_phases = np.angle(ratios)  # small residuals, no unwrap needed

            d_beta, d_alpha = np.polyfit(pilot_k, residual_phases, 1)
            self.alpha += d_alpha
            self.beta += d_beta

        # Apply full correction
        all_bins = np.arange(len(eq_symbol))
        correction = np.exp(-1j * (self.alpha + self.beta * all_bins))
        return eq_symbol * correction


def correct_phase_drift(eq_symbol, pilot_bins, expected_pilot_vals):
    """
    Stateless single-symbol phase correction (used when no tracker is available).
    Fits α + β·k to unwrapped pilot phase errors.
    """
    if len(pilot_bins) < 2:
        return eq_symbol

    received_pilots = eq_symbol[pilot_bins]
    expected = np.array(expected_pilot_vals, dtype=complex)

    expected_mag = np.abs(expected)
    valid = expected_mag > 0.01
    if np.sum(valid) < 2:
        return eq_symbol

    ratios = received_pilots[valid] / expected[valid]
    phase_errors = np.unwrap(np.angle(ratios))
    pilot_k = np.array(pilot_bins, dtype=float)[valid]

    beta, alpha = np.polyfit(pilot_k, phase_errors, 1)

    all_bins = np.arange(len(eq_symbol))
    correction = np.exp(-1j * (alpha + beta * all_bins))
    return eq_symbol * correction


def compensate_agc(eq_symbol, pilot_bins, expected_pilot_vals):
    """
    Compensate for AGC gain changes using pilot tones.

    Args:
        eq_symbol: equalized frequency-domain symbol
        pilot_bins: list of pilot bin indices
        expected_pilot_vals: expected pilot complex values

    Returns:
        gain-compensated symbol
    """
    if len(pilot_bins) == 0:
        return eq_symbol

    received_pilots = eq_symbol[pilot_bins]
    expected = np.array(expected_pilot_vals, dtype=complex)

    # Compute gain as ratio of received to expected magnitude
    expected_mag = np.abs(expected)
    valid = expected_mag > 0.01
    if not np.any(valid):
        return eq_symbol

    gains = np.abs(received_pilots[valid]) / expected_mag[valid]
    avg_gain = np.median(gains)  # median is more robust than mean

    if avg_gain > 0.01:
        return eq_symbol / avg_gain

    return eq_symbol


def demap_symbol(eq_value, modulation):
    """
    Hard-decision demapping: find nearest constellation point.

    Args:
        eq_value: complex equalized value
        modulation: "bpsk", "qpsk", "16qam", "64qam"

    Returns:
        integer (bits as int)
    """
    cmap = protocol.CONSTELLATION_MAPS[modulation]
    min_dist = float('inf')
    best_bits = 0

    for bits_val, point in cmap.items():
        dist = abs(eq_value - point)
        if dist < min_dist:
            min_dist = dist
            best_bits = bits_val

    return best_bits


def demap_symbol_vectorized(eq_values, modulation):
    """
    Vectorized hard-decision demapping for all data carriers of one symbol.

    Args:
        eq_values: numpy array of complex equalized values (one per data carrier)
        modulation: "bpsk", "qpsk", "16qam", "64qam"

    Returns:
        list of integers (each is the bits value for that carrier)
    """
    cmap = protocol.CONSTELLATION_MAPS[modulation]
    # Build reference arrays
    ref_bits = list(cmap.keys())
    ref_points = np.array([cmap[b] for b in ref_bits], dtype=complex)

    results = []
    for val in eq_values:
        distances = np.abs(val - ref_points)
        best_idx = np.argmin(distances)
        results.append(ref_bits[best_idx])

    return results


# ─── Full Symbol Demodulation ──────────────────────────────────────────

def demodulate_data_symbol(signal, symbol_start, H, profile_name, symbol_index,
                           phase_tracker=None):
    """
    Demodulate one data symbol: extract bits from an OFDM symbol.

    Args:
        signal: numpy array of audio samples
        symbol_start: sample index of this symbol's start (including CP)
        H: channel estimate
        profile_name: modulation profile
        symbol_index: running index for pilot pattern
        phase_tracker: optional PhaseTracker for incremental drift correction

    Returns:
        list of 0/1 bit values
    """
    profile = protocol.PROFILES[profile_name]
    mod = profile["modulation"]
    bpc = profile["bits_per_carrier"]

    # Extract and FFT
    rx_freq = extract_symbol(signal, symbol_start)

    # Equalize
    eq = equalize_symbol(rx_freq, H)

    # Pilot reference for this symbol
    pilot_vals = protocol.generate_pn_sequence(len(protocol.PILOT_BINS),
                                                seed=(protocol.PN_SEED + symbol_index) & 0xFF)
    expected_pilots = [complex(v * protocol.PILOT_AMPLITUDE, 0) for v in pilot_vals]

    # Phase drift correction (fixes sample clock offset between TX/RX)
    if phase_tracker is not None:
        eq = phase_tracker.correct(eq, protocol.PILOT_BINS, expected_pilots)
    else:
        eq = correct_phase_drift(eq, protocol.PILOT_BINS, expected_pilots)

    # AGC compensation via pilots
    eq = compensate_agc(eq, protocol.PILOT_BINS, expected_pilots)

    # Extract data from data bins
    data_values = eq[protocol.DATA_BINS]

    # Demap each carrier
    bit_ints = demap_symbol_vectorized(data_values, mod)

    # Convert to bit list
    bits = []
    for val in bit_ints:
        for i in range(bpc - 1, -1, -1):
            bits.append((val >> i) & 1)

    return bits
