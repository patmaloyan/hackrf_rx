import argparse
from pathlib import Path
import numpy as np

# Defaults copied from the notebook
FS = 5_000_000
SYMBOL_RATE = 100_000
FC = 10.1e6
FTONE = 10.0e6
DEFAULT_NUM_SYMBOLS = 200
DROP_MS = 2.0


def load_iq_dat(path: str | Path, fs: int = FS, drop_ms: float = DROP_MS) -> np.ndarray:
    """Load interleaved int8 IQ samples from HackRF-style .dat file."""
    raw = np.fromfile(path, dtype=np.int8)
    if raw.size < 2:
        raise ValueError("File is empty or too small to contain IQ data.")

    # Ensure even number of bytes for I/Q pairing
    if raw.size % 2 != 0:
        raw = raw[:-1]

    iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)

    drop_samps = int(fs * drop_ms / 1000.0)
    if drop_samps > 0 and drop_samps < iq.size:
        iq = iq[drop_samps:]

    iq = iq - np.mean(iq)
    return iq


def mix_down_and_filter(iq: np.ndarray, fs: int, fc: float, ftone: float, sps: int) -> tuple[np.ndarray, np.ndarray]:
    """Mix the tone to baseband and smooth with a moving-average LPF."""
    f_offset = ftone - fc
    n = np.arange(len(iq), dtype=np.float64)
    lo = np.exp(-1j * 2.0 * np.pi * f_offset * n / fs)
    bb = iq * lo

    h = np.ones(sps, dtype=np.float64) / sps
    bb_lp = np.convolve(bb, h, mode="same")
    return bb, bb_lp


def find_start_idx(bb_lp: np.ndarray, sps: int, threshold_scale: float = 0.1) -> tuple[int, np.ndarray, float]:
    """Find burst start using notebook-style power thresholding."""
    p = np.abs(bb_lp) ** 2
    thr = threshold_scale * np.max(p)

    offset = sps
    crossings = np.where(p[offset:] > thr)[0]
    if crossings.size == 0:
        raise RuntimeError("No burst detected above threshold.")

    start_idx = int(offset + crossings[0])
    return start_idx, p, float(thr)


def estimate_symbol_values(bb: np.ndarray, start_idx: int, sps: int, num_symbols: int) -> list[complex]:
    """Average over the middle part of each symbol period."""
    symbol_values: list[complex] = []
    for i in range(num_symbols):
        sym_start = start_idx + i * sps + sps // 8
        sym_stop = start_idx + (i + 1) * sps - sps // 8
        if sym_stop > len(bb):
            break
        symbol_values.append(np.mean(bb[sym_start:sym_stop]))
    return symbol_values


def determine_mapping(pilot_symbols: list[complex]) -> np.ndarray:
    """Resolve the QPSK quadrant rotation from the first 4 pilot symbols."""
    constellation = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j], dtype=np.complex64) / np.sqrt(2)
    bits = np.array([[1, 1], [0, 1], [0, 0], [1, 0]], dtype=np.uint8)

    if len(pilot_symbols) < 4:
        raise ValueError("Need at least 4 pilot symbols to determine mapping.")

    distances = np.abs(pilot_symbols[0] - constellation)
    idx = int(np.argmin(distances))

    mapping = np.roll(bits, idx, axis=0)
    mapping_constellation = np.roll(constellation, idx)

    # Warn if later pilot symbols do not line up with expected rotated pilot sequence
    expected_pilot = constellation
    for i in range(1, min(4, len(pilot_symbols))):
        d = np.abs(pilot_symbols[i] - constellation)
        got_idx = int(np.argmin(d))
        got_sym_after_mapping = mapping_constellation[got_idx]
        if not np.isclose(got_sym_after_mapping, expected_pilot[i]):
            print(
                f"Warning: pilot {i} mismatch: got {got_sym_after_mapping}, expected {expected_pilot[i]}"
            )

    return mapping


def symbol_to_bits(sym: complex, mapping: np.ndarray) -> np.ndarray:
    constellation = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j], dtype=np.complex64) / np.sqrt(2)
    idx = int(np.argmin(np.abs(sym - constellation)))
    return mapping[idx]


def bits_to_bytes(bit_array: np.ndarray) -> list[int]:
    nbytes = len(bit_array) // 8
    out: list[int] = []
    for i in range(nbytes):
        byte_bits = bit_array[8 * i : 8 * (i + 1)]
        value = 0
        for bit in byte_bits:
            value = (value << 1) | int(bit)
        out.append(value)
    return out


def printable_ascii(byte_vals: list[int]) -> str:
    return "".join(chr(b) if 32 <= b <= 126 else "." for b in byte_vals)


def decode_file(
    path: str | Path,
    fs: int = FS,
    symbol_rate: int = SYMBOL_RATE,
    fc: float = FC,
    ftone: float = FTONE,
    num_symbols: int = DEFAULT_NUM_SYMBOLS,
    threshold_scale: float = 0.1,
) -> dict:
    sps = int(fs // symbol_rate)
    iq = load_iq_dat(path, fs=fs)
    bb, bb_lp = mix_down_and_filter(iq, fs, fc, ftone, sps)
    start_idx, power, thr = find_start_idx(bb_lp, sps, threshold_scale=threshold_scale)
    symbol_values = estimate_symbol_values(bb, start_idx, sps, num_symbols)

    if len(symbol_values) < 4:
        raise RuntimeError("Not enough symbols found to extract pilots.")

    mapping = determine_mapping(symbol_values[:4])
    decoded_bits = np.array([symbol_to_bits(sym, mapping) for sym in symbol_values], dtype=np.uint8)

    data_bits = decoded_bits[4:].flatten()  # skip 4 pilot symbols
    byte_vals = bits_to_bytes(data_bits)
    ascii_text = printable_ascii(byte_vals)

    return {
        "sps": sps,
        "start_idx": start_idx,
        "start_time_s": start_idx / fs,
        "threshold": thr,
        "num_symbols_used": len(symbol_values),
        "pilot_symbols": symbol_values[:4],
        "first_12_symbols": symbol_values[:12],
        "decoded_bits": decoded_bits,
        "data_bits": data_bits,
        "byte_vals": byte_vals,
        "ascii_text": ascii_text,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean QPSK demod script for HackRF-style .dat IQ files")
    parser.add_argument("dat_file", nargs="?", default="/home/cubesat/Cubesat/Proj1/data/Saved/iqBlockHW.dat", help="Path to interleaved int8 IQ .dat file")
    parser.add_argument("--fs", type=int, default=FS)
    parser.add_argument("--symbol-rate", type=int, default=SYMBOL_RATE)
    parser.add_argument("--fc", type=float, default=FC)
    parser.add_argument("--ftone", type=float, default=FTONE)
    parser.add_argument("--num-symbols", type=int, default=DEFAULT_NUM_SYMBOLS)
    parser.add_argument("--threshold-scale", type=float, default=0.1)
    args = parser.parse_args()

    result = decode_file(
        args.dat_file,
        fs=args.fs,
        symbol_rate=args.symbol_rate,
        fc=args.fc,
        ftone=args.ftone,
        num_symbols=args.num_symbols,
        threshold_scale=args.threshold_scale,
    )

    print(f"file           : {args.dat_file}")
    print(f"sps            : {result['sps']}")
    print(f"start_idx      : {result['start_idx']}")
    print(f"start_time_s   : {result['start_time_s']:.9f}")
    print(f"threshold      : {result['threshold']:.6f}")
    print(f"num_symbols    : {result['num_symbols_used']}")
    print()

    print("pilot symbols:")
    for i, sym in enumerate(result["pilot_symbols"]):
        print(f"  [{i}] {sym.real:+.4f} {sym.imag:+.4f}j")
    print()

    print("first 12 symbol estimates:")
    for i, sym in enumerate(result["first_12_symbols"]):
        print(f"  [{i:02d}] {sym.real:+.4f} {sym.imag:+.4f}j")
    print()

    print("decoded bytes:")
    print(result["byte_vals"])
    print()

    print("ascii (non-printable -> '.'):")
    print(result["ascii_text"])


if __name__ == "__main__":
    main()