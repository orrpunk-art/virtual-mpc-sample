#!/usr/bin/env python3
"""Lab copy of the stock USB gadget scheme without its UAC2 (USB audio) function.

    gadget_scheme.py STOCK.scheme > LAB.scheme

The lab's USB device controller is dummy_hcd, which has no isochronous endpoints. The UAC2
function cannot bind there (afunc_bind: -ENODEV), and that takes the whole gadget down,
the NCM network (192.168.155.1) and USB MIDI with it. Everything else stays as in the
stock file, which is read from the firmware at build time and never committed.
"""
import re
import sys


def drop_block(text, name):
    """Remove `name : { ... };` with nested braces."""
    m = re.search(r"\n(\s*)" + re.escape(name) + r"\s*:\s*\{", text)
    if not m:
        raise SystemExit(f"{name} not found")
    depth, i = 0, m.end() - 1
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
        if depth == 0:
            break
    end = text.index(";", i) + 1
    return text[:m.start()] + text[end:]


def main():
    s = open(sys.argv[1]).read()
    s = drop_block(s, "uac2_audio")
    s, n = re.subn(r'\{\s*name\s*=\s*"uac2\.audio";\s*function\s*=\s*"uac2_audio";\s*\},\s*', "", s)
    if n != 1:
        raise SystemExit("uac2.audio config entry not found")
    assert "uac2" not in s and "ncm_network" in s and "midi_midi" in s
    sys.stdout.write(s)


if __name__ == "__main__":
    main()
