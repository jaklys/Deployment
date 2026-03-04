"""
Diagnostic script: trace PhaseTracker state and pilot quality
over the decoding of test_big_fast.wav to identify why decoding
fails after ~1300 symbols.
"""
import sys
import numpy as np

sys.path.insert(0, '.')
from decoder import protocol, ofdm_fast, audio_in, fec_fast

# Load signal
print("Loading WAV...")
signal, sr = audio_in.read_wav("test_big_fast.wav")
print(f"  Samples: {len(signal):,} ({len(signal)/sr:.1f}s)")

# Detect preamble
print("Detecting preamble...")
preamble_start, freq_offset = ofdm_fast.detect_preamble(signal)
print(f"  Preamble at sample {preamble_start}")

# Channel estimation
print("Estimating channel...")
H, data_start = ofdm_fast.estimate_channel(signal, preamble_start)

sym_len = protocol.SYMBOL_SAMPLES
remaining = len(signal) - data_start
max_symbols = remaining // sym_len
print(f"  Data starts at {data_start}, max symbols: {max_symbols}")

# Skip metadata symbols
meta_bpc = protocol.PROFILES["safe"]["bits_per_carrier"]
meta_bits_per_sym = protocol.NUM_DATA_BINS * meta_bpc
rs_cw_bits = protocol.RS_N * 8
meta_sym_count = (rs_cw_bits + meta_bits_per_sym - 1) // meta_bits_per_sym
print(f"  Metadata symbols: {meta_sym_count}")

# Now trace data symbols with detailed phase tracker logging
data_start_sym = meta_sym_count
phase_tracker = ofdm_fast.PhaseTracker()

profile_name = "fast"
profile = protocol.PROFILES[profile_name]
mod = profile["modulation"]
bpc = profile["bits_per_carrier"]

print(f"\n{'sym':>5} {'alpha':>10} {'beta':>12} {'|residual|':>12} {'pilot_err':>12} {'EVM_dB':>8}")
print("-" * 65)

num_syms_to_check = min(2000, max_symbols - data_start_sym)

for sym_i in range(num_syms_to_check):
    sym_start = data_start + (data_start_sym + sym_i) * sym_len

    # Extract and equalize
    rx_freq = ofdm_fast.extract_symbol(signal, sym_start)
    eq = ofdm_fast.equalize_symbol(rx_freq, H)

    # Get expected pilots
    pilot_vals = protocol.generate_pn_sequence(
        len(protocol.PILOT_BINS),
        seed=(protocol.PN_SEED + sym_i) & 0xFF)
    expected_pilots = [complex(v * protocol.PILOT_AMPLITUDE, 0) for v in pilot_vals]

    # Apply phase correction and capture state
    alpha_before = phase_tracker.alpha
    beta_before = phase_tracker.beta

    eq_corrected = phase_tracker.correct(eq, protocol.PILOT_BINS, expected_pilots)

    alpha_after = phase_tracker.alpha
    beta_after = phase_tracker.beta

    # Measure pilot quality after correction
    pilot_received = eq_corrected[protocol.PILOT_BINS]
    expected_arr = np.array(expected_pilots)
    pilot_errors = pilot_received - expected_arr
    pilot_err_rms = np.sqrt(np.mean(np.abs(pilot_errors) ** 2))

    # Measure EVM on data carriers
    data_values = eq_corrected[protocol.DATA_BINS]
    # For 16-QAM, find nearest constellation point
    cmap = protocol.CONSTELLATION_MAPS[mod]
    ref_bits = list(cmap.keys())
    ref_points = np.array([cmap[b] for b in ref_bits], dtype=complex)

    evm_sum = 0.0
    for val in data_values[:50]:  # sample first 50 carriers
        distances = np.abs(val - ref_points)
        best_idx = np.argmin(distances)
        evm_sum += distances[best_idx] ** 2
    evm_rms = np.sqrt(evm_sum / 50)
    evm_db = 20 * np.log10(evm_rms + 1e-10)

    # Residual = change in alpha/beta
    d_alpha = alpha_after - alpha_before
    d_beta = beta_after - beta_before
    residual_mag = np.sqrt(d_alpha**2 + d_beta**2)

    if sym_i < 20 or sym_i % 50 == 0 or (sym_i > 100 and sym_i % 10 == 0):
        print(f"{sym_i:5d} {alpha_after:10.4f} {beta_after:12.6f} "
              f"{residual_mag:12.6f} {pilot_err_rms:12.4f} {evm_db:8.2f}")

print("\nDone.")
