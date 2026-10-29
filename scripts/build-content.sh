#!/bin/sh
# Runs inside the lab container. Puts the device's factory library on the eMMC /content
# partition (partition 11, ext4, mounted read-only by the stock firmware), as on the device.
#   /content   (read-only bind mount) a copy of the device's /content: Projects/, Samples/
#   out/emmc.base.qcow2 is rewritten in place. /data lives in the run overlay
#   (out/run/emmc.qcow2), which never writes to the read-only /content, so it stays valid.
# The factory library is not part of the firmware update; README.md says how to copy it
# from your own MPC Sample.
set -eu
BASE=/lab/out/emmc.base.qcow2
IMG=/tmp/emmc.raw
[ -f "$BASE" ] || { echo "no $BASE: run \`lab.py build images\` first" >&2; exit 1; }
[ -d /content/Samples ] || [ -d /content/Projects ] || { echo "/content has no Samples/ or Projects/" >&2; exit 1; }

qemu-img convert -O raw "$BASE" "$IMG"
c=$(sgdisk -i 11 "$IMG" | awk '/First sector/ {print $3}')
n=$(sgdisk -i 11 "$IMG" | awk '/Partition size/ {print $3}')
sgdisk -i 11 "$IMG" | grep -q "Partition name: 'content'"
# straight from the bind mount (no staging copy: the content is ~4 GB and the build VM's disk
# is small), then the macOS litter (.DS_Store, ._*) is removed; "img?offset=" lets e2fsprogs
# work on the partition inside the disk image
mke2fs -q -F -t ext4 -L content -m 0 -E offset=$((c * 512)),root_owner=0:0 -d /content "$IMG" $((n / 2))k
P="$IMG?offset=$((c * 512))"
find /content \( -name .DS_Store -o -name '._*' \) | sed 's|^/content||' | while read -r f; do
    echo "rm \"$f\""
done > /tmp/litter.cmds
debugfs -w -f /tmp/litter.cmds "$P" >/dev/null 2>&1 || true
e2fsck -fy "$P" >/dev/null 2>&1 || true
e2fsck -fn "$P"
qemu-img convert -O qcow2 "$IMG" "$BASE.new"
rm -f "$IMG"
mv "$BASE.new" "$BASE"
echo "content: $(find /content -type f ! -name .DS_Store ! -name '._*' | wc -l) files on /content"
