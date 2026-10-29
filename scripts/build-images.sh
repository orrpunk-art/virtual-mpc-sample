#!/bin/sh
# Runs inside the lab container. Inputs are read-only:
#   /fw/rootfs.ext4    stock rootfs partition image (ext4 + dm-verity hash tree)
#   /fw/boot.ext2      stock boot partition (kernel.fit), /fw/recovery.ext2
# Outputs (out/):
#   emmc.raw           GPT disk laid out like the device eMMC, same PARTUUIDs
#   lab-layer.ext4     read-only overlay layer with lab-only files
#   initramfs.cpio.gz  lab initramfs
#   keys/              lab ssh keys (never committed)
set -eu
OUT=/lab/out
mkdir -p "$OUT/keys"

# --- ssh keys -----------------------------------------------------------------
[ -f "$OUT/keys/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C mpclab -f "$OUT/keys/id_ed25519"
[ -f "$OUT/keys/host_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C mpclab-host -f "$OUT/keys/host_ed25519"

# --- lab layer ----------------------------------------------------------------
STAGE=/tmp/layer
rm -rf "$STAGE"; cp -a /lab/overlay/lab-layer "$STAGE"
install -m 600 "$OUT/keys/host_ed25519" "$STAGE/etc/ssh/mpclab_host_ed25519_key"
install -m 600 "$OUT/keys/id_ed25519.pub" "$STAGE/root/.ssh/authorized_keys"
install -D -m 755 "$OUT/bridge/mpclab-audio-bridge" "$STAGE/opt/mpclab/bin/mpclab-audio-bridge"
chmod 700 "$STAGE/root/.ssh"
# USB gadget: the stock scheme without its UAC2 function, which cannot bind on the lab's
# dummy_hcd (no isochronous endpoints) and took the NCM network and USB MIDI down with it
debugfs -R "dump /usr/lib/az0x-usb-gadget/ac50.scheme /tmp/ac50.scheme" /fw/rootfs.ext4 2>/dev/null
install -d "$STAGE/usr/lib/az0x-usb-gadget"
python3 /lab/scripts/gadget_scheme.py /tmp/ac50.scheme > "$STAGE/usr/lib/az0x-usb-gadget/ac50.scheme"
chown -R 0:0 "$STAGE"
rm -f "$OUT/lab-layer.ext4"
mke2fs -q -t ext4 -L mpclab -d "$STAGE" "$OUT/lab-layer.ext4" 64M

# --- initramfs ----------------------------------------------------------------
IR=/tmp/initramfs
rm -rf "$IR"; mkdir -p "$IR/bin" "$IR/dev" "$IR/proc" "$IR/sys"
cp /opt/arm64/bin/busybox "$IR/bin/busybox"   # arm64 busybox-static, also on amd64 hosts
install -m 755 /lab/initramfs/init "$IR/init"
# Boot splash: the stock image from the FIT's own initramfs (where the device's splash
# daemon shows it), as raw pixels for /dev/fb0. Taken from the firmware, never committed.
debugfs -R "dump /kernel.fit /tmp/kernel.fit" /fw/boot.ext2 2>/dev/null
dumpimage -T flat_dt -p 1 -o /tmp/fit-initrd.zst /tmp/kernel.fit >/dev/null
rm -rf /tmp/fit-initrd; mkdir /tmp/fit-initrd
zstd -dc /tmp/fit-initrd.zst | (cd /tmp/fit-initrd && cpio -id --quiet usr/share/akai/splash/mpc-sample.png)
python3 /lab/scripts/splash_raw.py /tmp/fit-initrd/usr/share/akai/splash/mpc-sample.png "$IR/splash.raw"
(cd "$IR" && find . | cpio -o -H newc --quiet | gzip -9) > "$OUT/initramfs.cpio.gz"

# --- stock FIT (kernel cmdline with the dm-verity table is taken from it) -------
mkdir -p "$OUT/fw"
debugfs -R "dump /kernel.fit $OUT/fw/kernel.fit" /fw/boot.ext2 2>/dev/null
[ -s "$OUT/fw/kernel.fit" ]
# the stock command line (dm-verity table + root hash) lives in the FIT's fdt-cmdline overlay
dumpimage -T flat_dt -p 3 -o /tmp/fdt-cmdline.dtb "$OUT/fw/kernel.fit" >/dev/null
fdtget -t s /tmp/fdt-cmdline.dtb /fragment@0/__overlay__ bootargs > "$OUT/fw/cmdline.txt"
grep -q 'verity' "$OUT/fw/cmdline.txt"

# --- eMMC ---------------------------------------------------------------------
# Layout and GUIDs from the update image's inmusic,partitions property.
# Built on the container's own filesystem (sgdisk does not write reliably through the
# macOS file share), then exported as a compact qcow2 base image.
BASE=$OUT/emmc.base.qcow2
if [ -f "$BASE" ] && [ "${REBUILD_EMMC:-0}" != 1 ]; then
    echo "emmc.base.qcow2 exists, keeping it (REBUILD_EMMC=1 to recreate)"
    exit 0
fi
IMG=/tmp/emmc.raw
rm -f "$IMG"; truncate -s 8G "$IMG"
SPL=baafa14b-964b-53b5-8ffe-0223e56924a6
UBOOT=aaa01589-8cd3-5f10-8785-832ffbc41d64
sgdisk -a 1 -U 8298f2af-84f6-59ad-aee0-08df4262ed89 \
    -n 1:1088:+480K  -c 1:spl1     -t 1:$SPL   -u 1:23c69e0b-a8cf-5706-b569-cf6a7f8b75ca \
    -n 2:2112:+480K  -c 2:spl2     -t 2:$SPL   -u 2:6206f186-032f-505d-8b8e-28d414e1d88b \
    -n 3:0:+512K     -c 3:env1                 -u 3:60849ce3-fa20-5e5a-a67c-402552cfaf44 \
    -n 4:0:+512K     -c 4:env2                 -u 4:56d8d0dc-3948-5370-8960-8dcdc38e43a5 \
    -n 5:0:+4M       -c 5:uboot1   -t 5:$UBOOT -u 5:3ade6d1f-0527-555c-988c-4489a97d4135 \
    -n 6:0:+4M       -c 6:uboot2   -t 6:$UBOOT -u 6:71c60ca6-d542-5d84-ad24-3c20dfc41123 \
    -n 7:0:+64M      -c 7:factory              -u 7:f2a055c0-1536-5020-a0c9-3944f89ba52b \
    -n 8:0:+128M     -c 8:boot                 -u 8:72a55024-7d84-5f30-9e7e-92366ace0627 -A 8:set:2 \
    -n 9:0:+128M     -c 9:recovery             -u 9:b209cb1d-e99a-552c-8eda-d59515bcbeab -A 9:set:2 \
    -n 10:0:+1G      -c 10:rootfs              -u 10:fb5ae791-1e9d-53f5-a923-9f6ce79dcaf6 \
    -n 11:0:+4G      -c 11:content             -u 11:4054a70b-cc6a-4ac8-9a97-2a716b05bbb4 \
    -n 12:0:0        -c 12:data                -u 12:d6a62570-4c37-4a42-ae77-8f45bcbfda65 \
    "$IMG" >/dev/null

start() { s=$(sgdisk -i "$1" "$IMG" | awk '/First sector/ {print $3}'); [ -n "$s" ] || { echo "no partition $1" >&2; exit 1; }; echo "$s"; }
put() { dd if="$2" of="$IMG" bs=512 seek="$(start "$1")" conv=notrunc,sparse status=none; }

put 8 /fw/boot.ext2
put 9 /fw/recovery.ext2
put 10 /fw/rootfs.ext4
# /content: present but empty (factory content is not part of the update image).
# /factory and /data stay blank: the stock az0x-*-mkfs services format them on first boot.
c=$(start 11); n=$(sgdisk -i 11 "$IMG" | awk '/Partition size/ {print $3}')
mke2fs -q -t ext4 -L content -E offset=$((c * 512)) "$IMG" $((n / 2))k

sgdisk -p "$IMG"
echo "rootfs sha256 in image: $(dd if="$IMG" bs=512 skip="$(start 10)" count=$(( $(stat -c %s /fw/rootfs.ext4) / 512 )) status=none | sha256sum | cut -c1-16)"
echo "rootfs sha256 source  : $(sha256sum /fw/rootfs.ext4 | cut -c1-16)"
rm -f "$BASE"
qemu-img convert -O qcow2 "$IMG" "$BASE"
rm -f "$IMG"
ls -la "$BASE"
