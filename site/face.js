// 사진에서 얼굴을 찾아 되돌릴 수 없게 지운다. 사진은 이 탭 밖으로 나가지 않는다.
//
// 되돌릴 수 없게 만드는 것은 알고리즘이 아니라 남기는 정보량이다. 자세한 근거는
// watermark.py 의 같은 주석에 있다. 아래 상수는 watermark.py 와 같은 값이어야 한다 —
// 한쪽만 고치지 말 것.

// 모자이크 모드는 없다 — 재식별 실험에서 계획한 설정으로 200장 중 원본을
// 100% 맞혔다(우연은 0.5%). 어떤 설정으로도 평탄 채우기보다 나은 조합이 없었다.
// solid 가 유일한 모드다. mode 필드는 남겨 둔다 — 나중에 생성 얼굴 합성 모드가
// 여기 추가된다. 모르는 값은 조용히 solid 로 취급한다.
export const MASK_SOLID = 'solid';

const SOLID_LEVELS = 16, SOLID_NOISE = 6;
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
function contour(points, chin) {
  const jaw = points.slice(0, 17);
  const brow = points.slice(17, 27);
  const fore = brow.map((p) => ({ x: p.x + (p.x - chin.x) * 0.45,
                                  y: p.y + (p.y - chin.y) * 0.45 }));
  return hull([...jaw, ...fore]);
}

// Andrew monotone chain. 라이브러리를 더 들이지 않는다.
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
      face.poly = contour(pos, pos[8]).map((p) => [p.x / c.width, p.y / c.height]);
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

// f.mode 는 지금은 항상 solid 다 — 모르는 값(미래의 생성 얼굴 합성 모드 등)도
// 조용히 solid 로 취급한다. 아무것도 안 칠하는 실패를 만들지 않는다.
export function maskFaces(ctx, w, h, faces) {
  if (!faces || !faces.length) return;
  const image = ctx.getImageData(0, 0, w, h);
  for (const f of faces) {
    const r = regionMask(w, h, f);
    if (!r) continue;
    fillSolid(image.data, w, r.inside, r.box);
  }
  ctx.putImageData(image, 0, 0);
}
