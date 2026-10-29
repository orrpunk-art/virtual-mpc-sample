#!/usr/bin/env python3
"""Run a shell command on the VM's virtio console and print its output.

    con.py [--wait SECONDS] 'command'

The console (TCP port lab.PORTS["console"]) is a root shell via lab autologin. The command is
sent over the socket; output is read back from out/run/console.log between markers.
Use it when ssh is not available; prefer `python3 lab.py ssh` otherwise.
"""
import argparse
import os
import socket
import time
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lab import HOST, PORTS  # noqa: E402

RUN = os.path.join(os.path.dirname(__file__), "..", "out", "run")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=60.0)
    ap.add_argument("command")
    args = ap.parse_args()
    tag = uuid.uuid4().hex[:8]
    log = os.path.join(RUN, "console.log")
    offset = os.path.getsize(log)

    s = socket.create_connection((HOST, PORTS["console"]))
    # markers are built from two halves so the echoed command line never matches them
    s.sendall(f"echo B''{tag}; ( {args.command} ) 2>&1; echo E''{tag}\n".encode())

    begin, end = f"\nB{tag}\n", f"\nE{tag}\n"
    deadline = time.time() + args.wait
    text = ""
    while time.time() < deadline:
        with open(log, "rb") as f:
            f.seek(offset)
            text = "\n" + f.read().decode(errors="replace").replace("\r", "")
        if end in text:
            break
        time.sleep(0.3)
    s.close()
    b = text.find(begin)
    e = text.find(end)
    if b < 0 or e < 0:
        print(text)
        raise SystemExit("timed out before the command finished")
    print(text[b + len(begin): e + 1], end="")


if __name__ == "__main__":
    main()
