# AudioCoderAndDecoder - OFDM Audio Modem

Transfer files (1-10 MB) between computers via 3.5mm audio cable.
Designed for air-gapped environments where USB, network, and BT file transfer are blocked.

## How It Works

The encoder compresses a file/folder into tar.gz, adds Reed-Solomon error correction,
modulates the data using OFDM (Orthogonal Frequency Division Multiplexing), and outputs
a WAV file. The decoder captures the audio, demodulates, corrects errors, and reconstructs
the original files.

## Quick Start

### Sender (air-gapped PC - zero dependencies)
```bash
git clone <repo-url>
cd AudioCoderAndDecoder

# Encode a folder to WAV and play it
python -m encoder.encoder --input myproject/ --output signal.wav --play

# Or just generate WAV (play manually later)
python -m encoder.encoder --input myfile.zip --output signal.wav --profile fast
```

### Receiver (personal PC)
```bash
cd AudioCoderAndDecoder
pip install -r decoder/requirements.txt

# Decode from WAV file (for testing)
python -m decoder.decoder --input signal.wav --output ./received/

# Decode from live audio capture
python -m decoder.decoder --duration 300 --output ./received/ --profile standard

# List audio devices
python -m decoder.decoder --list-devices

# Use specific audio device
python -m decoder.decoder --device 2 --duration 120 --output ./received/
```

## Hardware Setup

1. 3.5mm male-to-male audio cable (stereo or mono)
2. Connect: **Sender headphone out** -> **Receiver line-in/mic**
3. Set sender output volume to 50-70%
4. Disable AGC on receiver (see below)

## Disabling AGC (Windows - Important!)

Windows may auto-adjust input gain (AGC), which corrupts the OFDM signal.
The modem has pilot-based AGC compensation, but disabling AGC gives better results.

1. Right-click speaker icon in taskbar -> **Sound settings**
2. Scroll to **Input** -> click your input device
3. Under **Input settings**, set a fixed level (50-75%)
4. Click **Device properties** -> **Additional device properties**
5. In **Advanced** tab, uncheck any "AGC" or "Automatic gain" options
6. In **Enhancements** tab, disable all enhancements

## Profiles

| Profile  | Modulation | Throughput | 1 MB   | 5 MB    | Use When                  |
|----------|-----------|-----------|--------|---------|---------------------------|
| safe     | BPSK      | ~1.9 kB/s | ~9 min | ~45 min | Noisy connection, first test |
| standard | QPSK      | ~3.8 kB/s | ~4.5 min | ~22 min | Default, recommended     |
| fast     | 16-QAM    | ~7.5 kB/s | ~2.3 min | ~11 min | Good cable, AGC disabled |
| turbo    | 64-QAM    | ~11 kB/s  | ~1.5 min | ~7.5 min | Excellent SNR only      |

## Testing

### Loopback test (no hardware needed)
```bash
python -m tests.test_loopback
```
Tests encode -> WAV -> decode pipeline for all profiles, including with simulated noise.

### Cable test (requires physical cable)
```bash
python -m tests.test_cable
```
Interactive test that checks audio levels, measures BER, and recommends the optimal profile.

## Protocol Details

- **Sample rate:** 44100 Hz
- **FFT size:** 1024 (43 Hz frequency resolution)
- **Cyclic prefix:** 128 samples (1/8 of FFT)
- **Usable band:** 300-20000 Hz (448 data carriers + 10 pilots)
- **FEC:** Reed-Solomon RS(255,223) - corrects up to 16 byte errors per codeword
- **Interleaving:** Block interleaver (depth 8) spreads burst errors across codewords
- **Preamble:** Schmidl-Cox (autocorrelation-based detection)
- **Channel estimation:** 4 training symbols with known patterns

## Project Structure

```
AudioCoderAndDecoder/
  encoder/         # ZERO external dependencies - stdlib only
    encoder.py     # Main CLI entry point
    protocol.py    # Shared constants and profiles
    fec.py         # Reed-Solomon GF(256) codec
    ofdm.py        # Pure Python FFT + OFDM modulation
    audio_out.py   # WAV writing + winsound playback

  decoder/         # Requires: numpy, scipy, sounddevice
    decoder.py     # Main CLI entry point
    protocol.py    # Copy of encoder/protocol.py
    fec_fast.py    # Reed-Solomon decoder
    ofdm_fast.py   # OFDM demodulation (numpy/scipy)
    audio_in.py    # Audio capture + WAV reading

  tests/
    test_loopback.py   # Local pipeline test
    test_cable.py      # Physical cable test
```

## Troubleshooting

- **Preamble not detected:** Increase output volume, check cable connection
- **High error rate:** Use a lower profile (standard -> safe), disable AGC
- **Decode fails but preamble found:** Check that sender and receiver use the same profile
- **WAV generation slow:** Expected for large files (pure Python FFT). ~90s for 1 MB at QPSK
