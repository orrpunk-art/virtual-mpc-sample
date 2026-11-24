#!/bin/sh
# Runs inside the lab container. Static arm64 strace for share/bin (the stock rootfs has none).
set -eu
VER=${STRACE_VERSION:-6.12}
BIN=${MPCLAB_SHARE:-/lab/share}/bin
mkdir -p /build "$BIN"
cd /build
[ -d "strace-$VER" ] || curl -fsSL "https://github.com/strace/strace/releases/download/v$VER/strace-$VER.tar.xz" | tar -xJ
cd "strace-$VER"
[ -f src/strace ] || { ./configure --host=aarch64-linux-gnu --enable-mpers=no LDFLAGS=-static >/dev/null && make -j"$(nproc)" >/dev/null; }
install -m 755 src/strace "$BIN/strace"
file "$BIN/strace"
# iolat: O_DIRECT read latency (lab diagnostics, tools/iolat.c)
aarch64-linux-gnu-gcc -O2 -static -o "$BIN/iolat" /lab/tools/iolat.c
aarch64-linux-gnu-gcc -O2 -static -o "$BIN/wakelat" /lab/tools/wakelat.c
