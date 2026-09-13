import * as A from './app-core.js';
import * as W from './wm.js';
import { createFacePanel } from './face-ui.js';

const $ = (s) => document.querySelector(s);
const show = (el, on) => el.classList.toggle('hide', !on);
const err = (el, m) => { el.textContent = m || ''; show(el, !!m); };
const esc = (s) => s.replace(/[<&]/g, (c) => ({ '<': '&lt;', '&': '&amp;' }[c]));
const fmt = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.round(b / 1024) + ' KB');

let pImg = null, vImg = null, sCar = null, CAP = null, KEY = null, ED = true;

function dropzone(zone, input, onPick) {
  zone.onclick = () => input.click();
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('over'); };
  zone.ondragleave = () => zone.classList.remove('over');
  zone.ondrop = (e) => { e.preventDefault(); zone.classList.remove('over');
    if (e.dataTransfer.files[0]) take(e.dataTransfer.files[0]); };
  input.onchange = () => input.files[0] && take(input.files[0]);
  function take(f) { zone.classList.add('has'); zone.firstChild.textContent = `${f.name} · ${fmt(f.size)}`; onPick(f); }
}
const dl = (blob, name) => {
  const u = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = u; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(u), 4000);
};
const busy = (bar, btn, on, label) => {
  show(bar, on); btn.disabled = on; if (label) btn.textContent = label;
};

document.querySelectorAll('.tab').forEach((t) => t.onclick = () => {
  document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('on', x === t));
  ['protect', 'verify', 'key'].forEach((id) => show($('#' + id), id === t.dataset.t));
});

async function showDiag() {
  const c = await A.capabilities();
  const row = (k, v, ok) => `<div class="kv"><span>${k}</span><span class="${ok ? '' : 'pill bad'}">${v}</span></div>`;
  $('#diag').innerHTML =
    row('보안 연결 (HTTPS)', c.secure ? '예' : '아니오', c.secure) +
    row('Ed25519 서명', c.ed25519 ? '지원' : '미지원' + (c.ed25519Err ? ` (${c.ed25519Err})` : ''), c.ed25519) +
    row('브라우저 저장소', c.idb ? '사용 가능' : '불가' + (c.idbErr ? ` (${c.idbErr})` : ''), c.idb) +
    `<div class="kv"><span>브라우저</span><span style="font-size:11px">${c.ua}</span></div>`;
}

async function refresh() {
  try { await refreshInner(); }
  catch (e) {
    // 초기화가 조용히 죽으면 화면은 '확인 중…' 인 채 멈추고 버튼도 안 보인다.
    // 무엇이 막혔는지 반드시 말해준다.
    $('#kdot').style.background = 'var(--bad)';
    $('#kmsg').textContent = '시작하지 못했습니다 — ' + (e?.message || e);
    show($('#setup'), false);
    try { await showDiag(); } catch {}
  }
}

async function refreshInner() {
  ED = await A.ed25519Supported();
  show($('#unsupported'), !ED);
  KEY = ED ? await A.loadKey() : null;
  $('#kdot').style.background = KEY ? 'var(--ok)' : 'var(--warn)';
  $('#kmsg').textContent = KEY ? '내 도장 준비됨' : '아직 도장이 없습니다';
  $('#kpub').textContent = KEY ? KEY.pub.slice(0, 16) + '…' : '';
  $('#pubbox').value = KEY ? KEY.pub : '아직 도장이 없습니다';
  show($('#setup'), !KEY && ED);
  if (!ED) $('#pkeyless').checked = true;
  $('#platform').innerHTML = Object.entries(A.PLATFORMS)
    .map(([k, v]) => `<option value="${k}">${v.label} — ${v.note}</option>`).join('');
  sync();
  showDiag();
}
const msgBytes = () => new TextEncoder().encode($('#pmsg').value.trim()).length;
function sync() {
  const kl = $('#pkeyless').checked || !ED;
  const tooLong = CAP !== null && msgBytes() > CAP;
  $('#pgo').disabled = !pImg || tooLong || (kl ? msgBytes() === 0 : !KEY);
  $('#vgo').disabled = !vImg;
}
function showCap() {
  const n = msgBytes();
  $('#pcap').innerHTML = CAP === null
    ? (pImg ? '길이 확인 중…' : '사진을 먼저 선택하면 넣을 수 있는 길이가 나옵니다')
    : `${n} / ${CAP} 바이트 <span style="opacity:.7">· 한글 ${Math.floor(CAP / 3)}자까지</span>`
      + (n > CAP ? ' <span class="pill bad">너무 깁니다</span>' : '');
  sync();
}
$('#pmsg').oninput = showCap;
$('#pkeyless').onchange = sync;
$('#platform').onchange = () => askCap();

async function askCap() {
  CAP = null; showCap();
  if (!pImg) return;
  try {
    const spec = A.PLATFORMS[$('#platform').value];
    const bmp = await A.loadImage(pImg);
    const { w, h } = A.fitCanvas(bmp, spec.maxW, spec.maxH);
    CAP = W.messageCapacity(w, h);
  } catch { CAP = null; }
  showCap();
}
const facePanel = createFacePanel($('#faces'));
dropzone($('#pdrop'), $('#pfile'), async (f) => {
  pImg = f; sync(); askCap();
  try { await facePanel.show(await A.loadImage(f)); show($('#facesCard'), true); }
  catch (e) { console.warn('얼굴 패널', e); }   // 패널이 죽어도 보호하기는 살아야 한다
});
dropzone($('#vdrop'), $('#vfile'), (f) => { vImg = f; sync(); });
dropzone($('#sdrop'), $('#sfile'), async (f) => { try { sCar = JSON.parse(await f.text()); } catch { sCar = null; } });

// ── 도장 만들기 ─────────────────────────────────────────────────────
$('#setupGo').onclick = async () => {
  err($('#setupErr'), '');
  const b = $('#setupGo');
  b.disabled = true; b.textContent = '만드는 중…';   // 멈춘 건지 알 수 있게
  try {
    await A.createKey(); await refresh(); show($('#setup'), false);
    $('#kmsg').textContent = '도장을 만들었습니다 — 이제 사진을 보호할 수 있습니다';
  } catch (e) { err($('#setupErr'), e?.message || String(e)); }
  b.disabled = false; b.textContent = '도장 만들기';
};
$('#setupSkip').onclick = () => { $('#pkeyless').checked = true; show($('#setup'), false); sync(); };

// ── 보호 ───────────────────────────────────────────────────────────
$('#pgo').onclick = async () => {
  err($('#perr'), ''); busy($('#pbar'), $('#pgo'), true, '처리 중…');
  try {
    const r = await A.protect(pImg, $('#platform').value, {
      message: $('#pmsg').value.trim(), noAi: $('#pnoai').checked,
      keyless: $('#pkeyless').checked || !ED,
      faces: facePanel.selected(),
    });
    const blob = new Blob([r.jpeg], { type: 'image/jpeg' });
    const url = URL.createObjectURL(blob);
    const stem = pImg.name.replace(/\.[^.]+$/, '');
    const name = `${stem}_${$('#platform').value}_protected.jpg`;
    $('#pres').innerHTML = `
      <h3>완료 — 육안으로는 구별되지 않습니다</h3>
      <img class="prev" src="${url}">
      <div class="kv"><span>해상도</span><span>${r.size[0]} × ${r.size[1]}</span></div>
      <div class="kv"><span>화질 (PSNR)</span><span>${r.psnr} dB</span></div>
      ${r.message ? `<div class="kv"><span>심어진 문장</span><span>${esc(r.message)}</span></div>` : ''}
      ${r.noAi ? `<div class="kv"><span>AI 학습 거부</span><span>선언 포함 (IPTC/PLUS)</span></div>` : ''}
      ${r.sidecar?.claim?.faces_masked
        ? `<div class="kv"><span>가린 얼굴</span><span>${r.sidecar.claim.faces_masked}명 — 되돌릴 수 없습니다</span></div>`
        : ''}
      ${r.sidecar ? '' : `<p class="note" style="color:var(--warn)"><b>도장 없이 만들었습니다.</b>
        문장은 남지만 서명이 없어 원작자 증명은 되지 않습니다.</p>`}
      <div class="step"><span class="num">1</span>
        <div><b>워터마크가 심긴 이미지</b><span class="sub">SNS에 올릴 파일</span></div>
        <button id="dimg">내려받기</button></div>
      ${r.sidecar ? `<div class="step"><span class="num">2</span>
        <div><b>사이드카 (.sig.json)</b><span class="sub">공개하지 마세요. 원작자 증명의 핵심</span></div>
        <span class="pill bad" id="scw">아직 안 받음</span><button id="dsc">내려받기</button></div>
      <div class="step"><span class="num">3</span>
        <div><b>원본 파일 보관</b><span class="sub">방금 올린 그 파일. 다시 저장하면 증명에 못 씁니다</span></div>
        <span class="pill warn">직접 보관</span></div>` : ''}`;
    show($('#pres'), true);
    $('#dimg').onclick = () => dl(blob, name);
    if (r.sidecar) $('#dsc').onclick = () => {
      dl(new Blob([JSON.stringify(r.sidecar, null, 2)], { type: 'application/json' }), name + '.sig.json');
      $('#scw').className = 'pill ok'; $('#scw').textContent = '받음';
    };
  } catch (e) { err($('#perr'), e.message); }
  busy($('#pbar'), $('#pgo'), false, '보호하기'); sync();
};

// ── 검증 ───────────────────────────────────────────────────────────
$('#vgo').onclick = async () => {
  err($('#verr'), ''); busy($('#vbar'), $('#vgo'), true, '검증 중…');
  try {
    const r = await A.verify(vImg, $('#vpub').value.trim() || sCar?.claim?.pub || '', sCar);
    let h = '';
    if (r.message) h += `<h3>사진에서 읽어낸 문장</h3>
      <p style="margin:0 0 6px;padding:11px;background:var(--bg);border:1px solid var(--line);
         border-radius:8px">${esc(r.message)}</p>
      <p class="note" style="margin-top:0">검사를 통과한 문장입니다. 한 글자라도 깨졌다면 표시되지 않습니다.</p>`;
    if (r.watermark) {
      const w = r.watermark;
      const odds = w.false_positive < 1e-9 ? '사실상 0'
        : '약 ' + Math.round(1 / w.false_positive).toLocaleString('ko-KR') + '분의 1';
      h += `<h3 style="margin-top:16px">워터마크 ${w.match ? '<span class="pill ok">일치</span>'
        : '<span class="pill bad">불일치</span>'}</h3>
        <div class="kv"><span>비트 일치</span><span>${w.matched_bits}/${w.total_bits}</span></div>
        <div class="kv"><span>우연히 맞을 확률</span><span>${odds}</span></div>
        ${w.regained ? '<div class="kv"><span>복구</span><span>밝기/대비를 되돌려 찾음</span></div>' : ''}`;
    }
    if (r.declaration) h += `<h3 style="margin-top:16px">AI 학습 거부 선언</h3>
      <p class="note" style="margin-top:0">이 파일에 거부 표시가 들어 있습니다.</p>`;
    if (r.sidecar) {
      const s = r.sidecar, strong = s.signature_valid && (s.is_protected_file || s.is_original_file);
      h += `<h3 style="margin-top:16px">사이드카 ${strong ? '<span class="pill ok">유효</span>'
        : s.signature_valid ? '<span class="pill warn">서명 유효 · 파일 불일치</span>'
        : '<span class="pill bad">서명 위조</span>'}</h3>
        <div class="kv"><span>서명</span><span>${s.signature_valid ? '진짜' : '검증 실패'}</span></div>
        <div class="kv"><span>서명 시각</span><span>${s.claim.created}</span></div>
        ${s.phash_verdict ? `<div class="kv"><span>원본과의 닮음</span><span>${
          { same: '사실상 같은 사진', edited: '편집된 같은 사진', different: '다른 사진' }[s.phash_verdict]
          } (63비트 중 ${s.phash_distance} 차이)</span></div>` : ''}
        ${s.phash_verdict && s.phash_verdict !== 'different' && r.watermark && !r.watermark.match
          ? `<p class="note" style="color:var(--warn)"><b>워터마크는 지워졌지만 서명된 원본에서 나온 사진으로 보입니다.</b>
             누군가 덮어썼을 가능성이 큽니다.</p>` : ''}`;
    }
    if (!h) h = `<h3>아무것도 찾지 못했습니다</h3><p class="note" style="margin-top:0">
      워터마크가 없거나, 크롭·재압축·색보정으로 깨졌을 수 있습니다.</p>`;
    $('#vres').innerHTML = h; show($('#vres'), true);
  } catch (e) { err($('#verr'), e.message); }
  busy($('#vbar'), $('#vgo'), false, '검증'); sync();
};

// ── 도장 관리 ───────────────────────────────────────────────────────
$('#copy').onclick = () => { navigator.clipboard.writeText($('#pubbox').value);
  $('#copy').textContent = '복사됨'; setTimeout(() => $('#copy').textContent = '복사', 1200); };
$('#expkey').onclick = async () => {
  err($('#kerr'), '');
  if (!KEY) return err($('#kerr'), '아직 도장이 없습니다');
  dl(new Blob([KEY.pkcs8], { type: 'application/octet-stream' }), 'watermark-key.bin');
};
$('#impkey').onclick = () => $('#keyfile').click();
$('#keyfile').onchange = async () => {
  err($('#kerr'), '');
  try { await A.importKey(await $('#keyfile').files[0].arrayBuffer()); await refresh(); }
  catch (e) { err($('#kerr'), '이 파일로는 도장을 불러올 수 없습니다'); }
};
$('#delkey').onclick = async () => {
  if (!confirm('도장을 지우면 지금까지 심은 워터마크를 증명할 수 없게 됩니다.\n내려받아 백업해 두셨나요?')) return;
  await A.deleteKey(); await refresh();
};

refresh();
