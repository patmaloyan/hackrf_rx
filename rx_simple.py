import subprocess
import os
from datetime import datetime

# --- Configuration ---
# IQ recording using subprocess command to call hackrf_transfer, saving to a timestamped file.
# dat file is the default file coming out from the hackrf


freq = 10.1e6    # Hz
sample_rate = 5_000_000  # Hz
num_samples = sample_rate * 1
amp = 1               # 1 = enable antenna power, 0 = disable
lna_gain = 0          # 0–40 dB
vga_gain = 0           # 0–62 dB


main_dir = "/home/cubesat/Cubesat/Proj1/rx/rx_checksum"
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
filename_prefix = f"iqdata-{timestamp}"
outfile = os.path.join(main_dir, filename_prefix + ".dat")

# --- Build command ---
cmd = [
    "hackrf_transfer",
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
