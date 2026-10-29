#!/usr/bin/env python3
"""Probe the control-surface input protocol in the lab VM.

    probe_cs.py STATUS_HEX FIRST_ID LAST_ID [--on 7F --off 00] [--settle 0.4]

For each id, sends `STATUS id on` then `STATUS id off` on the control-surface UART and
compares a QMP screendump before and after. Ids that change the screen are reported and
their screenshots kept in out/shots/probe-<status>-<id>.png. VM only: nothing here can
reach a physical device.
"""
import argparse
import hashlib
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import qmp  # noqa: E402
from lab import HOST, PORTS  # noqa: E402

LAB = os.path.join(os.path.dirname(__file__), "..")
SHOTS = os.path.join(LAB, "out", "shots")


def shot(name):
    path = os.path.abspath(os.path.join(SHOTS, name))
    qmp.call({"execute": "screendump", "arguments": {"filename": path, "format": "png"}})
    with open(path, "rb") as f:
        return path, hashlib.sha256(f.read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("status")
    ap.add_argument("first", type=int)
    ap.add_argument("last", type=int)
    ap.add_argument("--on", default="7F")
    ap.add_argument("--off", default="00")
    ap.add_argument("--settle", type=float, default=0.4)
    ap.add_argument("--release-status", help="status byte for the release frame (e.g. 80 for note-off); default: same as STATUS")
    a = ap.parse_args()
    os.makedirs(SHOTS, exist_ok=True)
    st, on, off = int(a.status, 16), int(a.on, 16), int(a.off, 16)
    rst = int(a.release_status, 16) if a.release_status else st

    s = socket.create_connection((HOST, PORTS["cs"]))
    s.settimeout(0.05)

    _, base = shot("probe-base.png")
    time.sleep(a.settle)
    _, again = shot("probe-base2.png")
    if base != again:
        sys.exit("screen is not static; cannot diff (animation on this page?)")

    for i in range(a.first, a.last + 1):
        s.sendall(bytes([st, i, on]))
        time.sleep(0.05)
        s.sendall(bytes([rst, i, off]))
        time.sleep(a.settle)
        try:
            back = s.recv(4096)
        except socket.timeout:
            back = b""
        path, h = shot(f"probe-{st:02x}-{i:03d}.png")
        changed = h != base
        if changed or back:
            print(f"{st:02X} {i:3d}: screen {'CHANGED' if changed else 'same'}"
                  f"{'  uart<- ' + back.hex(' ') if back else ''}", flush=True)
        if changed:
            base = h
        else:
            os.remove(path)


if __name__ == "__main__":
    main()
