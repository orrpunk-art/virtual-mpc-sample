"""Walk the stock MPC Sample UI in the lab and photograph every screen reached.

The pictures of stock-map/ come from here (stock-map/build/build.py makes the page from them).
Each path starts from Home (the Sample screen, transport stopped) and is a list of steps:
a button name, "SHIFT+NAME" (hold SHIFT, tap NAME), "turn:N" (DATA), or "K1:V" (a knob).
Every step's screen is saved to out/stockmap/NNNN.png and logged with its path in
out/stockmap/walk.json. Run with `lab.py run` up, the stock app only (no card, no preload).
The walk presses real functions (Normalize, Extract, Save Sample, ...): the project is copied
to /data/.stockmap-backup first and put back whenever Home cannot be reached and at the end;
samples it saves stay in /data/UserContent/Samples/User. Rerunning resumes after the last path.

    python3 tools/stock_walk.py

Needs Pillow.
"""
import hashlib, json, os, sys, time
LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(LAB, 'tools'))
from flow_test import c, shot                                  # noqa: E402

OUT = os.path.join(LAB, 'out', 'stockmap')
BUTTONS = ['SAMPLE', 'SEQ', 'PAD FX', 'KNOB FX', 'ERASE', 'NOTE REPEAT', 'PAD BANK', 'CHOP', 'MUTE',
           'LOOP', '16 LEVELS', 'SAMPLE SELECT', 'TAP TEMPO', 'MINUS', 'PLUS', 'SAMPLE RECORD',
           'SEQ RECORD', 'PLAY', 'STOP', 'DATA']
INSIDE = ['SCREEN 1', 'SCREEN 2', 'SCREEN 3', 'SHIFT+SCREEN 1', 'SHIFT+SCREEN 2', 'SHIFT+SCREEN 3',
          'DATA', 'turn:1', 'SCREEN 1|SCREEN 1', 'SCREEN 2|SCREEN 2', 'SCREEN 3|SCREEN 3']
log, n = [], 0


def press(name):                                           # DATA is its push; pads hit at 100
    pad = name.startswith('PAD ') and name[4:].isdigit()
    c(name, 'press', 100 if pad else None); time.sleep(0.09); c(name, 'release', 0 if pad else None)


def step(s):
    if s.startswith('turn:'):
        k = int(s[5:])
        for _ in range(abs(k)): c('DATA', 'turn', 1 if k > 0 else 0x7F); time.sleep(0.08)
    elif s[:2] in ('K1', 'K2', 'K3') and ':' in s:
        c(s[:2], 'set', int(s[3:]))
    elif s.startswith('SHIFT+'):
        c('SHIFT', 'press'); time.sleep(0.1); press(s[6:]); time.sleep(0.1); c('SHIFT', 'release')
    else:
        press(s)
    time.sleep(0.9)


def fingerprint(im):
    return hashlib.sha1(im.convert('L').resize((80, 60)).point(lambda v: v // 32 * 32).tobytes()).hexdigest()[:12]


HOME = None


def home():
    """Back to the Sample screen: STOP, Cancel/back on SCREEN 1 if a dialog holds, SAMPLE."""
    global HOME
    # tab 1 cycles Trim/Mix/Amp Env; modes that stay on (Chop, Note Repeat, ...) go off with their button
    mode = [last_entry] if last_entry in BUTTONS and last_entry not in ('SAMPLE', 'DATA', 'PLAY', 'STOP') else []
    escapes = ['SCREEN 1'] * 3 + [m for x in mode for m in (x, 'SCREEN 1', 'SCREEN 1', 'SCREEN 1')] + ['NOTE REPEAT'] + ['SCREEN 1'] * 3
    for attempt in range(len(escapes)):
        for _ in range(2): c('STOP', 'press'); time.sleep(0.05); c('STOP', 'release'); time.sleep(0.2)
        press('SAMPLE'); time.sleep(0.9)
        im = shot('_home')
        f = fingerprint(im.crop((0, 8, 320, 40)))          # the Sample tabs (not the play bar above them)
        if HOME is None or f == HOME:
            HOME = HOME or f
            return True
        press(escapes[attempt]); time.sleep(0.7)
    return False


def restore():
    """The project as it was before the walk (/data/.stockmap-backup), then a reboot."""
    import lab
    lab.guest('rm -rf /data/TempProjects && cp -a /data/.stockmap-backup/TempProjects /data/ && sync')
    lab.qmp('system_reset'); time.sleep(5); lab.wait_app(); time.sleep(12)


last_entry = None


def walk(path):
    global n, last_entry
    if not home():
        restore()
        if not home():
            log.append({'path': path, 'error': 'could not get home'}); return
    last_entry = path[0]
    for i, s in enumerate(path):
        for part in s.split('|'):
            step(part)
        n += 1
        name = f'{n:04d}'
        im = shot(name)
        os.replace(os.path.join(LAB, 'out', 'flow', name + '.png'), os.path.join(OUT, name + '.png'))
        log.append({'path': path[:i + 1], 'file': name + '.png', 'fp': fingerprint(im)})
    with open(os.path.join(OUT, 'walk.json'), 'w') as f:
        json.dump(log, f, indent=1)


def main():
    global n
    os.makedirs(OUT, exist_ok=True)
    done = set()
    if os.path.exists(os.path.join(OUT, 'walk.json')):        # resume: keep what is there
        log.extend(json.load(open(os.path.join(OUT, 'walk.json'))))
        n = max([int(e['file'][:4]) for e in log if 'file' in e] + [0])
        done = {tuple(e['path']) for e in log if 'file' in e}
    paths = []
    for b in BUTTONS:                                           # each button, and inside its screen
        paths.append([b])
        paths += [[b, x] for x in INSIDE]
    for b in BUTTONS + ['SHIFT', 'PAD 1']:                       # SHIFT + each button, and inside
        if b in ('SHIFT', 'PAD 1'): continue
        paths.append(['SHIFT+' + b])
        paths += [['SHIFT+' + b, x] for x in INSIDE[:7]]
    for p in range(1, 17):                                      # SHIFT + pad (labels under the pads)
        paths.append([f'SHIFT+PAD {p}'])
        paths += [[f'SHIFT+PAD {p}', x] for x in INSIDE[:7]]
    import lab                                                  # the project as it is now: what restore() brings back
    lab.guest('[ -d /data/.stockmap-backup ] || { mkdir -p /data/.stockmap-backup && cp -a /data/TempProjects /data/.stockmap-backup/; }; sync')
    restore()                                                   # a known start
    print(len(paths), 'paths', flush=True)
    for k, p in enumerate(paths):
        if tuple(p) in done and (len(p) == 1 or tuple(p[:1]) in done): continue
        walk(p)
        if k % 20 == 0: print(k, p, flush=True)
    restore()
    print('screens:', n, 'unique:', len({e['fp'] for e in log if 'fp' in e}))


if __name__ == '__main__':
    main()
