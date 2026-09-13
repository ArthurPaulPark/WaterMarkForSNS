// 브라우저에서 도는 워터마크 앱의 동작부. 사진은 이 탭 밖으로 나가지 않는다.
import * as W from './wm.js';
import { maskFaces } from './face.js';

export const PLATFORMS = {
  instagram: { label: 'Instagram', maxW: 1080, maxH: 1350, note: '피드 최대 1080px', q: 0.8 },
  x:         { label: 'X (Twitter)', maxW: 2048, maxH: 2048, note: '대형 이미지 2048px', q: 0.85 },
  youtube:   { label: 'YouTube 썸네일', maxW: 1280, maxH: 720, note: '1280x720 이내', q: 0.85 },
  original:  { label: '원본 크기 유지', maxW: 0, maxH: 0, note: '리사이즈 없음', q: 0.9 },
};
const JPEG_Q = 0.95, MIN_SIDE = 256;
export const DMI_PROHIBIT_AI = 'http://ns.useplus.org/ldf/vocab/DMI-PROHIBITED-AIMLTRAINING';
export const PHASH_SAME = 10, PHASH_EDITED = 16, MAX_FP = 1e-4;

// ── 이미지 입출력 ───────────────────────────────────────────────────
export async function loadImage(file) {
  const bmp = await createImageBitmap(file);
  return bmp;
}
export function fitCanvas(bmp, maxW, maxH) {
  let { width: w, height: h } = bmp;
  if (maxW) {
    const s = Math.min(maxW / w, maxH / h, 1);
    if (s < 1) { w = Math.max(1, Math.round(w * s)); h = Math.max(1, Math.round(h * s)); }
  }
  const c = new OffscreenCanvas(w, h);
  const cx = c.getContext('2d', { willReadFrequently: true });
  cx.imageSmoothingQuality = 'high';
  cx.drawImage(bmp, 0, 0, w, h);
  return { canvas: c, ctx: cx, w, h };
}
export const getPixels = (ctx, w, h) => ctx.getImageData(0, 0, w, h);
export const toJpeg = (canvas, q = JPEG_Q) => canvas.convertToBlob({ type: 'image/jpeg', quality: q });

// ── 오탐 확률 ──────────────────────────────────────────────────────
const logFact = (() => { const t = [0]; for (let i = 1; i <= 64; i++) t.push(t[i - 1] + Math.log(i)); return t; })();
const comb = (n, k) => Math.exp(logFact[n] - logFact[k] - logFact[n - k]);
export function falsePositive(matched, total, trials) {
  let tail = 0;
  for (let k = matched; k <= total; k++) tail += comb(total, k);
  const p = Math.min(1, 2 * tail / Math.pow(2, total));
  return 1 - Math.pow(1 - p, trials);
}

// ── 키 (WebCrypto Ed25519) ─────────────────────────────────────────
const KEY_DB = 'watermark', KEY_STORE = 'keys';
// 연결을 하나만 열어 재사용한다. 호출마다 새로 열고 닫지 않으면 연결이 쌓이고,
// 그 열린 연결들이 다른 탭의 업그레이드를 막아 onblocked 를 일으킨다
// (탭 두 개를 여는 것은 정상적인 사용인데 도장 만들기가 멈추는 원인이었다).
let dbPromise = null;
function db() {
  if (!dbPromise) {
    dbPromise = idb().then((d) => {
      // 다른 탭이 버전을 올리려 하면 붙잡고 있지 말고 비켜준다.
      d.onversionchange = () => { d.close(); dbPromise = null; };
      d.onclose = () => { dbPromise = null; };
      return d;
    }).catch((e) => { dbPromise = null; throw e; });
  }
  return dbPromise;
}

// indexedDB.open 은 성공도 실패도 오지 않는 상태가 있다 — 다른 탭이 붙잡고 있으면
// onblocked 만 오고 끝이다. 그대로 두면 await 가 영원히 매달려, 화면은 멈춘 채
// 아무 오류도 뜨지 않는다. 기한을 두고 onblocked 도 명시적으로 실패로 처리한다.
function idb(timeout = 5000) {
  return new Promise((res, rej) => {
    let done = false;
    const finish = (fn, v) => { if (!done) { done = true; clearTimeout(t); fn(v); } };
    const t = setTimeout(
      () => finish(rej, new Error('브라우저 저장소가 응답하지 않습니다 (다른 탭에서 열려 있을 수 있습니다)')),
      timeout);
    let r;
    try { r = indexedDB.open(KEY_DB, 1); }
    catch (e) { return finish(rej, e); }
    r.onupgradeneeded = () => r.result.createObjectStore(KEY_STORE);
    r.onsuccess = () => finish(res, r.result);
    r.onerror = () => finish(rej, r.error || new Error('브라우저 저장소를 열 수 없습니다'));
    r.onblocked = () => finish(rej, new Error('다른 탭에서 이 사이트가 열려 있습니다. 그 탭을 닫고 새로고침하세요'));
  });
}
async function idbGet(k) {
  const d = await db();
  return new Promise((res, rej) => {
    const t = d.transaction(KEY_STORE).objectStore(KEY_STORE).get(k);
    t.onsuccess = () => res(t.result); t.onerror = () => rej(t.error);
  });
}
async function idbPut(k, v) {
  const d = await db();
  return new Promise((res, rej) => {
    const t = d.transaction(KEY_STORE, 'readwrite').objectStore(KEY_STORE).put(v, k);
    t.onsuccess = () => res(); t.onerror = () => rej(t.error);
  });
}
async function idbDel(k) {
  const d = await db();
  return new Promise((res, rej) => {
    const t = d.transaction(KEY_STORE, 'readwrite').objectStore(KEY_STORE).delete(k);
    t.onsuccess = () => res(); t.onerror = () => rej(t.error);
  });
}

export const ed25519Supported = async () => (await capabilities()).ed25519;

/** 이 브라우저가 무엇을 할 수 있는지. 안 될 때 원인을 정확히 알려주기 위한 것. */
export async function capabilities() {
  const out = { secure: self.isSecureContext, ed25519: false, idb: false, ua: navigator.userAgent };
  if (!self.crypto?.subtle) { out.err = 'crypto.subtle 없음 (HTTPS 가 아닐 수 있습니다)'; return out; }
  try { await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']); out.ed25519 = true; }
  catch (e) { out.ed25519Err = e.name || String(e); }
  try { await db(); out.idb = true; }
  catch (e) { out.idbErr = e?.message || e?.name || String(e); }
  return out;
}

/** 새 신원을 만든다. 개인키는 이 브라우저에만 저장된다. */
export async function createKey() {
  const cap = await capabilities();
  if (!cap.secure) throw new Error('보안 연결(HTTPS)에서만 도장을 만들 수 있습니다');
  if (!cap.ed25519) throw new Error(
    '이 브라우저는 Ed25519 서명을 지원하지 않습니다 — Chrome 137+, Safari 17+, Firefox 129+ 가 필요합니다'
    + (cap.ed25519Err ? ` (${cap.ed25519Err})` : ''));
  // 원인이 서로 다르다 — 다른 탭이 붙잡은 것과 시크릿 모드는 해결 방법이 다르다.
  if (!cap.idb) throw new Error('도장을 저장할 수 없습니다 — ' + (cap.idbErr
    || '시크릿/프라이빗 모드이거나 사이트 데이터가 차단된 상태일 수 있습니다'));
  if (await idbGet('priv')) throw new Error('이미 도장이 있습니다');
  const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
  const raw = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey('pkcs8', kp.privateKey));
  await idbPut('priv', pkcs8); await idbPut('pub', W.bytesToHex(raw));
  return W.bytesToHex(raw);
}
export async function loadKey() {
  const pkcs8 = await idbGet('priv'), pub = await idbGet('pub');
  if (!pkcs8) return null;
  const priv = await crypto.subtle.importKey('pkcs8', pkcs8, { name: 'Ed25519' }, true, ['sign']);
  return { priv, pub, pkcs8 };
}
export const deleteKey = async () => { await idbDel('priv'); await idbDel('pub'); };
export async function importKey(pkcs8) {
  const priv = await crypto.subtle.importKey('pkcs8', pkcs8, { name: 'Ed25519' }, true, ['sign']);
  // 개인키에서 공개키를 되뽑기 위해 JWK 를 거친다
  const jwk = await crypto.subtle.exportKey('jwk', priv);
  const pubKey = await crypto.subtle.importKey('jwk', { kty: jwk.kty, crv: jwk.crv, x: jwk.x },
                                               { name: 'Ed25519' }, true, ['verify']);
  const raw = new Uint8Array(await crypto.subtle.exportKey('raw', pubKey));
  await idbPut('priv', new Uint8Array(pkcs8)); await idbPut('pub', W.bytesToHex(raw));
  return W.bytesToHex(raw);
}

/** 파이썬 json.dumps(sort_keys=True, separators=(",",":")) 와 바이트 단위로 같게.
 *
 * 파이썬은 기본이 ensure_ascii=True 라 비ASCII 를 \uXXXX 로 escape 한다.
 * JS 의 JSON.stringify 는 원문을 그대로 내보내므로, 그대로 두면 서명 대상 바이트가
 * 달라져 한쪽에서 만든 서명을 다른 쪽이 검증하지 못한다. 서로게이트 쌍도 파이썬처럼
 * UTF-16 코드 단위별로 escape 한다. */
const escapeNonAscii = (s) =>
  s.replace(/[\u0080-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
export function canonical(obj) {
  const keys = Object.keys(obj).sort();
  const parts = keys.map((k) => escapeNonAscii(JSON.stringify(k)) + ':' + escapeNonAscii(JSON.stringify(obj[k])));
  return new TextEncoder().encode('{' + parts.join(',') + '}');
}
export const sha256Hex = async (bytes) =>
  W.bytesToHex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));

// ── XMP 학습 거부 선언 (JPEG APP1) ──────────────────────────────────
const XMP_NS = new TextEncoder().encode('http://ns.adobe.com/xap/1.0/\0');
const xmpPacket = (v) => new TextEncoder().encode(
  `<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>` +
  `<x:xmpmeta xmlns:x="adobe:ns:meta/">` +
  `<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">` +
  `<rdf:Description rdf:about="" xmlns:plus="http://ns.useplus.org/ldf/xmp/1.0/">` +
  `<plus:DataMining>${v}</plus:DataMining>` +
  `</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>`);

export function addDeclaration(jpeg, value = DMI_PROHIBIT_AI) {
  const packet = xmpPacket(value), len = XMP_NS.length + packet.length + 2;
  const out = new Uint8Array(jpeg.length + 4 + XMP_NS.length + packet.length);
  out.set(jpeg.subarray(0, 2), 0);
  out.set([0xff, 0xe1, len >> 8, len & 255], 2);
  out.set(XMP_NS, 6); out.set(packet, 6 + XMP_NS.length);
  out.set(jpeg.subarray(2), 6 + XMP_NS.length + packet.length);
  return out;
}
export function readDeclaration(jpeg) {
  let i = 2;
  while (i + 4 <= jpeg.length && jpeg[i] === 0xff) {
    const m = jpeg[i + 1];
    if (m === 0xd8 || m === 0xd9 || (m >= 0xd0 && m <= 0xd7)) { i += 2; continue; }
    if (m === 0xda) break;
    const size = (jpeg[i + 2] << 8) | jpeg[i + 3];
    if (m === 0xe1) {
      const body = jpeg.subarray(i + 4, i + 2 + size);
      const txt = new TextDecoder().decode(body);
      const hit = txt.match(/<plus:DataMining>([^<]*)<\/plus:DataMining>/);
      if (hit) return hit[1];
    }
    i += 2 + size;
  }
  return null;
}

// ── 보호 ───────────────────────────────────────────────────────────
export async function protect(file, platform, { message = '', noAi = true, keyless = false,
                                                faces = [] } = {}) {
  const spec = PLATFORMS[platform];
  const bmp = await loadImage(file);
  const { canvas, ctx, w, h } = fitCanvas(bmp, spec.maxW, spec.maxH);
  if (Math.min(w, h) < MIN_SIDE) throw new Error(`이미지가 너무 작습니다 (짧은 변 최소 ${MIN_SIDE}px)`);

  // 가리기는 워터마크보다 먼저다. 순서가 반대면 가리기가 그 영역의 워터마크를 부순다.
  // 지각 해시도 이 뒤라야 발행본과 맞고, 가려지지 않은 원본의 지문이 남지 않는다.
  if (faces.length) maskFaces(ctx, w, h, faces);

  const key = keyless ? null : await loadKey();
  if (!key && !keyless) throw new Error('먼저 도장을 만들어 주세요');
  if (!key && !message) throw new Error('도장 없이 심으려면 문장이 필요합니다');

  const img = getPixels(ctx, w, h);
  const { Y, U, V } = W.rgbaToYuv(img.data, w, h);
  const before = W.perceptualHash(Y, w, h);
  const origBytes = new Uint8Array(await file.arrayBuffer());

  if (key) W.embedTag(Y, w, h, await W.authorTag(key.pub), await W.authorCells(key.pub));
  const capacity = W.messageCapacity(w, h);
  if (message) {
    if (new TextEncoder().encode(message).length > capacity)
      throw new Error(`문장이 너무 깁니다 — 이 크기에는 ${capacity}바이트까지`);
    W.embedMessage(Y, w, h, W.frameMessage(message, capacity));
  }
  W.yuvToRgba(Y, U, V, w, h, img.data);
  ctx.putImageData(img, 0, 0);

  let jpeg = new Uint8Array(await (await toJpeg(canvas)).arrayBuffer());
  if (noAi) jpeg = addDeclaration(jpeg);

  const psnr = await measurePsnr(bmp, spec, jpeg, faces);
  let sidecar = null;
  if (key) {
    const claim = {
      v: 1, alg: 'dwtDctSvd+ed25519', bits: W.NBITS, pub: key.pub, platform,
      size: [w, h], message, data_mining: noAi ? DMI_PROHIBIT_AI : null,
      phash: before,
      // 얼굴을 가렸으면 원본 파일 해시를 넣지 않는다. 원본을 가진 사람이 발행본과의
      // 연결을 증명할 수 있어, 초상권을 지키려고 가린 사진의 공개 증명서에 남길 이유가 없다.
      sha256_original: faces.length ? null : await sha256Hex(origBytes),
      sha256_protected: await sha256Hex(jpeg),
      faces_masked: faces.length,
      created: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    };
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, key.priv, canonical(claim)));
    sidecar = { claim, sig: W.bytesToHex(sig) };
  }
  return { jpeg, sidecar, size: [w, h], capacity, psnr, message, noAi, keyless: !key };
}

async function measurePsnr(bmp, spec, jpeg, faces = []) {
  const a = fitCanvas(bmp, spec.maxW, spec.maxH);
  // 가리기가 아니라 워터마크가 준 손상을 재는 값이다. 기준도 가린 뒤여야 한다.
  if (faces.length) maskFaces(a.ctx, a.w, a.h, faces);
  const pa = getPixels(a.ctx, a.w, a.h).data;
  const bmp2 = await createImageBitmap(new Blob([jpeg], { type: 'image/jpeg' }));
  const b = fitCanvas(bmp2, 0, 0);
  const pb = getPixels(b.ctx, b.w, b.h).data;
  let se = 0, n = 0;
  for (let i = 0; i < pa.length; i += 4)
    for (let k = 0; k < 3; k++) { const d = pa[i + k] - pb[i + k]; se += d * d; n++; }
  return Math.round(10 * Math.log10(255 * 255 / (se / n)) * 10) / 10;
}

// ── 검증 ───────────────────────────────────────────────────────────
export async function verify(file, pubHex, sidecar) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const bmp = await loadImage(file);
  const ref = sidecar?.claim?.size;
  const out = { declaration: readDeclaration(bytes) };

  const read = (maxW, maxH) => {
    const { ctx, w, h } = fitCanvas(bmp, maxW, maxH);
    const { Y } = W.rgbaToYuv(getPixels(ctx, w, h).data, w, h);
    return { Y, w, h };
  };
  const views = [read(0, 0)];
  if (ref && (bmp.width !== ref[0] || bmp.height !== ref[1]))
    views.push(scaleTo(bmp, ref[0], ref[1]));

  if (pubHex) {
    const target = await W.authorTag(pubHex), cells = await W.authorCells(pubHex);
    let best = 0, inverted = false, regained = null, trials = 0;
    const consider = (bits, gain) => {
      let m = 0; for (let i = 0; i < target.length; i++) if (bits[i] === target[i]) m++;
      trials++;
      const acc = Math.max(m, target.length - m) / target.length;
      if (acc > best) { best = acc; inverted = m < target.length / 2; regained = gain; }
    };
    for (const v of views) consider(W.decodeTag(v.Y, v.w, v.h, cells), null);
    let fp = falsePositive(Math.round(best * W.NBITS), W.NBITS, trials);
    if (fp > MAX_FP) {                       // 밝기/대비 되돌리기
      for (const v of views)
        for (const a of [0.8, 0.85, 0.9, 1.1, 1.15, 1.25, 1.4])
          for (const b of [-24, -12, 0, 12, 24]) {
            const g = regain(v.Y, a, b);
            consider(W.decodeTag(g, v.w, v.h, cells), [a, b]);
          }
      fp = falsePositive(Math.round(best * W.NBITS), W.NBITS, trials);
    }
    out.watermark = {
      matched_bits: Math.round(best * W.NBITS), total_bits: W.NBITS,
      inverted, regained: !!regained, false_positive: fp, match: fp <= MAX_FP, trials,
    };
    out.checked_pub = pubHex;
  }

  for (const v of views) {
    const cap = W.messageCapacity(v.w, v.h);
    if (cap <= 0) continue;
    const nbits = (cap + W.MSG_HEADER + W.MSG_CRC) * 8;
    const bits = W.decodeMessage(v.Y, v.w, v.h, nbits);
    const msg = W.unframeMessage(bits) ?? W.unframeMessage(bits.map((b) => b ^ 1));
    if (msg !== null) { out.message = msg; break; }
  }
  if (sidecar) out.sidecar = await checkSidecar(bytes, views[0], sidecar);
  return out;
}

const regain = (Y, a, b) => Float64Array.from(Y, (v) => Math.min(255, Math.max(0, Math.round((v - b) / a))));
function scaleTo(bmp, w, h) {
  const c = new OffscreenCanvas(w, h), cx = c.getContext('2d', { willReadFrequently: true });
  cx.imageSmoothingQuality = 'high'; cx.drawImage(bmp, 0, 0, w, h);
  const { Y } = W.rgbaToYuv(cx.getImageData(0, 0, w, h).data, w, h);
  return { Y, w, h };
}

async function checkSidecar(bytes, view, sidecar) {
  const { claim, sig } = sidecar;
  let valid = false;
  try {
    const pub = await crypto.subtle.importKey('raw', W.hexToBytes(claim.pub),
                                              { name: 'Ed25519' }, true, ['verify']);
    valid = await crypto.subtle.verify({ name: 'Ed25519' }, pub, W.hexToBytes(sig), canonical(claim));
  } catch { valid = false; }
  const digest = await sha256Hex(bytes);
  const out = {
    signature_valid: valid,
    is_protected_file: digest === claim.sha256_protected,
    is_original_file: digest === claim.sha256_original,
    claim,
  };
  if (claim.phash) {
    const d = W.phashDistance(claim.phash, W.perceptualHash(view.Y, view.w, view.h));
    out.phash_distance = d;
    out.phash_verdict = d <= PHASH_SAME ? 'same' : d <= PHASH_EDITED ? 'edited' : 'different';
  }
  return out;
}
