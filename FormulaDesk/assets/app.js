'use strict';

const $ = (id) => document.getElementById(id);
const editor = $('result');
const rendered = $('rendered');
const state = {
  busy: false, ready: false, inputMode: 'image', mode: 'formula',
  source: null, crop: null, strokes: [], activeStroke: null,
  result: {formula: '', document: ''}, version: 0, valid: false, math: null
};

function status(text, variant = '') {
  $('status').className = variant;
  $('status').lastElementChild.textContent = text;
}

async function call(name, ...args) {
  const response = await window.pywebview.api[name](...args);
  if (!response.ok) throw new Error(response.error);
  return response.data;
}

function updateControls() {
  const locked = state.busy || !state.ready;
  for (const id of ['choose', 'paste', 'change', 'sample', 'imageMode', 'drawMode', 'formulaMode', 'docMode', 'zoom', 'clearCrop']) {
    $(id).disabled = locked;
  }
  $('recognize').disabled = locked || (state.inputMode === 'image' ? !state.source : !state.strokes.length);
  $('undo').disabled = $('clearPad').disabled = locked || !state.strokes.length;
  $('prev').disabled = locked || !state.source || state.source.page <= 1;
  $('next').disabled = locked || !state.source || state.source.page >= state.source.pages;
  $('pageNum').disabled = locked;
  $('enhance').disabled = locked;
  editor.readOnly = state.busy;
  const hasResult = !locked && !!editor.value.trim();
  const canExportMath = hasResult && state.valid && state.mode === 'formula';
  $('copy').disabled = $('export').disabled = !hasResult;
  $('mathml').disabled = $('png').disabled = !canExportMath;
  for (const item of $('saveMenu').querySelectorAll('button')) {
    const kind = item.dataset.save;
    item.hidden = state.mode === 'document' ? kind !== 'markdown' : kind === 'markdown';
    item.disabled = (kind === 'png' || kind === 'mathml') && !canExportMath;
  }
}

async function run(message, action) {
  if (state.busy || !state.ready) return;
  state.busy = true;
  updateControls();
  status(message, 'busy');
  try {
    status(await action() || '就绪');
  } catch (error) {
    status(error.message || '操作失败，请重试。', 'error');
  } finally {
    state.busy = false;
    updateControls();
  }
}

function setTab(id, active) {
  $(id).classList.toggle('selected', active);
  $(id).setAttribute('aria-pressed', active);
}

function inputMode(mode) {
  state.inputMode = mode;
  const image = mode === 'image';
  $('inputView').hidden = !image;
  $('drawView').hidden = image;
  setTab('imageMode', image);
  setTab('drawMode', !image);
  updateHint();
  updateControls();
}

function updateHint() {
  let hint = state.crop ? '将识别框选区域。' : '导入图片后可框选单个公式。';
  if (state.source?.pages && !state.crop) hint = state.mode === 'formula' ? '请框选当前页中的一个公式。' : '将提取当前页文字，也可框选局部。';
  if (state.inputMode === 'draw') hint = '每次书写一个公式，尽量把笔画分开。';
  $('inputHint').textContent = hint;
  $('enhanceLabel').hidden = !state.source || state.inputMode !== 'image' || state.mode !== 'formula';
}

function showSource(source) {
  state.source = source;
  state.crop = null;
  $('sourceImage').src = source.src;
  $('fileName').textContent = source.name;
  $('fileName').title = source.name + ' · ' + source.width + ' × ' + source.height;
  $('dropZone').hidden = true;
  $('loaded').hidden = false;
  $('pageNav').hidden = !source.pages;
  $('pageNum').value = source.page;
  $('pageNum').max = source.pages || 1;
  $('pageTotal').textContent = '/ ' + source.pages;
  redrawCrop();
  inputMode('image');
}

function choose() {
  return run('等待选择文件…', async () => {
    const source = await call('choose');
    if (source) showSource(source);
    return source ? '文件已导入' : '就绪';
  });
}

function paste() {
  return run('读取剪贴板…', async () => {
    showSource(await call('paste'));
    return '已粘贴图片';
  });
}

async function upload(file) {
  return run('正在导入文件…', async () => {
    await call('upload_start', file.name || '剪贴板图片', file.size);
    const chunkSize = 384 * 1024;
    for (let offset = 0; offset < file.size; offset += chunkSize) {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('无法读取文件，请重新导入。'));
        reader.readAsDataURL(file.slice(offset, offset + chunkSize));
      });
      await call('upload_part', dataUrl.split(',')[1]);
    }
    showSource(await call('upload_finish'));
    return '文件已导入';
  });
}

function goToPage(page) {
  if (!state.source?.pages) return;
  if (!Number.isInteger(page) || page < 1 || page > state.source.pages) {
    $('pageNum').value = state.source.page;
    status('请输入有效的 PDF 页码。', 'error');
    return;
  }
  if (page === state.source.page) return;
  return run('正在打开 PDF 页面…', async () => {
    try {
      showSource(await call('page', page));
      return '已打开第 ' + page + ' 页';
    } finally {
      $('pageNum').value = state.source.page;
    }
  });
}

function redrawCrop() {
  for (const overlay of [$('selection'), $('zoomSelection')].filter(Boolean)) {
    overlay.hidden = !state.crop;
    if (!state.crop) continue;
    const [x1, y1, x2, y2] = state.crop;
    Object.assign(overlay.style, {
      left: (x1 * 100) + '%', top: (y1 * 100) + '%',
      width: ((x2 - x1) * 100) + '%', height: ((y2 - y1) * 100) + '%'
    });
  }
  $('clearCrop').hidden = !state.crop;
  updateHint();
}

function enableCrop(box) {
  let start = null;
  let previous = null;
  function point(event) {
    const rect = box.getBoundingClientRect();
    const clamp = (v) => Math.min(1, Math.max(0, v));
    return [clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)];
  }
  function move(event) {
    if (!start || !box.hasPointerCapture(event.pointerId)) return;
    const [x, y] = point(event);
    const [sx, sy] = start;
    state.crop = [Math.min(sx, x), Math.min(sy, y), Math.max(sx, x), Math.max(sy, y)];
    redrawCrop();
  }
  box.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || state.busy || start) return;
    start = point(event);
    previous = state.crop;
    box.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  box.addEventListener('pointermove', move);
  box.addEventListener('pointerup', (event) => {
    if (!start || !box.hasPointerCapture(event.pointerId)) return;
    move(event);
    const [x1, y1, x2, y2] = state.crop;
    if ((x2 - x1) * box.clientWidth < 4 || (y2 - y1) * box.clientHeight < 4) state.crop = previous;
    start = null;
    redrawCrop();
  });
  box.addEventListener('pointercancel', () => { start = null; state.crop = previous; redrawCrop(); });
}

function showZoom() {
  const box = document.createElement('div');
  box.className = 'zoom-box';
  const img = document.createElement('img');
  img.alt = '放大后的待识别内容';
  img.draggable = false;
  img.style.width = Math.min(1700, Math.max(700, state.source.width)) + 'px';
  img.src = state.source.src;
  const overlay = document.createElement('div');
  overlay.id = 'zoomSelection';
  box.append(img, overlay);
  $('zoomStage').replaceChildren(box);
  enableCrop(box);
  redrawCrop();
  $('cropDialog').showModal();
}

const pad = $('pad');
const pen = pad.getContext('2d');
function drawPad() {
  pen.fillStyle = '#fff';
  pen.fillRect(0, 0, pad.width, pad.height);
  pen.strokeStyle = pen.fillStyle = '#131d28';
  pen.lineWidth = 6;
  pen.lineJoin = pen.lineCap = 'round';
  for (const stroke of state.strokes) {
    pen.beginPath();
    if (stroke.length === 1) {
      pen.arc(...stroke[0], 3, 0, 2 * Math.PI);
      pen.fill();
      continue;
    }
    pen.moveTo(...stroke[0]);
    for (const point of stroke.slice(1)) pen.lineTo(...point);
    pen.stroke();
  }
  updateControls();
}
function padPoint(event) {
  const box = pad.getBoundingClientRect();
  return [(event.clientX - box.left) / box.width * pad.width, (event.clientY - box.top) / box.height * pad.height];
}
pad.addEventListener('pointerdown', (event) => {
  if (event.button !== 0 || state.busy || state.activeStroke !== null) return;
  pad.setPointerCapture(event.pointerId);
  state.activeStroke = event.pointerId;
  state.strokes.push([padPoint(event)]);
  drawPad();
});
pad.addEventListener('pointermove', (event) => {
  if (state.activeStroke !== event.pointerId) return;
  const points = event.getCoalescedEvents?.();
  for (const item of points?.length ? points : [event]) state.strokes.at(-1).push(padPoint(item));
  drawPad();
});
pad.addEventListener('lostpointercapture', () => { state.activeStroke = null; });

function resultMode(mode) {
  if (state.mode === mode) return;
  state.mode = mode;
  const isFormula = mode === 'formula';
  setTab('formulaMode', isFormula);
  setTab('docMode', !isFormula);
  $('editorLabel').textContent = isFormula ? 'LaTeX 源码' : 'Markdown 源码';
  $('resultHint').textContent = isFormula ? '预览公式，并直接编辑 LaTeX。' : '提取图片或 PDF 当前页中的文字。';
  $('copy').textContent = isFormula ? '复制 LaTeX' : '复制 Markdown';
  $('mathml').hidden = $('png').hidden = !isFormula;
  editor.placeholder = isFormula ? '识别后可在这里修改 LaTeX，也可直接输入公式。' : '识别后可在这里编辑 Markdown 文本。';
  editor.value = state.result[mode];
  closeMenu();
  updateHint();
  render();
}

function cleanTex(source) {
  return source.trim().replace(/^\$\$([\s\S]*)\$\$$|^\\\[([\s\S]*)\\\]$|^\\\(([\s\S]*)\\\)$|^\$([^\n]*)\$$/, (_, a, b, c, d) => a ?? b ?? c ?? d).trim();
}

async function render() {
  const version = ++state.version;
  const text = editor.value.trim();
  state.valid = false;
  state.math = null;
  rendered.classList.toggle('document', state.mode === 'document');
  rendered.replaceChildren();
  rendered.hidden = !text;
  $('previewEmpty').hidden = !!text;
  $('renderStatus').textContent = '';
  updateControls();
  if (!text) return;
  if (state.mode === 'document') {
    rendered.textContent = text;
    return;
  }
  try {
    await MathJax.startup.promise;
    const source = cleanTex(text);
    const node = await MathJax.tex2svgPromise(source, {display: true});
    if (version !== state.version) return;
    if (!node.querySelector('svg') || node.querySelector('[data-mml-node="merror"]')) throw new Error();
    rendered.append(node);
    state.math = source;
    state.valid = true;
  } catch {
    if (version !== state.version) return;
    rendered.hidden = true;
    $('previewEmpty').hidden = false;
    $('renderStatus').textContent = 'LaTeX 暂时无法渲染';
  }
  updateControls();
}

function setResult(text) {
  state.result[state.mode] = text;
  editor.value = text;
  return render();
}

async function recognize() {
  if ($('recognize').disabled) return;
  return run('正在本地识别… 首次运行需要载入模型', async () => {
    const result = state.inputMode === 'draw'
      ? await call('recognize_drawing', pad.toDataURL('image/png').split(',')[1])
      : await call('recognize', state.mode, state.crop, $('enhance').checked);
    await setResult(result.text);
    return '识别完成 · ' + result.seconds + ' 秒';
  });
}

async function formulaPng(svg) {
  const clone = svg.cloneNode(true);
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.removeAttribute('style');
  clone.setAttribute('color', '#192a38');
  const viewBox = clone.getAttribute('viewBox').trim().split(/\s+/).map(Number);
  const ratio = viewBox[2] / viewBox[3];
  const width = Math.min(2400, Math.max(80, Math.round(ratio * 170)));
  const height = Math.min(1400, Math.max(22, Math.round(width / ratio)));
  clone.setAttribute('width', width);
  clone.setAttribute('height', height);
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], {type: 'image/svg+xml;charset=utf-8'}));
  try {
    const pic = new Image();
    pic.src = url;
    await pic.decode();
    const canvas = document.createElement('canvas');
    canvas.width = width + 44;
    canvas.height = height + 44;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(pic, 22, 22);
    return canvas.toDataURL('image/png').split(',')[1];
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function mathML() {
  const value = await MathJax.tex2mmlPromise(state.math, {display: true});
  return value.trim();
}

function closeMenu() {
  $('saveMenu').hidden = true;
  $('export').setAttribute('aria-expanded', 'false');
}

async function sample() {
  return run('正在载入示例…', async () => {
    await MathJax.startup.promise;
    const tex = String.raw`\int_{0}^{\infty}e^{-x^{2}}\,dx=\frac{\sqrt{\pi}}{2}`;
    const node = await MathJax.tex2svgPromise(tex, {display: true});
    const png = await formulaPng(node.querySelector('svg'));
    resultMode('formula');
    showSource(await call('image_data', png, '示例 · 高斯积分.png'));
    return '示例已导入，点击「开始识别」';
  });
}

$('sample').onclick = sample;
$('choose').onclick = $('change').onclick = choose;
$('paste').onclick = paste;
$('imageMode').onclick = () => inputMode('image');
$('drawMode').onclick = () => { inputMode('draw'); resultMode('formula'); };
$('undo').onclick = () => { state.strokes.pop(); drawPad(); };
$('clearPad').onclick = () => { state.strokes = []; drawPad(); };
$('prev').onclick = () => goToPage(state.source.page - 1);
$('next').onclick = () => goToPage(state.source.page + 1);
$('pageNum').onchange = () => goToPage(Number($('pageNum').value));
$('pageNum').onkeydown = (event) => { if (event.key === 'Enter') $('pageNum').blur(); };
$('zoom').onclick = showZoom;
$('closeZoom').onclick = $('doneZoom').onclick = () => $('cropDialog').close();
$('clearCrop').onclick = () => { state.crop = null; redrawCrop(); };
$('formulaMode').onclick = () => resultMode('formula');
$('docMode').onclick = () => { inputMode('image'); resultMode('document'); };
$('recognize').onclick = recognize;
editor.oninput = () => { state.result[state.mode] = editor.value; render(); };
$('copy').onclick = () => run('正在复制…', async () => {
  await call('copy_text', editor.value.trim());
  return '已复制到剪贴板';
});
$('mathml').onclick = () => run('正在复制…', async () => {
  await call('copy_text', await mathML(), true);
  return '已复制 MathML';
});
$('png').onclick = () => run('正在生成 PNG…', async () => {
  await call('copy_png', await formulaPng(rendered.querySelector('svg')));
  return '已复制 PNG';
});
$('export').onclick = () => {
  $('saveMenu').hidden = !$('saveMenu').hidden;
  $('export').setAttribute('aria-expanded', String(!$('saveMenu').hidden));
};
$('saveMenu').onclick = (event) => {
  const kind = event.target.dataset.save;
  if (!kind || event.target.disabled) return;
  closeMenu();
  run('正在另存为…', async () => {
    let payload = editor.value.trim();
    if (kind === 'mathml') payload = await mathML();
    if (kind === 'png') payload = await formulaPng(rendered.querySelector('svg'));
    return await call('save', kind, payload) ? '文件已保存' : '就绪';
  });
};

function carriesFiles(event) {
  return Array.from(event.dataTransfer.types).includes('Files');
}
document.addEventListener('dragover', (event) => {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = state.busy ? 'none' : 'copy';
  if (!state.busy) $('inputView').classList.add('dragging');
});
document.addEventListener('dragleave', (event) => {
  if (!event.relatedTarget) $('inputView').classList.remove('dragging');
});
document.addEventListener('drop', (event) => {
  event.preventDefault();
  $('inputView').classList.remove('dragging');
  const file = event.dataTransfer.files[0];
  if (file) upload(file);
});
document.addEventListener('paste', (event) => {
  const file = Array.from(event.clipboardData.files).find((item) => item.type.startsWith('image/'));
  if (!file) return;
  event.preventDefault();
  upload(file);
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.save-wrap')) closeMenu();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeMenu();
  if (event.ctrlKey && event.key === 'Enter' && !$('cropDialog').open) {
    event.preventDefault();
    recognize();
  }
});

enableCrop($('imageBox'));
drawPad();
function ready() {
  state.ready = true;
  updateControls();
}
window.addEventListener('pywebviewready', ready);
if (window.pywebview?.api?.choose) ready();
