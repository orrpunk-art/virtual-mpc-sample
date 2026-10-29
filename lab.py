#!/usr/bin/env python3
"""Virtual MPC Sample: one launcher for macOS, Windows and Linux.

    python3 lab.py build              # Docker: tools image, kernel, bridge, disk images
    python3 lab.py run [--headless] [--accel tcg]
    python3 lab.py panel              # virtual MPC Sample at http://127.0.0.1:8765/
    python3 lab.py ssh [command...]   # root shell in the VM
    python3 lab.py preload LIB.so | --off
    python3 lab.py autorun PACKAGE.zip [--no-reboot] [--yes] [--device-label] | --results
    python3 lab.py stop | reset | status

Host requirements: Python 3.9+, QEMU 9+ (qemu-system-aarch64, qemu-img), OpenSSH, and
Docker for `build`. No shell, make or dtc on the host: the device tree is patched here.
Everything talks over TCP on 127.0.0.1 (PORTS), so the same code runs on Windows.
"""
import argparse
import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request

LAB = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(LAB, "out")
RUN = os.path.join(OUT, "run")
SHARE = os.path.join(LAB, "share")
SDCARD = os.path.join(LAB, "sdcard")            # host folder that becomes the SD card
SD_IMG = os.path.join(RUN, "sdcard.img")
SD_SIZE = 512 << 20                             # QEMU's sd-card wants a power-of-two size
FW_DIR = os.environ.get("FW_DIR", os.path.join(LAB, "firmware"))   # extracted firmware, never committed
CONTENT_DIR = os.environ.get("CONTENT_DIR", os.path.join(FW_DIR, "content"))   # factory library copy
IMAGE = "mpclab-tools"
HOST = "127.0.0.1"
PORTS = {"cs": 5551, "ext_midi": 5552, "console": 5553, "qmp": 5554, "qmp_panel": 5555, "audio": 5556, "pcm": 5557, "vnc": 5910, "ssh": 2022, "usb_ssh": 2222, "panel": 8765}
WIN = sys.platform == "win32"


# ----------------------------------------------------------------------------- helpers
def die(msg):
    sys.exit(f"lab: {msg}")


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((HOST, port)) == 0


def tool(name):
    path = shutil.which(name)
    if not path and WIN:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
            cand = os.path.join(base, "qemu", name + ".exe")
            if os.path.exists(cand):
                path = cand
    if not path:
        die(f"{name} not found in PATH")
    return path


def qmp(command, port=None, **arguments):
    """One QMP command on the lab VM; returns the reply (dict)."""
    with socket.create_connection((HOST, port or PORTS["qmp"]), timeout=5) as s:
        f = s.makefile("rw")
        f.readline()
        for cmd in ({"execute": "qmp_capabilities"}, {"execute": command, **({"arguments": arguments} if arguments else {})}):
            f.write(json.dumps(cmd) + "\n")
            f.flush()
            while True:
                reply = json.loads(f.readline())
                if "event" not in reply:
                    break
        return reply


def power_button():
    """Press the device's power button. QEMU's virt machine wires system_powerdown to a
    gpio-keys KEY_POWER press; MPC Sample takes it like its own power key: it asks "Do you
    want to shut down?", saves, shows "Shutting down" and powers the system off itself.
    QEMU runs with -no-shutdown, so a powered-off guest stays in QEMU's "shutdown" state and
    the next press powers it on again (reset + continue), like the device."""
    if qmp("query-status")["return"]["status"] == "shutdown":
        qmp("system_reset")
        qmp("cont")
    else:
        qmp("system_powerdown")


def yes_button():
    """Press SCREEN 3 (the button above the screen under "Yes"). The panel server owns the
    panel UART while it runs, so go through it; without it, write the MCU frames directly."""
    try:
        for action in ("press", "release"):
            urllib.request.urlopen(urllib.request.Request(
                f"http://{HOST}:{PORTS['panel']}/control", method="POST",
                data=json.dumps({"name": "SCREEN 3", "action": action}).encode()), timeout=3)
            time.sleep(0.1)
    except OSError:
        with socket.create_connection((HOST, PORTS["cs"]), timeout=3) as s:
            s.sendall(bytes.fromhex("90 0E 7F"))                 # SCREEN 3 = note 14
            time.sleep(0.1)
            s.sendall(bytes.fromhex("80 0E 00"))


def guest_off():
    try:
        return qmp("query-status")["return"]["status"] == "shutdown"
    except OSError:
        return True


def shut_down(timeout=60):
    """Power button, then Yes in the app's "Do you want to shut down?" (the button above the
    screen under "Yes"), then wait until the guest has powered off."""
    if guest_off():
        return True
    power_button()
    time.sleep(2)                                                # the dialog appears
    yes_button()
    for _ in range(timeout * 2):
        if guest_off():
            return True
        time.sleep(0.5)
    return False


def wait_closed(port, seconds):
    for _ in range(int(seconds * 4)):
        if not port_open(port):
            return True
        time.sleep(0.25)
    return False


def docker(*args, mounts=()):
    cmd = ["docker", "run", "--rm", "-v", "mpclab-build:/build", "-v", f"{LAB}:/lab"]
    for src, dst in mounts:
        cmd += ["-v", f"{src}:{dst}:ro"]
    subprocess.run(cmd + [IMAGE, *args], check=True)


# ----------------------------------------------------------------------------- device tree
# Minimal flattened-device-tree reader/writer: enough to add the AC50 properties and the
# serial-midi nodes to QEMU's own virt DTB without dtc on the host.
class Node:
    def __init__(self, name):
        self.name, self.props, self.children = name, {}, []

    def child(self, name, create=True):
        for c in self.children:
            if c.name == name:
                return c
        if not create:
            raise KeyError(name)
        c = Node(name)
        self.children.append(c)
        return c


def fdt_load(blob):
    magic, total, off_struct, off_strings, off_rsv, version, _, _, size_strings, size_struct = struct.unpack(">10I", blob[:40])
    if magic != 0xD00DFEED:
        die("not a device tree blob")
    strings = blob[off_strings:off_strings + size_strings]
    rsv = blob[off_rsv:off_struct]
    pos, stack, root = off_struct, [], None
    while True:
        tok = struct.unpack(">I", blob[pos:pos + 4])[0]
        pos += 4
        if tok == 1:                                    # FDT_BEGIN_NODE
            end = blob.index(b"\0", pos)
            node = Node(blob[pos:end].decode())
            pos = (end + 4) & ~3
            if stack:
                stack[-1].children.append(node)
            else:
                root = node
            stack.append(node)
        elif tok == 2:                                  # FDT_END_NODE
            stack.pop()
        elif tok == 3:                                  # FDT_PROP
            ln, nameoff = struct.unpack(">II", blob[pos:pos + 8])
            name = strings[nameoff:strings.index(b"\0", nameoff)].decode()
            stack[-1].props[name] = blob[pos + 8:pos + 8 + ln]
            pos = (pos + 8 + ln + 3) & ~3
        elif tok == 4:                                  # FDT_NOP
            continue
        elif tok == 9:                                  # FDT_END
            return root, rsv
        else:
            die(f"bad FDT token {tok}")


def fdt_dump(root, rsv):
    strings, offsets, st = bytearray(), {}, bytearray()

    def soff(n):
        if n not in offsets:
            offsets[n] = len(strings)
            strings.extend(n.encode() + b"\0")
        return offsets[n]

    def emit(node):
        st.extend(struct.pack(">I", 1) + node.name.encode() + b"\0")
        while len(st) % 4:
            st.append(0)
        for k, v in node.props.items():
            st.extend(struct.pack(">III", 3, len(v), soff(k)) + v)
            while len(st) % 4:
                st.append(0)
        for c in node.children:
            emit(c)
        st.extend(struct.pack(">I", 2))

    emit(root)
    st.extend(struct.pack(">I", 9))
    off_rsv = 40
    off_struct = off_rsv + len(rsv)
    off_strings = off_struct + len(st)
    total = off_strings + len(strings)
    hdr = struct.pack(">10I", 0xD00DFEED, total, off_struct, off_strings, off_rsv, 17, 16, 0, len(strings), len(st))
    return hdr + bytes(rsv) + bytes(st) + bytes(strings)


def s(*vals):
    return b"".join(v.encode() + b"\0" for v in vals)


def patch_dtb(blob):
    """QEMU virt DTB + what MPC Sample reads from the device tree (see docs)."""
    root, rsv = fdt_load(blob)
    root.props.update({
        "model": s("MPC Sample"),
        "compatible": s("inmusic,ac50", "inmusic,az07", "rockchip,rk3566", "linux,dummy-virt"),
        "inmusic,product-code": s("AC50"),
        "inmusic,az07-pcb-rev": s("A"),
        "serial-number": s("MPCLAB0000001"),
    })
    chosen = root.child("chosen")
    chosen.props.pop("stdout-path", None)     # the first PL011 is the panel, not a console
    chosen.props["inmusic,emmc-content"] = b""
    chosen.props["u-boot,version"] = s("2024.01-mpclab")
    # same bindings as serial@fe670000 (panel MCU) and serial@fe680000 (rear MIDI) on the device
    cs = root.child("pl011@9000000", create=False).child("control-surface")
    cs.props.update({"compatible": s("serial-midi"), "label": s("Control Surface")})
    ext = root.child("pl011@9040000", create=False).child("midi")
    ext.props.update({"compatible": s("serial-midi"), "current-speed": struct.pack(">I", 31250), "label": s("External MIDI")})
    # mmc index 0 belongs to the eMMC (virtio vda here): the SD slot becomes mmc1, so the card is
    # /dev/mmcblk1p1 as on the device, and never mmcblk0, which fw_env.config uses for U-Boot env.
    root.child("emmc").props["status"] = s("disabled")
    root.child("aliases").props["mmc0"] = s("/emmc")
    return fdt_dump(root, rsv)


# ----------------------------------------------------------------------------- SD card
def sdcard_image(qimg=None):
    """sdcard/ -> FAT16 image labelled like the device's card ("MPC SAMPLE"), built by QEMU's vvfat.
    shortcut: vvfat FAT16 holds at most ~500 MB; write a FAT32/exFAT builder when that is too small."""
    os.makedirs(SDCARD, exist_ok=True)
    os.makedirs(RUN, exist_ok=True)
    spec = json.dumps({"driver": "vvfat", "dir": SDCARD, "fat-type": 16, "label": "MPC SAMPLE"})
    tmp = SD_IMG + ".new"
    r = subprocess.run([qimg or tool("qemu-img"), "convert", "-q", "-O", "raw", "json:" + spec, tmp], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("sdcard/ does not fit on a 500 MB FAT16 card" if "fit" in r.stderr else r.stderr.strip())
    os.truncate(tmp, SD_SIZE)
    os.replace(tmp, SD_IMG)
    return SD_IMG


def qarg(path):
    return path.replace(",", ",,")   # QEMU option values escape commas by doubling them


# ----------------------------------------------------------------------------- run
def accel_choice(want):
    if want != "auto":
        return want
    mach = platform.machine().lower()
    if sys.platform == "darwin" and mach == "arm64":
        return "hvf"
    if sys.platform.startswith("linux") and mach in ("aarch64", "arm64") and os.access("/dev/kvm", os.R_OK | os.W_OK):
        return "kvm"
    return "tcg"   # x86 hosts (Windows, Intel Mac, x86 Linux): software emulation of the A55


def cmd_run(a):
    if port_open(PORTS["qmp"]):
        die("the lab VM is already running (stop it first: python3 lab.py stop)")
    for f in ("kernel/Image", "initramfs.cpio.gz", "emmc.base.qcow2", "lab-layer.ext4", "fw/cmdline.txt"):
        if not os.path.exists(os.path.join(OUT, f)):
            die(f"missing out/{f}: run `python3 lab.py build` first")
    os.makedirs(RUN, exist_ok=True)
    os.makedirs(SHARE, exist_ok=True)
    qemu, qimg = tool("qemu-system-aarch64"), tool("qemu-img")
    accel = accel_choice(a.accel)
    cpu = "host" if accel in ("hvf", "kvm") else "cortex-a55"
    machine = ["-M", "virt", "-accel", accel if accel != "tcg" else "tcg,thread=multi", "-cpu", cpu, "-smp", "4", "-m", "2G"]

    # The DTB is generated from the exact machine options used below; two -serial make
    # QEMU's virt create the second PL011 used for External MIDI.
    raw = os.path.join(RUN, "virt.dtb")
    subprocess.run([qemu, *machine[:1], "virt,dumpdtb=" + raw, *machine[2:], "-display", "none",
                    "-serial", "null", "-serial", "null"], check=True, capture_output=True)
    dtb = os.path.join(RUN, "mpc-virt.dtb")
    with open(raw, "rb") as f, open(dtb, "wb") as g:
        g.write(patch_dtb(f.read()))

    # The app's audio buffer: 1024 frames (23 ms) instead of the device's 384 (8.7 ms). macOS
    # sometimes holds a vCPU for 10-30 ms, which the device's buffer cannot ride out (clicks);
    # this halved the clicks for ~15 ms more pad latency. `--extra snd_aloop.min_buffer=0`
    # gives the device's buffer back (the later option wins). Kernel patch 0003.
    BUFFER = "snd_aloop.min_buffer=1024"
    # Stock command line from the signed FIT (extracted at build time), eMMC = first virtio disk.
    # tty1 stays empty under the boot splash: no cursor, no stock getty login prompt there
    # nohz=off: tickless idle made HVF wake idle vCPUs 15-30 ms late for every disk read
    # (cold project load 42-46 s -> 23 s); costs ~15% of one host core while idle.
    with open(os.path.join(OUT, "fw", "cmdline.txt")) as f:
        args = f.read().strip().replace("/dev/mmcblk0p", "/dev/vda") + " console=hvc0 vt.global_cursor_default=0 systemd.mask=getty@tty1.service nohz=off " + BUFFER + " " + a.extra

    emmc = os.path.join(RUN, "emmc.qcow2")
    if not os.path.exists(emmc):
        subprocess.run([qimg, "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", os.path.join(OUT, "emmc.base.qcow2"), emmc], check=True)

    # SD card: inserted at boot when sdcard/ has files. The image is kept between runs so what
    # the app saves on the card survives a reboot; re-insert from the panel to copy sdcard/ again.
    os.makedirs(SDCARD, exist_ok=True)
    if not os.path.exists(SD_IMG) and os.listdir(SDCARD):
        sdcard_image(qimg)
    card = f",file={qarg(SD_IMG)},format=raw" if os.path.exists(SD_IMG) else ""

    def tcp(name, port, extra=""):
        return f"socket,id={name},host={HOST},port={port},server=on,wait=off{extra}"

    cmd = [qemu, *machine,
           "-kernel", os.path.join(OUT, "kernel", "Image"), "-initrd", os.path.join(OUT, "initramfs.cpio.gz"),
           "-dtb", dtb, "-append", args,
           "-drive", f"if=none,id=emmc,file={qarg(emmc)},format=qcow2", "-device", "virtio-blk-pci,drive=emmc,serial=mpclab-emmc",
           "-drive", f"if=none,id=layer,file={qarg(os.path.join(OUT, 'lab-layer.ext4'))},format=raw,readonly=on",
           "-device", "virtio-blk-pci,drive=layer,serial=mpclab-layer",
           "-device", "sdhci-pci,id=sdhci", "-drive", "if=none,id=sdcard" + card, "-device", "sd-card,drive=sdcard,id=sd0",
           "-device", "virtio-gpu-pci,xres=320,yres=240", "-device", "virtio-tablet-pci", "-device", "virtio-keyboard-pci",
           "-chardev", tcp("cs", PORTS["cs"]), "-serial", "chardev:cs",
           "-chardev", tcp("ext", PORTS["ext_midi"]), "-serial", "chardev:ext",
           "-device", "virtio-serial-pci",
           "-chardev", tcp("con", PORTS["console"], ",logfile=" + os.path.join(RUN, "console.log")), "-device", "virtconsole,chardev=con",
           "-chardev", tcp("audio", PORTS["audio"]), "-device", "virtserialport,chardev=audio,name=mpclab.audio",
           # host sound: the bridge streams PCM here and the panel page's audio worker plays it,
           # connected straight to this WebSocket (no QEMU audio device, no relay in between)
           "-chardev", tcp("pcm", PORTS["pcm"], ",nodelay=on,websocket=on"), "-device", "virtserialport,chardev=pcm,name=mpclab.pcm",
           "-vnc", f"{HOST}:{PORTS['vnc'] - 5900}",      # the panel page's screen
           # ssh: the lab's own sshd (port 22 of the lab network). usb_ssh: the device's USB
           # network address 192.168.155.1, port 2222, where community root-SSH autoruns listen
           "-netdev", f"user,id=net0,hostfwd=tcp:{HOST}:{PORTS['ssh']}-:22,"
                      f"hostfwd=tcp:{HOST}:{PORTS['usb_ssh']}-192.168.155.1:2222",
           "-device", "virtio-net-pci,netdev=net0",
           "-qmp", f"tcp:{HOST}:{PORTS['qmp']},server=on,wait=off",
           "-qmp", f"tcp:{HOST}:{PORTS['qmp_panel']},server=on,wait=off",
           "-monitor", "none",
           "-no-shutdown"]   # a guest poweroff only stops the guest; POWER turns it on again
    if a.headless:
        cmd += ["-display", "none"]
    if not WIN:   # 9p share (/lab in the VM) is a convenience; QEMU for Windows has no virtfs
        cmd += ["-virtfs", f"local,path={SHARE},mount_tag=lab,security_model=none"]
    # The panel server is the panel MCU: start it first, as the MCU is there from power-on
    # (the app asks it for the pot positions, MAIN VOLUME included, right at start).
    panel = None
    if not a.no_panel and not port_open(PORTS["panel"]):
        panel = subprocess.Popen([sys.executable, os.path.join(LAB, "panel", "server.py")])
        print(f"lab: panel at http://{HOST}:{PORTS['panel']}/")
    print(f"lab: accel={accel} cpu={cpu}, sound plays in the panel page" + (" (software CPU emulation: expect it slower than the device)" if accel == "tcg" else ""))
    with open(os.path.join(RUN, "cmdline.txt"), "w") as f:
        f.write(args + "\n")
    env = dict(os.environ)
    # QEMU gets its own process group, so Ctrl-C reaches only this script, which then powers
    # the device down the way its power key does (the app saves first) instead of killing it.
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {"start_new_session": True}
    proc = subprocess.Popen(cmd, env=env, **group)
    try:
        while True:
            try:
                rc = proc.wait()
                break
            except KeyboardInterrupt:
                if proc.poll() is not None:
                    continue
                if getattr(cmd_run, "pressed", False):
                    print("\nlab: killing QEMU")
                    proc.kill()
                    continue
                cmd_run.pressed = True
                print("\nlab: power button + Yes: MPC Sample saves and shuts down (Ctrl-C again to kill QEMU)")
                try:
                    if not shut_down():
                        print("lab: no shutdown after 60 s (a question on the screen?), quitting QEMU")
                    qmp("quit")
                except OSError:
                    proc.kill()
    finally:
        if panel:
            panel.terminate()
    sys.exit(rc)


# ----------------------------------------------------------------------------- ssh & friends
def ssh_base():
    key = os.path.join(OUT, "keys", "id_ed25519")
    if WIN:
        # Windows OpenSSH refuses keys readable by others; keep a private copy in the profile.
        dst = os.path.join(os.path.expanduser("~"), ".ssh", "mpclab_ed25519")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(key, dst)
        subprocess.run(["icacls", dst, "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:R"], capture_output=True)
        key = dst
    return ["-i", key, "-p", str(PORTS["ssh"]), "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=" + ("NUL" if WIN else "/dev/null"), "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10"]


def vm(command, check=True):
    return subprocess.run(["ssh", *ssh_base(), "root@" + HOST, command], check=check).returncode


def cmd_ssh(a):
    sys.exit(subprocess.call(["ssh", *ssh_base(), "root@" + HOST, *a.command]))


def cmd_preload(a):
    dropin = "/run/systemd/system/ac50.service.d/mpclab-preload.conf"
    if a.lib == "--off":
        sys.exit(vm(f"rm -f {dropin} && systemctl daemon-reload && systemctl restart ac50 && systemctl is-active ac50"))
    name = os.path.basename(a.lib)
    vm("mkdir -p /run/mpclab-preload /run/mpc-lab-ui-test " + os.path.dirname(dropin))
    base = ssh_base()
    port_i = base.index("-p")
    scp = ["scp", *base[:port_i], "-P", base[port_i + 1], *base[port_i + 2:]]
    subprocess.run([*scp, a.lib, f"root@{HOST}:/run/mpclab-preload/{name}"], check=True)
    # /run is tmpfs: a reboot always returns to the stock service. /run/mpc-lab-ui-test is where
    # the mpc-lab libraries write overlay.log (their 'prepare' step creates it on the device).
    sys.exit(vm(f"printf '[Service]\\nEnvironment=LD_PRELOAD=/run/mpclab-preload/{name}\\n' > {dropin} && "
                f"systemctl daemon-reload && systemctl restart ac50 && sleep 10 && systemctl is-active ac50 && "
                f"p=$(pidof 'MPC Sample') && grep -q '{name}' /proc/$p/maps && echo 'mapped in MPC Sample' && "
                f"(tail -n 3 /run/mpc-lab-ui-test/overlay.log 2>/dev/null; true)"))


def cmd_stop(a):
    if not port_open(PORTS["qmp"]):
        die("the VM is not running")
    if not shut_down():                       # as on the device: power key, Yes, the app saves
        print("lab: no shutdown after 60 s (a question on the screen?), quitting QEMU: unsaved changes are lost")
    qmp("quit")
    wait_closed(PORTS["qmp"], 10)
    wait_closed(PORTS["panel"], 5)            # `run` closes its panel server after QEMU exits;
                                              # a quick restart must not find it still up


def cmd_reset(a):
    if port_open(PORTS["qmp"]):
        die("stop the VM first")
    path = os.path.join(RUN, "emmc.qcow2")
    if os.path.exists(path):
        os.remove(path)
    print("lab: next start uses a clean eMMC (fresh /data and /factory)")


def cmd_status(a):
    for name, port in PORTS.items():
        print(f"{name:10s} {HOST}:{port}  {'open' if port_open(port) else '-'}")


# ----------------------------------------------------------------------------- autorun packages
# Test SD autorun scripts (root-SSH, mods) the way a user would on the device: the package's
# autorun/ folder goes on a fresh exFAT card, the MPC restarts without a card, the card goes in,
# the app shows the script's question. The card is made inside the running VM by the
# firmware's own fdisk and mkfs.exfat, in RAM on a loop device, so the app sees only the
# finished card. Label without a space: some autorun loaders refuse /media/MPC SAMPLE.
AUTORUN_IMG = os.path.join(RUN, "autorun-card.img")
AUTORUN_DIR = os.path.join(RUN, "autorun-pkg")
AUTORUN_LABEL = "MPCSAMPLE"
DEVICE_LABEL = "MPC SAMPLE"             # how the device formats a card: /media/MPC SAMPLE
LITTER = ("__MACOSX", ".DS_Store")


def unpack_autorun(zip_path, dest=AUTORUN_DIR):
    """Checked extraction; returns the card root, the folder that holds autorun/autorun.json."""
    import zipfile
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            parts = [x for x in info.filename.replace("\\", "/").split("/") if x]
            if not parts or parts[0] in LITTER or parts[-1] in LITTER or parts[-1].startswith("._"):
                continue
            if info.filename.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
                raise ValueError(f"unsafe path in the zip: {info.filename}")
            total += info.file_size
            if total > 1 << 30:
                raise ValueError("the zip expands to more than 1 GiB")
            target = os.path.join(dest, *parts)
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    if os.path.isfile(os.path.join(dest, "autorun.json")):          # zipped the folder's contents
        inner = os.path.join(dest, "autorun")
        os.makedirs(inner)
        for n in os.listdir(dest):
            if n != "autorun":
                shutil.move(os.path.join(dest, n), inner)
    for root in [dest] + sorted(os.path.join(dest, d) for d in os.listdir(dest)):
        if os.path.isfile(os.path.join(root, "autorun", "autorun.json")):
            return root
    raise ValueError("no autorun/autorun.json in the zip")


def guest(command, data=None, timeout=300):
    for attempt in range(30):            # sshd may still be starting (or the MPC rebooting)
        r = subprocess.run(["ssh", *ssh_base(), "root@" + HOST, command], input=data, capture_output=True, timeout=timeout)
        transient = r.returncode == 255 and any(m in r.stderr for m in (
            b"kex_exchange_identification", b"Connection reset", b"Connection refused",
            b"Connection closed", b"timed out"))
        if not transient:
            break
        time.sleep(3)
    if r.returncode:
        raise RuntimeError(f"VM: {r.stderr.decode(errors='replace').strip()[-400:] or 'exit ' + str(r.returncode)}")
    return r.stdout


def make_autorun_card(card_root, label=AUTORUN_LABEL):
    """Fresh exFAT card with card_root's files, written to AUTORUN_IMG; returns its size."""
    import gzip
    import io
    import tarfile
    total = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(card_root) for f in fs)
    size = 64 << 20
    while size < total * 1.3 + (16 << 20):
        size *= 2                                                   # sd-card wants a power of two
    buf = io.BytesIO()
    def as_root(ti):          # in place: TarInfo.replace() deep-copies the whole archive per file
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        return ti
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as t:
        for n in sorted(os.listdir(card_root)):
            t.add(os.path.join(card_root, n), arcname=n, filter=as_root)
    script = f"""set -e
I=/tmp/mpclab-card.img; M=/tmp/mpclab-card
rm -f $I; dd if=/dev/zero of=$I bs=1 count=0 seek={size} 2>/dev/null
printf 'o\\nn\\np\\n1\\n2048\\n\\nt\\n7\\nw\\n' | fdisk $I >/dev/null 2>&1 || true
S=$(dd if=$I bs=1 skip=454 count=4 2>/dev/null | od -D | awk 'NR==1 {{print $2 + 0}}')   # partition start (LBA)
[ "$S" -gt 0 ]
L=$(losetup -f); losetup -o $((S * 512)) $L $I
mkfs.exfat -L '{label}' $L >/dev/null
mkdir -p $M; mount -t exfat $L $M
tar -x -C $M
sync; umount $M; losetup -d $L
gzip -1 -c $I; rm -f $I"""
    image = gzip.decompress(guest(script, data=buf.getvalue(), timeout=600))
    if len(image) != size:
        raise RuntimeError(f"card image is {len(image)} bytes, expected {size}")
    with open(AUTORUN_IMG + ".new", "wb") as f:
        f.write(image)
    os.replace(AUTORUN_IMG + ".new", AUTORUN_IMG)
    return size


def card_swap(path=None):
    """Eject the SD card; insert `path` if given. The guest needs to see the card leave first."""
    qmp("eject", id="sd0", force=True)
    time.sleep(2)
    if path:
        qmp("blockdev-change-medium", id="sd0", filename=os.path.abspath(path), format="raw")


def press(name, hold=0.12):
    for action in ("press", "release"):
        urllib.request.urlopen(urllib.request.Request(
            f"http://{HOST}:{PORTS['panel']}/control", method="POST",
            data=json.dumps({"name": name, "action": action}).encode()), timeout=3)
        time.sleep(hold)


def wait_app(timeout=240):
    """Until the app's audio stream runs (the app is up), plus time for its first screen."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            guest("grep -q RUNNING /proc/asound/Loopback/pcm0p/sub0/status", timeout=15)
            time.sleep(10)
            return
        except (RuntimeError, subprocess.TimeoutExpired):
            time.sleep(3)
    raise RuntimeError("the app did not come up")


def run_autorun(zip_path, reboot=True, confirm=False, log=print, label=AUTORUN_LABEL):
    if not re.fullmatch(r"[A-Za-z0-9_ -]{1,11}", label):   # goes into a shell line in the VM
        raise ValueError("card label: 1-11 letters, digits, space, _ or -")
    root = unpack_autorun(zip_path)
    if guest_off():                      # the card is built inside the running system
        log("the MPC is off: power on")
        power_button()
        wait_app()
    files = sum(len(fs) for _, _, fs in os.walk(root))
    log(f"package: {files} files, autorun/: {', '.join(sorted(os.listdir(os.path.join(root, 'autorun'))))}")
    log("building an exFAT card with the firmware's fdisk/mkfs.exfat in the VM")
    size = make_autorun_card(root, label)
    log(f"card ready: {size >> 20} MiB, label {label}")
    if reboot:
        # as on the device: power off, insert the card, power on; the stock runner starts
        # the package at power-on (silently when its autorun.json says askQuestion: false)
        log("power off (power key, Yes), insert the card, power on: clean RAM, the package runs at boot")
        if not shut_down():
            # a running mod's sandbox can block poweroff; on the device one then holds POWER
            log("no clean shutdown in 60 s: hard power off, like holding POWER (unsaved work is lost)")
            qmp("stop")
        card_swap(AUTORUN_IMG)
        qmp("system_reset")              # power on (from shutdown or the stopped VM)
        qmp("cont")
        wait_app()
        log(f"MPC is up with the card in: /media/{label}")
    else:
        card_swap(AUTORUN_IMG)
        log(f"card inserted: /media/{label}; the app shows the script's question")
    if confirm:
        time.sleep(6)
        press("SCREEN 1")
        log("confirmed (left button above the screen)")


def autorun_results():
    """Script runner lines of this boot, the card's files and the tail of logs scripts leave."""
    return guest("""journalctl -b --no-pager -o short-monotonic 2>/dev/null | grep -iE 'run script|script-runner|autorun' | tail -n 15
for m in /media/*/; do [ -d "$m/autorun" ] || continue; echo; echo "== $m"; ls -la "$m" "$m/autorun" 2>&1; done
for f in /media/*/autorun/*.txt /media/*/*.txt /run/*/events.txt /run/*/*.log; do [ -f "$f" ] && {{ echo; echo "== $f"; tail -n 25 "$f"; }; done; true""",
                 timeout=60).decode(errors="replace")


def cmd_autorun(a):
    if a.results:
        print(autorun_results())
        return
    if not a.zip:
        die("usage: lab.py autorun PACKAGE.zip [--no-reboot] [--yes] [--device-label] | --results")
    run_autorun(a.zip, reboot=not a.no_reboot, confirm=a.yes, log=lambda m: print("lab:", m),
                label=DEVICE_LABEL if a.device_label else AUTORUN_LABEL)


def cmd_panel(a):
    sys.exit(subprocess.call([sys.executable, os.path.join(LAB, "panel", "server.py")]))


# ----------------------------------------------------------------------------- build (Docker)
def cmd_build(a):
    subprocess.run(["docker", "build", "-q", "-t", IMAGE, os.path.join(LAB, "docker")], check=True)
    steps = a.steps or ["kernel", "bridge", "images", "content"]
    if "kernel" in steps:
        docker("sh", "/lab/scripts/build-kernel.sh")
    if "bridge" in steps:
        docker("sh", "/lab/scripts/build-bridge.sh")
    if "tools" in steps:
        docker("sh", "/lab/scripts/build-strace.sh")
    if "images" in steps:
        fw = {"rootfs.ext4": "extracted/part2", "boot.ext2": "extracted/part1", "recovery.ext2": "extracted/parr"}
        for src in fw.values():
            if not os.path.exists(os.path.join(FW_DIR, src)):
                die(f"firmware input {src} not found in FW_DIR={FW_DIR}")
        verify_inputs()
        docker("sh", "/lab/scripts/build-images.sh", mounts=[(os.path.join(FW_DIR, v), "/fw/" + k) for k, v in fw.items()])
    if "content" in steps:
        if not os.path.isdir(CONTENT_DIR):
            print(f"lab: no factory library at CONTENT_DIR={CONTENT_DIR}: /content stays empty (README: Factory library)")
        elif port_open(PORTS["qmp"]):
            die("stop the VM first (python3 lab.py stop): the content step rewrites the eMMC base image")
        else:
            docker("sh", "/lab/scripts/build-content.sh", mounts=[(CONTENT_DIR, "/content")])


def verify_inputs():
    import hashlib
    with open(os.path.join(LAB, "inputs.sha256")) as f:
        for line in f:
            digest, rel = line.split(None, 1)
            rel = rel.strip()
            h = hashlib.sha256()
            with open(os.path.join(FW_DIR, rel), "rb") as g:
                for chunk in iter(lambda: g.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != digest:
                die(f"{rel}: SHA-256 mismatch")
            print(f"{rel}: OK")


# ----------------------------------------------------------------------------- self-test
def selftest():
    # build a small tree, write it, read it back, patch-like edits survive
    root = Node("")
    root.props["compatible"] = s("linux,dummy-virt")
    root.child("chosen").props["stdout-path"] = s("/pl011@9000000")
    root.child("pl011@9000000").props["reg"] = struct.pack(">4I", 0, 0x9000000, 0, 0x1000)
    root.child("pl011@9040000")
    blob = patch_dtb(fdt_dump(root, b"\0" * 16))
    r2, rsv = fdt_load(blob)
    assert rsv == b"\0" * 16
    assert r2.props["model"] == b"MPC Sample\0" and "stdout-path" not in r2.child("chosen").props
    midi = r2.child("pl011@9040000").child("midi")
    assert midi.props["label"] == b"External MIDI\0" and struct.unpack(">I", midi.props["current-speed"])[0] == 31250
    assert fdt_dump(*fdt_load(blob)) == blob
    assert r2.child("aliases").props["mmc0"] == b"/emmc\0" and r2.child("emmc").props["status"] == b"disabled\0"
    if shutil.which("qemu-img"):   # card image: power-of-two size, FAT label where blkid looks for it
        global SDCARD, SD_IMG
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            SDCARD, SD_IMG = os.path.join(d, "sd"), os.path.join(d, "sd.img")
            os.makedirs(SDCARD)
            with open(os.path.join(SDCARD, "Kick é 01.wav"), "wb") as f:
                f.write(b"RIFF" + b"\0" * 1000)
            sdcard_image()
            with open(SD_IMG, "rb") as f:
                img = f.read(64 * 512)
            assert os.path.getsize(SD_IMG) == SD_SIZE and img[63 * 512 + 43:63 * 512 + 54] == b"MPC SAMPLE "
    # autorun zips: autorun/ at the root, one folder down, or only its contents; no path escapes
    import tempfile
    import zipfile
    with tempfile.TemporaryDirectory() as d:
        for layout in ({"autorun/autorun.json": b"{}", "autorun/autorun.sh": b"x", "README.md": b"r"},
                       {"pkg/autorun/autorun.json": b"{}", "pkg/licenses/L": b"l", "__MACOSX/._x": b""},
                       {"autorun.json": b"{}", "autorun.sh": b"x"}):
            zp = os.path.join(d, "p.zip")
            with zipfile.ZipFile(zp, "w") as z:
                for n, data in layout.items():
                    z.writestr(n, data)
            root = unpack_autorun(zp, os.path.join(d, "out"))
            assert os.path.isfile(os.path.join(root, "autorun", "autorun.json")), layout
            assert not os.path.exists(os.path.join(d, "out", "__MACOSX"))
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("../evil", b"x")
        try:
            unpack_autorun(zp, os.path.join(d, "out"))
            raise AssertionError("zip with ../ accepted")
        except ValueError:
            pass
    print("lab selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("steps", nargs="*", help="any of: kernel bridge tools images content (default: kernel bridge images content)")
    r = sub.add_parser("run")
    r.add_argument("--headless", action="store_true")
    r.add_argument("--accel", default="auto", help="hvf, kvm, tcg (default: auto)")
    r.add_argument("--extra", default="", help="extra kernel arguments")
    r.add_argument("--no-panel", action="store_true", help="do not start the panel server")
    s_ = sub.add_parser("ssh"); s_.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("preload"); p.add_argument("lib")
    ar = sub.add_parser("autorun", help="run an SD autorun package (zip) on the emulated MPC")
    ar.add_argument("zip", nargs="?")
    ar.add_argument("--no-reboot", action="store_true", help="insert the card without restarting the MPC first")
    ar.add_argument("--yes", action="store_true", help="also confirm the script's question")
    ar.add_argument("--device-label", action="store_true",
                    help=f"label the card '{DEVICE_LABEL}' like the device does (mounted with a space in the path)")
    ar.add_argument("--results", action="store_true", help="show script runner lines, card files and script logs")
    for n in ("panel", "stop", "reset", "status", "selftest"):
        sub.add_parser(n)
    a = ap.parse_args()
    {"build": cmd_build, "run": cmd_run, "panel": cmd_panel, "ssh": cmd_ssh, "preload": cmd_preload, "autorun": cmd_autorun,
     "stop": cmd_stop, "reset": cmd_reset, "status": cmd_status, "selftest": lambda _: selftest()}[a.cmd](a)


if __name__ == "__main__":
    main()
