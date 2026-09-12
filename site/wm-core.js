// 워터마크 핵심 수학. 파이썬 구현(dwtdctsvd.py)과 같은 결과를 내야 한다.
// 언어가 달라도 서로 읽을 수 있어야 하므로, 난수 라이브러리에 기대는 부분은 없다.

export const BLOCK = 4;

// ── 4x4 DCT 행렬 (OpenCV cv2.dct 와 동일한 정규직교 DCT-II) ──────────────
function dctMatrix(n = BLOCK) {
  const m = new Float64Array(n * n);
  for (let k = 0; k < n; k++) {
    const c = k === 0 ? Math.sqrt(1 / n) : Math.sqrt(2 / n);
    for (let i = 0; i < n; i++) m[k * n + i] = c * Math.cos((Math.PI * (2 * i + 1) * k) / (2 * n));
  }
  return m;
}
export const D = dctMatrix();

/** 4x4 행렬 곱 (평탄 배열 16개) */
export function mul4(a, b, out = new Float64Array(16)) {
  for (let i = 0; i < 4; i++)
    for (let j = 0; j < 4; j++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[i * 4 + k] * b[k * 4 + j];
      out[i * 4 + j] = s;
    }
  return out;
}
export function transpose4(a, out = new Float64Array(16)) {
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) out[i * 4 + j] = a[j * 4 + i];
  return out;
}
const DT = transpose4(D);
export const dct4 = (x) => mul4(mul4(D, x), DT);
export const idct4 = (x) => mul4(mul4(DT, x), D);

// ── 최대 특이삼중항 ─────────────────────────────────────────────────
// 삽입은 s0 만 바꾸고 나머지는 그대로 두므로 전체 SVD 가 필요 없다.
//   A' = A + (s0' - s0) * u0 ⊗ v0
// 파이썬 쪽 전체 SVD 재구성과 오차 1e-11 수준으로 일치함을 확인했다.
// A^T A 의 최대 고유벡터를 거듭제곱법으로 구한다. 이미지 블록은 DC 성분이
// 지배적이라 빠르게 수렴하지만, 안전하게 충분히 반복한다.
export function topSingular(A) {
  const AT = transpose4(A);
  const M = mul4(AT, A);                       // 4x4 대칭
  let v = new Float64Array([1, 1, 1, 1]);
  for (let it = 0; it < 200; it++) {
    const w = new Float64Array(4);
    for (let i = 0; i < 4; i++) {
      let s = 0;
      for (let j = 0; j < 4; j++) s += M[i * 4 + j] * v[j];
      w[i] = s;
    }
    let n = Math.hypot(w[0], w[1], w[2], w[3]);
    if (n < 1e-300) return { s0: 0, u0: new Float64Array(4), v0: new Float64Array(4) };
    for (let i = 0; i < 4; i++) w[i] /= n;
    let diff = 0;
    for (let i = 0; i < 4; i++) diff += Math.abs(w[i] - v[i]);
    v = w;
    if (diff < 1e-15) break;
  }
  const Av = new Float64Array(4);
  for (let i = 0; i < 4; i++) {
    let s = 0;
    for (let j = 0; j < 4; j++) s += A[i * 4 + j] * v[j];
    Av[i] = s;
  }
  const s0 = Math.hypot(Av[0], Av[1], Av[2], Av[3]);
  const u0 = new Float64Array(4);
  if (s0 > 1e-300) for (let i = 0; i < 4; i++) u0[i] = Av[i] / s0;
  return { s0, u0, v0: v };
}

// ── Haar DWT (pywt 의 정규직교 haar 와 동일) ────────────────────────────
// 1D: cA[k] = (x[2k] + x[2k+1]) / √2 · 2D 는 분리 적용 → 상수 v 는 레벨마다 2배.
// 크기를 2^level 배수로 잘라 쓰므로 패딩이 개입하지 않는다.
export function dwt2(plane, w, h) {
  const hw = w >> 1, hh = h >> 1, r = Math.SQRT1_2;
  const cA = new Float64Array(hw * hh), cH = new Float64Array(hw * hh),
        cV = new Float64Array(hw * hh), cD = new Float64Array(hw * hh);
  for (let i = 0; i < hh; i++)
    for (let j = 0; j < hw; j++) {
      const a = plane[2 * i * w + 2 * j], b = plane[2 * i * w + 2 * j + 1],
            c = plane[(2 * i + 1) * w + 2 * j], d = plane[(2 * i + 1) * w + 2 * j + 1];
      const k = i * hw + j;
      cA[k] = (a + b + c + d) * r * r;
      cH[k] = (a + b - c - d) * r * r;   // 행 방향 저역, 열 방향 고역
      cV[k] = (a - b + c - d) * r * r;
      cD[k] = (a - b - c + d) * r * r;
    }
  return { cA, cH, cV, cD, w: hw, h: hh };
}
export function idwt2(cA, cH, cV, cD, hw, hh) {
  const w = hw * 2, h = hh * 2, r = Math.SQRT1_2;
  const out = new Float64Array(w * h);
  for (let i = 0; i < hh; i++)
    for (let j = 0; j < hw; j++) {
      const k = i * hw + j, A = cA[k], H = cH[k], V = cV[k], Dd = cD[k];
      out[2 * i * w + 2 * j]           = (A + H + V + Dd) * r * r;
      out[2 * i * w + 2 * j + 1]       = (A + H - V - Dd) * r * r;
      out[(2 * i + 1) * w + 2 * j]     = (A - H + V - Dd) * r * r;
      out[(2 * i + 1) * w + 2 * j + 1] = (A - H - V + Dd) * r * r;
    }
  return { out, w, h };
}

/** level 단계까지 분해. 각 단계의 디테일을 보관해 되돌릴 수 있게 한다. */
export function wavedec2(plane, w, h, level) {
  const details = [];
  let cur = plane, cw = w, ch = h;
  for (let l = 0; l < level; l++) {
    const r = dwt2(cur, cw, ch);
    details.push({ cH: r.cH, cV: r.cV, cD: r.cD, w: r.w, h: r.h });
    cur = r.cA; cw = r.w; ch = r.h;
  }
  return { cA: cur, w: cw, h: ch, details };
}
export function waverec2(cA, details) {
  let cur = cA;
  for (let l = details.length - 1; l >= 0; l--) {
    const d = details[l];
    cur = idwt2(cur, d.cH, d.cV, d.cD, d.w, d.h).out;
  }
  return cur;
}
