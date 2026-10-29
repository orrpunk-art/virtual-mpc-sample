"""Full flow of the Pad Resample mod package in the lab, as on the device: cold start with the
card in, the app up with the library, mixer, knob sweeps at hand speed, nothing of it reaching
the app's page, leaving with B1 (also while playing), master slot, step sequencer. The panel
paces bytes like the MCU's UART. Prints PASS/FAIL lines; screenshots go to out/flow/.

    python3 tools/flow_test.py PACKAGE.zip     # with `lab.py run` (and its panel) up

Needs Pillow (pip install pillow) for the screenshots.
"""
import json, os, subprocess, sys, time, urllib.request
LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)
import lab                                                   # noqa: E402
from PIL import Image                                        # noqa: E402

OUT = os.path.join(LAB, 'out', 'flow')
SETTINGS = '/data/Settings/Akai/MPC Sample/MPC Sample.settings'
ORANGE = (232, 138, 42)                                      # our screens' title colour
fails = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (f'  ({detail})' if detail else ''), flush=True)
    if not ok: fails.append(name)


def c(n, a, v=None):
    d = {'name': n, 'action': a}
    if v is not None: d['value'] = v
    urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/control', method='POST', data=json.dumps(d).encode()), timeout=3)


def tap(n, hold=None):
    if hold: c(hold, 'press'); time.sleep(0.1)
    c(n, 'press'); time.sleep(0.08); c(n, 'release')
    if hold: time.sleep(0.1); c(hold, 'release')
    time.sleep(0.35)


def double(n):                                              # hold SHIFT, press n twice: our page
    c('SHIFT', 'press'); time.sleep(0.1)
    for _ in range(2): c(n, 'press'); time.sleep(0.08); c(n, 'release'); time.sleep(0.15)
    c('SHIFT', 'release'); time.sleep(0.4)


def turn(n):
    for _ in range(abs(n)): c('DATA', 'turn', 1 if n > 0 else 0x7F); time.sleep(0.08)
    time.sleep(0.3)


def sweep(knob, a, b, step_ms=8):                            # a hand turn: one CC per ~8 ms
    for v in range(a, b + (1 if b >= a else -1), 1 if b >= a else -1):
        c(knob, 'set', v); time.sleep(step_ms / 1000)


def shot(name):
    p = os.path.join(OUT, name)
    lab.qmp('screendump', filename=p + '.ppm')
    im = Image.open(p + '.ppm').convert('RGB'); im.save(p + '.png'); os.remove(p + '.ppm')
    return im


def ours_on(im):                                             # our title (Mixer / Inserts) at the top centre
    return sum(1 for x in range(120, 200) for y in range(12, 24) if im.getpixel((x, y)) == ORANGE) > 20


def guest(cmd, **kw):
    return lab.guest(cmd, **kw).decode(errors='replace')


def main(package):
    os.makedirs(OUT, exist_ok=True)
    # settings to Off, while no app runs (the app writes the file when it stops)
    guest("systemctl stop mpc-clone-trial-v1-clone.service ac50.service 2>/dev/null; "
          f"sed -i -e 's|PadMidiIn\" val=\"1\"|PadMidiIn\" val=\"0\"|' -e 's|MidiSyncOut\" val=\"1\"|MidiSyncOut\" val=\"0\"|' '{SETTINGS}'; sync; true")
    t0 = time.time()
    r = subprocess.run([sys.executable, '-u', os.path.join(LAB, 'lab.py'), 'autorun', package], cwd=LAB, capture_output=True, text=True)
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])
    ready = None
    for i in range(150):                                     # boot: until the app with our library is up
        m = guest('p=$(systemctl show -p MainPID --value ac50.service); grep -c padresample /proc/$p/maps 2>/dev/null; '
                  'test -f /run/mpc-pad-resample/ui-ready && echo ui; true', timeout=20).split()
        if ready is None and 'ui' in m and m[0] != '0': ready = time.time() - t0
        if ready and time.time() - t0 > ready + 8: break
        if i % 4 == 0: shot(f'boot{i:03d}')
        time.sleep(1)
    starts = guest("journalctl -b -u ac50.service -o cat | grep -c '^Started' || true").strip()
    check('app up with our library', ready is not None, f'{ready and round(ready)} s after autorun')
    check('app started twice: boot, then once with the library', starts == '2', f'ac50 starts {starts}')
    check('no RAM-copy mod', guest('systemctl is-active mpc-clone-trial-v1-clone.service || true').strip() != 'active')
    s = guest(f"grep -o 'PadMidiIn\" val=\"[01]\"' '{SETTINGS}'; grep -o 'MidiSyncOut\" val=\"[01]\"' '{SETTINGS}'")
    check('MIDI settings on', s.count('val="1"') == 2, s.replace('\n', ' '))
    maps = guest('for p in $(pidof "MPC Sample"); do grep -c padresample /proc/$p/maps; done; true').split()
    check('library in the running app', any(m != '0' for m in maps), str(maps))

    tap('SAMPLE'); time.sleep(1); base = shot('sample')
    check('sample page without our screen', not ours_on(base))
    tap('MUTE', hold='SHIFT'); time.sleep(0.5)
    check('SHIFT + MUTE stays the app\'s (UNMUTE ALL)', not ours_on(shot('unmute_all')))
    tap('KNOB FX', hold='SHIFT'); time.sleep(0.5)
    fx_sel = shot('fx_select')                               # the mixer opens over it (first press)
    check('one SHIFT + KNOB FX: the app\'s FX SELECT, not ours', not ours_on(fx_sel))
    tap('SAMPLE'); time.sleep(0.8)
    double('KNOB FX'); time.sleep(0.5)
    check('mixer opens', ours_on(shot('mixer')))
    sweep('K1', 0, 127); time.sleep(0.15); a = shot('k1_after'); time.sleep(1.5); b = shot('k1_later')
    check('K1 sweep: screen settles at once (no inertia)', a.tobytes() == b.tobytes())
    sweep('K1', 127, 50); sweep('K2', 0, 127); sweep('K2', 127, 64); sweep('K3', 0, 90)
    time.sleep(0.15); a = shot('k123_after'); time.sleep(1.5); b = shot('k123_later')
    check('K1-K3 sweeps: screen settles at once', a.tobytes() == b.tobytes())
    tap('SCREEN 3'); tap('SCREEN 2'); tap('SCREEN 1'); time.sleep(0.2); tap('SCREEN 1'); time.sleep(0.6)
    left = shot('left_b1')
    check('B1 leaves: our screen gone', not ours_on(left))
    check('no knob or button reached the app (FX SELECT as before)', left.tobytes() == fx_sel.tobytes())
    tap('PLAY'); time.sleep(0.8)
    double('KNOB FX'); time.sleep(0.4)
    frames = []
    for i in range(15): frames.append(ours_on(shot(f'play{i:02d}'))); time.sleep(0.05)
    check('mixer stays on screen while playing', all(frames), f'{sum(frames)}/15')
    sweep('K1', 50, 110); tap('SCREEN 1'); time.sleep(0.2)
    frames = []
    for i in range(10): frames.append(ours_on(shot(f'playleft{i:02d}'))); time.sleep(0.1)
    check('B1 while playing: our screen gone', not any(frames), f'{sum(frames)}/10 frames still ours')
    tap('STOP'); time.sleep(0.5)
    # master slot 4: Delay, its Time on K1, back out
    double('KNOB FX'); tap('SCREEN 2', hold='SHIFT'); time.sleep(0.4)
    turn(3); tap('SCREEN 3'); tap('SCREEN 3'); time.sleep(0.8)
    sweep('K1', 0, 100); time.sleep(0.2); m = shot('master')
    check('master slot screen', ours_on(m))
    tap('SCREEN 2'); time.sleep(0.4)                         # clear it again
    tap('SCREEN 1'); tap('SCREEN 1'); time.sleep(0.6)
    check('leaving master: our screen gone', not ours_on(shot('left_master')))
    # our step sequencer (SHIFT + SEQ twice), a pad, then back to SAMPLE
    double('SEQ'); time.sleep(0.4)
    shot('seq')
    c('PAD 1', 'press', 100); time.sleep(0.1); c('PAD 1', 'release', 0); time.sleep(0.5); shot('seq_pad')
    tap('SAMPLE'); time.sleep(1)
    check('SAMPLE after the sequencers: app screen', not ours_on(shot('end')))
    print('RESULT', 'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails))


if __name__ == '__main__':
    main(sys.argv[1])
