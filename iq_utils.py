"""IQ utilities: convert between complex64 .dat and HackRF raw uint8 I/Q files.

Usage examples:
  # Convert complex64 .dat (real,imag interleaved) to uint8 I/Q for tx
  python3 iq_utils.py to-u8 input_complex64.dat output_u8.dat

  # Convert raw hackrf_transfer uint8 I/Q to complex64 numpy array file
  python3 iq_utils.py to-complex input_u8.dat output_complex64.dat

Notes:
  - HackRF raw format is unsigned 8-bit interleaved: I,Q,I,Q,... centered near 127.5.
  - Conversion scales floats in [-1,1] to [0,255] when creating uint8 for transmission.
  - When converting from uint8 back to complex64, values are normalized to approximately [-1,1].
"""
from __future__ import annotations

import sys
import numpy as np
from typing import Tuple


def complex64_to_u8(in_path: str, out_path: str) -> None:
    """Read a complex64 interleaved .dat file and write unsigned 8-bit interleaved I/Q.

    The input is expected to be np.complex64 interleaved (real, imag).
    Output bytes follow the HackRF/hackrf_transfer convention: uint8 I,Q,I,Q,...
    """
    data = np.fromfile(in_path, dtype=np.complex64)
    if data.size == 0:
        raise ValueError("Input file contains no samples")

    # Separate real and imag and clip to [-1, 1]
    I = np.real(data).astype(np.float32)
    Q = np.imag(data).astype(np.float32)
    I = np.clip(I, -1.0, 1.0)
    Q = np.clip(Q, -1.0, 1.0)

    # Scale from [-1,1] -> [0,255]
    I_u8 = ((I * 127.5) + 127.5).round().astype(np.uint8)
    Q_u8 = ((Q * 127.5) + 127.5).round().astype(np.uint8)

    # Interleave
    out = np.empty(I_u8.size * 2, dtype=np.uint8)
    out[0::2] = I_u8
    out[1::2] = Q_u8
    out.tofile(out_path)


def u8_to_complex64(in_path: str, out_path: str) -> None:
    """Read unsigned 8-bit interleaved I/Q and write complex64 interleaved .dat file.

    The output file will be dtype complex64 interleaved (real, imag) suitable for NumPy.fromfile(..., dtype=np.complex64).
    """
    data = np.fromfile(in_path, dtype=np.uint8)
    if data.size == 0:
        raise ValueError("Input file contains no samples")
    if data.size % 2 != 0:
        # drop last byte if odd number
        data = data[:-1]

    I_u8 = data[0::2].astype(np.float32)
    Q_u8 = data[1::2].astype(np.float32)

    I = (I_u8 - 127.5) / 127.5
    Q = (Q_u8 - 127.5) / 127.5
    complex_arr = (I + 1j * Q).astype(np.complex64)
    complex_arr.tofile(out_path)


def print_usage_and_exit() -> None:
    print(__doc__)
    print("\nCLI usage:\n  python3 iq_utils.py to-u8 in_complex.dat out_u8.dat\n  python3 iq_utils.py to-complex in_u8.dat out_complex.dat")
    sys.exit(1)


def main(argv: list[str]) -> None:
    if len(argv) < 4:
        print_usage_and_exit()

    cmd = argv[1]
    in_path = argv[2]
    out_path = argv[3]

    if cmd == "to-u8":
        complex64_to_u8(in_path, out_path)
        print(f"Wrote uint8 I/Q file: {out_path}")
    elif cmd == "to-complex":
        u8_to_complex64(in_path, out_path)
        print(f"Wrote complex64 .dat file: {out_path}")
    else:
        print_usage_and_exit()


if __name__ == "__main__":
    main(sys.argv)
