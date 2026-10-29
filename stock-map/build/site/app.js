// Renders window.MAP (data.js) in Russian, English or Chinese; the choice is kept in localStorage.
const UI = {
  ru: {
    title: 'Карта экранов MPC Sample, <em>сток</em>',
    lede: 'Прошивка 1.3.0.12 без модов. С экрана Sample нажата каждая кнопка панели и каждое сочетание с SHIFT, а на открывшемся экране — SCREEN 1–3, двойные SCREEN, SHIFT + SCREEN и колесо DATA. Картинки — экран прибора в этот момент (снято в эмуляторе Virtual MPC Sample). «Что даёт» пересказывает официальное руководство Akai. Нажмите на картинку, чтобы увеличить.',
    find: 'Поиск: кнопка или слово',
    does: 'Что даёт', seen: 'На снимке', keys: 'SCREEN 1–3', knobs: 'K1–K3', data: 'DATA', exit: 'Выход',
    guide: 'Руководство Akai', quiet: 'Без видимых изменений:', none: 'Ничего не найдено.',
    kinds: [['Свой экран', 'Открывают отдельную страницу или меню.'], ['Всплывающее окно', 'Переключатель или действие: поверх экрана ненадолго всплывает надпись.'],
            ['Значок у имени сэмпла', 'Режим звука пэда включается и выключается, на экране Sample меняется значок.'], ['Без нового экрана', 'Работают вместе с пэдами или меняют то, что на экране не видно.']],
    foot: 'Снимки сделаны в эмуляторе по стоковой прошивке; поведение с пэдами и внешними устройствами обход не проверял. Тексты «Что даёт» — пересказ руководства akaipro.com/guides/mpc-sample.',
  },
  en: {
    title: 'MPC Sample <em>stock</em> screen map',
    lede: 'Firmware 1.3.0.12, no mods. From the Sample screen every panel button and every SHIFT combination was pressed, then SCREEN 1–3, double SCREEN, SHIFT + SCREEN and the DATA wheel on the screen it opened. Pictures are the device screen at that moment (captured in the Virtual MPC Sample emulator). "What it does" summarizes the official Akai user guide. Click a picture to enlarge it.',
    find: 'Search: button or word',
    does: 'What it does', seen: 'In the shot', keys: 'SCREEN 1–3', knobs: 'K1–K3', data: 'DATA', exit: 'Exit',
    guide: 'Akai user guide', quiet: 'No visible change:', none: 'Nothing found.',
    kinds: [['A screen of its own', 'Open a separate page or menu.'], ['Popup', 'A toggle or an action: a message pops up over the screen for a moment.'],
            ['Icon by the sample name', 'A pad sound mode turns on or off; an icon on the Sample screen changes.'], ['No new screen', 'Work together with the pads, or change something not shown on screen.']],
    foot: 'Shots taken in an emulator running the stock firmware; behaviour with pads and external gear was not walked. "What it does" texts summarize the guide at akaipro.com/guides/mpc-sample.',
  },
  zh: {
    title: 'MPC Sample <em>原厂</em>屏幕地图',
    lede: '固件 1.3.0.12，无任何改装。从 Sample 屏幕出发，按下了面板上的每个按键和每个 SHIFT 组合键，然后在打开的屏幕上依次按 SCREEN 1–3、连按两次 SCREEN、SHIFT + SCREEN 并转动 DATA 旋轮。图片是当时设备屏幕的样子（在 Virtual MPC Sample 模拟器中截取）。“功能”一栏概述了 Akai 官方用户手册。点击图片可放大。',
    find: '搜索：按键或关键词',
    does: '功能', seen: '截图内容', keys: 'SCREEN 1–3', knobs: 'K1–K3', data: 'DATA', exit: '退出',
    guide: 'Akai 用户手册', quiet: '无可见变化：', none: '未找到结果。',
    kinds: [['独立屏幕', '打开单独的页面或菜单。'], ['弹出提示', '开关或操作：屏幕上短暂弹出一条提示。'],
            ['采样名称旁的图标', '打击垫的发声模式开启或关闭，Sample 屏幕上的图标随之变化。'], ['不打开新屏幕', '与打击垫配合使用，或更改屏幕上看不到的设置。']],
    foot: '截图来自运行原厂固件的模拟器；未测试与打击垫和外部设备配合时的行为。“功能”文字概述自 akaipro.com/guides/mpc-sample 的用户手册。',
  },
};
const KINDS = [
  ['SAMPLE', 'SEQ', 'PAD FX', 'KNOB FX', 'CHOP', 'NOTE REPEAT', 'SAMPLE SELECT', 'SAMPLE RECORD', 'SHIFT+SAMPLE', 'SHIFT+SEQ', 'SHIFT+PAD FX', 'SHIFT+KNOB FX',
   'SHIFT+SAMPLE SELECT', 'SHIFT+PAD 5', 'SHIFT+PAD 8', 'SHIFT+PAD 9', 'SHIFT+PAD 11', 'SHIFT+PAD 12', 'SHIFT+PAD 14', 'SHIFT+PAD 16'],
  ['SHIFT+MUTE', 'SHIFT+16 LEVELS', 'SHIFT+TAP TEMPO', 'SHIFT+MINUS', 'SHIFT+PLUS', 'SHIFT+SAMPLE RECORD', 'SHIFT+PAD 1', 'SHIFT+PAD 2', 'SHIFT+PAD 3',
   'SHIFT+PAD 4', 'SHIFT+PAD 6', 'SHIFT+PAD 7', 'SHIFT+PAD 10', 'SHIFT+PAD 15'],
  ['LOOP', '16 LEVELS', 'SHIFT+CHOP', 'SHIFT+LOOP'],
];
const PAD_LABEL = ['FULL LEVEL', 'HALF SEQ', 'DOUBLE SEQ', 'COUNT-IN', 'COMPRESSOR', 'HALF SPEED', 'DOUBLE SPEED', 'MIDI CONFIG',
  'FADER', 'REC QUANTIZE', 'RESAMPLE', 'SONG', 'TRIM SAMPLE', 'TIME CORRECT', 'WARP', 'PROJECT'];

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const anchor = id => id.replace(/\+/g, '-').replace(/ /g, '_');
const stepName = s => s.replace('turn:1', 'DATA ⟳').replace('|', ' ×2 ').replace(' ×2 SCREEN', ' ×2');
function keyName(id) {
  const m = id.match(/^SHIFT\+PAD (\d+)$/);
  return id.replace('SHIFT+', '⇧ ') + (m ? ' · ' + PAD_LABEL[m[1] - 1] : '');
}
const chip = s => `<span class="key">${esc(s)}</span>`;
const byId = Object.fromEntries(MAP.entries.map(e => [e.id, e]));

let lang = 'ru';
try { lang = localStorage.getItem('mpc-map-lang') || (['ru', 'zh'].find(l => navigator.language.startsWith(l)) || 'en'); } catch (e) { /* storage off */ }

function row(label, vals) {
  return vals ? `<tr><th>${label}</th>${vals.map(v => `<td>${v ? esc(v) : '–'}</td>`).join('')}</tr>` : '';
}

function render() {
  const t = UI[lang], L = e => e[lang];
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : lang;
  $('title').innerHTML = t.title;
  $('lede').textContent = t.lede;
  $('find').placeholder = t.find;
  $('foot').textContent = t.foot;
  document.querySelectorAll('.lang button').forEach(b => b.setAttribute('aria-pressed', b.dataset.lang === lang));
  const rest = MAP.entries.map(e => e.id).filter(id => !KINDS.flat().includes(id));
  $('kinds').innerHTML = [...KINDS, rest].map((ids, i) =>
    `<div class="kind"><h3>${t.kinds[i][0]}</h3><p>${t.kinds[i][1]}</p><div class="chips">${ids.filter(id => byId[id]).map(id => `<a href="#${anchor(id)}">${chip(keyName(id))}</a>`).join('')}</div></div>`).join('');
  $('toc').innerHTML = MAP.groups.map(g => `<div><h3>${esc(g[lang])}</h3><div class="tocrow">${g.ids.map(id =>
    `<a class="tocbtn" href="#${anchor(id)}"><b>${esc(keyName(id))}</b><small>${esc(L(byId[id]).screen || '')}</small></a>`).join('')}</div></div>`).join('');
  $('entries').innerHTML = MAP.entries.map(e => {
    const x = L(e);
    const table = (x.keys || x.knobs) ? `<div class="tablewrap"><table class="info">${row(t.keys, x.keys)}${row(t.knobs, x.knobs)}</table></div>` : '';
    const steps = e.steps.map(s => `<figure class="step"><img src="${s.img}" alt="${esc(e.id)} → ${esc(s.step)}" loading="lazy" data-zoom><figcaption>${chip(stepName(s.step))}<span>${esc(s[lang])}</span></figcaption></figure>`).join('');
    const quiet = e.quiet.length ? `<p class="quiet">${t.quiet} ${e.quiet.map(q => chip(stepName(q))).join('')}</p>` : '';
    return `<section class="entry" id="${anchor(e.id)}" data-text="${esc((e.id + ' ' + keyName(e.id) + ' ' + JSON.stringify(e.ru) + JSON.stringify(e.en) + JSON.stringify(e.zh)).toLowerCase())}">
  <header><h2>${chip(keyName(e.id))}</h2><p class="screen">${esc(x.screen || '')}</p></header>
  <div class="body">
    <figure class="main"><img src="${e.img}" alt="${esc(e.id)}" loading="lazy" data-zoom></figure>
    <div class="facts">
      ${x.does ? `<div class="does"><b>${t.does}</b>${esc(x.does)}</div>` : ''}
      ${table}
      ${x.data ? `<p><b>${t.data}:</b> ${esc(x.data)}</p>` : ''}
      ${x.exit ? `<p><b>${t.exit}:</b> ${esc(x.exit)}</p>` : ''}
      ${x.note ? `<p class="seen"><b>${t.seen}:</b> ${esc(x.note)}</p>` : ''}
      ${e.guide ? `<a class="guide" href="${e.guide}" target="_blank" rel="noopener">${t.guide} ↗</a>` : ''}
    </div>
  </div>
  ${steps ? `<div class="steps">${steps}</div>` : ''}${quiet}
</section>`;
  }).join('') + `<p id="none" hidden>${t.none}</p>`;
  filter();
}

function filter() {
  const q = $('find').value.trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('.entry').forEach(s => { const on = !q || s.dataset.text.includes(q); s.hidden = !on; shown += on; });
  $('none').hidden = shown > 0;
}

document.querySelectorAll('.lang button').forEach(b => b.addEventListener('click', () => {
  lang = b.dataset.lang;
  try { localStorage.setItem('mpc-map-lang', lang); } catch (e) { /* storage off */ }
  render();
}));
$('find').addEventListener('input', filter);
const z = $('zoom'), zi = z.querySelector('img');
document.addEventListener('click', e => {
  if (e.target.matches('[data-zoom]')) { zi.src = e.target.src; zi.alt = e.target.alt; z.hidden = false; }
  else if (!z.hidden) z.hidden = true;
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') z.hidden = true; });
render();
