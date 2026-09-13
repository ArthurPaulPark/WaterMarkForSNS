// 사진에서 얼굴을 찾아 되돌릴 수 없게 지운다. 사진은 이 탭 밖으로 나가지 않는다.
//
// 되돌릴 수 없게 만드는 것은 알고리즘이 아니라 남기는 정보량이다. 자세한 근거는
// watermark.py 의 같은 주석에 있다. 아래 상수는 watermark.py 와 같은 값이어야 한다 —
// 한쪽만 고치지 말 것.

// 기본은 solid 다 — 출력이 양자화된 색 세 개를 통해서만 원본에 의존하므로
// 복원할 것이 남지 않는다.
//
// mosaic 은 그 모양을 원하는 사용자를 위한 선택지다 — 보호 수단이 아니다.
// 지터는 블록 평균 위에 잡음을 더할 뿐, 평균 자체(원본이 준 정보)는 그대로
// 남는다. 재식별 측정(후보 200장 중 진짜 원본 하나 맞히기, 우연은 0.5%):
// 블록4/지터12 = 100.0%, 블록4/지터24 = 100.0%, 블록3/지터12 = 100.0% — 이
// 설정들에서 공격자는 사실상 매번 정확한 원본을 되찾는다. 실제로 지키는 것은
// solid(재식별 2.0%)뿐이다. 자세한 근거와 전체 측정표는 watermark.py 의 같은
// 주석과 docs/superpowers/specs/2026-09-13-face-masking-design.md 에 있다.
// 아래 상수는 watermark.py 와 같은 값이어야 한다 — 한쪽만 고치지 말 것.
export const MASK_SOLID = 'solid';
export const MASK_MOSAIC = 'mosaic';

const SOLID_LEVELS = 16, SOLID_NOISE = 6;
const MOSAIC_BLOCKS = 4, MOSAIC_LEVELS = 16, MOSAIC_JITTER = 12;
export const GROW_POLY = 1.08, GROW_BOX = 1.25, GROW_MANUAL = 1.0;
const DETECT_SIDE = 640;

// 모델 경로는 이 모듈 파일 기준이어야 한다. 문서 URL 기준(상대경로 그대로)이면
// 데스크톱 앱처럼 이 파일이 /lib/face.js 로 서빙되고 문서는 / 인 경우 깨진다.
const MODELS = new URL('./vendor/models', import.meta.url).href;

// ── 탐지 ───────────────────────────────────────────────────────────
let ready = null;
let faceapi = null;

export function loadDetector() {
  if (!ready) {
    ready = (async () => {
      // face-api 안에 딸려온 long.js 가 로드되는 순간 무조건 wasm 으로 64비트 곱셈을
      // 가속해 보려 한다. 기능과 무관한 최적화인데(자체 try/catch 로 순수 JS 폴백),
      // script-src 'self' 아래서는 CSP 위반이 된다. import 가 끝나기 전에 일어나므로
      // import 를 감싸서 막는다. 이유를 모르고 지우면 위반이 되살아난다.
      const realWasmModule = WebAssembly.Module;
      WebAssembly.Module = function () { throw new Error('wasm Long 가속 비활성화'); };
      let mod;
      try {
        mod = await import('./vendor/face-api.esm.js');
      } finally {
        WebAssembly.Module = realWasmModule;
      }
      faceapi = mod;
      // wasm 백엔드도 등록에서 지운다 — WebGL 이 없는 환경에서 tfjs 가 wasm 을
      // 골라버리면 WebAssembly.instantiate/validate 를 통해 위반이 되살아난다.
      delete faceapi.tf.engine().registryFactory.wasm;

      await faceapi.nets.tinyFaceDetector.loadFromUri(MODELS);
      await faceapi.nets.faceLandmark68TinyNet.loadFromUri(MODELS);
    })().catch((e) => { ready = null; throw e; });   // 실패는 캐시하지 않는다
  }
  return ready;
}

function scratch(bitmap, side) {
  const s = Math.min(side / bitmap.width, side / bitmap.height, 1);
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(bitmap.width * s));
  c.height = Math.max(1, Math.round(bitmap.height * s));
  const cx = c.getContext('2d', { willReadFrequently: true });
  cx.imageSmoothingQuality = 'high';
  cx.drawImage(bitmap, 0, 0, c.width, c.height);
  return c;
}

// 68점 중 이마는 없다. 눈썹을 턱 반대쪽으로 밀어 이마선을 추정한다.
// 68점으로 얼굴에 맞는 기울어진 타원을 만든다.
// 이전에는 눈썹을 턱 반대 방향으로 밀어 이마를 추정하고 볼록껍질을 씌웠는데,
// 그 밀기가 위쪽뿐 아니라 옆으로도 작용해 이마선이 45% 넓어졌고, 껍질이 뿔처럼
// 솟아 머리 위 배경까지 지웠다. 얼굴은 다각형보다 타원에 가깝다.
const mean = (ps) => ({ x: ps.reduce((a, p) => a + p.x, 0) / ps.length,
                        y: ps.reduce((a, p) => a + p.y, 0) / ps.length });

function contour(pos) {
  const jaw = pos.slice(0, 17);                       // 턱선: 귀 앞 → 턱끝 → 귀 앞
  const chin = pos[8];
  const eyes = mean([mean(pos.slice(36, 42)), mean(pos.slice(42, 48))]);

  // 턱끝 → 눈 중앙이 얼굴의 위쪽. 고개가 기울면 이 축도 같이 기운다.
  let ux = eyes.x - chin.x, uy = eyes.y - chin.y;
  const len = Math.hypot(ux, uy) || 1;
  ux /= len; uy /= len;
  const px = -uy, py = ux;                            // 얼굴의 가로 방향

  // 이마는 랜드마크에 없다. 턱끝~눈 거리의 0.5배 위가 대략 머리카락 경계다.
  const top = { x: eyes.x + ux * len * 0.5, y: eyes.y + uy * len * 0.5 };
  const c = { x: (chin.x + top.x) / 2, y: (chin.y + top.y) / 2 };
  let semiV = Math.hypot(top.x - chin.x, top.y - chin.y) / 2;

  // 가로 반지름은 턱선이 세로축에서 가장 멀리 벗어난 거리. 얼굴보다 넓어지지 않는다.
  let semiH = 0;
  for (const q of jaw) semiH = Math.max(semiH, Math.abs((q.x - c.x) * px + (q.y - c.y) * py));

  // 턱선 17점이 전부 타원 안에 들어올 때까지 균일하게 키운다. 얼굴이 새는 것은
  // 배경을 조금 더 먹는 것보다 나쁘다 — 새면 가릴 이유가 없어진다.
  let k = 1;
  for (const q of jaw) {
    const a = ((q.x - c.x) * px + (q.y - c.y) * py) / semiH;
    const b = ((q.x - c.x) * ux + (q.y - c.y) * uy) / semiV;
    k = Math.max(k, Math.hypot(a, b));
  }
  semiH *= k; semiV *= k;

  const out = [];
  for (let i = 0; i < 24; i++) {
    const t = (i / 24) * Math.PI * 2;
    const a = Math.cos(t) * semiH, b = Math.sin(t) * semiV;
    out.push({ x: c.x + a * px + b * ux, y: c.y + a * py + b * uy });
  }
  return out;
}

function hull(pts) {
  const p = [...pts].sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const half = (src) => {
    const out = [];
    for (const q of src) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], q) <= 0) out.pop();
      out.push(q);
    }
    out.pop();
    return out;
  };
  return [...half(p), ...half([...p].reverse())];
}

export async function detectFaces(bitmap) {
  await loadDetector();
  const c = scratch(bitmap, DETECT_SIDE);
  const found = await faceapi
    .detectAllFaces(c, new faceapi.TinyFaceDetectorOptions({ inputSize: 416, scoreThreshold: 0.4 }))
    .withFaceLandmarks(true);

  return found.map((r) => {
    const b = r.detection.box;
    const face = {
      x: b.x / c.width, y: b.y / c.height,
      w: b.width / c.width, h: b.height / c.height,
      score: r.detection.score,
      mode: MASK_SOLID,
      grow: GROW_BOX,
    };
    const pos = r.landmarks?.positions;
    if (pos && pos.length === 68) {
      face.poly = contour(pos).map((p) => [p.x / c.width, p.y / c.height]);
      face.grow = GROW_POLY;      // 윤곽이 있으면 훨씬 적게 넓혀도 얼굴을 덮는다
    }
    return face;
  });
}

// ── 가리기 ─────────────────────────────────────────────────────────
// crypto.getRandomValues 는 한 번에 65536바이트까지만 채워 준다 — 큰 얼굴 영역은
// 이를 넘으므로 나눠서 채운다.
function randBytes(n) {
  const a = new Uint8Array(n);
  for (let o = 0; o < n; o += 65536) crypto.getRandomValues(a.subarray(o, o + 65536));
  return a;
}

// 폴리곤 좌표가 형식에 맞는지 검사한다. 안 맞으면 상자 타원으로 물러난다 —
// 초상권에서는 놓치는 쪽(칠하지 않고 건너뛰기)이 잘못 넓게 칠하는 쪽보다 훨씬 나쁘다.
function validPoly(poly) {
  if (!Array.isArray(poly) || poly.length < 3) return null;
  for (const p of poly) {
    if (!Array.isArray(p) || p.length !== 2 || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) return null;
  }
  return poly;
}

// 캔버스가 폴리곤과 타원을 직접 칠해 준다. 직접 래스터라이즈하지 않는다.
// 다만 안티에일리어싱이 경계를 번지게 하므로 알파 128 에서 자른다 — 페더링 금지.
//
// watermark.py 의 _mask_region 과 계약이 같다: 상자(x/y/w/h)를 먼저 보고, 넓이가
// 0이면(ax<0.5 또는 ay<0.5) 그때만 정말 가릴 게 없다고 보고 null 을 돌려준다.
// 상자에 넓이가 있으면 poly 를 시도하되, poly 가 없거나 망가졌거나 상자 타원
// 넓이의 5% 도 못 채우면(뭉개진 좌표 등) 조용히 상자 타원으로 되돌아간다 —
// 얼굴을 건너뛰는 실패는 이 기능에서 제일 나쁜 결과다.
function regionMask(w, h, f) {
  if (f.x == null || f.y == null || f.w == null || f.h == null) return null;  // 상자 정보조차 없다
  const grow = f.grow ?? 1.0;
  const cxp = (f.x + f.w / 2) * w, cyp = (f.y + f.h / 2) * h;
  const ax = f.w * w * grow / 2, ay = f.h * h * grow / 2;
  if (ax < 0.5 || ay < 0.5) return null;   // 상자 자체에 넓이가 없다 — 이때만 정말 가릴 게 없다
  const boxArea = Math.PI * ax * ay;

  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const cx = c.getContext('2d', { willReadFrequently: true });
  cx.fillStyle = '#fff';

  let painted = false;
  const poly = validPoly(f.poly);
  if (poly) {
    const mx = poly.reduce((s, p) => s + p[0], 0) / poly.length;
    const my = poly.reduce((s, p) => s + p[1], 0) / poly.length;
    cx.beginPath();
    poly.forEach(([px, py], i) => {
      const X = (mx + (px - mx) * grow) * w, Y = (my + (py - my) * grow) * h;
      i ? cx.lineTo(X, Y) : cx.moveTo(X, Y);
    });
    cx.closePath();
    cx.fill();

    // 그려진 면적이 상자 타원보다 턱없이 작으면(좌표가 뒤틀린 경우 등) 폴리곤을 못 믿는다 —
    // 얼굴을 건너뛰지 말고 상자 타원으로 다시 칠한다.
    const check = cx.getImageData(0, 0, w, h).data;
    let cnt = 0;
    for (let p = 3; p < check.length; p += 4) if (check[p] >= 128) cnt++;
    painted = cnt >= boxArea * 0.05;
  }

  if (!painted) {
    cx.clearRect(0, 0, w, h);
    cx.beginPath();
    cx.ellipse(cxp, cyp, ax, ay, 0, 0, Math.PI * 2);
    cx.fill();
  }

  const a = cx.getImageData(0, 0, w, h).data;
  const inside = new Uint8Array(w * h);
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let i = 0, p = 0; i < inside.length; i++, p += 4) {
    if (a[p + 3] >= 128) {
      inside[i] = 1;
      const X = i % w, Y = (i / w) | 0;
      if (X < x0) x0 = X; if (X > x1) x1 = X;
      if (Y < y0) y0 = Y; if (Y > y1) y1 = Y;
    }
  }
  // x1 < 0 이면 상자가 캔버스와 겹치지 않는 경우다 — 이때도 정말 가릴 게 없다
  return x1 < 0 ? null : { inside, box: [x0, y0, x1 + 1, y1 + 1] };
}

function fillSolid(img, w, inside, box) {
  const [x0, y0, x1, y1] = box;
  const chans = [[], [], []];
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      if (!inside[y * w + x]) continue;
      const p = (y * w + x) * 4;
      chans[0].push(img[p]); chans[1].push(img[p + 1]); chans[2].push(img[p + 2]);
    }
  if (!chans[0].length) return;
  const step = 256 / SOLID_LEVELS;
  // 평균이 아니라 중앙값 — 배경이 조금 섞여도 색이 끌려가지 않는다.
  // numpy.median 과 같게: 개수가 짝수면 가운데 두 값의 평균을 쓴다 — 하나만
  // 집으면 워터마크.py 와 양자화 경계에서 갈릴 수 있다.
  const base = chans.map((v) => {
    v.sort((a, b) => a - b);
    const n = v.length;
    const m = n % 2 ? v[n >> 1] : (v[(n >> 1) - 1] + v[n >> 1]) / 2;
    return Math.min(255, Math.floor(m / step) * step + step / 2);
  });
  const noise = randBytes((x1 - x0) * (y1 - y0) * 3);
  let n = 0;
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const i = y * w + x;
      if (!inside[i]) { n += 3; continue; }
      const p = i * 4;
      for (let k = 0; k < 3; k++) {
        const j = (noise[n++] % (2 * SOLID_NOISE + 1)) - SOLID_NOISE;
        img[p + k] = Math.max(0, Math.min(255, Math.round(base[k] + j)));
      }
    }
}

// 굵은 블록으로 나눠 블록 평균으로 채우고, 블록마다 잡음을 더한다. 보호 수단이
// 아니다 — 위쪽 MASK_MOSAIC 주석과 측정 참고.
//
// 블록 경계와 평균 계산 방식이 watermark.py 의 _fill_mosaic 과 정확히 같아야
// 한다 — 브라우저의 이미지 리사이즈나 cv2.resize 처럼 알고리즘이 문서화돼 있지
// 않은 함수에 맡기면 두 언어가 갈릴 수 있다(실측: 무작위 패치에서 블록의 절반
// 가까이가 양자화 구간 하나만큼 갈렸다). 그래서 블록 경계를 직접 계산하고,
// 그 안의 픽셀을 합/개수로 평균낸다 — 정수 픽셀이라 부동소수 오차 없이 파이썬과
// 정확히 같은 값이 나온다. 평균은 상자 전체(마스크 밖 포함)로 내는 것도 파이썬과
// 같다 — 칠할 때만 inside 로 걸러낸다.
function fillMosaic(img, w, inside, box) {
  const [x0, y0, x1, y1] = box;
  const pw = x1 - x0, ph = y1 - y0;
  const b = Math.max(8, Math.round(pw / MOSAIC_BLOCKS));
  const sw = Math.max(1, Math.ceil(pw / b)), sh = Math.max(1, Math.ceil(ph / b));
  const step = 256 / MOSAIC_LEVELS;
  const noise = randBytes(sw * sh * 3);
  const val = new Uint8Array(sw * sh * 3);

  for (let sy = 0; sy < sh; sy++) {
    const by0 = y0 + sy * b, by1 = Math.min(y1, by0 + b);
    for (let sx = 0; sx < sw; sx++) {
      const bx0 = x0 + sx * b, bx1 = Math.min(x1, bx0 + b);
      const sum = [0, 0, 0];
      let count = 0;
      for (let y = by0; y < by1; y++)
        for (let x = bx0; x < bx1; x++) {
          const p = (y * w + x) * 4;
          sum[0] += img[p]; sum[1] += img[p + 1]; sum[2] += img[p + 2];
          count++;
        }
      const s = sy * sw + sx;
      for (let k = 0; k < 3; k++) {
        const mean = count ? sum[k] / count : 128;
        const q = Math.floor(mean / step) * step + step / 2;
        // 지터는 양자화 폭과 맞먹는다 — 그래도 블록 값이 곧 원본 평균이라는
        // 사실은 바뀌지 않는다(재식별을 막지 못한다. 위 주석 참고).
        const j = (noise[s * 3 + k] % (2 * MOSAIC_JITTER + 1)) - MOSAIC_JITTER;
        val[s * 3 + k] = Math.max(0, Math.min(255, Math.round(q + j)));
      }
    }
  }
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const i = y * w + x;
      if (!inside[i]) continue;
      const sy = Math.min(sh - 1, ((y - y0) / b) | 0);
      const sx = Math.min(sw - 1, ((x - x0) / b) | 0);
      const s = sy * sw + sx;
      const p = i * 4;
      for (let k = 0; k < 3; k++) img[p + k] = val[s * 3 + k];
    }
}

// f.mode 가 MASK_MOSAIC 이면 모자이크로, 그 외(기본 포함)에는 solid 로 채운다.
// 모르는 값은 조용히 solid 로 취급한다. 아무것도 안 칠하는 실패를 만들지 않는다.
export function maskFaces(ctx, w, h, faces) {
  if (!faces || !faces.length) return;
  const image = ctx.getImageData(0, 0, w, h);
  for (const f of faces) {
    const r = regionMask(w, h, f);
    if (!r) continue;
    if (f.mode === MASK_MOSAIC) fillMosaic(image.data, w, r.inside, r.box);
    else fillSolid(image.data, w, r.inside, r.box);
  }
  ctx.putImageData(image, 0, 0);
}
