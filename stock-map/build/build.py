"""Build stock-map/index.html: the stock screen map as one self-contained page.

    python3 tools/stock_walk.py          # photograph every stock screen (out/stockmap/, ~45 min)
    python3 stock-map/build/build.py     # then build the page from those shots and the notes here

Inputs: out/stockmap/walk.json and its PNGs (from the walk), notes_{ru,en,zh}.json (what
each button does and what the shots show), order.json (panel order), site/ (page template).
Needs Pillow only for the walk, not here."""
import json, os, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(LAB, 'out', 'stockmap')
OUT = os.path.join(LAB, 'out', 'stock-map-build')                  # scratch; the result goes to stock-map/index.html
walk = [e for e in json.load(open(os.path.join(SRC, 'walk.json'))) if 'file' in e]
ru, en, zh = (json.load(open(os.path.join(HERE, f'notes_{l}.json'))) for l in ('ru', 'en', 'zh'))
order = json.load(open(os.path.join(HERE, 'order.json')))
GROUP_EN = {'Левая колонка': 'Left column', 'Правая колонка': 'Right column', 'Запись и транспорт': 'Record and transport',
            'Колесо DATA': 'DATA wheel', 'SHIFT + пэды (подписи под пэдами)': 'SHIFT + pads (labels under the pads)'}
GROUP_ZH = {'Левая колонка': '左列', 'Правая колонка': '右列', 'Запись и транспорт': '录音与走带',
            'Колесо DATA': 'DATA 滚轮', 'SHIFT + пэды (подписи под пэдами)': 'SHIFT + 打击垫（打击垫下方的标签）'}

shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(os.path.join(OUT, 'img'))
used = set()
def img(f):
    used.add(f); return 'img/' + f

by = {}
for e in walk:
    p = e['path']; d = by.setdefault(p[0], {'main': None, 'steps': []})
    if len(p) == 1 and d['main'] is None: d['main'] = e
    elif len(p) == 2: d['steps'].append(e)

entries, groups = [], []
for g in order:
    ids = []
    for k in g['entries']:
        if k not in by: continue
        d, r, n, c = by[k], ru[k], en[k], zh[k]
        main_fp = d['main']['fp']
        steps, quiet, seen = [], [], {main_fp}
        for s in d['steps']:
            st = s['path'][1]
            known = st in r.get('steps', {})
            if s['fp'] in seen and not known:
                if s['fp'] == main_fp and st not in quiet: quiet.append(st)
                continue
            seen.add(s['fp'])
            steps.append({'step': st, 'img': img(s['file']), 'ru': r.get('steps', {}).get(st, ''), 'en': n.get('steps', {}).get(st, ''), 'zh': c.get('steps', {}).get(st, '')})
        pick = lambda x: {f: x.get(f) for f in ('screen', 'does', 'keys', 'knobs', 'data', 'exit', 'note') if x.get(f)}
        entries.append({'id': k, 'img': img(d['main']['file']), 'guide': r.get('guide'), 'steps': steps, 'quiet': quiet, 'ru': pick(r), 'en': pick(n), 'zh': pick(c)})
        ids.append(k)
    groups.append({'ru': g['name'], 'en': GROUP_EN[g['name']], 'zh': GROUP_ZH[g['name']], 'ids': ids})

for f in used: shutil.copy(os.path.join(SRC, f), os.path.join(OUT, 'img', f))
data = {'groups': groups, 'entries': entries}
open(os.path.join(OUT, 'data.js'), 'w').write('window.MAP = ' + json.dumps(data, ensure_ascii=False) + ';\n')
for f in ('index.html', 'styles.css', 'app.js'):
    shutil.copy(os.path.join(HERE, 'site', f), os.path.join(OUT, f))
size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(OUT) for f in fs)
print(len(entries), 'entries,', len(used), 'images,', round(size / 1e6, 1), 'MB')

# One self-contained index.html as well: images as data URIs, styles and scripts inline, so it
# works opened from anywhere (inside a zip on Windows, a chat attachment, no server).
import base64, re
page = open(os.path.join(OUT, 'index.html')).read()
data_js = open(os.path.join(OUT, 'data.js')).read()
data_js = re.sub(r'"img/([0-9]+\.png)"', lambda m: '"data:image/png;base64,' + base64.b64encode(open(os.path.join(OUT, 'img', m.group(1)), 'rb').read()).decode() + '"', data_js)
page = page.replace('<link rel="stylesheet" href="styles.css">', '<style>\n' + open(os.path.join(OUT, 'styles.css')).read() + '</style>')
page = page.replace('<script src="data.js"></script>', '<script>\n' + data_js + '</script>')
page = page.replace('<script src="app.js"></script>', '<script>\n' + open(os.path.join(OUT, 'app.js')).read() + '</script>')
assert 'src="data.js"' not in page and 'href="styles.css"' not in page and '"img/' not in page
for f in ('styles.css', 'app.js', 'data.js'): os.remove(os.path.join(OUT, f))
shutil.rmtree(os.path.join(OUT, 'img'))
open(os.path.join(os.path.dirname(HERE), 'index.html'), 'w').write(page)
shutil.rmtree(OUT)
print('stock-map/index.html:', round(len(page) / 1e6, 1), 'MB')
