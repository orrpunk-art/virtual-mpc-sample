#!/usr/bin/env python3
"""Minimal QMP client for the lab VM (TCP port lab.PORTS["qmp"]).

    qmp.py screendump out.png
    qmp.py cmd '{"execute": "query-status"}'
"""
import json
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lab import HOST, PORTS  # noqa: E402


def call(cmd):
    s = socket.create_connection((HOST, PORTS["qmp"]))
    f = s.makefile("rw")
    json.loads(f.readline())  # greeting
    for msg in ({"execute": "qmp_capabilities"}, cmd):
        f.write(json.dumps(msg) + "\n")
        f.flush()
        while True:
            reply = json.loads(f.readline())
            if "event" not in reply:
                break
        if "error" in reply:
            sys.exit(f"qmp error: {reply['error']}")
    return reply.get("return")


if __name__ == "__main__":
    if sys.argv[1] == "screendump":
        call({"execute": "screendump",
              "arguments": {"filename": os.path.abspath(sys.argv[2]), "format": "png"}})
    else:
        print(json.dumps(call(json.loads(sys.argv[2])), indent=1))
