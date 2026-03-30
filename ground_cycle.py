import subprocess
import os
import time
from datetime import datetime

# ============================================================
# USER SETTINGS
# ============================================================

# HackRF settings
CENTER_FREQ_RX = 10_000_000      # Hz
CENTER_FREQ_TX = 10_100_000      # Hz
SAMPLE_RATE = 5_000_000          # Hz

TX_GAIN = 40
TX_AMP_ENABLE = 1

RX_AMP = 0
RX_LNA_GAIN = 16
RX_VGA_GAIN = 24

# Slot timings
RX_MS = 80
GUARD_MS = 40
TX_MS = 80

# Files/directories
TX_FILE = "/home/cubesat/Cubesat/Proj1/tx/waveforms/Hamming_burst_QPSK_msg_5Mhzfs_3sec.dat"
RX_DIR = "/home/cubesat/Cubesat/Proj1/data/iq"

# Optional serial if needed
HACKRF_SERIAL = None

# Number of cycles
NUM_CYCLES = 5

# Use external trigger for first phase
USE_TRIGGER_FOR_FIRST_PHASE = True

# ============================================================
# DERIVED VALUES
# ============================================================

RX_SAMPLES = int(SAMPLE_RATE * RX_MS / 1000)
TX_SAMPLES = int(SAMPLE_RATE * TX_MS / 1000)

# ============================================================
# HELPERS
# ============================================================

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def timestamp_string():
    return datetime.now().strftime("%Y%m%d-%H%M%S")

def add_device_arg(cmd):
    if HACKRF_SERIAL:
        cmd += ["-d", HACKRF_SERIAL]
    return cmd

def run_rx(outfile: str, freq: int, sample_rate: int, num_samples: int,
           amp: int, lna_gain: int, vga_gain: int, use_trigger: bool):
    cmd = [
        "hackrf_transfer",
        "-r", outfile,
        "-f", str(freq),
        "-s", str(sample_rate),
        "-n", str(num_samples),
        "-a", str(amp),
        "-l", str(lna_gain),
        "-g", str(vga_gain),
    ]

    if use_trigger:
        cmd.append("-H")

    cmd = add_device_arg(cmd)

    print("RX CMD:", " ".join(cmd))
    start = time.monotonic()
    subprocess.run(cmd, check=True)
    end = time.monotonic()
    print(f"RX finished in {(end - start)*1000:.2f} ms")

def run_tx(tx_file: str, freq: int, sample_rate: int, num_samples: int,
           tx_gain: int, amp_enable: int, use_trigger: bool):
    cmd = [
        "hackrf_transfer",
        "-t", tx_file,
        "-f", str(freq),
        "-s", str(sample_rate),
        "-x", str(tx_gain),
        "-n", str(num_samples),
        "-a", str(amp_enable),
    ]

    if use_trigger:
        cmd.append("-H")

    cmd = add_device_arg(cmd)

    print("TX CMD:", " ".join(cmd))
    start = time.monotonic()
    subprocess.run(cmd, check=True)
    end = time.monotonic()
    print(f"TX finished in {(end - start)*1000:.2f} ms")

# ============================================================
# MAIN LOOP
# ============================================================

def main():
    ensure_dir(RX_DIR)

    if not os.path.exists(TX_FILE):
        raise FileNotFoundError(
            f"TX file not found: {TX_FILE}\n"
            "Create it first before running this script."
        )

    print("====================================")
    print("Ground side: RX -> GUARD -> TX")
    print(f"RX slot   : {RX_MS} ms")
    print(f"Guard slot: {GUARD_MS} ms")
    print(f"TX slot   : {TX_MS} ms")
    print(f"Cycles    : {NUM_CYCLES}")
    print("====================================")

    for cycle in range(1, NUM_CYCLES + 1):
        print(f"\n===== GROUND CYCLE {cycle}/{NUM_CYCLES} =====")

        # ----------------------------------------------------
        # Phase 1: RX
        # ----------------------------------------------------
        print("Phase 1: RX")
        print("Arm this before the PPS arrives if using -H.")
        rx_outfile = os.path.join(
            RX_DIR,
            f"ground_rx_cycle{cycle}_{timestamp_string()}.dat"
        )

        run_rx(
            outfile=rx_outfile,
            freq=CENTER_FREQ_RX,
            sample_rate=SAMPLE_RATE,
            num_samples=RX_SAMPLES,
            amp=RX_AMP,
            lna_gain=RX_LNA_GAIN,
            vga_gain=RX_VGA_GAIN,
            use_trigger=USE_TRIGGER_FOR_FIRST_PHASE
        )

        print("Saved RX file:", rx_outfile)

        # ----------------------------------------------------
        # Guard
        # ----------------------------------------------------
        print(f"Guard: sleeping for {GUARD_MS} ms")
        time.sleep(GUARD_MS / 1000.0)

        # ----------------------------------------------------
        # Phase 2: TX
        # ----------------------------------------------------
        print("Phase 2: TX")
        run_tx(
            tx_file=TX_FILE,
            freq=CENTER_FREQ_TX,
            sample_rate=SAMPLE_RATE,
            num_samples=TX_SAMPLES,
            tx_gain=TX_GAIN,
            amp_enable=TX_AMP_ENABLE,
            use_trigger=False
        )

        time.sleep(0.5)

    print("\nGround cycles complete.")

if __name__ == "__main__":
    main()








"""
# --- Configuration ---

freq = 10.1e6    # Hz
sample_rate = 5_000_000  # Hz
num_samples = sample_rate * 1
amp = 1               # 1 = enable antenna power, 0 = disable
lna_gain = 0          # 0–40 dB
vga_gain = 0          # 0–62 dB

serial_rx = "0000000000000000c66c63dc3234a083"

main_dir = "/home/cubesat/Cubesat/Proj1/data/iq"
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
filename_prefix = f"iqdata-{timestamp}"
outfile = os.path.join(main_dir, filename_prefix + ".dat")

# --- Build command ---
cmd = [
    "hackrf_transfer",
    "-H",                         # wait for trigger
    "-d", serial_rx,              # specify triggered HackRF
    "-r", outfile,
    "-f", str(freq),
    "-s", str(sample_rate),
    "-n", str(num_samples),
    "-a", str(amp),
    "-l", str(lna_gain),
    "-g", str(vga_gain)
]

print("Running:", " ".join(cmd))

# --- Run command ---
subprocess.run(cmd, check=True)

print("Capture complete. File saved as", outfile)

"""