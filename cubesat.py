#!/usr/bin/env python3
import os
import sys
import time
import signal
import struct
import threading
from datetime import datetime

import usb.core
import usb.util

# ============================================================
# User config
# ============================================================
TX_FILE = "/home/cubesat/CubeSat/Tx/waveforms/Hamming_burst_QPSK_msg_5Mhzfs_3sec.dat"
RX_DIR  = "/home/cubesat/CubeSat/Tx/data/iq"

SAMPLE_RATE_HZ = 5_000_000
CENTER_FREQ_HZ = 10_000_000

AMP_ENABLE = 0
ANTENNA_ENABLE = 0
LNA_GAIN = 16      # RX IF gain
VGA_GAIN = 20      # RX BB gain
TXVGA_GAIN = 20    # TX IF gain

CHUNK_SIZE = 0x4000         # 16384 bytes, matches your firmware USB_TRANSFER_SIZE
READ_TIMEOUT_MS = 20
WRITE_TIMEOUT_MS = 20
STATUS_PRINT_SEC = 2.0

# HackRF USB IDs
VID = 0x1D50
PID = 0x6089

# Bulk endpoints
EP_IN = 0x81   # device -> host
EP_OUT = 0x02  # host -> device

# ============================================================
# HackRF vendor requests
# ============================================================
VENDOR_OUT = 0x40
VENDOR_IN  = 0xC0

HACKRF_VENDOR_REQUEST_SET_TRANSCEIVER_MODE = 1
HACKRF_VENDOR_REQUEST_SAMPLE_RATE_SET      = 6
HACKRF_VENDOR_REQUEST_BASEBAND_FILTER_BW   = 7
HACKRF_VENDOR_REQUEST_SET_FREQ             = 16
HACKRF_VENDOR_REQUEST_AMP_ENABLE           = 17
HACKRF_VENDOR_REQUEST_SET_LNA_GAIN         = 19
HACKRF_VENDOR_REQUEST_SET_VGA_GAIN         = 20
HACKRF_VENDOR_REQUEST_SET_TXVGA_GAIN       = 21
HACKRF_VENDOR_REQUEST_ANTENNA_ENABLE       = 23

TRANSCEIVER_MODE_OFF = 0

running = True
tx_bytes = 0
rx_bytes = 0
tx_lock = threading.Lock()
rx_lock = threading.Lock()


def log(msg: str) -> None:
    print(msg, flush=True)


def is_timeout(exc: Exception) -> bool:
    s = str(exc).lower()
    return "timed out" in s or "timeout" in s


def ctrl_out(dev, request: int, value: int = 0, index: int = 0, data: bytes = b"") -> None:
    dev.ctrl_transfer(VENDOR_OUT, request, value, index, data, timeout=1000)


def ctrl_in(dev, request: int, value: int = 0, index: int = 0, length: int = 1) -> bytes:
    data = dev.ctrl_transfer(VENDOR_IN, request, value, index, length, timeout=1000)
    return bytes(data)


def set_mode(dev, mode: int) -> None:
    ctrl_out(dev, HACKRF_VENDOR_REQUEST_SET_TRANSCEIVER_MODE, mode, 0, b"")


def set_freq(dev, freq_hz: int) -> None:
    mhz = freq_hz // 1_000_000
    hz  = freq_hz % 1_000_000
    payload = struct.pack("<II", mhz, hz)
    ctrl_out(dev, HACKRF_VENDOR_REQUEST_SET_FREQ, 0, 0, payload)


def set_sample_rate(dev, freq_hz: int, divider: int = 1) -> None:
    payload = struct.pack("<II", freq_hz, divider)
    ctrl_out(dev, HACKRF_VENDOR_REQUEST_SAMPLE_RATE_SET, 0, 0, payload)


def set_baseband_filter_bw(dev, bw_hz: int) -> None:
    value = bw_hz & 0xFFFF
    index = (bw_hz >> 16) & 0xFFFF
    ctrl_out(dev, HACKRF_VENDOR_REQUEST_BASEBAND_FILTER_BW, value, index, b"")


def set_amp_enable(dev, enabled: bool) -> None:
    ctrl_out(dev, HACKRF_VENDOR_REQUEST_AMP_ENABLE, 1 if enabled else 0, 0, b"")


def set_antenna_enable(dev, enabled: bool) -> None:
    ctrl_out(dev, HACKRF_VENDOR_REQUEST_ANTENNA_ENABLE, 1 if enabled else 0, 0, b"")


def set_lna_gain(dev, gain: int) -> None:
    resp = ctrl_in(dev, HACKRF_VENDOR_REQUEST_SET_LNA_GAIN, 0, gain, 1)
    if not resp:
        raise RuntimeError("Failed to set LNA gain")


def set_vga_gain(dev, gain: int) -> None:
    resp = ctrl_in(dev, HACKRF_VENDOR_REQUEST_SET_VGA_GAIN, 0, gain, 1)
    if not resp:
        raise RuntimeError("Failed to set VGA gain")


def set_txvga_gain(dev, gain: int) -> None:
    resp = ctrl_in(dev, HACKRF_VENDOR_REQUEST_SET_TXVGA_GAIN, 0, gain, 1)
    if not resp:
        raise RuntimeError("Failed to set TXVGA gain")


def configure_device(dev) -> None:
    # Host config only. Firmware owns PPS timing + RX/TX switching.
    set_mode(dev, TRANSCEIVER_MODE_OFF)
    set_sample_rate(dev, SAMPLE_RATE_HZ, 1)
    set_baseband_filter_bw(dev, 5_000_000)
    set_freq(dev, CENTER_FREQ_HZ)
    set_amp_enable(dev, bool(AMP_ENABLE))
    set_antenna_enable(dev, bool(ANTENNA_ENABLE))
    set_lna_gain(dev, LNA_GAIN)
    set_vga_gain(dev, VGA_GAIN)
    set_txvga_gain(dev, TXVGA_GAIN)
    set_mode(dev, TRANSCEIVER_MODE_OFF)


def make_rx_path() -> str:
    os.makedirs(RX_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(RX_DIR, f"pps_rx_{stamp}.dat")


def tx_worker(dev, tx_fp):
    global running, tx_bytes

    while running:
        chunk = tx_fp.read(CHUNK_SIZE)

        if not chunk:
            tx_fp.seek(0)
            chunk = tx_fp.read(CHUNK_SIZE)
            if not chunk:
                time.sleep(0.01)
                continue

        if len(chunk) < CHUNK_SIZE:
            tx_fp.seek(0)
            remaining = CHUNK_SIZE - len(chunk)
            chunk += tx_fp.read(remaining)
            if len(chunk) < CHUNK_SIZE:
                chunk += b"\x00" * (CHUNK_SIZE - len(chunk))

        try:
            written = dev.write(EP_OUT, chunk, timeout=WRITE_TIMEOUT_MS)
            with tx_lock:
                tx_bytes += int(written)
        except usb.core.USBError as e:
            if is_timeout(e):
                # Normal when firmware is not currently in TX mode
                continue
            log(f"[TX] USB error: {e}")
            running = False
            return
        except Exception as e:
            log(f"[TX] fatal error: {e}")
            running = False
            return


def rx_worker(dev, rx_fp):
    global running, rx_bytes

    while running:
        try:
            data = dev.read(EP_IN, CHUNK_SIZE, timeout=READ_TIMEOUT_MS)
            raw = bytes(data)
            if raw:
                rx_fp.write(raw)
                with rx_lock:
                    rx_bytes += len(raw)
        except usb.core.USBError as e:
            if is_timeout(e):
                # Normal when firmware is not currently in RX mode
                continue
            log(f"[RX] USB error: {e}")
            running = False
            return
        except Exception as e:
            log(f"[RX] fatal error: {e}")
            running = False
            return


def status_worker():
    global running
    last_tx = 0
    last_rx = 0

    while running:
        time.sleep(STATUS_PRINT_SEC)

        with tx_lock:
            cur_tx = tx_bytes
        with rx_lock:
            cur_rx = rx_bytes

        dtx = cur_tx - last_tx
        drx = cur_rx - last_rx
        last_tx = cur_tx
        last_rx = cur_rx

        log(
            f"[STATUS] total_tx={cur_tx} B total_rx={cur_rx} B "
            f"| tx_rate={dtx / STATUS_PRINT_SEC:.1f} B/s "
            f"| rx_rate={drx / STATUS_PRINT_SEC:.1f} B/s"
        )


def handle_signal(signum, frame):
    global running
    running = False
    log(f"\nStopping on signal {signum}...")


def main():
    global running

    if not os.path.isfile(TX_FILE):
        raise FileNotFoundError(f"TX file not found: {TX_FILE}")

    rx_path = make_rx_path()

    log(f"TX file:      {TX_FILE}")
    log(f"RX output:    {rx_path}")
    log(f"Sample rate:  {SAMPLE_RATE_HZ} Hz")
    log(f"Center freq:  {CENTER_FREQ_HZ} Hz")

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("HackRF not found")

    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass

    dev.set_configuration()
    usb.util.claim_interface(dev, 0)

    configure_device(dev)
    log("HackRF configured. Firmware remains in OFF mode until PPS scheduler switches modes.")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    with open(TX_FILE, "rb") as tx_fp, open(rx_path, "wb") as rx_fp:
        tx_thread = threading.Thread(target=tx_worker, args=(dev, tx_fp), daemon=True)
        rx_thread = threading.Thread(target=rx_worker, args=(dev, rx_fp), daemon=True)
        st_thread = threading.Thread(target=status_worker, daemon=True)

        tx_thread.start()
        rx_thread.start()
        st_thread.start()

        try:
            while running:
                time.sleep(0.2)
                rx_fp.flush()
        finally:
            running = False
            time.sleep(0.2)
            try:
                set_mode(dev, TRANSCEIVER_MODE_OFF)
            except Exception:
                pass
            try:
                usb.util.release_interface(dev, 0)
            except Exception:
                pass
            usb.util.dispose_resources(dev)

    log("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"Fatal: {e}")
        sys.exit(1)