#!/usr/bin/env python3
"""Virtual MPC Sample (front and rear panel) for the lab VM.

    python3 panel/server.py [--port 8765]

Front: plays the panel MCU on the control-surface UART (QEMU chardev on TCP, lab.PORTS):
  * answers the firmware-tool Device Enquiry with the bundled MCU version,
  * tracks what the application drives (button LEDs, pad colours),
  * turns clicks and key presses from the web page into input frames (panel/controls.json).
Rear: the External MIDI UART is the MIDI IN/OUT jack pair; the audio jacks are routed by
mpclab-audio-bridge in the VM over the virtio-serial port "mpclab.audio" (routing lines out,
level lines back). The screen comes from QEMU's QMP. All TCP on 127.0.0.1, so it runs on
Windows as well as macOS and Linux. Stdlib only.
"""
import argparse
import base64
import collections
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import lab  # noqa: E402
from lab import HOST, PORTS  # noqa: E402
RUN = os.path.join(HERE, "..", "out", "run")
CONTROLS = os.path.join(HERE, "controls.json")

# Reply to F0 7E xx 06 01 F7, in the layout az0x-firmware-tool parses:
# F0 7E 00 06 02 <mfr 47> <product 5C> <2 bytes> <4 version bytes> F7. Version 1.0.0.051 = the
# UpdateImage.rbin shipped in 1.3.0.12, so the stock tool reports "Firmware up-to-date".
ENQUIRY_REPLY = bytes.fromhex("F0 7E 00 06 02 47 5C 00 00 01 00 00 33 F7")

# Pots on the panel (CC 16-18 K1-K3, 19 MAIN VOLUME, 20 fader) and their start positions.
# The MCU reports all five in one go when the app asks (MCU fn 0x080172C4, called from its
# SysEx handler); the only SysEx the app sends at start is 0x42, so we answer that.
# MAIN VOLUME is software volume on this device: starting it at 0 would mean silence.
# Only MAIN VOLUME is reported, though: the app takes a K1-K3 or fader value as a move and
# applies it to the open page (measured: CC 16 = 64 put the Trim start of the current pad at
# 50 %), and our reports come seconds after start, when a project may already be loaded.
UART_BYTE_S = 0.00032                   # one byte at 31250 baud (10 bits)
POTS = {16: 64, 17: 64, 18: 64, 19: 100, 20: 64}
REPORTED_POTS = (19,)
INIT_REQUEST = bytes.fromhex("F0 47 7F 5C 42 00 00 F7")


class Panel:
    """State of the emulated MCU and the UART connection to QEMU."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sock = None
        self.leds = {}      # LED id -> state byte (B0 id state)
        self.pads = {}      # pad index -> [r, g, b] (0..127)
        self.values = {}    # 14-bit outbound values (BF pairs), id -> 0..16383
        self.log = []       # recent app->MCU frames (hex)
        self.connected = False
        self.rx_bytes = 0
        self.pots = dict(POTS)
        self.wlock = threading.Lock()

    # --- UART (see uart_loop) --------------------------------------------
    def attach(self, s):
        with self.lock:
            self.sock, self.connected = s, s is not None
        self.buf = bytearray()
        if s is not None:
            # The app learns the pot positions (MAIN VOLUME = software volume) only from MCU
            # reports. Report them whenever the panel (re)connects, so a panel server started
            # after the app does not leave it at volume 0. At boot this is lost harmlessly and
            # the 0x42 request below reports them again.
            threading.Timer(0.5, self.report_pots).start()

    def feed(self, chunk):
        self.buf += chunk
        self.rx_bytes += len(chunk)
        while True:
            frame, self.buf = take_frame(self.buf)
            if frame is None:
                break
            self.on_frame(frame)

    def on_frame(self, f):
        with self.lock:
            self.log = (self.log + [f"{time.time():.1f} " + f.hex(" ")])[-2000:]
        if f[:1] == b"\xF0":
            if len(f) == 6 and f[1] == 0x7E and f[3:5] == b"\x06\x01":
                self.send(ENQUIRY_REPLY)
            elif f == INIT_REQUEST:
                # Sent right away the report is lost: the app writes 0x42 before it starts
                # reading, and the lab's serial-midi driver drops input until then. Absolute
                # values, so sending twice is harmless.
                for delay in (1.5, 5.0):
                    threading.Timer(delay, self.report_pots).start()
            elif f[:7] == bytes.fromhex("F0 47 7F 5C 65 00 04") and len(f) == 12:
                with self.lock:
                    self.pads[f[7]] = list(f[8:11])
            return
        st = f[0] & 0xF0
        ch = f[0] & 0x0F
        if st == 0xB0 and len(f) == 3:
            with self.lock:
                if ch == 0:
                    self.leds[f[1]] = f[2]
                elif ch == 15:
                    # 14-bit value: even id = MSB, odd id = LSB
                    cid, half = f[1] >> 1, f[1] & 1
                    v = self.values.get(cid, 0)
                    v = (v & 0x7F) | (f[2] << 7) if half == 0 else (v & ~0x7F) | f[2]
                    self.values[cid] = v

    def report_pots(self):
        with self.lock:
            pots = dict(self.pots)
        self.send(b"".join(bytes([0xB0, cc, pots[cc]]) for cc in REPORTED_POTS))

    def send(self, data):
        with self.lock:
            s = self.sock
            # remember pot positions the app has been told about
            for i in range(0, len(data) - 2):
                if data[i] == 0xB0 and data[i + 1] in self.pots:
                    self.pots[data[i + 1]] = data[i + 2]
        if s is None:
            return False
        with self.wlock:                       # paced like the MCU's UART: the app's reads see
            for i in range(len(data)):         # messages split across reads, as on the device
                s.sendall(data[i:i + 1])
                time.sleep(UART_BYTE_S)
        return True

    def snapshot(self):
        with self.lock:
            return {"connected": self.connected, "leds": self.leds, "pads": self.pads,
                    "values": self.values, "pots": self.pots, "rx_bytes": self.rx_bytes, "log": self.log[-30:], "sysex": [l for l in self.log if " f0 " in l and " 65 00 04 " not in l][-40:]}


def take_frame(buf):
    """Split one MIDI message off the front of buf. Returns (frame or None, rest)."""
    while buf and buf[0] < 0x80 and buf[0] != 0xF0:
        buf = buf[1:]  # stray data byte (no running status from this app)
    if not buf:
        return None, buf
    if buf[0] == 0xF0:
        end = buf.find(0xF7)
        if end < 0:
            return None, buf
        return bytes(buf[:end + 1]), buf[end + 1:]
    n = 2 if (buf[0] & 0xF0) in (0xC0, 0xD0) else 3
    if buf[0] >= 0xF8:
        n = 1
    if len(buf) < n:
        return None, buf
    return bytes(buf[:n]), buf[n:]


def uart_loop(port, owner):
    """Keep a connection to a QEMU chardev (TCP server); feed received bytes to owner."""
    while True:
        try:
            s = socket.create_connection((HOST, port))
            owner.attach(s)
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                owner.feed(chunk)
        except OSError:
            pass
        owner.attach(None)
        time.sleep(1)


class MidiParser:
    """Byte stream -> MIDI messages, with running status (ALSA's sequencer may use it)."""

    def __init__(self):
        self.status, self.buf, self.sysex = None, [], None

    def feed(self, data):
        out = []
        for b in data:
            if b >= 0xF8:                       # realtime, may sit inside other messages
                out.append(bytes([b]))
            elif b == 0xF0:
                self.sysex, self.status = [b], None
            elif self.sysex is not None and b < 0x80:
                self.sysex.append(b)
            elif b == 0xF7 and self.sysex is not None:
                out.append(bytes(self.sysex + [b]))
                self.sysex = None
            elif b >= 0x80:
                self.sysex = None
                self.buf = [b]
                self.status = b if b < 0xF0 else None
                if b == 0xF6:                   # tune request, no data
                    out.append(bytes([b]))
                    self.buf = []
            elif self.buf or self.status is not None:
                if not self.buf:
                    self.buf = [self.status]
                self.buf.append(b)
                st = self.buf[0]
                need = 2 if (st & 0xF0) in (0xC0, 0xD0) or st in (0xF1, 0xF3) else 3
                if len(self.buf) == need:
                    out.append(bytes(self.buf))
                    self.buf = []
        return out


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def describe(m):
    """Human-readable text for one MIDI message."""
    st = m[0]
    if st == 0xF0:
        return f"SysEx {len(m)} bytes"
    names = {0xF8: "Clock", 0xFA: "Start", 0xFB: "Continue", 0xFC: "Stop", 0xFE: "Active sensing", 0xFF: "Reset"}
    if st in names:
        return names[st]
    if st == 0xF2 and len(m) == 3:
        return f"Song position {m[1] | m[2] << 7}"
    kind, ch = st & 0xF0, (st & 0x0F) + 1
    note = lambda n: f"{NOTE_NAMES[n % 12]}{n // 12 - 1} ({n})"
    if kind == 0x90 and len(m) == 3:
        return f"ch{ch} Note on {note(m[1])} vel {m[2]}" if m[2] else f"ch{ch} Note off {note(m[1])}"
    if kind == 0x80 and len(m) == 3:
        return f"ch{ch} Note off {note(m[1])}"
    if kind == 0xA0 and len(m) == 3:
        return f"ch{ch} Poly pressure {note(m[1])} {m[2]}"
    if kind == 0xB0 and len(m) == 3:
        return f"ch{ch} CC {m[1]} = {m[2]}"
    if kind == 0xC0 and len(m) == 2:
        return f"ch{ch} Program {m[1] + 1}"
    if kind == 0xD0 and len(m) == 2:
        return f"ch{ch} Channel pressure {m[1]}"
    if kind == 0xE0 and len(m) == 3:
        return f"ch{ch} Pitch bend {(m[1] | m[2] << 7) - 8192}"
    return "?"


class ExtMidi:
    """The rear MIDI jacks: External MIDI UART. Bytes from the VM are MIDI OUT, bytes we
    write are MIDI IN. With the loop cable patched, MIDI OUT is echoed straight into MIDI IN."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sock, self.connected, self.loop = None, False, False
        self.out_parser, self.in_parser = MidiParser(), MidiParser()
        self.log = collections.deque(maxlen=300)   # (time, "out"|"in", hex, text)
        self.counts = {"out": 0, "in": 0}
        self.clock = collections.deque(maxlen=48)  # arrival times of MIDI OUT clock ticks
        self.seq = 0

    def attach(self, s):
        with self.lock:
            self.sock, self.connected = s, s is not None

    def feed(self, chunk):                          # MIDI OUT jack
        if self.loop:
            self.send(chunk, echo=True)
        for m in self.out_parser.feed(chunk):
            self.record("out", m)

    def send(self, data, echo=False):               # MIDI IN jack
        with self.lock:
            s = self.sock
        if s is None:
            return False
        s.sendall(data)
        for m in self.in_parser.feed(data):
            self.record("in", m, echo)
        return True

    def record(self, d, m, echo=False):
        with self.lock:
            self.counts[d] += 1
            if m[0] == 0xF8:
                if d == "out":
                    self.clock.append(time.time())
                return                              # 24 per beat: counted, not listed
            if m[0] == 0xFE:
                return
            self.seq += 1
            self.log.append((self.seq, round(time.time(), 3), d, m.hex(" "), describe(m) + (" (loop)" if echo else "")))

    def bpm(self):
        with self.lock:
            c = list(self.clock)
        if len(c) < 12 or time.time() - c[-1] > 1:
            return None
        return round(60 / (24 * (c[-1] - c[0]) / (len(c) - 1)), 1)

    def snapshot(self, since=0):
        with self.lock:
            log = [e for e in self.log if e[0] > since]
            return {"connected": self.connected, "loop": self.loop, "counts": dict(self.counts),
                    "log": log[-100:]}


# Rear jacks and what can be plugged into them. "loop" is one cable between OUT and IN.
JACKS = {
    "PHONES": ["none", "mac"],
    "SYNC OUT": ["none"],          # not emulated: how the app drives CV/gate is unknown
    "MIDI OUT": ["none", "monitor", "loop"],
    "MIDI IN": ["none", "keyboard", "loop"],
    "AUDIO OUT": ["none", "mac", "loop"],
    "AUDIO IN": ["none", "tone", "loop", "input"],
}
LOOP_PAIRS = {"MIDI OUT": "MIDI IN", "MIDI IN": "MIDI OUT", "AUDIO OUT": "AUDIO IN", "AUDIO IN": "AUDIO OUT"}


class Rear:
    """Patch state of the rear jacks. Talks to mpclab-audio-bridge in the VM over the
    virtio-serial port (see uart_loop): sends routing, receives levels."""

    def __init__(self, midi):
        self.lock = threading.Lock()
        self.midi = midi
        self.patch = {j: "none" for j in JACKS}
        self.rec_gain = 50
        self.tone_hz = 440
        self.sock, self.buf = None, b""
        self.lv, self.lv_time = {}, None

    def attach(self, s):
        with self.lock:
            self.sock, self.buf = s, b""
        self.write_routing()

    def feed(self, chunk):
        self.buf += chunk
        *lines, self.buf = self.buf.split(b"\n")
        for ln in lines:
            words = ln.decode(errors="replace").split()
            if words[:2] != ["levels", "out"] or "in" not in words:
                continue
            i = words.index("in")
            try:
                lv = {"out": [float(x) for x in words[2:i]], "in": [float(x) for x in words[i + 1:i + 3]]}
                lv.update({k: int(v) for k, v in zip(words[i + 3::2], words[i + 4::2])})   # drop, under counters
            except ValueError:
                continue
            with self.lock:
                self.lv, self.lv_time = lv, time.time()

    def route(self, jack, target):
        if jack not in JACKS or target not in JACKS[jack]:
            raise KeyError(f"{jack} -> {target}")
        with self.lock:
            old = self.patch[jack]
            self.patch[jack] = target
            pair = LOOP_PAIRS.get(jack)
            if pair and target == "loop":
                self.patch[pair] = "loop"
            elif pair and old == "loop":
                self.patch[pair] = "none"
            self.midi.loop = self.patch["MIDI OUT"] == "loop"
        self.write_routing()

    def set_gain(self, v):
        with self.lock:
            self.rec_gain = max(0, min(100, int(v)))
        self.write_routing()

    def routing(self):
        p = self.patch
        # Nothing in PHONES or AUDIO OUT: the built-in speaker plays, i.e. the Mac. A plug
        # elsewhere (the loop cable) mutes the speaker, as the jack detectors do on the device.
        outs = (p["PHONES"], p["AUDIO OUT"])
        out = "on" if "mac" in outs or outs == ("none", "none") else "off"
        # The app drives 4 output channels. 1-2 follow MAIN VOLUME (measured: CC 19 = 5 / 40 /
        # 127 gave peaks 0.04 / 0.56 / 1.0 there); 3-4 carry a second mix at a fixed level,
        # taken here as the phones pair. Speaker and AUDIO OUT play channels 1-2.
        pair = 2 if p["PHONES"] == "mac" and p["AUDIO OUT"] != "mac" else 0
        # "input" = the computer's microphone, recorded by the panel page (it asks for access).
        src = {"none": "silence", "tone": "tone", "loop": "loop", "input": "host"}[p["AUDIO IN"]]
        return {"out": out, "pair": pair, "in": src, "gain": self.rec_gain, "tone_hz": self.tone_hz}

    def routing_line(self):
        with self.lock:
            r = self.routing()
        return "routing " + " ".join(f"{k}={v}" for k, v in r.items()) + "\n"

    def write_routing(self):
        line = self.routing_line()
        with self.lock:
            s = self.sock
        if s is not None:
            try:
                s.sendall(line.encode())
            except OSError:
                pass

    def levels(self):
        if self.lv_time is None:
            return {"age": None}
        return {"age": round(time.time() - self.lv_time, 1), **self.lv}

    def snapshot(self):
        with self.lock:
            return {"patch": dict(self.patch), "jacks": JACKS, "rec_gain": self.rec_gain,
                    "routing": self.routing(), "levels": self.levels()}


class Qmp:
    """Screen capture through QEMU's QMP socket (the app ignores tablet/touch input)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.f = None
        self.png = os.path.join(RUN, "panel-screen.png")
        self.ppm = os.path.join(RUN, "panel-screen.ppm")

    def call(self, cmd):
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.f is None:
                        s = socket.create_connection((HOST, PORTS["qmp_panel"]), timeout=5)
                        self.f = s.makefile("rw")
                        json.loads(self.f.readline())
                        self.f.write('{"execute":"qmp_capabilities"}\n')
                        self.f.flush()
                        self.f.readline()
                    self.f.write(json.dumps(cmd) + "\n")
                    self.f.flush()
                    while True:
                        r = json.loads(self.f.readline())
                        if "event" not in r:
                            return r
                except (OSError, ValueError):
                    self.f = None
                    if attempt:
                        return None

    def power(self):
        """'on' / 'off' (guest powered off; QEMU keeps running, POWER turns it on), cached 1 s."""
        now = time.time()
        if now - getattr(self, "_pw_t", 0) > 1:
            r = self.call({"execute": "query-status"}) or {}
            self._pw = "off" if r.get("return", {}).get("status") == "shutdown" else "on"
            self._pw_t = now
        return getattr(self, "_pw", "on")

    def frame(self):
        """Screendump as PPM (half the cost of PNG inside QEMU's main loop), returned in the
        VNC frame format the page draws: u16 w, u16 h (LE) + RGBX."""
        r = self.call({"execute": "screendump", "arguments": {"filename": self.ppm, "format": "ppm"}})
        if not r or "error" in r:
            return None
        with open(self.ppm, "rb") as f:
            data = f.read()
        magic, w, h, maxval, rgb = data.split(maxsplit=4)
        w, h = int(w), int(h)
        out = bytearray(w * h * 4)
        for c in range(3):
            out[c::4] = rgb[c:w * h * 3:3]
        return struct.pack("<HH", w, h) + bytes(out)

    def screen(self):
        r = self.call({"execute": "screendump", "arguments": {"filename": self.png, "format": "png"}})
        if not r or "error" in r:
            return None
        with open(self.png, "rb") as f:
            return f.read()


class SdCard:
    """The rear SD slot: QEMU sd-card "sd0"; inserting copies the host folder sdcard/ onto a fresh card."""

    def __init__(self, qmp):
        self.qmp, self.lock, self.inserted, self.error = qmp, threading.Lock(), None, ""

    def snapshot(self):
        if self.inserted is None:   # first ask: the VM may have booted with a card in
            r = self.qmp.call({"execute": "query-block"}) or {}
            for b in r.get("return", []):
                if b.get("device") == "sdcard":
                    self.inserted = "inserted" in b
        return {"inserted": bool(self.inserted), "error": self.error, "folder": lab.SDCARD}

    def eject(self):
        r = self.qmp.call({"execute": "eject", "arguments": {"id": "sd0", "force": True}})
        self.inserted = False
        return r

    def insert(self):
        with self.lock:
            if self.inserted is not False:
                # QEMU must let go of the old image first (Windows cannot replace an open file),
                # and the guest must see the card leave: swapped within milliseconds it kept
                # the old mount and its card commands timed out
                self.eject()
                time.sleep(2)
            try:
                img = lab.sdcard_image()
            except (RuntimeError, OSError) as e:
                self.error = str(e)
                return False
            r = self.qmp.call({"execute": "blockdev-change-medium",
                               "arguments": {"id": "sd0", "filename": img, "format": "raw"}})
            self.error = "" if r and "error" not in r else str((r or {}).get("error", "QEMU not reachable"))
            self.inserted = not self.error
            return self.inserted


class Vnc:
    """The app's screen from QEMU's VNC server (-vnc, PORTS["vnc"]): QEMU pushes an update as
    soon as the framebuffer changes, which beats polling screendumps (PNG via QMP) on both
    frame rate and latency. Raw encoding, 32 bpp RGBX; the browser draws it on a canvas."""

    def __init__(self):
        self.cv = threading.Condition()
        self.version, self.frame = 0, None   # frame: u16 w, u16 h (LE) + w*h*4 RGBX bytes

    def wait(self, seen, timeout):
        with self.cv:
            if self.version == seen:
                self.cv.wait(timeout)
            return self.version, self.frame

    def loop(self):
        while True:
            try:
                self.session()
            except (OSError, ValueError, struct.error):
                pass
            time.sleep(1)

    def session(self):
        s = socket.create_connection((HOST, PORTS["vnc"]), timeout=10)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        f = s.makefile("rb")

        def need(n):
            b = f.read(n)
            if len(b) < n:
                raise OSError("vnc closed")
            return b
        need(12)
        s.sendall(b"RFB 003.008\n")
        types = need(need(1)[0])
        if 1 not in types:
            raise ValueError("VNC needs a password")
        s.sendall(b"\x01")
        if struct.unpack(">I", need(4))[0]:
            raise ValueError("VNC refused")
        s.sendall(b"\x01")                                   # shared
        w, h = struct.unpack(">HH", need(4))
        need(16)
        need(struct.unpack(">I", need(4))[0])                # name
        # 32 bpp little-endian true colour, R at bit 0: bytes arrive as R G B X
        s.sendall(struct.pack(">B3xBBBBHHHBBB3x", 0, 32, 24, 0, 1, 255, 255, 255, 0, 8, 16))
        s.sendall(struct.pack(">BxHi", 2, 2, 0) + struct.pack(">i", -223))   # raw + DesktopSize
        fb = bytearray(w * h * 4)
        s.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, w, h))
        s.settimeout(None)
        while True:
            kind = need(1)[0]
            if kind == 2:                                    # bell
                continue
            if kind == 1:                                    # colour map: not used for true colour
                need(3)
                need(struct.unpack(">H", need(2))[0] * 6)
                continue
            if kind == 3:                                    # cut text
                need(3)
                need(struct.unpack(">I", need(4))[0])
                continue
            if kind != 0:
                raise ValueError(f"vnc message {kind}")
            need(1)
            for _ in range(struct.unpack(">H", need(2))[0]):
                x, y, rw, rh, enc = struct.unpack(">HHHHi", need(12))
                if enc == -223:                              # new size
                    w, h, fb = rw, rh, bytearray(rw * rh * 4)
                    continue
                if enc != 0:
                    raise ValueError(f"vnc encoding {enc}")
                data = need(rw * rh * 4)
                if x == 0 and rw == w:                       # full-width band: one copy
                    fb[y * w * 4:(y + rh) * w * 4] = data
                else:
                    for row in range(rh):
                        o = ((y + row) * w + x) * 4
                        fb[o:o + rw * 4] = data[row * rw * 4:(row + 1) * rw * 4]
            with self.cv:
                self.frame = struct.pack("<HH", w, h) + bytes(fb)
                self.version += 1
                self.cv.notify_all()
            s.sendall(struct.pack(">BBHHHH", 3, 1, 0, 0, w, h))   # next update when something changes


class Burst:
    """QEMU's VNC server checks for screen changes less and less often while the screen is
    still (up to every 3 s), so the first frame after a pad hit came 100-500 ms late. For a
    second after each input, screendumps (synchronous, QMP, ~1 ms each as PPM) at ~30 fps
    cover that gap. Measured: they roughly doubled the app's xruns as PNG at this rate."""

    def __init__(self, qmp, clients):
        self.qmp, self.clients, self.until, self.cv = qmp, clients, 0.0, threading.Condition()

    def poke(self):
        with self.cv:
            self.until = time.time() + 1.0
            self.cv.notify()

    def loop(self):
        while True:
            with self.cv:
                while time.time() >= self.until:
                    self.cv.wait()
            t = time.time()
            frame = self.qmp.frame()
            if frame:
                with self.clients.lock:
                    targets = list(self.clients.clients)
                for ws in targets:
                    ws.send(b"\x01" + frame)
            time.sleep(max(0.0, 0.033 - (time.time() - t)))


class Pages:
    """The open panel pages' WebSockets (screendumps after an input go to all of them).
    Sound does not pass here: each page's audio worker talks to QEMU's "mpclab.pcm" chardev
    directly (a WebSocket server, PORTS["pcm"]). Measured: chunks left QEMU every 5.8 ms (max
    gap 9 ms) but came out of a Python relay with gaps up to 16 ms, which the page then had to
    buffer."""

    def __init__(self):
        self.lock, self.clients = threading.Lock(), []


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Ws:
    """Minimal RFC 6455 server side: unfragmented frames, which is what browsers send for
    messages this small."""

    def __init__(self, sock, rfile):
        self.sock, self.rfile, self.lock, self.open = sock, rfile, threading.Lock(), True

    def send(self, data, binary=True):
        n = len(data)
        head = bytes([0x82 if binary else 0x81]) + (bytes([n]) if n < 126 else
                                                     struct.pack(">BH", 126, n) if n < 65536 else struct.pack(">BQ", 127, n))
        with self.lock:
            if not self.open:
                return
            try:
                self.sock.sendall(head + data)
            except OSError:
                self.open = False

    def recv(self):
        h = self.rfile.read(2)
        if len(h) < 2:
            return None, b""
        n = h[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", self.rfile.read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self.rfile.read(8))[0]
        mask = self.rfile.read(4) if h[1] & 0x80 else b"\0\0\0\0"
        data = self.rfile.read(n)
        if n:
            m = (mask * (n // 4 + 1))[:n]
            data = (int.from_bytes(data, "little") ^ int.from_bytes(m, "little")).to_bytes(n, "little")
        return h[0] & 0x0F, data


class AutorunJob:
    """One SD autorun package at a time (lab.run_autorun in a thread); its log for the page."""

    def __init__(self):
        self.lock, self.running, self.log = threading.Lock(), False, []

    def add(self, msg):
        self.log.append(time.strftime("%H:%M:%S ") + msg)

    def start(self, data, reboot, confirm):
        with self.lock:
            if self.running:
                raise RuntimeError("a package is already running")
            self.running, self.log = True, []
        path = os.path.join(RUN, "autorun-upload.zip")
        with open(path, "wb") as f:
            f.write(data)
        threading.Thread(target=self.run, args=(path, reboot, confirm), daemon=True).start()

    def run(self, path, reboot, confirm):
        try:
            lab.run_autorun(path, reboot=reboot, confirm=confirm, log=self.add)
            self.add("done: press Results for the script's output")
        except Exception as e:                       # shown on the page; the server keeps running
            self.add(f"error: {e}")
        finally:
            self.running = False

    def snapshot(self):
        return {"running": self.running, "log": self.log[-40:]}


def frames_for(controls, name, action, value):
    """Translate a panel action into bytes using controls.json templates."""
    c = controls["controls"].get(name)
    if c is None:
        raise KeyError(name)
    tpl = c.get(action)
    if tpl is None:
        raise KeyError(f"{name}: no '{action}' frame")
    fields = dict(c)
    if value is not None:
        fields["value"] = value
        if c.get("bits") == 14:
            v = max(0, min(16383, int(value)))
            fields["msb"], fields["lsb"] = v >> 7, v & 0x7F
    out = bytearray()
    for tok in tpl.split():
        if tok.startswith("{"):
            out.append(int(fields[tok[1:-1]]) & 0x7F)
        else:
            out.append(int(tok, 16))
    return bytes(out)


def make_handler(panel, qmp, midi, rear, sd, vnc, pages, burst, job=None):
    job = job or AutorunJob()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def state(self, since):
            return {**panel.snapshot(), "midi": {**midi.snapshot(since), "bpm": midi.bpm()},
                    "rear": rear.snapshot(), "sdcard": sd.snapshot(), "pcm_port": PORTS["pcm"],
                    "power": qmp.power()}

        def websocket(self):
            """One ordered channel per page: controls in; screen frames, state and audio out."""
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
            self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                              f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            self.close_connection = True
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            ws = Ws(self.connection, self.rfile)
            with open(CONTROLS) as f:
                controls = json.load(f)

            def push():
                seen, since, last, t = -1, 0, None, 0.0
                while ws.open:
                    v, frame = vnc.wait(seen, 0.05)
                    if v != seen and frame:
                        ws.send(b"\x01" + frame)
                        seen = v
                    if time.time() - t >= 0.1:
                        t = time.time()
                        st = json.dumps(self.state(since))
                        if st != last:
                            ws.send(st.encode(), binary=False)
                            last = st
                            log = json.loads(st)["midi"]["log"]
                            since = max([since] + [e[0] for e in log])
            threading.Thread(target=push, daemon=True).start()
            with pages.lock:
                pages.clients.append(ws)
            try:
                while True:
                    op, data = ws.recv()
                    if op is None or op == 8:
                        break
                    if op == 9:
                        with ws.lock:
                            ws.sock.sendall(bytes([0x8A, len(data)]) + data)
                    elif op == 1:
                        m = json.loads(data)
                        try:
                            if m["t"] == "control":
                                panel.send(frames_for(controls, m["name"], m["action"], m.get("value")))
                                burst.poke()
                            elif m["t"] == "send":
                                panel.send(bytes.fromhex(m["hex"]))
                            elif m["t"] == "midi":
                                midi.send(bytes.fromhex(m["hex"]))
                        except (KeyError, ValueError):
                            pass
            except (OSError, ValueError):
                pass
            finally:
                ws.open = False
                with pages.lock:
                    if ws in pages.clients:
                        pages.clients.remove(ws)

        def autorun_post(self, n):
            """POST /autorun?reboot=1&confirm=0 with the zip as the body; POST /autorun/results."""
            if self.path.startswith("/autorun/results"):
                self.rfile.read(n)
                try:
                    return self.reply(200, {"text": lab.autorun_results()})
                except Exception as e:
                    return self.reply(500, {"error": str(e)})
            if not 0 < n <= 512 << 20:
                self.rfile.read(n)
                return self.reply(400, {"error": "send a zip of at most 512 MB"})
            data = self.rfile.read(n)
            q = dict(kv.split("=", 1) for kv in self.path.partition("?")[2].split("&") if "=" in kv)
            try:
                job.start(data, reboot=q.get("reboot", "1") == "1", confirm=q.get("confirm") == "1")
            except RuntimeError as e:
                return self.reply(409, {"error": str(e)})
            return self.reply(200, job.snapshot())

        def do_GET(self):
            if self.path == "/autorun":
                return self.reply(200, job.snapshot())
            if self.path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
                return self.websocket()
            if self.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self.reply(200, f.read(), "text/html; charset=utf-8")
            if self.path == "/controls.json":
                with open(CONTROLS, "rb") as f:
                    return self.reply(200, f.read())
            if self.path.startswith("/screen.png"):
                png = qmp.screen()
                return self.reply(200, png, "image/png") if png else self.reply(503, {"error": "no screen"})
            if self.path.startswith("/state"):
                since = int(self.path.split("since=")[1]) if "since=" in self.path else 0
                return self.reply(200, self.state(since))
            self.reply(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            if self.path.startswith("/autorun"):
                return self.autorun_post(n)
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                if self.path == "/control":
                    with open(CONTROLS) as f:
                        controls = json.load(f)
                    data = frames_for(controls, req["name"], req["action"], req.get("value"))
                elif self.path == "/send":
                    data = bytes.fromhex(req["hex"])
                elif self.path == "/midi":
                    ok = midi.send(bytes.fromhex(req["hex"]))
                    return self.reply(200 if ok else 503, {"ok": ok})
                elif self.path == "/route":
                    rear.route(req["jack"], req["target"])
                    return self.reply(200, rear.snapshot())
                elif self.path == "/power":
                    # the device's power key (QEMU virt: gpio-keys KEY_POWER); the app asks first.
                    # QEMU runs with -no-shutdown: when the guest is off, the key powers it on.
                    st = qmp.call({"execute": "query-status"}) or {}
                    if st.get("return", {}).get("status") == "shutdown":
                        qmp.call({"execute": "system_reset"})
                        r = qmp.call({"execute": "cont"})
                    else:
                        r = qmp.call({"execute": "system_powerdown"})
                    return self.reply(200 if r and "error" not in r else 503, {"ok": bool(r)})
                elif self.path == "/sdcard":
                    ok = sd.insert() if req["action"] == "insert" else bool(sd.eject())
                    return self.reply(200 if ok else 500, sd.snapshot())
                elif self.path == "/recgain":
                    rear.set_gain(req["value"])
                    return self.reply(200, {"ok": True})
                else:
                    return self.reply(404, {"error": "not found"})
            except (KeyError, ValueError) as e:
                return self.reply(400, {"error": str(e)})
            ok = panel.send(data)
            self.reply(200 if ok else 503, {"ok": ok, "sent": data.hex(" ")})

    return H


def selftest():
    f, rest = take_frame(bytearray(bytes.fromhex("F0 47 7F 5C 42 00 00 F7 B0 39 7F 90")))
    assert f == bytes.fromhex("F0 47 7F 5C 42 00 00 F7")
    f, rest = take_frame(rest)
    assert f == bytes.fromhex("B0 39 7F") and take_frame(rest)[0] is None
    p = Panel()
    p.on_frame(bytes.fromhex("F0 47 7F 5C 65 00 04 03 7F 10 00 F7"))
    p.on_frame(bytes.fromhex("BF 04 01"))
    p.on_frame(bytes.fromhex("BF 05 02"))
    assert p.pads[3] == [0x7F, 0x10, 0] and p.values[2] == (1 << 7) | 2
    p.send(bytes.fromhex("B0 13 22"))  # no socket: nothing sent, but the position is kept
    assert p.pots[19] == 0x22
    class Wire:
        def __init__(self): self.out = b""
        def sendall(self, d): self.out += d
    q = Panel()
    q.sock = Wire()
    q.report_pots()                        # only MAIN VOLUME: K1-K3/fader would edit the open page
    assert q.sock.out == bytes([0xB0, 19, 100]), q.sock.out
    ctl = {"controls": {"K1": {"bits": 14, "id": 3, "set": "B0 {id} {msb} B0 {id} {lsb}"}}}
    assert frames_for(ctl, "K1", "set", 16383) == bytes.fromhex("B0 03 7F B0 03 7F")
    # running status, realtime inside a message, sysex
    mp = MidiParser()
    msgs = mp.feed(bytes.fromhex("90 3C 64 40 00 F8 3E 7F F0 7E 00 F7 B0 07"))
    assert msgs == [bytes.fromhex(h) for h in ("90 3C 64", "90 40 00", "F8", "90 3E 7F", "F0 7E 00 F7")], msgs
    assert mp.feed(b"\x10") == [bytes.fromhex("B0 07 10")]
    assert describe(bytes.fromhex("90 3C 64")) == "ch1 Note on C4 (60) vel 100"
    # patching: one loop cable, speaker muted when only the loop is plugged
    m = ExtMidi(); r = Rear(m)
    assert r.routing() == {"out": "on", "pair": 0, "in": "silence", "gain": 50, "tone_hz": 440}
    r.feed(b"levels out 0.5000 0.2500 0 0 in 0.1000 0.2000 drop 3 under 1\n")
    assert r.lv == {"out": [0.5, 0.25, 0.0, 0.0], "in": [0.1, 0.2], "drop": 3, "under": 1}, r.lv
    # WebSocket: a masked client frame decodes; server frames carry the right length header
    import io
    payload, mask = json.dumps({"t": "control"}).encode(), b"\x11\x22\x33\x44"
    masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    assert Ws(None, io.BytesIO(bytes([0x81, 0x80 | len(payload)]) + mask + masked)).recv() == (1, payload)

    class Sink:
        def __init__(self): self.out = b""
        def sendall(self, d): self.out += d
    k = Sink()
    Ws(k, None).send(b"x" * 70000)
    assert k.out[:2] == b"\x82\x7f" and struct.unpack(">Q", k.out[2:10])[0] == 70000
    r.route("PHONES", "mac")
    assert r.routing()["pair"] == 2
    r.route("PHONES", "none")
    r.route("AUDIO OUT", "loop")
    assert r.patch["AUDIO IN"] == "loop" and r.routing()["out"] == "off" and r.routing()["in"] == "loop"
    r.route("AUDIO IN", "tone")
    assert r.patch["AUDIO OUT"] == "none" and r.routing()["out"] == "on"
    r.route("MIDI OUT", "loop")
    assert m.loop and r.patch["MIDI IN"] == "loop"
    assert r.routing_line() == "routing out=on pair=0 in=tone gain=50 tone_hz=440\n"
    r.feed(b"levels out 0.5 0.25 0 0 in 0.1 0.1\nlev")
    assert r.levels()["out"][:2] == [0.5, 0.25] and r.buf == b"lev"
    # every control drawn on the page must have frames in controls.json
    import re
    with open(os.path.join(HERE, "index.html")) as f:
        page = f.read()
    names = set(re.findall(r'B\("([^"]+)"', page)) | {f"PAD {i}" for i in range(1, 17)} | \
        {"K1", "K2", "K3", "FADER", "DATA", "MAIN VOLUME"}
    with open(CONTROLS) as f:
        mapped = json.load(f)["controls"]
    missing = sorted(n for n in names if not any(k in mapped.get(n, {}) for k in ("press", "set", "turn")))
    assert not missing, f"controls without frames: {missing}"
    print(f"selftest ok ({len(names)} panel controls mapped)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORTS["panel"])
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        raise SystemExit
    panel, qmp, midi = Panel(), Qmp(), ExtMidi()
    rear = Rear(midi)
    # panel input (control frames to the UART) should not wait behind the VNC thread's copies
    sys.setswitchinterval(0.0005)
    vnc, pages = Vnc(), Pages()
    for port, owner in ((PORTS["cs"], panel), (PORTS["ext_midi"], midi), (PORTS["audio"], rear)):
        threading.Thread(target=uart_loop, args=(port, owner), daemon=True).start()
    burst = Burst(qmp, pages)
    threading.Thread(target=vnc.loop, daemon=True).start()
    threading.Thread(target=burst.loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, a.port), make_handler(panel, qmp, midi, rear, SdCard(qmp), vnc, pages, burst))
    print(f"MPC Sample panel: http://{HOST}:{a.port}/")
    srv.serve_forever()
