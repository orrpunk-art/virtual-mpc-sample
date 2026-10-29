#!/usr/bin/env python3
"""PNG (grayscale or RGB, 1/8-bit, non-interlaced) -> raw XRGB8888 for /dev/fb0.

    splash_raw.py IN.png OUT.raw

Used at build time on the stock boot splash (/usr/share/akai/splash/mpc-sample.png in the
FIT's initramfs, 320x240 1-bit): the lab initramfs writes it to the framebuffer at power-on,
where the device's splash daemon shows the same image. The image is read from the firmware,
never committed.
"""
import struct
import sys
import zlib


def decode(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat = 8, b""
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    assert interlace == 0 and ctype in (0, 2) and depth in (1, 8) and (ctype == 0 or depth == 8)
    channels = 3 if ctype == 2 else 1
    stride = (w * channels * depth + 7) // 8
    bpp = max(1, channels * depth // 8)
    raw = zlib.decompress(idat)
    rows, prev, o = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[o]
        line = bytearray(raw[o + 1:o + 1 + stride])
        o += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    out = bytearray()
    for line in rows:
        for x in range(w):
            if ctype == 2:
                r, g, b = line[x * 3:x * 3 + 3]
            elif depth == 1:
                r = g = b = 255 if line[x >> 3] >> (7 - (x & 7)) & 1 else 0
            else:
                r = g = b = line[x]
            out += bytes((b, g, r, 0))                     # XRGB8888, little endian
    return w, h, bytes(out)


def main():
    w, h, pixels = decode(open(sys.argv[1], "rb").read())
    open(sys.argv[2], "wb").write(pixels)
    print(f"splash: {w}x{h}")


if __name__ == "__main__":
    main()
