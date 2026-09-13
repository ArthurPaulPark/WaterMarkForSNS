// 워터마크 삽입·추출. 파이썬 watermark.py 와 같은 형식을 쓴다.
import { BLOCK, dct4, idct4, topSingular, wavedec2, waverec2 } from './wm-core.js';

export const NBITS = 48, LEVEL = 2, SCALE = 180, TILE = [12, 16];
export const MSG_LEVEL = 1, MSG_SCALE = 24, MSG_SAMPLES = 16, MSG_HEADER = 2, MSG_CRC = 4;
// 지각 마스킹. dwtdctsvd.py 의 MASK_K / MASK_FLOOR 와 같은 값이어야 한다 —
// 다르면 브라우저에서 보호한 사진과 데스크톱에서 보호한 사진이 서로 달라진다.
// MASK_K=0.5 인 이유는 dwtdctsvd.py 의 주석 참고: 0.35~0.65 스윕 결과가 동일했고
// 0.85 는 실제 사진 실패를 1/11→4/11 로 늘렸다 (진짜 무늬까지 마스킹했기 때문).
export const MASK_K = 0.5, MASK_FLOOR = 0.35, MASK_READ = 0.10;
const NCELL = TILE[0] * TILE[1];

const sha256 = async (bytes) =>
  new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
const cat = (...a) => {
  const n = a.reduce((s, x) => s + x.length, 0), o = new Uint8Array(n);
  let p = 0; for (const x of a) { o.set(x, p); p += x.length; } return o;
};
export const hexToBytes = (h) =>
  Uint8Array.from(h.match(/../g).map((b) => parseInt(b, 16)));
export const bytesToHex = (b) =>
  [...b].map((x) => x.toString(16).padStart(2, '0')).join('');

/** 공개키 → 48비트 태그 (SHA256 앞 6바이트) */
export async function authorTag(pubHex) {
  const d = await sha256(hexToBytes(pubHex));
  const bits = new Uint8Array(NBITS);
  for (let i = 0; i < NBITS; i++) bits[i] = (d[i >> 3] >> (7 - (i & 7))) & 1;
  return bits;
}

/** 공개키 → 192칸 중 48칸. 해시 순위로만 정해 난수 라이브러리에 기대지 않는다. */
export async function authorCells(pubHex) {
  const seed = await sha256(cat(hexToBytes(pubHex), new TextEncoder().encode('cells')));
  const ranked = [];
  for (let i = 0; i < NCELL; i++) {
    const k = await sha256(cat(seed, new Uint8Array([i >> 8, i & 255])));
    ranked.push({ i, k: k.slice(0, 8) });
  }
  const cmp = (a, b) => { for (let x = 0; x < 8; x++) if (a.k[x] !== b.k[x]) return a.k[x] - b.k[x]; return 0; };
  ranked.sort(cmp);
  return Int32Array.from(ranked.slice(0, NBITS).map((r) => r.i).sort((a, b) => a - b));
}

// ── 블록 격자 ──────────────────────────────────────────────────────
/** 블록마다 어느 비트를 싣는지. -1 이면 건드리지 않는다. */
function bitIndexMap(gh, gw, cells) {
  const [ph, pw] = TILE;
  const lookup = new Int32Array(NCELL).fill(-1);
  for (let b = 0; b < cells.length; b++) lookup[cells[b]] = b;
  const idx = new Int32Array(gh * gw);
  for (let i = 0; i < gh; i++)
    for (let j = 0; j < gw; j++) idx[i * gw + j] = lookup[(i % ph) * pw + (j % pw)];
  return idx;
}
const getBlock = (ca, cw, bi, bj, out = new Float64Array(16)) => {
  for (let i = 0; i < BLOCK; i++)
    for (let j = 0; j < BLOCK; j++) out[i * 4 + j] = ca[(bi * BLOCK + i) * cw + bj * BLOCK + j];
  return out;
};
const putBlock = (ca, cw, bi, bj, blk) => {
  for (let i = 0; i < BLOCK; i++)
    for (let j = 0; j < BLOCK; j++) ca[(bi * BLOCK + i) * cw + bj * BLOCK + j] = blk[i * 4 + j];
};

/** 블록의 AC 노름. DC(인덱스 0)를 뺀 에너지의 제곱근 = 활동도 척도. */
const acNorm = (d) => {
  let s = 0;
  for (let i = 1; i < 16; i++) s += d[i] * d[i];
  return Math.sqrt(s);
};
/** numpy.quantile 의 기본(선형 보간)과 같은 값. 두 구현의 기준선을 맞추기 위해서다. */
function quantile(sorted, q) {
  const pos = q * (sorted.length - 1), lo = Math.floor(pos), f = pos - lo;
  return sorted[lo] + (sorted[Math.min(lo + 1, sorted.length - 1)] - sorted[lo]) * f;
}
/** 마스킹 기준선. 파이썬 dwtdctsvd._bar 와 같아야 한다 (act 는 오름차순 정렬됨). */
function maskBar(act, scale, trip) {
  const hard = MASK_K * scale;
  if (!act.length) return hard;
  let above = 0;
  for (let i = act.length - 1; i >= 0 && act[i] >= hard; i--) above++;
  return above / act.length >= trip ? hard : quantile(act, 1 - MASK_FLOOR);
}

/** 근사계수에 비트를 심는다 (양자화). idxMap 이 -1 인 블록은 손대지 않는다.
 *
 * 평탄한 블록도 건너뛴다 — 숨겨줄 무늬가 없어 격자로 보이기 때문이다.
 * 파이썬 dwtdctsvd.DwtDctSvd._mask 와 같은 계산이어야 한다 (이유는 그쪽 주석에).
 */
function embedInto(ca, cw, ch, bits, idxMap, scale) {
  const gh = (ch / BLOCK) | 0, gw = (cw / BLOCK) | 0;
  const blk = new Float64Array(16);
  // 1차: 후보 블록의 활동도를 모아 기준선을 정한다. 절대 기준만 쓰면 평평한 사진이
  // 통째로 비어버리므로, 활동도 상위 MASK_FLOOR 비율은 기준에 못 미쳐도 심는다.
  const act = [];
  for (let bi = 0; bi < gh; bi++)
    for (let bj = 0; bj < gw; bj++)
      if (idxMap[bi * gw + bj] >= 0) act.push(acNorm(dct4(getBlock(ca, cw, bi, bj, blk))));
  if (!act.length) return;
  act.sort((a, b) => a - b);
  const bar = maskBar(act, scale, MASK_FLOOR);

  for (let bi = 0; bi < gh; bi++)
    for (let bj = 0; bj < gw; bj++) {
      const want = idxMap[bi * gw + bj];
      if (want < 0) continue;
      getBlock(ca, cw, bi, bj, blk);
      const d = dct4(blk);
      if (acNorm(d) < bar) continue;
      const { s0, u0, v0 } = topSingular(d);
      // 같은 비트를 뜻하는 격자점은 scale 마다 하나씩 있다. 가장 가까운 것을 고르면
      // 이동량이 반으로 줄지만 판독은 s0 % scale 만 보므로 여유는 그대로다.
      const off = 0.25 + 0.5 * bits[want];
      const target = (Math.round(s0 / scale - off) + off) * scale;
      const delta = target - s0;
      const d2 = new Float64Array(16);
      for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++)
        d2[i * 4 + j] = d[i * 4 + j] + delta * u0[i] * v0[j];
      putBlock(ca, cw, bi, bj, idct4(d2));
    }
}

/** 근사계수에서 비트별 평균 점수를 읽는다.
 *
 * 삽입 때 건너뛴 평탄한 블록은 여기서도 뺀다 — 섞으면 표의 여유가 삽입 비율만큼
 * 줄어 공격을 못 견딘다. 판단은 받은 이미지에서 다시 계산하므로 부가 정보가 아니다.
 * 파이썬 dwtdctsvd 의 _read 와 같아야 한다 (기준선 모집단도 '모든 블록'으로 같다).
 */
function readVotes(ca, cw, ch, idxMap, scale, n) {
  const gh = (ch / BLOCK) | 0, gw = (cw / BLOCK) | 0;
  const tot = new Float64Array(n), cnt = new Float64Array(n), blk = new Float64Array(16);
  const act = [];
  for (let bi = 0; bi < gh; bi++)
    for (let bj = 0; bj < gw; bj++) act.push(acNorm(dct4(getBlock(ca, cw, bi, bj, blk))));
  act.sort((a, b) => a - b);
  const bar = maskBar(act, scale, MASK_READ);
  for (let bi = 0; bi < gh; bi++)
    for (let bj = 0; bj < gw; bj++) {
      const k = idxMap[bi * gw + bj];
      if (k < 0) continue;
      getBlock(ca, cw, bi, bj, blk);
      const d = dct4(blk);
      if (acNorm(d) < bar) continue;
      const { s0 } = topSingular(d);
      tot[k] += ((s0 % scale) > scale * 0.5) ? 1 : 0;
      cnt[k] += 1;
    }
  // 표본이 없는 비트는 0.5 (중립).
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) out[i] = cnt[i] ? tot[i] / cnt[i] : 0.5;
  return out;
}

const cropTo = (n, unit) => Math.floor(n / unit) * unit;

/** 루마 평면에 작성자 태그를 심는다. y 는 Float64Array (w*h). 제자리 수정. */
export function embedTag(y, w, h, bits, cells) {
  const unit = 1 << LEVEL, cw0 = cropTo(w, unit), ch0 = cropTo(h, unit);
  const sub = subPlane(y, w, cw0, ch0);
  const { cA, w: cw, h: ch, details } = wavedec2(sub, cw0, ch0, LEVEL);
  const gh = (ch / BLOCK) | 0, gw = (cw / BLOCK) | 0;
  embedInto(cA, cw, ch, bits, bitIndexMap(gh, gw, cells), SCALE);
  writeBack(y, w, waverec2(cA, details), cw0, ch0);
}

/** 루마 평면에서 태그 비트를 읽는다. */
export function decodeTag(y, w, h, cells) {
  const unit = 1 << LEVEL, cw0 = cropTo(w, unit), ch0 = cropTo(h, unit);
  const { cA, w: cw, h: ch } = wavedec2(subPlane(y, w, cw0, ch0), cw0, ch0, LEVEL);
  const gh = (ch / BLOCK) | 0, gw = (cw / BLOCK) | 0;
  const v = readVotes(cA, cw, ch, bitIndexMap(gh, gw, cells), SCALE, NBITS);
  return Uint8Array.from(v, (x) => (x * 255 > 127 ? 1 : 0));
}

function subPlane(y, w, cw, ch) {
  if (cw === w) return y.slice(0, cw * ch);
  const o = new Float64Array(cw * ch);
  for (let i = 0; i < ch; i++) o.set(y.subarray(i * w, i * w + cw), i * cw);
  return o;
}
function writeBack(y, w, plane, cw, ch) {
  for (let i = 0; i < ch; i++)
    for (let j = 0; j < cw; j++) y[i * w + j] = Math.min(255, Math.max(0, Math.round(plane[i * cw + j])));
}

// ── 색 변환 (OpenCV BGR<->YUV 와 같은 계수. 실측 최대차 1, 0.1% 픽셀) ────────
export function rgbaToYuv(data, w, h) {
  const n = w * h, Y = new Float64Array(n), U = new Float64Array(n), V = new Float64Array(n);
  for (let i = 0, p = 0; i < n; i++, p += 4) {
    const r = data[p], g = data[p + 1], b = data[p + 2];
    Y[i] = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
    U[i] = Math.round(0.492 * (b - Y[i]) + 128);
    V[i] = Math.round(0.877 * (r - Y[i]) + 128);
  }
  return { Y, U, V };
}
export function yuvToRgba(Y, U, V, w, h, out) {
  const n = w * h, c = (x) => Math.min(255, Math.max(0, Math.round(x)));
  for (let i = 0, p = 0; i < n; i++, p += 4) {
    const y = Y[i], u = U[i] - 128, v = V[i] - 128;
    out[p]     = c(y + 1.140 * v);
    out[p + 1] = c(y - 0.395 * u - 0.581 * v);
    out[p + 2] = c(y + 2.032 * u);
    out[p + 3] = 255;
  }
  return out;
}

// ── CRC32 (zlib.crc32 과 동일) ─────────────────────────────────────
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
export function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 255] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

// ── 문장 층 ────────────────────────────────────────────────────────
export const messageCapacity = (w, h) =>
  Math.max(0, Math.floor(Math.floor(((h / 8) | 0) * ((w / 8) | 0) / MSG_SAMPLES) / 8) - MSG_HEADER - MSG_CRC);

export function frameMessage(text, capacity) {
  const data = new TextEncoder().encode(text);
  if (data.length > capacity) throw new Error(`문장이 너무 깁니다 (${data.length} > ${capacity}바이트)`);
  const body = new Uint8Array(MSG_HEADER + capacity);
  body[0] = data.length >> 8; body[1] = data.length & 255;
  body.set(data, MSG_HEADER);
  const crc = crc32(body);
  const frame = new Uint8Array(body.length + MSG_CRC);
  frame.set(body);
  frame.set([crc >>> 24 & 255, crc >>> 16 & 255, crc >>> 8 & 255, crc & 255], body.length);
  const bits = new Uint8Array(frame.length * 8);
  for (let i = 0; i < bits.length; i++) bits[i] = (frame[i >> 3] >> (7 - (i & 7))) & 1;
  return bits;
}
export function unframeMessage(bits) {
  const bytes = new Uint8Array(bits.length / 8);
  for (let i = 0; i < bits.length; i++) if (bits[i]) bytes[i >> 3] |= 1 << (7 - (i & 7));
  const body = bytes.subarray(0, bytes.length - MSG_CRC), crc = bytes.subarray(bytes.length - MSG_CRC);
  const want = crc32(body);
  if (crc[0] !== (want >>> 24 & 255) || crc[1] !== (want >>> 16 & 255) ||
      crc[2] !== (want >>> 8 & 255) || crc[3] !== (want & 255)) return null;
  const len = (body[0] << 8) | body[1];
  if (len > body.length - MSG_HEADER) return null;
  try { return new TextDecoder('utf-8', { fatal: true }).decode(body.subarray(MSG_HEADER, MSG_HEADER + len)); }
  catch { return null; }
}

const rasterMap = (gh, gw, n) => {
  const idx = new Int32Array(gh * gw);
  for (let i = 0; i < gh * gw; i++) idx[i] = i % n;
  return idx;
};

export function embedMessage(y, w, h, bits) {
  const unit = 1 << MSG_LEVEL, cw0 = cropTo(w, unit), ch0 = cropTo(h, unit);
  const { cA, w: cw, h: ch, details } = wavedec2(subPlane(y, w, cw0, ch0), cw0, ch0, MSG_LEVEL);
  const gh = (ch / BLOCK) | 0, gw = (cw / BLOCK) | 0;
  embedInto(cA, cw, ch, bits, rasterMap(gh, gw, bits.length), MSG_SCALE);
  writeBack(y, w, waverec2(cA, details), cw0, ch0);
}
export function decodeMessage(y, w, h, nbits) {
  const unit = 1 << MSG_LEVEL, cw0 = cropTo(w, unit), ch0 = cropTo(h, unit);
  const { cA, w: cw, h: ch } = wavedec2(subPlane(y, w, cw0, ch0), cw0, ch0, MSG_LEVEL);
  const gh = (ch / BLOCK) | 0, gw = (cw / BLOCK) | 0;
  const v = readVotes(cA, cw, ch, rasterMap(gh, gw, nbits), MSG_SCALE, nbits);
  return Uint8Array.from(v, (x) => (x * 255 > 127 ? 1 : 0));
}

// ── 지각 해시 (사진의 '모양'을 63비트로) ─────────────────────────────
export function perceptualHash(Y, w, h) {
  // OpenCV INTER_AREA 와 같은 면적 가중 평균. 경계 픽셀은 겹치는 만큼만 센다
  // (정수 경계로 자르면 지각해시가 3~9비트 어긋나 파이썬과 호환되지 않았다).
  const N = 32, small = new Float64Array(N * N);
  const span = (len, k) => {
    const a = (k * len) / N, b = ((k + 1) * len) / N, out = [];
    for (let p = Math.floor(a); p < Math.min(len, Math.ceil(b)); p++)
      out.push([p, Math.min(b, p + 1) - Math.max(a, p)]);
    return out;
  };
  const rows = [], cols = [];
  for (let k = 0; k < N; k++) { rows.push(span(h, k)); cols.push(span(w, k)); }
  for (let i = 0; i < N; i++)
    for (let j = 0; j < N; j++) {
      let s = 0, tw = 0;
      for (const [yy, wy] of rows[i]) for (const [xx, wx] of cols[j]) {
        s += Y[yy * w + xx] * wy * wx; tw += wy * wx;
      }
      // OpenCV 는 uint8 입력에 uint8 을 돌려준다 = 정수로 반올림된다.
      // 반올림을 빼면 파이썬과 평균 8비트나 어긋난다.
      // OpenCV 내부는 정수 고정소수점이라 동점 처리까지 똑같이 맞출 수는 없었다.
      // 실측 결과 이 방식이 최악 2비트 차이로 가장 가깝다 — 지각해시는 거리 10 이하를
      // '같은 사진'으로 보므로 이 정도 오차는 판정에 영향을 주지 않는다.
      small[i * N + j] = Math.round(s / tw);
    }
  // 32x32 DCT-II 후 좌상단 8x8 (DC 제외)
  const cos = [];
  for (let k = 0; k < N; k++) {
    const row = new Float64Array(N), c = k === 0 ? Math.sqrt(1 / N) : Math.sqrt(2 / N);
    for (let i = 0; i < N; i++) row[i] = c * Math.cos((Math.PI * (2 * i + 1) * k) / (2 * N));
    cos.push(row);
  }
  const tmp = new Float64Array(N * N), out = new Float64Array(N * N);
  for (let k = 0; k < N; k++) for (let j = 0; j < N; j++) {
    let s = 0; for (let i = 0; i < N; i++) s += cos[k][i] * small[i * N + j]; tmp[k * N + j] = s;
  }
  for (let i = 0; i < N; i++) for (let k = 0; k < N; k++) {
    let s = 0; for (let j = 0; j < N; j++) s += tmp[i * N + j] * cos[k][j]; out[i * N + k] = s;
  }
  const v = [];
  for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++) if (i || j) v.push(out[i * N + j]);
  // 63개(홀수)의 중앙값은 가운데 하나다. 두 개를 평균내면 파이썬과 어긋난다.
  const med = [...v].sort((a, b) => a - b);
  const m = med[(med.length - 1) >> 1];
  let hex = 0n;
  for (const x of v) hex = (hex << 1n) | (x > m ? 1n : 0n);
  return hex.toString(16).padStart(16, '0');
}
export const phashDistance = (a, b) => {
  let x = BigInt('0x' + a) ^ BigInt('0x' + b), n = 0;
  while (x) { n += Number(x & 1n); x >>= 1n; }
  return n;
};
