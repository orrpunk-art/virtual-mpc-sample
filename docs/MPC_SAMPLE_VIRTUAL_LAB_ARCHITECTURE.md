# Virtual MPC Sample — architecture

Date: 2026-10-27. Firmware: 1.3.0.12. Sources of facts: a local copy of the rootfs
`$FW_DIR/rootfs` (FW_DIR = folder with the extracted firmware), the image
`MPCSAMPLE-1.3.0.12-update.img`, the kernel FIT image
`lab-output/kernel-fit/kernel.fit` (DTB decompiled with `dtc`), reports of
read-only SSH probes of the device (2026-10-08/09), and static analysis of
`usr/bin/MPC Sample` (SHA-256 `b6584cba…3508`).

Confidence labels: **PROVEN** — read from firmware files or captured from the
device; **INFERENCE** — derived from PROVEN facts; **HYPOTHESIS** — requires
verification, the verification method is given.

---

## 0. Short verdict

Running the original `MPC Sample` in QEMU is **realistic**, but not by emulating
the RK3566 SoC (QEMU has no such machine); instead it runs on the generic machine
`qemu-system-aarch64 -M virt` with HVF acceleration on Apple Silicon, a **custom
lab kernel** and the stock rootfs. Rationale: the application does not depend on
any Rockchip-specific kernel interface — only on standard Linux ABIs
(DRM/KMS dumb buffers without a GPU, ALSA PCM/rawmidi/seq, libinput+udev, io_uring,
configfs USB gadget, sysfs device tree). The Cortex-A55 CPU (ARMv8.2) code runs
natively on M-series chips, without binary translation.

The optimal architecture is a three-tier hybrid:

| Tier | What it is | Purpose |
|---|---|---|
| L1 "chroot" | Docker (linux/arm64, native) + rootfs as a chroot, no hardware | building and link-testing our `.so` files, headless scripts, CI |
| L2 "VM" | QEMU virt + HVF + lab kernel + virtual peripherals + MCU emulator | full UI, LD_PRELOAD, events, screen on the Mac, snapshots |
| L3 "device" | the real MPC Sample, RAM/`/tmp` only, read-only eMMC | final validation of what the VM does not cover (audio codec, RT timings, MCU) |

---

## 1. What is established from the firmware (PROVEN)

### 1.1 Platform

| Parameter | Value | Source |
|---|---|---|
| SoC | Rockchip **RK3566**, Cortex-A55, GPU Mali-G52 (panfrost) | DTB: `compatible = "inmusic,ac50","inmusic,az07","rockchip,rk3566"` |
| Architecture / ABI | AArch64, LP64, glibc (Yocto `aarch64-poky-linux-gcc` 13.4) | ELF, `os-release` |
| Distribution | inMusic `az0x` on Yocto **scarthgap 5.0.14** | `/etc/os-release` |
| Kernel | **6.12.60-imb-2025-12-03-rt13**, `SMP PREEMPT_RT` | `lib/modules/`, strings in `Image` |
| Bootloader | U-Boot 2024.01, signed FIT (`sha256,rsa2048`), key `akai-1.pub` | update image, initramfs |
| Image format | `AZ0x` container with BOOT/PARR/… sections; `part1`=boot (ext2, contains `kernel.fit`), `parr`=recovery (ext2), `part2`=rootfs (**ext4, 427 MB, no encryption and no dm-verity on the image itself**) | `xxd`, `file` |
| eMMC layout | GPT: spl1/spl2, env1/env2, uboot1/uboot2, factory 64M, boot 128M, recovery 128M, rootfs 1G, content 4G, data (remainder) | `inmusic,partitions` in the image |
| Mounts on the device | `/` rootfs ro; `/content` ext4 ro (p11); `/data` ext4 rw (p12, also `/var/lib`); `/factory` ext4 (p7); `/tmp`,`/run` tmpfs **without noexec**; SD `/media/MPC SAMPLE` exFAT **noexec** | `/proc/mounts` from the device |
| initramfs | zstd-cpio, custom ELF `init` (libmount), reads `chosen/inmusic,startup-mode`, mounts `/dev/mmcblk0p3`?, performs `switch_root`; contains `az0x-splash`, `az01-image`, dtc/fdt utilities | unpacking the FIT |

The device tree is readable in full: `scratchpad/fit/ac50.dts`
(3458 lines) — see §9.

### 1.2 The application `/usr/bin/MPC Sample`

| Parameter | Value |
|---|---|
| ELF | AArch64 **PIE**, dynamic, stripped, RELRO full, interp `/usr/lib/ld-linux-aarch64.so.1`, 119 MB, JUCE-based C++ |
| Launch | `ac50.service` → `/usr/bin/launch-ac50`: first `az0x-firmware-tool …/UpdateImage.rbin` (Lua script, updates the panel MCU via `amidi`), then `ulimit -S -s 1024; MALLOC_ARENA_MAX=1; exec setarch -R -- "/usr/bin/MPC Sample"`; `Restart=always`; `HOME=/tmp` |
| Dependencies (NEEDED) | libasound, libdrm, libinput, libxkbcommon, libudev, libusbgx, libdbus-1, libcurl, libfreetype, libmp3lame, libmpg123, libaio, liburing, libfmt, libstdc++, libm/libmvec/libc, **libaz0x-info** (→ libteec), **libaz01-prog**, **libaz0x-splashctl** |
| CLI options | `--help --version --settings --project --messageFile --sentryTags --skipStartupChecks --painttest --touchtest --remotescreen --script --scriptLibs --scriptOutputPath --terminateOnFinish` |
| Screen | libdrm KMS **without GBM/EGL**: `drmModeGetResources/GetConnector/GetEncoder/GetPlane…`, `drmModeAddFB`, `drmModeSetCrtc`, `drmModePageFlip`, `drmHandleEvent`, `drmModeSetCursor2`, `drmSetMaster`, `drmIoctl` (INFERENCE: dumb buffers, software rendering) |
| Panel | SPI display `vitek,vt024k-zn01` on `spi@fe640000`, 25 MHz, `rotation = 270`, **generic `panel-mipi-dbi`** driver (builtin) with the init blob `/lib/firmware/vitek,vt024k-zn01`; pwm-backlight. The resulting KMS mode is 320×240 (INFERENCE from the rotation and earlier analysis "320 x 240 display") |
| Control surface (pads/buttons/encoders) | **UART `serial@fe670000`** with the child node `control-surface { compatible="serial-midi"; label="Control Surface"; }` → mainline driver `snd-serial-generic` → ALSA rawmidi. The application looks up rawmidi by the names `Control Surface` / `MIDI UART` / `SerialMIDI`, reads with `snd_rawmidi_read`, writes with `snd_rawmidi_write` (3-byte CCs, 14-bit values as a CC pair on channel 16, init SysEx `F0 47 7F 5C 42 00 00 F7`) |
| External MIDI (DIN) | UART `serial@fe680000`, `serial-midi`, 31250 baud, label `External MIDI` |
| USB MIDI / mass storage / audio | USB gadget via configfs + libusbgx (`f_midi`, `mass_storage`, `uac2`); scheme `ac50.scheme` (VID 0x09e8 PID 0x205c, ncm networking); `az0x-usb-gadget@ac50.service` |
| ALSA seq ports at runtime | client `<MPC Sample>`: 128:0 ← External MIDI, 128:1 ← f_midi, 128:2/128:3 — outputs (PROVEN from the device) |
| Audio | simple-audio-card `I2S`, codec **AK4558** (I2C), amplifiers `Speaker`/`Line Input`; the application opens `hw:%d` / `hw:%d,%d`, looks up by-path `platform-sound` |
| Other paths | `/dev/dri`, `/dev/rga2` (RK3288 legacy), `/dev/mem` (there is an error string "failed to open /dev/mem" — it is handled), `/dev/ffs-*`, `/sys/kernel/config/usb_gadget/ac50/...`, `/sys/firmware/devicetree/base/{compatible,serial-number,inmusic,product-code,inmusic,az07-pcb-rev,chosen/…}`, `/sys/class/backlight/*/brightness`, `/data/Settings`, `/data/softwareunlock/`, `/content`, `/media/…` |
| libinput input | context `libinput_udev_create_context` + seat; handles pointer/touch/keyboard. The AC50 DTB has no `gpio-keys`/touchscreen → the device has no evdev sources (except the power-button udev rule) |
| Scripting | an engine exists (`--script`, `/data/scriptingDefaultEnabled`, Lua according to earlier xrefs) |

### 1.3 Device kernel

222 modules in total; **virtio is completely absent** (neither as a module nor builtin);
builtin: drm, rockchipdrm, panel-mipi-dbi/tiny panels, evdev, snd-rawmidi,
snd-seq, usb_f_midi, libcomposite, loop; modules: `snd-aloop`,
`snd-serial-generic`, `overlay`, panfrost. Consequence (INFERENCE): the stock
kernel **cannot** be booted in QEMU `virt` — there is no virtio-blk/net/gpu and
no Rockchip machine in QEMU.

### 1.4 What earlier work has already proven on the device

- `LD_PRELOAD` libraries are loaded on a fresh launch; custom screens,
  encoders and page switching work (per the task statement).
- ELF binaries cannot be executed from the SD card (`noexec`), but can be from `/tmp` (tmpfs).
- Cross-process injection into the panel rawmidi is impossible (the handle is exclusive);
  injection into the seq ports 128:0/128:1 is possible.

---

## 2. Comparison of approaches

| Criterion | A. QEMU System | B. QEMU User / chroot | C. Simulation with stubs |
|---|---|---|---|
| What runs | stock rootfs, systemd, **lab kernel**, original binary | original binary + stock libraries in a chroot inside an arbitrary arm64 Linux | original binary + substituted libdrm/libasound (shim) |
| CPU | HVF, native | native (Docker Desktop = arm64 VM) ; `qemu-user` **does not exist** on macOS | native |
| Screen | virtio-gpu → QEMU window, real KMS path (`AddFB/SetCrtc/PageFlip`) | no KMS in the Docker kernel → **no screen** without a shim | shim libdrm draws into shm → custom viewer |
| Control surface | real `snd-serial-generic` on pl011 ← MCU emulator outside the VM | no rawmidi (no modules) | shim libasound |
| MIDI/audio | ALSA in the kernel: virtio-snd/aloop, snd-virmidi, f_midi via dummy_hcd | none | shim |
| Fidelity | high: same userspace, same kernel ABIs, same systemd ordering | medium: only libc/libstdc++/loader | low: behavior depends on the quality of the stubs |
| Complexity | kernel build (Docker) + QEMU config + MCU emulator | minimal (a day) | large: ~25 libdrm functions + ~80 ALSA functions, ioctl/mmap semantics |
| What it does NOT cover | AK4558 audio codec, RT timings, real MCU, USB gadget to the host, OP-TEE | all hardware | everything that is not stubbed |
| Iteration speed | service restart in the VM ≈ seconds; qcow2 snapshots | instant | instant |

**Choice:** A as the foundation (L2), B as the fast build/link-test loop (L1),
C only selectively, if A reveals a blocking dependency (for example, a shim
for a single function via the same `LD_PRELOAD` mechanism that is already used).

---

## 3. Proven facts vs hypotheses for running in the VM

> Update 2026-10-27: the PoC has been carried out, H1–H8 and H10 are confirmed, H9 was not tested.
> Details and remaining discrepancies are in [POC_RESULTS.md](POC_RESULTS.md).

| # | Claim | Status | How to verify |
|---|---|---|---|
| H1 | The application starts on any KMS device with dumb buffers (virtio-gpu), without requiring rockchipdrm | HYPOTHESIS (strong: no GBM/EGL/panfrost imports) | PoC: `strace -f -e ioctl` on `/dev/dri/card0`; see which DRM ioctls and FB formats it requests |
| H2 | Exactly the 320×240 mode is required; a different mode → refusal or distortion | HYPOTHESIS | virtio-gpu with `xres=320,yres=240`; then try 640×480 |
| H3 | A rawmidi named `Control Surface` (serial-generic on pl011) is recognized as the panel | INFERENCE (string comparison at `0x023c8280`) | `amidi -l` in the VM; strace the opening of `/dev/snd/midiC*D0` |
| H4 | Without an MCU reply to the init SysEx the application still shows the UI | HYPOTHESIS | PoC with a "mute" MCU; if the UI does not appear, a transcript from the device is needed (§7, file F6) |
| H5 | `az0x-firmware-tool` without an MCU exits without blocking, and `launch-ac50` proceeds | HYPOTHESIS | read the Lua script in full; run it manually in the VM with a timeout |
| H6 | The absence of `/run/az0x-splash-control`, OP-TEE (`/dev/tee0`), `/dev/rga2`, `/dev/mem`, UDC is not fatal | HYPOTHESIS (for `/dev/mem` there is a handled error) | PoC log; if fatal — `dummy_hcd` for the UDC, run `az0x-splash` from the initramfs, shim |
| H7 | The application starts without `/content` (factory content is not part of the update image) | HYPOTHESIS | PoC without `/content`; then with a `/content` dump from the device |
| H8 | The audio card is selected by index/name, and `snd-aloop` with `id=I2S` is accepted | HYPOTHESIS | strace the `snd_pcm_open` arguments; ltrace on libasound |
| H9 | Mouse clicks in the QEMU window (virtio-tablet → libinput pointer) are interpreted by the application as touch | HYPOTHESIS (touch code exists, `--touchtest`) | PoC: `-device virtio-tablet-pci`, observe the reaction |
| H10 | `LD_PRELOAD` is accepted by the loader when launched via `launch-ac50` (not setuid, root) | INFERENCE | PoC step 5 |
| P1 | The ext4 rootfs from the image mounts as is; dm-verity is not needed | PROVEN (`file part2`; the signature is checked only by `az01-signed-fs` during an update) | — |
| P2 | The binary runs on Apple Silicon without translation | PROVEN (ARMv8.2-A ⊂ M-series; HVF) | — |

---

## 4. Main obstacles

1. **No RK3566 machine in QEMU** → the stock kernel/FIT cannot be booted; a custom
   6.12.x kernel for `virt` is needed with virtio, DRM virtio-gpu, ALSA (virtio-snd, aloop,
   virmidi, serial-generic + serdev), `dummy_hcd` + configfs gadget, overlayfs,
   exfat, io_uring. It is built in Docker (arm64, native). This is the main
   engineering work.
2. **Only one PL011 in `virt`** (a second UART appears only in recent QEMU versions; this needs
   to be checked with `qemu-system-aarch64 -M virt -machine help`/dumpdtb). Solution:
   the PL011 is assigned to `Control Surface`, the kernel console is `console=hvc0` via
   virtio-console; External MIDI goes through `snd-virmidi`/f_midi.
3. **The MCU protocol** is only partially reconstructed (frames, dispatchers, some IDs).
   The full "ID ↔ physical control" map and the expected replies to the init SysEx
   must either be reconstructed further statically (the `AC50MidiParser` tables) or
   captured from the device (§7, F6). Without a transcript the MCU emulator is "mute".
4. **Startup checks**: firmware-tool, splash, software-unlock/TEE,
   `/content`, DT properties, gadget. Each one is a potential early exit;
   there is a `--skipStartupChecks` option (semantics unknown — to be checked).
5. **Content and data**: `/content` (up to 4 GB) and `/factory` are not part of the
   update image — read-only dumps from the device or verification of H7 are needed.
6. **Audio fidelity**: the VM does not reproduce AK4558/RT behavior; xruns in the VM
   must not be treated as firmware defects.
7. **Legal boundary**: the rootfs, images, `MPC Sample` and the panel init blob stay outside
   the public repository; the repository stores only scripts, configs,
   kernel patches, our code and the SHA-256 hashes of the expected inputs.

---

## 5. Target architecture (L2 "VM")

```
┌──────────────────────── macOS (Apple Silicon) ─────────────────────────┐
│                                                                        │
│  lab/ (git)                 firmware/ (outside git, RO)                │
│  ├ kernel/  (config, patches) ├ rootfs.ext4  ← part2 from update.img   │
│  ├ qemu/    (run.sh, dtb)    ├ content.ext4 ← /content dump (opt.)     │
│  ├ mcu-emu/ (panel emulator) └ sdcard.exfat ← SD backup                │
│  ├ overlay/ (our .so, units)                                           │
│  └ tests/   (pytest, --script scripts)                                 │
│                                                                        │
│  ┌─────────────── qemu-system-aarch64 -M virt -accel hvf ────────────┐ │
│  │ lab kernel 6.12 + -dtb virt-ac50.dtb (inmusic,* + serial-midi node)│ │
│  │                                                                    │ │
│  │  virtio-blk ro: rootfs.ext4 ──► qcow2 overlay (snapshot/reset)     │ │
│  │  virtio-blk:    data.ext4 (clean, recreated), content, sd          │ │
│  │  virtiofs/9p:   /lab ← host lab/overlay (our .so, scripts)         │ │
│  │  virtio-gpu 320x240 ──► QEMU window (MPC screen)                   │ │
│  │  pl011 ◄─chardev unix socket─► mcu-emu (pads/buttons/encoders/LED) │ │
│  │  virtio-console (hvc0) — console; virtio-net — ssh/gdbserver       │ │
│  │  virtio-snd → CoreAudio; snd-aloop (id=I2S) — capture to a file    │ │
│  │  dummy_hcd + configfs: f_midi / mass_storage as on the device      │ │
│  │                                                                    │ │
│  │  systemd (stock) → ac50.service → launch-ac50 → MPC Sample         │ │
│  │        + drop-in: Environment=LD_PRELOAD=/lab/libmpc_lab_*.so       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│        ▲                                   ▲                           │
│  mcu-emu GUI (web/SDL: mouse → MIDI frames)  test runner (ssh, journal)│
└────────────────────────────────────────────────────────────────────────┘
```

### 5.1 Components

| Component | Implementation | Interaction |
|---|---|---|
| Lab kernel | mainline 6.12.y, `defconfig` + virtio*, `DRM_VIRTIO_GPU`, `DRM_VKMS` (fallback), `SND_VIRTIO`, `SND_ALOOP`, `SND_VIRMIDI`, `SND_SERIAL_GENERIC`, `SERIAL_DEV_BUS`+`SERIAL_DEV_CTRL_TTYPORT`, `USB_DUMMY_HCD`, `USB_CONFIGFS_F_MIDI/F_MASS_STORAGE/F_UAC2`, `OVERLAY_FS`, `EXFAT_FS`, `IO_URING`, `VIRTIO_FS`/`9P_FS`; optionally `PREEMPT_RT` for parity | booted with `-kernel Image -initrd`(optional) `-append root=/dev/vda ro console=hvc0` |
| DTB | `-M virt,dumpdtb` → `dtc` → add to the root node `model`, `compatible = "inmusic,ac50","inmusic,az07","rockchip,rk3566","linux,dummy-virt"`, `inmusic,product-code="AC50"`, `inmusic,az07-pcb-rev="A"`, `serial-number`, `chosen/u-boot,version`; into the pl011 node — `control-surface { compatible="serial-midi"; label="Control Surface"; }`; remove `stdout-path` pointing to pl011 | `-dtb virt-ac50.dtb`. Fallback without a DTB: bind-mount a directory over `/sys/firmware/devicetree/base` |
| Rootfs | `part2` (ext4) read-only; `qemu-img create -f qcow2 -b rootfs.raw -F raw work.qcow2`; reset = delete the overlay, or `-snapshot` | the lab overlay contains only: a drop-in for `ac50.service`, `sshd` keys, `/etc/fstab` for `/lab`, masking of `tee-supplicant`/`connman` if needed |
| /data | a fresh ext4 for every run (or a snapshot with known Settings) | mounted as on the device (stock `data.mount`, `var-lib.mount`) |
| Screen | virtio-gpu-pci `xres=320,yres=240`; QEMU window (cocoa) with scaling | tests grab a frame with `screendump` via QMP → PNG → comparison |
| MCU emulator (`mcu-emu`) | a host process (Python/Go) listening on the pl011 unix-socket chardev; implements: parsing of outgoing frames (LED/display feedback, init SysEx → reply from the transcript), generation of incoming frames (buttons as bool, encoders as relative delta, pads with velocity/pressure, 14-bit faders); GUI — a local web page with a panel mock-up; API — unix/TCP JSON for tests (`press("PLAY")`, `turn("DATA", -3)`) | ↔ QEMU chardev ↔ `snd-serial-generic` ↔ `/dev/snd/midiC0D0` ↔ application |
| Audio | `virtio-sound-pci,audiodev=coreaudio` for listening; `snd-aloop id=I2S` for deterministic capture (`arecord` inside the VM) | H8 will determine which card to make card zero |
| MIDI | `snd-virmidi` → seq ports for "External MIDI"; `f_midi` on `dummy_hcd` provides the stock `MIDI Gadget` inside the VM | tests send notes via `aconnect`/`amidi` in the VM or over the network (ALSA seq over ssh) |
| SD storage | exFAT image (from the backup) as `virtio-blk`, mounted by the udev rule `90-edisks-az07` or manually with `noexec`, as on the device | project load/save check |
| Our `.so` files | built in Docker (L1) with `--sysroot=rootfs` (clang or gcc-aarch64 from the container), copied to `lab/overlay` → `/lab` in the VM | drop-in `Environment=LD_PRELOAD=/lab/x.so`; or a manual launch of `launch-ac50` with env |
| Diagnostics | `journalctl -fu ac50`, `strace -f -p`, `gdbserver :1234 --attach`, `perf` in the VM; QMP for snapshots/screenshots; host-side `gdb-multiarch` | ssh over virtio-net (user-mode `hostfwd=tcp::2222-:22`) |
| Test runner | pytest on the host: boots the VM from a clean snapshot, waits for `ac50.service active`, drives it through the mcu-emu API, captures frames/logs, uses `--script … --terminateOnFinish` for headless cases | GitHub Actions on an arm64 runner — the L1 loop; L2 locally |

### 5.2 Tier L1 "chroot" (Docker, no hardware)

`docker run --platform linux/arm64 -v firmware/rootfs:/rootfs:ro …` and `chroot`
(or `systemd-nspawn` in a Lima arm64 VM). Covers: compiling `.so` files against
the real glibc/libstdc++/libasound/libdrm; `ldd` checks; running our
unit tests that `dlopen` the `.so` files; headless application commands
(`--version`, `--help`, possibly `--script` with `--skipStartupChecks` — H status).
Does not cover the screen or MIDI (no KMS/rawmidi in the Docker kernel).

### 5.3 Tier L3 "device"

Stays as it is now: RAM-only (`/tmp`), no writes to eMMC, no changes to
`ac50.service`, launches only after explicit permission. The VM takes over
80–90 % of iterations from the device; what remains for the device: the audio codec,
exact timings, the MCU, the USB host.

---

## 6. Minimal Proof of Concept

The goal of the PoC is to answer H1, H3–H7, H10 in a single cycle, without the GUI MCU emulator.

1. Build the lab kernel in Docker (arm64) from 6.12.y with the config from §5.1
   (`Image`, no modules — everything builtin for simplicity).
2. Extract `rootfs.raw` = `extracted/part2` (already available), create a qcow2 overlay;
   put into the overlay `sshd`, a key, and an `ac50.service` drop-in with
   `ExecStartPre=/bin/sleep 10` (to have time to attach strace) and
   `Restart=no`.
3. Generate `virt-ac50.dtb` (§5.1) with a `serial-midi` node on pl011.
4. Launch:
   ```
   qemu-system-aarch64 -M virt -accel hvf -cpu host -smp 4 -m 2G \
     -kernel Image -dtb virt-ac50.dtb \
     -append "root=/dev/vda rw console=hvc0 systemd.unit=multi-user.target" \
     -drive if=none,id=root,file=work.qcow2 -device virtio-blk-pci,drive=root \
     -drive if=none,id=data,file=data.ext4,format=raw -device virtio-blk-pci,drive=data \
     -device virtio-gpu-pci,xres=320,yres=240 -display cocoa \
     -chardev socket,id=cs,path=/tmp/cs.sock,server=on,wait=off -serial chardev:cs \
     -device virtio-serial -chardev stdio,id=con -device virtconsole,chardev=con \
     -netdev user,id=n,hostfwd=tcp::2222-:22 -device virtio-net-pci,netdev=n \
     -device virtio-tablet-pci
   ```
   (check device/option names against the installed QEMU version).
5. PoC success criteria:
   - `amidi -l` in the VM shows the rawmidi `Control Surface`;
   - `MPC Sample` stays alive > 60 s, no restart loop, `strace` shows
     `open /dev/dri/card0`, `DRM_IOCTL_MODE_CREATE_DUMB`, `PAGE_FLIP`,
     and the opening of `/dev/snd/midiC0D0`;
   - some image (splash/screen) appears in the QEMU window;
   - a frame `BF 00 7F` / `BF 01 7F` (or any reconstructed button frame)
     written to `/tmp/cs.sock` changes the state on the screen;
   - `LD_PRELOAD=/lab/libmpc_lab_screen.so` (an existing library)
     is loaded, its output is visible in the journal, and the page appears.
6. If the UI does not appear because of a "mute" MCU (H4), the PoC counts as partially
   successful, and the next step is transcript F6.

PoC effort estimate: kernel build 0.5 day, QEMU/DTB/overlay 0.5 day,
debugging the startup checks 1–3 days.

---

## 7. Implementation plan

| Stage | Deliverable | Verification |
|---|---|---|
| 0. Infrastructure | `lab/` repository, `.gitignore` for `firmware/`, `Makefile`/scripts, SHA-256 manifest of the inputs | `make verify-inputs` |
| 1. L1 chroot | Docker build image; `.so` files are built against the sysroot; `chroot` runs `MPC Sample --version` | CI job on arm64 |
| 2. Lab kernel + DTB | `Image`, `virt-ac50.dtb`, `run.sh` script | the VM boots to login, `ls /dev/dri /dev/snd` |
| 3. PoC (§6) | answers to H1,H3–H8,H10; list of required stubs | PoC report in `docs/` |
| 4. MCU emulator v1 | frame parser/generator per CONTROL_SURFACE_PROTOCOL + JSON API, no GUI | pytest: button press → screen change (screendump diff) |
| 5. Panel GUI | web mock-up (pads, buttons, 2 encoders, faders) → API v1; LED feedback from outgoing frames | manual check |
| 6. Audio/MIDI | virtio-snd + aloop; virmidi; gadget on dummy_hcd | `arecord` capture while a sample plays; note via seq → sound |
| 7. Data | `data.ext4` with reference Settings; `content.ext4` from the dump; SD image | loading a project from SD |
| 8. Test harness | pytest fixtures: VM from a snapshot, journal, screenshots, `--script` headless cases | `make test` is green |
| 9. Parity with the device | checklist of VM↔device discrepancies; a `PREEMPT_RT` config if needed | comparing the `journalctl` startup order |

---

## 8. Tools and technologies

- QEMU ≥ 9.x (`brew install qemu`): `qemu-system-aarch64`, HVF, virtio-gpu,
  virtio-sound (CoreAudio), QMP; `qemu-img` for qcow2/snapshots.
- Docker Desktop (installed) with `--platform linux/arm64` — kernel build and L1.
- Kernel: linux-6.12.y; `dtc`, `fdtput`, `dumpimage` (already installed:
  dtc 1.8.1, u-boot-tools 2026.07).
- e2fsprogs (`debugfs`), `exfatprogs`/`mkfs.exfat` in Docker for images.
- Analysis: radare2 (available), optionally Ghidra; `strace`, `ltrace`, `gdbserver`
  (from the Yocto SDK or static arm64 builds — put them into `/lab`).
- MCU emulator: Python (`pyserial` not needed — unix socket) + `mido` for
  MIDI parsing; GUI — a simple web page (WebSocket) or SDL.
- Tests: pytest, QMP client (`qemu.qmp`), PNG comparison (`pillow`).
- ALSA inside the VM: `alsa-utils` (`amidi`, `aconnect`, `arecord`) — the rootfs
  already has `amidi` (used by firmware-tool); add the rest to `/lab`.

---

## 9. What needs to be provided (files and information)

Already available locally and used: rootfs 1.3.0.12, update.img and its
parts, `kernel.fit` (+ DTB, initramfs), probe reports, SD backup
(`$SD_BACKUP_DIR`, folder with the SD card backup), your `.so` files.

Additionally needed (all actions on the device are read-only):

| # | What | Why | How to capture |
|---|---|---|---|
| F1 | `journalctl -b --no-pager` of a normal boot | service order/errors, `MPC Sample` startup messages, what firmware-tool does | ssh, read-only |
| F2 | `ls -laR /factory`, `ls -laR /data` (without the Settings contents, if sensitive), `df -h` | what the application needs in `/factory`/`/data`; size of `/content` | ssh |
| F3 | `ls -la /dev/snd /dev/snd/by-path /dev/dri /dev/dri/by-path /dev/input`, `aplay -l`, `amidi -l`, `aconnect -l`, `udevadm info -e` | exact card/path names the application looks for (H3, H8) | ssh |
| F4 | `cat /proc/<pid MPC>/maps`, `ls -l /proc/<pid>/fd` | list of devices and libraries actually opened at runtime | ssh (partially available already) |
| F5 | `cat /proc/device-tree/chosen/*`, `serial-number`, `u-boot,version` (values may be masked) | populating the VM DTB | ssh |
| F6 | **Transcript of the MCU exchange**: the first ~5 s after startup (init SysEx and reply) + one press of each control | MCU emulator v1; H4 | option A: `strace -e trace=read,write -p <pid> -s 64` on the rawmidi fd (ptrace-attach to a live process — does not write to disk, but interferes with the process: requires your separate decision); option B: a hardware UART sniffer on the MCU line; option C: statically reconstruct the `AC50MidiParser` tables |
| F7 | Dump of `/content` (`tar -C /content -cf - . \| ssh …`), if H7 is not confirmed | factory content in the VM | ssh, read-only |
| F8 | Output of `MPC Sample --help` and, if safe, `--version` (launching a second copy next to the running one may be unsafe — better in the VM) | semantics of `--skipStartupChecks`, `--painttest` | in the VM at stage 3 |
| F9 | QEMU version on the Mac (`qemu-system-aarch64 --version`, after installation) and the Mac model | number of UARTs in `virt`, virtio-sound support | locally |
| F10 | Sources/description of your existing `.so` files (which symbols/addresses are hooked) | so that PoC step 5 uses exactly them, and to check the Build-ID gate | on your side |

---

## 10. Safety and reproducibility

- `firmware/` is read-only, outside git, with a SHA-256 manifest; all runs go
  on top of a qcow2 overlay or `-snapshot`; `make reset` deletes the overlay and
  recreates `data.ext4`.
- No write operations on the device: the F1–F7 procedures contain only
  read commands; F6 option A is marked as requiring a separate decision.
- Tests are deterministic: a fixed VM snapshot + fixed Settings +
  a scripted MCU emulator; audio is checked via `snd-aloop`, not via
  CoreAudio.
- Public repository: only `lab/` (scripts, kernel config, patches, DTB
  generator, mcu-emu, tests, our code). Nothing from the rootfs/images/blobs.

---

## Appendix A. Key facts for quick reference

```
SoC:        Rockchip RK3566 (Cortex-A55), DT "inmusic,ac50" / "inmusic,az07"
Kernel:     6.12.60-imb-2025-12-03-rt13, SMP PREEMPT_RT, no virtio
Distro:     Yocto scarthgap 5.0.14 (az0x), glibc (poky gcc 13.4)
App:        /usr/bin/MPC Sample, AArch64 PIE, JUCE, SHA-256 b6584cba…3508
Launch:     ac50.service -> launch-ac50 -> az0x-firmware-tool; setarch -R
Display:    SPI panel vitek,vt024k-zn01 @ spi@fe640000, panel-mipi-dbi, rot 270 -> KMS 320x240
Controls:   UART serial@fe670000, serial-midi "Control Surface" -> ALSA rawmidi
DIN MIDI:   UART serial@fe680000, serial-midi "External MIDI", 31250
Audio:      simple-audio-card "I2S", codec AK4558
USB:        configfs gadget ac50 (VID 09e8 PID 205c): f_midi, mass_storage, uac2, ncm
Storage:    rootfs ro (ext4), /content ro, /data rw, /factory, SD exFAT noexec, /tmp exec
Image:      AZ0x container; part2 = rootfs ext4 (plain); kernel.fit (Image + initrd zstd + DTB)
```
