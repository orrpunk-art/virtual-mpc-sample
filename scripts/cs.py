#!/usr/bin/env python3
"""Talk to the VM's control-surface UART (TCP port lab.PORTS["cs"]) as the panel MCU would.

    cs.py [--listen SECONDS] [HEX BYTES ...]

Sends the given bytes (e.g. `BF 00 7F`), then prints every byte the application
writes back for SECONDS (default 2). Only one client can hold the socket at a time.
"""
import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lab import HOST, PORTS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=float, default=2.0)
    ap.add_argument("bytes", nargs="*")
    args = ap.parse_args()
    payload = bytes(int(b, 16) for b in args.bytes)

    s = socket.create_connection((HOST, PORTS["cs"]))
    if payload:
        s.sendall(payload)
        print("sent:", payload.hex(" "))
    s.settimeout(0.2)
    got = bytearray()
    end = time.time() + args.listen
    while time.time() < end:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            break
        got += chunk
    print(f"received {len(got)} bytes:", got.hex(" "))


if __name__ == "__main__":
    main()
