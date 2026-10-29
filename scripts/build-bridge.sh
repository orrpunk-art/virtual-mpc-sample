#!/bin/sh
# Runs inside the lab container (arm64 or amd64; aarch64-linux-gnu-gcc either way). Dynamic link against libasound.so.2, which the stock rootfs
# provides (alsa-lib 1.2.11); Debian bookworm's glibc 2.36 is older than the device's 2.39.
set -eu
mkdir -p /lab/out/bridge
aarch64-linux-gnu-gcc -O2 -Wall -Wextra -o /lab/out/bridge/mpclab-audio-bridge /lab/bridge/mpclab-audio-bridge.c -lasound -lm
file /lab/out/bridge/mpclab-audio-bridge
