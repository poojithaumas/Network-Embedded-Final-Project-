import socket
import select
import time
import numpy as np
import math
from collections import deque
import csv

# ============================================================
#                    SERVER SETUP
# ============================================================

server = socket.socket()
server.bind(("0.0.0.0", 5000))
server.listen(5)
server.setblocking(False)

print("Waiting for ESP32 devices...")

sockets = [server]
buffers = {}
device_id = {}              # socket -> "A" or "B"
events = {"A": {}, "B": {}} # raw ESP timestamps in µs
event_PC = {}               # PC timestamp (monotonic seconds)

# ============================================================
#                    CSV LOGGING
# ============================================================

csv_file = open("sync_data.csv", "w", newline="")
csv_writer = csv.writer(csv_file)
csv_writer.writerow([
    "event_no",
    "tA_corr_s", "tB_corr_s",
    "offset_us",
    "netdelayA_us", "netdelayB_us",
    "residA_us", "residB_us",
    "driftA_ppm", "driftB_ppm", "relative_drift_ppm"
])
csv_file.flush()

# ============================================================
#                   CALIBRATION STATE
# ============================================================

CALIB_SAMPLES = 20
calib_A = []
calib_B = []
calib_PC = []
calib_count = 0
calibration_done = False

a1 = b1 = None
a2 = b2 = None

# ============================================================
#               RESIDUAL & DELAY TRACKING (µs)
# ============================================================

RESID_WINDOW = 500
residA_us_history = deque(maxlen=RESID_WINDOW)
residB_us_history = deque(maxlen=RESID_WINDOW)
netdelayA_us_history = deque(maxlen=RESID_WINDOW)
netdelayB_us_history = deque(maxlen=RESID_WINDOW)

# ============================================================
#                   WRAPAROUND HANDLING
# ============================================================

MICROS_WRAP = 2**32
WRAP_THRESHOLD_US = 1_000_000

last_raw_ts = {"A": None, "B": None}
wrap_accum = {"A": 0, "B": 0}

def fix_wraparound(dev, ts_raw):
    last = last_raw_ts[dev]
    if last is None:
        last_raw_ts[dev] = ts_raw
        return ts_raw

    if ts_raw < last and (last - ts_raw) > WRAP_THRESHOLD_US:
        wrap_accum[dev] += MICROS_WRAP

    last_raw_ts[dev] = ts_raw
    return ts_raw + wrap_accum[dev]

# ============================================================
#                    LINEAR REGRESSION
# ============================================================

def fit_esp_to_pc(us_list, pc_list):
    x = np.array(us_list, dtype=float) / 1_000_000.0
    y = np.array(pc_list, dtype=float)

    A = np.vstack([x, np.ones(len(x))]).T
    a, b = np.linalg.lstsq(A, y, rcond=None)[0]

    y_pred = a*x + b
    rmse = math.sqrt(((y - y_pred)**2).mean())

    return float(a), float(b), float(rmse)

# ============================================================
#                    METRIC COMPUTATION
# ============================================================

def compute_metrics(event_no, tA_us, tB_us, tPC_s):
    global a1, b1, a2, b2

    tA_corr = a1 * (tA_us / 1e6) + b1
    tB_corr = a2 * (tB_us / 1e6) + b2

    residA_us = (tA_corr - tPC_s) * 1e6
    residB_us = (tB_corr - tPC_s) * 1e6

    netdelayA_us = (tPC_s - tA_corr) * 1e6
    netdelayB_us = (tPC_s - tB_corr) * 1e6

    offset_us = (tA_corr - tB_corr) * 1e6

    residA_us_history.append(residA_us)
    residB_us_history.append(residB_us)
    netdelayA_us_history.append(netdelayA_us)
    netdelayB_us_history.append(netdelayB_us)

    driftA_ppm = (a1 - 1.0) * 1e6
    driftB_ppm = (a2 - 1.0) * 1e6
    relative_drift_ppm = (a2 - a1) * 1e6

    print(f"A_corr = {tA_corr:.9f} s   resid={residA_us:.3f} µs   delay={netdelayA_us:.3f} µs")
    print(f"B_corr = {tB_corr:.9f} s   resid={residB_us:.3f} µs   delay={netdelayB_us:.3f} µs")
    print(f"Offset (A-B) = {offset_us:.3f} µs")
    print(f"Drift A={driftA_ppm:.2f} ppm, Drift B={driftB_ppm:.2f} ppm, Rel={relative_drift_ppm:.2f} ppm\n")

    csv_writer.writerow([
        event_no,
        tA_corr, tB_corr,
        offset_us,
        netdelayA_us, netdelayB_us,
        residA_us, residB_us,
        driftA_ppm, driftB_ppm, relative_drift_ppm
    ])
    csv_file.flush()

# ============================================================
#                   MAIN CALIBRATION HANDLER
# ============================================================

def process_calibration_and_apply(event_no, tA_us, tB_us, tPC_s):
    global calib_count, calibration_done, a1, b1, a2, b2

    if not calibration_done:
        calib_A.append(tA_us)
        calib_B.append(tB_us)
        calib_PC.append(tPC_s)
        calib_count += 1

        print(f"CALIB: {calib_count}/{CALIB_SAMPLES}")

        if calib_count >= CALIB_SAMPLES:
            print("\n=== CALIBRATION COMPLETE ===")

            a1, b1, _ = fit_esp_to_pc(calib_A, calib_PC)
            a2, b2, _ = fit_esp_to_pc(calib_B, calib_PC)

            calibration_done = True

            print(f"A->PC: a1={a1:.12f}  b1={b1:.9f}")
            print(f"B->PC: a2={a2:.12f}  b2={b2:.9f}")
            print("Models frozen.\n")

        return

    compute_metrics(event_no, tA_us, tB_us, tPC_s)

# ============================================================
#                 PAIRING FUNCTION
# ============================================================

def try_compute(event_no):
    if event_no in events["A"] and event_no in events["B"]:
        tA = events["A"][event_no]
        tB = events["B"][event_no]

        print(f"\nEVENT {event_no}")
        print(f"tA = {tA} µs")
        print(f"tB = {tB} µs")

        tPC = time.monotonic()
        event_PC[event_no] = tPC
        print(f"PC_time = {tPC:.9f} s")

        process_calibration_and_apply(event_no, tA, tB, tPC)

# ============================================================
#                       MAIN LOOP
# ============================================================

while True:
    readable, _, _ = select.select(sockets, [], [], 0.05)

    for s in readable:
        if s is server:
            conn, addr = server.accept()
            conn.setblocking(False)
            sockets.append(conn)
            buffers[conn] = ""
            print(f"Device connected from {addr}")
            continue

        data = s.recv(1024).decode(errors="ignore")
        if not data:
            continue

        buffers[s] += data

        while "\n" in buffers[s]:
            line, buffers[s] = buffers[s].split("\n", 1)
            line = line.strip()

            # ------------------ DEVICE ID ------------------
            if line.startswith("ID:"):
                dev = line.split("ID:")[1].strip()  # "A" or "B"
                device_id[s] = dev
                print(f"Registered device as {dev}")
                continue

            # ensure the device has sent ID first
            if s not in device_id:
                print("ERROR: Device sent data before ID! Ignoring:", line)
                continue

            dev = device_id[s]

            # ------------------ EVENT FORMAT ------------------
            if not (line.startswith("tA") or line.startswith("tB")) or "=" not in line:
                print("Invalid:", line)
                continue

            left, ts_str = line.split("=", 1)
            prefix = left[:2]      # tA or tB
            event_no = int(left[2:])
            ts_val = int(ts_str)

            # strict verification: A must send tA, B must send tB
            if prefix[1] != dev:
                print(f"WARNING: Device {dev} sent mismatched prefix {prefix}")
                continue

            # wraparound correction
            ts_val = fix_wraparound(dev, ts_val)

            # store
            events[dev][event_no] = ts_val
            print(f"{prefix}{event_no} = {ts_val}")

            try_compute(event_no)
