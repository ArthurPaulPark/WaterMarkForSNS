// 찾은 얼굴을 보여주고 사용자가 확인·수정하게 한다.
//
// 탐지는 옆얼굴과 작은 얼굴을 놓치고 오탐도 낸다. 초상권에서 놓침은 실제 피해라
// 자동 결과를 최종으로 쓰지 않는다 — 기본 전부 체크, 수동 추가·삭제가 1급 기능이다.
//
// 가리기 방식은 기본이 solid(유일하게 지키는 방식)이고, 모자이크는 그 모양을
// 원하는 사용자를 위한 선택지다 — 보호 수단이 아니다(재식별 측정: face.js,
// watermark.py 의 같은 주석 참고). 그래서 체크박스는 기본 꺼짐(solid)이고,
// 라벨에 보호되지 않는다는 사실을 그대로 적는다.
import * as F from './face.js';

const el = (tag, css, text) => {
  const n = document.createElement(tag);
  if (css) n.style.cssText = css;
  if (text != null) n.textContent = text;
  return n;
};

export function createFacePanel(host) {
  let faces = [], bmp = null, drawing = null;

  const wrap = el('div', 'display:none');
  const head = el('div', 'display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:10px');
  const title = el('b', '', '얼굴 가리기');
  const status = el('span', 'color:var(--dim);font-size:12.5px');
  const mosaicLabel = el('label',
    'display:flex;align-items:center;gap:5px;font-size:12.5px;color:var(--dim);cursor:pointer');
  const mosaicBox = document.createElement('input');
  mosaicBox.type = 'checkbox';
  mosaicLabel.append(mosaicBox, document.createTextNode('모자이크 — 모양만 가립니다. 복원될 수 있습니다'));
  head.append(title, status, mosaicLabel, el('div', 'flex:1'));

  // stage 는 사진에 딱 맞게 줄어들어야 한다. 블록으로 두면 컨테이너 전체 폭이 되는데,
  // view 는 max-width 라 컨테이너보다 좁은 사진에서는 늘어나지 않는다. 그러면
  // over(=stage 의 100%)가 사진보다 넓어져 박스가 가로로 늘어나며 밀린다 —
  // 세로 사진을 넓은 창에서 열면 204px 어긋났다. inline-block 이면 둘이 항상 같다.
  const stage = el('div', 'position:relative;display:inline-block;max-width:100%;line-height:0;user-select:none;touch-action:none');
  const view = el('canvas', 'max-width:100%;height:auto;border-radius:8px;cursor:crosshair');
  const over = el('canvas', 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none');
  stage.append(view, over);

  const list = el('div', 'display:flex;gap:8px;flex-wrap:wrap;margin-top:11px');
  const hint = el('p', 'color:var(--dim);font-size:12.5px;margin:9px 0 0');
  hint.innerHTML = '놓친 얼굴은 <b>사진 위를 끌어서</b> 직접 칠하세요. '
                 + '체크한 얼굴은 되돌릴 수 없게 지워집니다.';
  const oops = el('p', 'color:var(--bad);font-size:12.5px;margin:9px 0 0;display:none');

  wrap.append(head, stage, list, hint, oops);
  host.append(wrap);

  function paint() {
    const cx = over.getContext('2d');
    cx.clearRect(0, 0, over.width, over.height);
    cx.lineWidth = Math.max(2, over.width / 300);
    cx.font = `${Math.max(13, over.width / 40)}px sans-serif`;
    faces.forEach((f, i) => {
      const on = f.on !== false;
      cx.strokeStyle = on ? '#2f6df6' : '#9aa3ae';
      cx.setLineDash(on ? [] : [6, 5]);
      const x = f.x * over.width, y = f.y * over.height;
      const w = f.w * over.width, h = f.h * over.height;
      cx.strokeRect(x, y, w, h);
      cx.fillStyle = on ? '#2f6df6' : '#9aa3ae';
      cx.fillRect(x, y - 20, 24, 20);
      cx.fillStyle = '#fff';
      cx.fillText(String(i + 1), x + 6, y - 5);
    });
    if (drawing) {
      cx.setLineDash([4, 4]); cx.strokeStyle = '#c22b2b';
      cx.strokeRect(drawing.x * over.width, drawing.y * over.height,
                    drawing.w * over.width, drawing.h * over.height);
    }
  }

  function chips() {
    list.textContent = '';
    faces.forEach((f, i) => {
      const on = f.on !== false;
      const b = el('button', 'display:flex;align-items:center;gap:7px;padding:6px 10px;'
        + 'border-radius:7px;font-size:12.5px;cursor:pointer;border:1px solid var(--line);'
        + `background:${on ? 'var(--accent)' : 'transparent'};color:${on ? '#fff' : 'var(--fg)'}`);
      b.type = 'button';
      b.textContent = `${i + 1}. ${on ? '가림' : '그대로'}${f.manual ? ' (직접)' : ''}`;
      b.onclick = () => { f.on = !on; paint(); chips(); tally(); };
      const x = el('span', 'opacity:.7;font-weight:700', '×');
      x.title = '이 얼굴 지우기';
      x.onclick = (e) => { e.stopPropagation(); faces.splice(i, 1); paint(); chips(); tally(); };
      b.append(x);
      list.append(b);
    });
  }

  function tally() {
    const n = faces.filter((f) => f.on !== false).length;
    status.textContent = faces.length
      ? `${faces.length}명 찾음 · ${n}명 가림`
      : '얼굴을 못 찾았습니다 — 가릴 곳이 있으면 직접 끌어서 칠하세요';
  }

  // 사진 위를 끌면 박스가 생긴다. 사용자가 그린 박스는 넓히지 않는다 — 의도 그대로.
  const at = (e) => {
    const r = view.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
  };
  view.onpointerdown = (e) => {
    view.setPointerCapture(e.pointerId);
    const p = at(e);
    drawing = { x: p.x, y: p.y, w: 0, h: 0, ox: p.x, oy: p.y };
  };
  view.onpointermove = (e) => {
    if (!drawing) return;
    const p = at(e);
    drawing.x = Math.min(p.x, drawing.ox); drawing.y = Math.min(p.y, drawing.oy);
    drawing.w = Math.abs(p.x - drawing.ox); drawing.h = Math.abs(p.y - drawing.oy);
    paint();
  };
  view.onpointerup = () => {
    if (!drawing) return;
    const d = drawing; drawing = null;
    if (d.w > 0.01 && d.h > 0.01)
      faces.push({ x: d.x, y: d.y, w: d.w, h: d.h, grow: F.GROW_MANUAL, manual: true, on: true });
    paint(); chips(); tally();
  };

  return {
    async show(bitmap) {
      bmp = bitmap;
      wrap.style.display = '';
      oops.style.display = 'none';
      faces = [];
      // 미리보기는 640px 이면 충분하다. 큰 사진을 통째로 그리지 않는다.
      const s = Math.min(640 / bitmap.width, 640 / bitmap.height, 1);
      view.width = over.width = Math.max(1, Math.round(bitmap.width * s));
      view.height = over.height = Math.max(1, Math.round(bitmap.height * s));
      const cx = view.getContext('2d');
      cx.imageSmoothingQuality = 'high';
      cx.drawImage(bitmap, 0, 0, view.width, view.height);
      status.textContent = '얼굴을 찾는 중…';
      chips(); paint();
      try {
        faces = (await F.detectFaces(bitmap)).map((f) => ({ ...f, on: true }));
      } catch (e) {
        // 모델을 못 불러와도 기능이 죽지는 않는다. 수동 도구가 남는다.
        oops.style.display = '';
        oops.textContent = '얼굴 자동 찾기를 쓸 수 없습니다 ('
          + (e?.message || e) + ') — 직접 끌어서 칠할 수는 있습니다.';
      }
      paint(); chips(); tally();
    },
    selected() {
      const mode = mosaicBox.checked ? F.MASK_MOSAIC : F.MASK_SOLID;
      return faces.filter((f) => f.on !== false).map((f) => ({
        x: f.x, y: f.y, w: f.w, h: f.h,
        ...(f.poly ? { poly: f.poly } : {}),
        ...(f.manual ? { manual: true } : {}),
        grow: f.grow ?? F.GROW_MANUAL,
        mode,
      }));
    },
    count() {
      return { found: faces.length, checked: faces.filter((f) => f.on !== false).length };
    },
    clear() { faces = []; bmp = null; wrap.style.display = 'none'; paint(); chips(); },
  };
}
