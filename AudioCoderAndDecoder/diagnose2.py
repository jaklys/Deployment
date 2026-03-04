"""
Diagnostic 2: Estimate SCO precisely, test resampled signal quality.
"""
import sys
import numpy as np
from scipy import signal as sp_signal

sys.path.insert(0, '.')
from decoder import protocol, ofdm_fast, audio_in

# Load signal
print("Loading WAV...")
sig, sr = audio_in.read_wav("test_big_fast.wav")
print(f"  Samples: {len(sig):,} ({len(sig)/sr:.1f}s)")

# Detect preamble
preamble_start, _ = ofdm_fast.detect_preamble(sig)
print(f"  Preamble at sample {preamble_start}")

# Channel estimation
H, data_start = ofdm_fast.estimate_channel(sig, preamble_start)

sym_len = protocol.SYMBOL_SAMPLES
meta_sym_count = (protocol.RS_N * 8 + protocol.NUM_DATA_BINS - 1) // protocol.NUM_DATA_BINS

# Track phase for 500 symbols to get precise SCO estimate
print("\nEstimating SCO from first 500 data symbols...")
tracker = ofdm_fast.PhaseTracker()
betas = []
alphas = []

for sym_i in range(500):
    sym_start = data_start + (meta_sym_count + sym_i) * sym_len
    rx_freq = ofdm_fast.extract_symbol(sig, sym_start)
    eq = ofdm_fast.equalize_symbol(rx_freq, H)

    pilot_vals = protocol.generate_pn_sequence(
        len(protocol.PILOT_BINS),
        seed=(protocol.PN_SEED + sym_i) & 0xFF)
    expected_pilots = [complex(v * protocol.PILOT_AMPLITUDE, 0) for v in pilot_vals]

    # Use original (non-predictive) correction for clean measurement
    expected = np.array(expected_pilots, dtype=complex)
    valid = np.abs(expected) > 0.01
    pilot_k = np.array(protocol.PILOT_BINS, dtype=float)[valid]

    if not tracker.initialized:
        received = eq[protocol.PILOT_BINS]
        ratios = received[valid] / expected[valid]
        phase_errors = np.unwrap(np.angle(ratios))
        tracker.beta, tracker.alpha = np.polyfit(pilot_k, phase_errors, 1)
        tracker.initialized = True
    else:
        all_bins = np.arange(len(eq))
        pre = np.exp(-1j * (tracker.alpha + tracker.beta * all_bins))
        corrected = eq * pre
        received = corrected[protocol.PILOT_BINS]
        ratios = received[valid] / expected[valid]
        residual_phases = np.unwrap(np.angle(ratios))
        d_beta, d_alpha = np.polyfit(pilot_k, residual_phases, 1)
        tracker.alpha += d_alpha
        tracker.beta += d_beta

    betas.append(tracker.beta)
    alphas.append(tracker.alpha)

# Fit linear trend to beta
idxs = np.arange(len(betas))
beta_rate, beta_0 = np.polyfit(idxs, betas, 1)
alpha_rate, alpha_0 = np.polyfit(idxs, alphas, 1)

print(f"  beta_rate = {beta_rate:.8f} rad/bin per symbol")
print(f"  alpha_rate = {alpha_rate:.8f} rad per symbol")

# Convert to SCO
delta_samples = beta_rate * protocol.FFT_SIZE / (2 * np.pi)
sco_ppm = delta_samples / protocol.SYMBOL_SAMPLES * 1e6
print(f"  SCO = {delta_samples:.6f} samples/symbol = {sco_ppm:.2f} ppm")

# Resample signal to correct SCO
resample_ratio = 1.0 / (1.0 + sco_ppm / 1e6)
print(f"\nResampling signal with ratio {resample_ratio:.10f}...")
# Use scipy.signal.resample_poly for precise resampling
# Approximate ratio as p/q
from fractions import Fraction
frac = Fraction(resample_ratio).limit_denominator(10000)
p, q = frac.numerator, frac.denominator
print(f"  Rational approximation: {p}/{q}")

# For very precise ratio, use direct interpolation
num_out = int(len(sig) * resample_ratio)
resampled = sp_signal.resample(sig, num_out)
print(f"  Original: {len(sig)} samples, Resampled: {len(resampled)} samples")

# Now test decoding on resampled signal
print("\nTesting resampled signal...")
preamble_start2, _ = ofdm_fast.detect_preamble(resampled)
print(f"  Preamble at sample {preamble_start2}")

H2, data_start2 = ofdm_fast.estimate_channel(resampled, preamble_start2)

# Track phase on resampled signal
tracker2 = ofdm_fast.PhaseTracker()
betas2 = []

for sym_i in range(500):
    sym_start = data_start2 + (meta_sym_count + sym_i) * sym_len
    if sym_start + sym_len > len(resampled):
        break
    rx_freq = ofdm_fast.extract_symbol(resampled, sym_start)
    eq = ofdm_fast.equalize_symbol(rx_freq, H2)

    pilot_vals = protocol.generate_pn_sequence(
        len(protocol.PILOT_BINS),
        seed=(protocol.PN_SEED + sym_i) & 0xFF)
    expected_pilots = [complex(v * protocol.PILOT_AMPLITUDE, 0) for v in pilot_vals]

    expected = np.array(expected_pilots, dtype=complex)
    valid = np.abs(expected) > 0.01
    pilot_k = np.array(protocol.PILOT_BINS, dtype=float)[valid]

    if not tracker2.initialized:
        received = eq[protocol.PILOT_BINS]
        ratios = received[valid] / expected[valid]
        phase_errors = np.unwrap(np.angle(ratios))
        tracker2.beta, tracker2.alpha = np.polyfit(pilot_k, phase_errors, 1)
        tracker2.initialized = True
    else:
        all_bins = np.arange(len(eq))
        pre = np.exp(-1j * (tracker2.alpha + tracker2.beta * all_bins))
        corrected = eq * pre
        received = corrected[protocol.PILOT_BINS]
        ratios = received[valid] / expected[valid]
        residual_phases = np.unwrap(np.angle(ratios))
        d_beta, d_alpha = np.polyfit(pilot_k, residual_phases, 1)
        tracker2.alpha += d_alpha
        tracker2.beta += d_beta

    betas2.append(tracker2.beta)

# Compare
beta_rate2, _ = np.polyfit(np.arange(len(betas2)), betas2, 1)
print(f"\n  Original beta_rate: {beta_rate:.8f}")
print(f"  Resampled beta_rate: {beta_rate2:.8f}")
print(f"  Reduction factor: {abs(beta_rate / beta_rate2) if abs(beta_rate2) > 1e-12 else 'inf':.1f}x")

# EVM comparison at symbol 200
for label, s, d, h in [("Original", sig, data_start, H),
                         ("Resampled", resampled, data_start2, H2)]:
    sym_start = d + (meta_sym_count + 200) * sym_len
    if sym_start + sym_len > len(s):
        continue
    rx_freq = ofdm_fast.extract_symbol(s, sym_start)
    eq = ofdm_fast.equalize_symbol(rx_freq, h)
    # Simple phase correction
    pilot_vals = protocol.generate_pn_sequence(
        len(protocol.PILOT_BINS), seed=(protocol.PN_SEED + 200) & 0xFF)
    expected_pilots = [complex(v * protocol.PILOT_AMPLITUDE, 0) for v in pilot_vals]
    eq = ofdm_fast.correct_phase_drift(eq, protocol.PILOT_BINS, expected_pilots)
    eq = ofdm_fast.compensate_agc(eq, protocol.PILOT_BINS, expected_pilots)

    data_vals = eq[protocol.DATA_BINS]
    cmap = protocol.CONSTELLATION_MAPS["16qam"]
    ref_points = np.array(list(cmap.values()), dtype=complex)

    evm_sum = 0.0
    for val in data_vals[:100]:
        best = np.argmin(np.abs(val - ref_points))
        evm_sum += np.abs(val - ref_points[best])**2
    evm = np.sqrt(evm_sum / 100)
    print(f"  {label} EVM at sym 200: {20*np.log10(evm+1e-10):.1f} dB")

print("\nDone.")
