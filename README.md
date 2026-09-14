# WaterMark

**Invisible watermarking for photos, with cryptographic authorship proof.**
Everything runs on your own machine — photos and private keys never leave the computer.

This is **not** a tool that prevents theft. It is a tool that **traces** theft and
**proves** who made the photo. Everything it cannot do is listed under
[Limitations](#limitations).

### How it works

Three layers, each with a different job:

| Layer | Where | Survives | Proves |
|---|---|---|---|
| **Author tag** (48 bit) | DWT level 2, sparse | recompression, crop, denoise, **being overwritten** | points at your public key |
| **Message** | DWT level 1 | the normal upload path | nothing on its own |
| **Sidecar** (`.sig.json`) | separate file | (not in the image) | Ed25519 signature — undeniable |

Two design choices carry most of the weight:

- **Resize to the platform's size first, then embed.** Otherwise the platform's own
  resize drops detection to chance level (50%).
- **Sparse, key-derived placement.** Each public key picks 48 of 192 grid cells, so when
  someone re-watermarks your photo their cells mostly miss yours and your tag survives
  (measured 41–44 of 48 bits, reproduced across 6 random key pairs).

### Measured results

Instagram 1080px, bits recovered out of 48. The right column is the quality of the
*attacked photo itself* — an attack strong enough to remove the mark wrecks the photo too.

| Attack | Result | Attacked photo |
|---|---|---|
| Upload recompression | 48/48 | 39 dB |
| Screenshot | 48/48 | 38 dB |
| Downscale to 50% | 48/48 | 39 dB |
| Crop 10% off the edges | 48/48 | 30 dB |
| Crop to centre 40% | **lost** (43/48) | 16 dB |
| Denoising (removal attempt) | 45/48 | 38 dB |
| Brightness / contrast | 48/48 | 18 dB |
| Someone overwrites it | 41–44/48 | — |
| Crop **and** colour-grade together | lost | 15.7 dB |
| AI regeneration (img2img) | lost | — |

Embedding costs about 46–47 dB PSNR (SSIM 0.96–0.998) — not perceptible.

### Perceptual masking

Without it, smooth areas (sky, walls) show a visible grid: QIM snaps every block's
value to a lattice, and a flat block has no texture to hide that snap behind. Blocks
whose AC energy is below a threshold (`MASK_K = 0.5` of the quantization step) are
skipped instead of watermarked, with a floor so a fully flat photo still gets some
signal. This removes the grid completely (smooth-region max pixel change 9 → 0 on a
gradient test image). Masking's own share of that is 6 → 0 (43.4 → 46.1 dB PSNR on
smooth content) — the rest, 9 → 6, came from a separate fix that landed in the same
change: snapping to the *nearest* quantisation lattice point instead of always
rounding down.

The price: on a real photograph, masking increases attack failures from 1 to 2 out of
11 — the watermark is now lost on **centre-crop** and **crop-plus-colour-adjustment**,
where before only the latter failed. This is a deliberate trade: invisibility on
ordinary photos was judged more important than surviving those two specific edits. A
`MASK_K` sweep (0.35 / 0.50 / 0.65 / 0.85) showed 0.35–0.65 perform identically — flat
blocks have almost no AC energy, so any of these thresholds catches them the same way
— while 0.85 cuts into genuinely textured blocks and is measurably worse (4 failures
instead of 2 on the same real photo). `MASK_K = 0.5` was chosen as the middle of the
identical range.

### Face masking

Finds faces in the photo and irreversibly erases the ones you pick, before the
watermark and the perceptual hash go in. Detection is never treated as final:
every found face defaults to checked, and dragging on the photo adds or
removes a box by hand.

There are two fill strengths. **Solid** (the default) replaces the region with
its own quantised median colour plus a little noise — the output depends on
the original through three numbers and nothing else. **Mosaic** is offered
only because some users want that look; it is not a protection. It keeps the
block averages of the original, and jitter on top of them does not change
that. Measured re-identification (picking the correct original out of 200
candidates; chance is 0.5%):

| Masking | Re-identification top-1 | Samples left |
|---|---|---|
| Solid (default) | 2.0% | 3 |
| Mosaic, blocks 4 / jitter 12 (default mosaic) | 100.0% | 48 |
| Mosaic, blocks 4 / jitter 24 | 100.0% | 48 |
| Mosaic, blocks 4 / jitter 48 | 74.5% | 48 |
| Mosaic, blocks 4 / jitter 96 | 13.5% | 48 |
| Mosaic, blocks 3 / jitter 12 | 100.0% | 27 |
| Mosaic, blocks 3 / jitter 24 | 82.5% | 27 |
| Mosaic, blocks 3 / jitter 48 | 25.5% | 27 |
| Mosaic, blocks 3 / jitter 96 | 6.0% | 27 |
| Mosaic, blocks 2 / jitter 12 | 45.5% | 12 |
| Mosaic, blocks 2 / jitter 24 | 17.5% | 12 |
| Mosaic, blocks 2 / jitter 48 | 5.5% | 12 |
| Mosaic, blocks 2 / jitter 96 | 1.0% | 12 |

Solid measures above chance because the quantised median colour is retained
by design — that is the entire channel left open, and it is not enough to
reconstruct a face. Mosaic, at every setting that still looks like an ordinary
mosaic, lets an attacker recover the correct original essentially every time.
**If you actually need to protect someone, use solid. Mosaic is appearance
only.**

### Metadata cleanup

A checkbox on the same screen: **"Remove EXIF (location · time · device) without a
watermark."** No key, no message, nothing to prove — just a photo in, a clean photo
out. It exists because embedding a watermark already strips every EXIF tag as a
side effect of re-encoding, but until now there was no way to get that side effect
without also agreeing to the watermark's key/message requirement.

The photo is decoded and redrawn to a canvas, then re-encoded — the same pipeline
`protect()` uses, so GPS coordinates, capture time, camera make and model disappear
for the same reason, and orientation is baked into the pixels first so a portrait
photo doesn't come out sideways. Face masking and the AI-training-refusal
declaration both work in this mode too, since neither depends on the watermark.

### Privacy

No server. Verified by watching sockets throughout processing: **zero non-loopback
connections**. Bound to `127.0.0.1` only, photos are held in memory (never written to
disk), and the server shuts itself down after 10 idle minutes.

### Install (macOS)

Double-click `실행.command`. First run takes 1–2 minutes to fetch dependencies; after
that the browser opens straight away. Or from a terminal:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py     # → http://127.0.0.1:8765
```

### Limitations

- Cannot stop anyone from taking the photo. It only helps afterwards.
- Cannot block AI training. It writes the IPTC/PLUS `DataMining` opt-out, but that has
  no enforcement and social networks strip metadata on upload.
- Does not survive generative regeneration (img2img) — a limitation shared by every
  invisible watermark today.
- Crop combined with colour grading can slip through.
- A targeted attacker who knows your public key and edits the code can erase it.
- Does not prove who was *first*. Signature timestamps are self-reported.
- **Automatic face detection is not reliable.** Three detectors are run and their results
  merged — TinyFaceDetector at two input sizes plus SSD MobileNet v1 — which finds 22 of 24
  faces across a hard test set where a single pass found 19. What still fails: a face whose
  forehead is cropped out of frame (1 of 3 found). Merging also produces the occasional
  false positive, which is the safe direction — you can untick it, whereas a face nobody
  found gets published. Look at the preview before posting and paint over anything missed.

### Two ways to run it

- **Web app** — open the deployed site. Everything runs in the browser tab; nothing is
  uploaded. Verified interoperable with the desktop version: a photo watermarked in the
  browser is read by the Python app (48/48 bits) and its Ed25519 signature verifies there.
- **Desktop** — clone and run locally (below).

**The UI and the detailed documentation below are in Korean.**

MIT licensed. `dwtdctsvd.py` derives from
[invisible-watermark](https://github.com/ShieldMnt/invisible-watermark)
(MIT, Copyright (c) 2021 ShieldMnt) — see `NOTICE`.

---

# 한국어

사진에 **육안으로 보이지 않는 워터마크**를 심고, **Ed25519 서명**으로 원작자를 증명한다.
전부 이 컴퓨터에서만 돌아간다 — 사진도 개인키도 어디로도 전송되지 않는다.

**이 도구는 도용을 막지 못한다. 도용을 추적하고 원작자를 증명하는 도구다.**
못 하는 것은 [알려진 한계](#알려진-한계-과장하지-않기)에 전부 적어두었다.

## 실행

**파인더에서 `실행.command` 를 더블클릭한다.** 그게 전부다.

처음 한 번은 필요한 부품을 내려받느라 1~2분 걸리고(인터넷 필요), 그다음부터는 바로 열린다.
브라우저가 자동으로 `http://127.0.0.1:8765` 를 연다. 이미 켜져 있으면 새로 띄우지 않고
브라우저만 연다.

끄려면 그 창을 닫거나 Control-C. **10분간 쓰지 않으면 저절로 꺼진다.**

터미널에서 직접 실행하려면:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py     # → http://127.0.0.1:8765
```

처음 열면 **첫 화면에서 "내 도장"을 한 번 만든다.** 셋 중 하나를 고르면 된다.

- **암호 걸고 만들기** (권장) — 암호를 두 번 입력받는다
- **암호 없이 만들기** — 파일 권한(0600)으로만 보호된다
- **필요 없어요** — 증명은 포기하고 문장만 심는 [간단 모드](#키-없이-쓰기-간단-모드)로 넘어간다

개인키는 `~/.watermark/key.pem` 에 저장된다.
**반드시 백업할 것 — 잃어버리면 과거에 심은 워터마크를 증명할 수 없다.**

---

## 구조: 세 겹

| | 작성자 태그 | 문장 | 사이드카 `.sig.json` |
|---|---|---|---|
| 어디에 | 이미지 안 (DWT 레벨 2) | 이미지 안 (DWT 레벨 1) | 별도 파일 |
| 내용 | `SHA256(공개키)` 앞 48비트 | 임의의 문장 | 해시·문장·지각해시에 대한 서명 |
| 견디는 것 | 재압축·크롭·디노이즈·색보정·**덮어쓰기** | SNS 업로드 경로까지 | (이미지와 무관) |
| 증명력 | "이 공개키를 가리킴" | 없음 | 부인 불가 |

**공개키가 곧 신원 등록부**다. 프로필·깃허브 등에 공개해 두면 누구나 검증할 수 있고 별도 DB가
필요 없다. 48비트 태그에 512비트 서명은 안 들어가므로, 강한 증명은 사이드카가 맡는다.

---

## 핵심 설계 1: 플랫폼 규격으로 먼저 줄인 뒤 심는다

워터마크는 리사이즈에 취약하다. 3000px 사진을 그대로 심어 인스타에 올리면 1080px로 깎이면서
**검출률이 찍기 수준(50%)으로 떨어진다.** 그래서 업로드할 해상도로 **먼저** 줄인 다음 심는다.
플랫폼이 추가로 건드릴 게 없어져 워터마크가 그대로 살아남는다.

## 핵심 설계 2: 거친 구조에 심는다

디노이즈·블러·리사이즈 같은 제거 시도는 고주파만 건드린다. 그래서 워터마크를 DWT 레벨 2,
즉 이미지의 **거친 구조**에 싣는다. 이걸 지우려면 사진의 형태 자체가 무너진다.

## 핵심 설계 3: 희소 배치 — 덮어써도 살아남는다

양자화(QIM)는 나중에 쓴 값이 이긴다. 모든 블록에 조밀하게 심으면 남이 덮어쓸 때 전부 잃는다.

그래서 **격자 12x16 = 192칸 중 공개키가 고른 48칸에만** 심는다. 키가 다르면 고르는 칸도
달라서(평균 12~15칸만 겹침), 남이 덮어써도 겹치지 않은 칸에 내 비트가 남는다.

---

## 측정 결과

### 공격별 검출 (Instagram 1080x720, 48비트 중 일치 비트)

| 공격 | 결과 | 공격당한 사진 화질 |
|---|---|---|
| 업로드 재압축 | 48/48 검출 | 39.0dB |
| 강한 재압축 (q60) | 48/48 검출 | 36.2dB |
| 스크린샷 캡처 | 48/48 검출 | 38.4dB |
| 저장 후 50% 축소 | 48/48 검출 | 38.8dB |
| 가장자리 10% 크롭 | 48/48 검출 | 29.5dB |
| 정중앙 40%만 남김 | **43/48 소실** | 16.3dB |
| 노이즈 제거 (제거 시도) | 45/48 검출 | 37.6dB |
| 노이즈 제거 + 강한 재압축 | 46/48 검출 | 36.9dB |
| 12%까지 축소 후 복원 | 47/48 검출 | 38.2dB |
| 밝기/대비 조작 | 48/48 검출 | 18.2dB |
| 크롭 + 밝기/대비 | **41/48 소실** | 15.7dB |

위 수치는 지각 마스킹(격자 제거)을 켠 상태의 실측이다. 마스킹 전에는 정중앙 크롭도
48/48 로 살아남았다 — 그것을 잃는 대신 매끈한 배경에서 격자가 보이지 않게 된 것이다.

오른쪽 dB는 **공격당한 사진 자체의 화질**이다. 워터마크를 지울 만큼 센 공격은 사진도 같이
망가진다는 것이 이 도구가 기댈 수 있는 유일한 보장이다.

### 덮어쓰기 생존

| | 원작자 태그 | 도용자 태그 | 읽히는 문장 |
|---|---|---|---|
| 원작자가 보호 | 48/48 | — | "원작자 홍길동…" |
| 도용자가 그 위에 다시 보호 | **41~44/48 검출** | 48/48 | "도용자 mallory…" |

**둘이 공존한다.** 랜덤 키 6쌍으로 6/6 재현했고, 브라우저 UI 전 과정으로도 확인했다
(도용본에서 원작자 태그 43/48, 우연히 맞을 확률 7,300만분의 1).
조밀 배치이던 이전 설계에서는 같은 자리에서 47/48 → 33/48 로 소멸했다.

### 화질

| 구성 | PSNR |
|---|---|
| 태그만 | 47.2dB |
| 태그 + 문장 | 45.9dB |
| 키 없이 문장만 | 49.2dB |

SSIM 은 0.96~0.998다. 32px 블록 격자 얼룩은 지각 마스킹이 없으면 매끈한 영역에 그대로
보인다(아래 절 참고) — 마스킹을 켠 지금은 매끈한 영역의 픽셀 차이가 평균·최대 모두
0으로 사라져 격자도 함께 사라진다.

### 지각 마스킹 — 격자를 없애는 대가

마스킹이 없으면 하늘·벽처럼 매끈한 영역에 격자가 눈에 보인다. QIM 이 블록마다 값을
격자점으로 스냅하는데, 평평한 블록에는 그 스냅을 가려줄 무늬가 없기 때문이다. AC
에너지(양자화 간격 기준 `MASK_K = 0.5` 미만)가 부족한 블록은 심지 않고 건너뛴다 —
다만 사진 전체가 평평해도 최소한의 신호는 남도록 바닥(`MASK_FLOOR`)을 둔다. 이걸로
그라디언트 테스트 이미지의 매끈한 영역 최대 픽셀 변화가 9 → 0 으로 사라진다. 이 중
마스킹 자신의 몫은 6 → 0(매끈한 영역 PSNR 43.4 → 46.1dB)이고, 나머지 9 → 6 은 같은
변경에 함께 들어간 별개의 수정 — 양자화 격자점 중 아무 데나가 아니라 **가장 가까운**
점으로 옮기게 한 것 — 이 낸 몫이다.

대가: 실제 사진 한 장 기준으로 11개 공격 중 실패가 1개 → 2개로 늘었다 — 이제
**정중앙 크롭**과 **크롭+밝기/대비 조작**에서 워터마크를 잃는다 (마스킹 전에는
후자만 실패했다). 이건 사용자가 측정치를 보고 의도적으로 고른 교환이다: 흔한 사진에서
안 보이는 것이 그 두 가지 편집을 견디는 것보다 중요하다고 판단했다. `MASK_K` 를
0.35/0.50/0.65/0.85 로 스윕한 결과 0.35~0.65 는 완전히 동일했다 — 평평한 블록은 AC
에너지가 거의 0 이라 어느 문턱이든 똑같이 걸러지기 때문이다 — 반면 0.85 는 진짜
무늬가 있는 블록까지 파고들어 눈에 띄게 더 나빴다(같은 사진에서 실패 2개가 아니라
4개). 그래서 동일 구간의 중앙값인 `MASK_K = 0.5` 를 골랐다.

---

## 문장 심기 (선택)

48비트 태그에는 문장이 안 들어간다. 한글 한 문장이면 700비트가 넘는다. 그래서 문장은
**별도 층**(DWT 레벨 1)에 심는다. 블록이 4배 촘촘해 용량이 크지만 그만큼 약하다.

| 이미지 | 용량 | 한글 |
|---|---|---|
| 1080x720 (인스타 가로) | 88바이트 | 29자 |
| 1080x1350 (인스타 세로) | 171바이트 | 57자 |
| 2048x1365 (X) | 334바이트 | 111자 |

| | 문장 | 작성자 태그 |
|---|---|---|
| 업로드 재압축 / 스크린샷 / 50% 축소 | 그대로 | 검출 |
| 강한 재압축 (q60) / 크롭 / 밝기·대비 | 깨짐 | 검출 |

즉 **정상 업로드·다운로드 경로에서는 문장이 읽히고, 편집당하면 문장은 잃되 태그는 남는다.**

문장이 **정확히 복원됐을 때만** 보여준다 (CRC 검사). 한 비트만 틀려도 글자가 깨지므로,
깨진 글자를 진짜로 착각하는 일이 없도록 아예 표시하지 않는다.
문장은 사이드카 서명이 덮으므로, 이미지 속 문장이 깨져도 원래 문구는 증명할 수 있다.

두 층은 서로 간섭한다. 반드시 **태그를 먼저, 문장을 나중에** 심는다 (순서를 바꾸면 거친 층
삽입이 미세 층 계수를 흔들어 768비트 중 230비트가 깨졌다). 문장층 강도도 36이 아니라 24다 —
36이면 문장은 더 질기지만 태그의 크롭 복구가 무너진다.

## 키 없이 쓰기 (간단 모드)

증명이 필요 없고 "이 사진은 내 것"이라는 문장만 남기고 싶다면 키 없이 쓸 수 있다.
[보호하기] 탭의 **"키 없이 문장만 심기"** 체크.

| | 키 있음 | 키 없음 |
|---|---|---|
| 심는 것 | 태그 + 문장 | 문장만 |
| 사이드카 | 서명된 `.sig.json` | **없음** |
| 원작자 증명 | 암호학적으로 가능 | **불가능** |
| 화질 | 39.9dB | **45.8dB** |

**포기하는 것을 분명히 해둔다.** 서명이 없으면 남이 같은 문장을 자기 사진에 심어도 구별할
방법이 없고, 이 사진을 자기 것이라 주장해도 반박할 근거가 없다.

검증도 키가 필요 없다. [검증하기] 탭에 사진만 넣으면 심긴 문장을 읽어준다.

## AI 학습 거부 선언

파일에 **IPTC/PLUS 표준**의 데이터 마이닝 거부 표시를 넣는다 (기본 켜짐).

```
plus:DataMining = http://ns.useplus.org/ldf/vocab/DMI-PROHIBITED-AIMLTRAINING
```

IPTC Photo Metadata 2023.1 이 PLUS 어휘를 받아들여 표준화한 항목이고, ExifTool 12.67 부터
지원한다. JPEG 의 XMP(APP1) 세그먼트에 쓰며 418바이트를 더한다. Pillow 로 읽어 네임스페이스까지
맞는지 확인했다. 사이드카 서명이 이 선언까지 덮으므로 나중에 부인할 수 없다.

**한계를 분명히 해둔다.**

- **강제력이 없다.** 크롤러가 읽고 지켜줘야 의미가 있는 '의사 표시'다. 기술적 차단이 아니다.
- **SNS 를 거치면 사라진다.** 인스타·X 는 업로드 때 메타데이터를 지운다. 포트폴리오·메일·
  직접 다운로드처럼 파일을 그대로 배포하는 경로에서만 살아남는다.

### 왜 워터마크가 아니라 메타데이터에 넣나

크롤러가 읽는 곳은 파일 메타데이터이지 우리 워터마크가 아니다. 워터마크에 넣으면 SNS 는
통과하지만 **아무도 읽지 않는다.** 읽히는 곳에 쓰는 편이 낫다고 판단했다.

### 이미지를 깨뜨려 학습을 막는 방법은 넣지 않았다

확산 모델 인코더를 겨냥한 적대적 섭동(PhotoGuard 계열)은 **리사이즈와 JPEG 재압축에 가장 먼저
죽는다.** 그런데 이 도구는 플랫폼 규격으로 먼저 줄이고, SNS 가 또 재압축한다 —
**워터마크를 살리려고 만든 구조가 그 섭동을 스스로 지운다.** PyTorch 와 수 GB 모델 가중치가
필요해 "완전 로컬" 성질도 깨진다. 효과가 없을 것을 알면서 넣는 것은 보호받는다는 착각만 준다.

---

## 얼굴 가리기

사진에 함께 찍힌 **다른 사람의 초상권**을 지킨다. 사진에서 얼굴을 찾아 번호를 붙이고,
사용자가 고른 얼굴만 워터마크·지각 해시보다 **먼저** 되돌릴 수 없게 지운다.

가리는 방식은 두 가지다. **단색(기본)** 은 그 영역의 채널별 중앙값 색을 16단계로
양자화해 채우고 화소당 미세한 난수를 더한다. 출력은 원본 얼굴에 **양자화된 색 세
개**를 통해서만 의존하므로, 복원할 정보 자체가 남지 않는다 — 알고리즘의 문제가
아니라 정보량의 문제라 증명 가능하다. **모자이크**는 그 모양을 원하는 사용자를
위한 선택지일 뿐, 보호 수단이 아니다. 블록 평균은 원본이 준 정보 그대로 남고,
그 위에 얹는 지터는 그 사실을 바꾸지 못한다. 재식별 측정(후보 200장 중 진짜
원본 하나 맞히기, 우연은 0.5%):

| 가리기 방식 | 재식별 top-1 | 남는 표본 |
|---|---|---|
| 단색 (기본) | 2.0% | 3개 |
| 모자이크, 블록4/지터12 (모자이크 기본값) | 100.0% | 48개 |
| 모자이크, 블록4/지터24 | 100.0% | 48개 |
| 모자이크, 블록4/지터48 | 74.5% | 48개 |
| 모자이크, 블록4/지터96 | 13.5% | 48개 |
| 모자이크, 블록3/지터12 | 100.0% | 27개 |
| 모자이크, 블록3/지터24 | 82.5% | 27개 |
| 모자이크, 블록3/지터48 | 25.5% | 27개 |
| 모자이크, 블록3/지터96 | 6.0% | 27개 |
| 모자이크, 블록2/지터12 | 45.5% | 12개 |
| 모자이크, 블록2/지터24 | 17.5% | 12개 |
| 모자이크, 블록2/지터48 | 5.5% | 12개 |
| 모자이크, 블록2/지터96 | 1.0% | 12개 |

단색이 우연(0.5%)보다 높게 나오는 이유는 분명하다 — **양자화된 중앙값 색이
설계상 그대로 남기 때문**이다. 그것이 유일하게 열어 둔 통로이고, 얼굴을 복원하기에는
부족한 양이다. 모자이크는 보통의 모자이크로 보이는 설정 전부에서 공격자가 사실상
매번 정확한 원본을 되찾는다. **초상권을 실제로 지켜야 한다면 단색을 쓴다. 모자이크는
모양만 가릴 뿐이다.**

**자동 탐지는 최종 결과가 아니다.** 찾은 얼굴은 기본으로 전부 체크되고, 사진 위를
끌면 직접 박스를 추가·삭제할 수 있다 — 옆얼굴·작은 얼굴·가려진 얼굴은 탐지가 놓치므로
직접 칠하는 손이 1급 기능이다.

## 메타데이터 지우기

같은 화면의 체크박스 하나: **"워터마크 없이 위치·시각·기종 정보(EXIF)만 지우기."**
키도 문장도 필요 없다 — 증명할 게 없으니까. 사진을 넣으면 정리된 사진이 나온다.

워터마크를 심을 때도 재인코딩의 부산물로 EXIF가 이미 전부 사라지는데, 그동안은
그 부산물만 얻으려 해도 워터마크의 키·문장 요구를 함께 받아들여야 했다. 이 체크박스는
그 요구를 떼어낸 것뿐이다.

사진을 디코드해서 캔버스에 다시 그리고 재인코딩한다 — `protect()`가 쓰는 경로와
같다. 그래서 GPS 좌표·촬영 시각·기종이 같은 이유로 사라지고, 방향(Orientation)도
먼저 픽셀에 구워 넣어 세로 사진이 옆으로 눕지 않는다. 얼굴 가리기와 AI 학습 거부
선언 둘 다 이 모드에서도 쓸 수 있다 — 둘 다 워터마크에 기대지 않는 기능이라서다.

---

## 변형을 되돌리는 3단계

변형마다 되돌리는 방법이 달라서, 싼 것부터 단계적으로 넓혀 간다. 앞 단계에서 확실해지면 멈춘다.

1. **그대로 읽기** — 리사이즈됐다면 원래 크기로 되돌린 것도 함께 (후보 2개)
2. **밝기/대비 되돌리기** — 이득·오프셋 후보 40가지 (후보 80개)
3. **크롭 정렬 전수 탐색** — 그 40가지마다 픽셀 오프셋·격자 시프트를 전부 (후보 200만개)

3단계가 빠른 이유: 픽셀 오프셋이 4 차이나면 DWT 계수가 그만큼 밀린 것과 같으므로,
DWT 는 4가지만 계산하고 나머지는 계수를 잘라 재사용한다 (4가지 DWT x 4x4 블록 x 192 시프트).

| 상황 | 소요 |
|---|---|
| 그대로 읽힘 | 0.0초 |
| 크롭 50% (탐색으로 찾음) | 0.9초 |
| 워터마크 없음 (예산 소진) | 13.6초 |

찾으면 즉시 멈추지만 **못 찾을 때**가 문제여서, 2·3단계에 합쳐 12초의 벽시계 예산을 뒀다.
없으면 몇 분씩 걸렸다.

### 판정은 비트 수가 아니라 확률로

후보를 많이 볼수록 우연히 맞을 확률이 오른다. 그래서 "지금까지 본 후보 수"를 반영한
오탐 확률로 판정한다. 기준은 **1e-4 이하**이며 화면에 실제 확률을 함께 보여준다.

| 단계 | 후보 수 | 필요한 일치 |
|---|---|---|
| 1단계 | 2 | 39/48 |
| 2단계 | 80 | 41/48 |
| 3단계 | 200만 | 46/48 |

**페이로드가 48비트인 이유가 여기 있다.** 32비트였다면 3단계에서 완전 일치(32/32)로도
기준을 못 넘어 크롭 탐색 자체가 불가능했다. 예산이 커진 덕에 색보정이 섞인 크롭도 잡는다.

---

## 덮어쓰기에 대한 3중 방어

누군가 이 사진에 자기 워터마크를 다시 심는 공격(ambiguity attack)은 비가시 워터마크 전반의
알려진 약점이다. 세 겹으로 대응한다.

### 1. 희소 배치 — 워터마크가 살아남는다

위 [측정 결과](#덮어쓰기-생존) 참조. 41~44/48 로 검출된다.

**왜 레벨 2인가.** 레벨 3 조밀 배치(이전 설계)는 블록이 726개뿐이라 희소하게 나눌 여유가
없었다. 레벨 2는 3015개라 4분의 1만 써도 비트당 표본이 15개 남는다.

**레벨 3 조밀 + 레벨 2 희소를 겹치는 방법도 시도했으나 실패했다.** 남의 레벨 3 쓰기가
레벨 2 전체를 흔들어 생존층이 20/48 로 무너졌고(거친 층에 쓰면 아래 층이 통째로 흔들린다),
그걸 버틸 만큼 생존층을 키우면 이번에는 내 레벨 3 태그가 30/48 로 무너졌다.
두 층이 서로를 죽이므로 하나만 골라야 했다. 강도는 180이 최적이다 —
160·200 은 랜덤 키 6개 중 1~2개에서 생존에 실패했다.

### 2. 지각 해시 — 워터마크가 지워져도 파생 관계를 보인다

워터마크는 덮어쓸 수 있지만 **사진 자체는 못 지운다.** 사이드카에 원본의 지각 해시
(사진의 '모양'을 63비트로 요약)를 함께 서명해 둔다.

| 대상 | 거리 (63비트 중) | 판정 |
|---|---|---|
| 덮어쓴 도용본 / 재압축 / 축소 / 밝기조작 | 2~6 | 같은 사진 |
| 가장자리 10% 크롭 | 16 | 편집된 같은 사진 |
| 정중앙 50% 크롭 | 26 | (판정 불가) |
| 무관한 사진 20종 | 22~40 | 다른 사진 |

**닮았다는 것은 증거 보강이지 확증이 아니다.** 도용자도 자기가 훔친 사진의 지각 해시에
서명할 수 있으므로 이것만으로 "누가 먼저인지"는 가려지지 않는다. 표본이 합성 이미지라
비슷한 풍경 사진끼리는 더 가까울 수 있어 기준을 보수적으로 잡았다.

### 3. 사이드카 — 확정 증명

덮어쓴 도용자는 자기 사이드카에 "이것이 내 원본"이라고 서명하는데, **그 해시가 원작자의
출력물과 바이트 단위로 같다.**

```
도용자가 '내 원본'이라 서명한 해시 : e76627e1859d25cf…
원작자가 '내 보호본'이라 서명한 해시: e76627e1859d25cf…   ← 동일
원작자만 가진 진짜 원본의 해시     : 804f111088c4df9c…   ← 도용자는 못 만듦
```

원작자는 워터마크 이전의 원본 파일을 갖고 있고 그 해시가 서명돼 있다. 도용자는 그 파일을
만들어낼 수 없다. **원본 파일과 사이드카를 반드시 보관해야 하는 이유가 이것이다.**

그래서 화면에서도 안내문으로 끝내지 않는다. 보호가 끝나면 챙길 것 세 가지(보호된 이미지 /
사이드카 / 원본 파일)를 단계로 보여주고, **사이드카를 실제로 내려받았는지 추적한다.**
안 받았으면 배지가 "아직 안 받음"으로 남고, 그 상태로 창을 닫으려 하면 브라우저가 확인을 묻는다.

다만 서명의 **시각은 자기 신고**라 둘 다 "내가 먼저"라고 주장할 수 있다. 선점을 증명하려면
OpenTimestamps 같은 외부 앵커가 필요한데, 그건 해시를 외부 서버로 보내는 일이라
"아무것도 이 컴퓨터를 벗어나지 않는다"는 이 도구의 성질과 충돌한다. 넣지 않았다.

### 덮어쓰기 차단 (실수 방지용, 보안 통제 아님)

기존 워터마크가 감지되면 삽입을 거부하고, 사용자가 명시적으로 "그래도 덮어쓰기"를 눌러야
진행한다. 감지 경로는 둘이다 — **내 워터마크**(공개키 대조, 정확)와 **남의 문장**(CRC 통과,
우연 확률 2⁻³²). 후자는 발견한 문장을 그대로 보여준다.

**문장 없이 태그만 심은 남의 워터마크는 알아내지 못한다.** 희소 배치라 블록의 1/4 에만
표시가 있어 통계로는 정상 사진과 구별되지 않았다 (칸별 통계까지 시도했으나 문서형 이미지에서
오탐이 났다). 조밀 배치이던 시절에는 위상 쏠림으로 잡을 수 있었지만, 희소 배치로 얻은
생존성과 맞바꾼 셈이다. 덮어써도 워터마크가 살아남으므로 경고를 놓쳐도 피해는 크지 않다.

이 차단은 같은 프로그램을 쓰는 상대에게만 걸린다. 도용자는 이 프로그램을 안 쓰거나
파이썬 코드 한 줄을 지우면 그만이다. **실수 방지 장치이지 보안 통제가 아니다.**

---

## 보안: 데이터가 이 컴퓨터를 벗어나지 않는다

| 항목 | 확인 방법 | 결과 |
|---|---|---|
| 소스에 외부 송신 코드 | requests·urllib·socket 등 전수 검색 | 없음 |
| 화면이 부르는 주소 | fetch 호출 전수 확인 | `/api/*` 상대경로뿐 |
| 외부 리소스 (폰트·CDN) | 외부 URL 검색 | 없음 (`data:`/`blob:` 만) |
| 처리 중 실제 통신 | 사진 처리 내내 `lsof` 로 소켓 감시 | 루프백 외 연결 0건 |
| 네트워크 노출 | LAN IP 로 접속 시도 | 연결 거부 |
| 사진이 디스크에 남는지 | 업로드 중 임시파일 감시 | 없음 (메모리에서만) |
| 개인키 유출 경로 | 응답에 실리는 값 확인 | 공개키만 |

점검에서 실제로 두 가지가 걸렸고 둘 다 고쳤다.

**1. 1MB 넘는 업로드가 디스크 임시파일로 떨어졌다.** 웹 프레임워크(starlette)의 기본 스풀
임계값이 1MB라 웬만한 사진이 처리 중 시스템 임시 폴더에 기록됐다. 임계값을 업로드 상한
위로 올려 해결했다.

**2. 악성 웹사이트가 이 로컬 서버를 부릴 수 있었다.** 127.0.0.1 에 묶여 있어도 사용자가
방문한 악성 페이지가 브라우저를 통해 POST 를 보낼 수 있다 (CORS 는 응답을 못 읽게 할 뿐
요청은 막지 않는다). DNS 리바인딩이면 읽기까지 뚫린다. 사진을 보관·반환하는 엔드포인트가
없어 사진 유출은 불가능하지만, 남이 이 사용자의 개인키로 서명을 시킬 수 있었다.
Host/Origin 검사로 막았다.

**3. 서버가 떠 있는 시간이 곧 노출 시간이다.** 화면은 탭이 보이고 최근에 조작이 있을 때만
1분마다 신호를 보낸다. 탭을 닫든 자리를 비우든 신호가 끊기고 **10분 뒤 서버가 종료**된다.

남는 위험:

- 서버가 떠 있는 동안 같은 컴퓨터의 다른 프로그램은 `127.0.0.1:8765` 에 접근할 수 있다.
- 키 암호는 루프백 HTTP 로 평문 전송된다 (컴퓨터를 벗어나지는 않는다).
- 개인키 파일(권한 0600)을 읽을 수 있는 프로그램은 신원을 훔칠 수 있다.

---

## 알려진 한계 (과장하지 않기)

- **생성형 재생성(img2img)에는 못 버틴다.** 확산 모델로 이미지를 다시 만들면 워터마크는
  사라진다. 이건 이 도구만의 한계가 아니라 현재 모든 비가시 워터마크의 공통 한계다.
- **표적 덮어쓰기는 못 막는다.** 칸 선택이 공개키에서 유도되므로, 내 공개키를 아는 상대가
  프로그램을 고치면 내 칸만 노릴 수 있다. 모든 칸을 덮어쓰면 그만이기도 하다.
- **너무 작은 조각은 못 읽는다.** 격자 12x16 에 블록 하나가 16px 이므로 최소 **256 x 192px**
  은 남아 있어야 한다.
- **크롭 후 리사이즈**는 못 찾는다. 정렬은 되돌려도 배율은 모른다.
- **문장 층은 편집을 못 견딘다.** 크롭·강한 재압축·밝기 조작 중 하나만 있어도 깨진다.
  용량과 강인성이 정면으로 맞바꿔지기 때문이고, 그래서 작성자 태그를 따로 둔 것이다.
- **워터마크 단독은 위조 가능.** 남의 공개키 태그를 자기 사진에 심을 수 있다.
  확정 증명은 사이드카 쪽이다.
- **시각 증명 없음.** 서명의 시각은 자기 신고다.
- **AI 학습 자체를 막지는 못한다.** 이건 추적·귀속 도구지 차단 도구가 아니다.
- **얼굴 자동 찾기는 완벽하지 않다.** 탐지기 셋을 돌려 결과를 합친다 — TinyFaceDetector
  를 두 가지 입력 크기로, 그리고 SSD MobileNet v1 로. 어려운 사진 8장(정답 24명)에서
  22명을 찾는다. 한 번만 돌리던 이전 방식은 19명이었다. 여전히 못 찾는 것: **이마가 화면
  밖으로 잘린 얼굴**(3명 중 1명). 합치다 보니 오탐이 가끔 하나 생기는데, 이건 안전한
  방향이다 — 오탐은 체크를 끄면 그만이지만 아무도 못 찾은 얼굴은 그대로 발행된다.
  올리기 전에 미리보기를 눈으로 확인하고 놓친 얼굴은 직접 칠해야 한다.

  측정 (어려운 사진 8장, 정답 24명):

  | 경우 | 한 번만 | 셋을 합침 |
  |---|---|---|
  | 쉬운 정면 3명 | 3 | 3 |
  | 입·턱 가림 | 3 | 3 |
  | 이마가 잘림 | 1 | 1 |
  | 화면 끝에 반쯤 걸침 | 1 | 1 |
  | 고개 25도 기울임 | 3 | 4 (오탐 1) |
  | 멀리 있는 작은 얼굴 | **0** | **3** |
  | 어두운 사진 | 3 | 3 |
  | 쉬운 5명 | 5 | 5 |
  | **합계** | **19/24** | **22/24** |

  모델을 크게 바꾸는 것만으로는 안 됐다. SSD MobileNet 하나로 갈아타도 19/24 로 같았고
  (작은 얼굴에서 이기고 어두운 사진에서 졌다), 이기는 조합은 서로 놓치는 게 다른 셋을
  합치는 것이었다. 대신 모델이 5.6MB 늘었다 — 얼굴 가리기를 처음 쓸 때만 받는다.

---

## 파일

- `dwtdctsvd.py` — DWT-DCT-SVD 알고리즘. invisible-watermark(MIT)에서 가져와 정리했다.
  이식 시점에 원본과 바이트 단위로 같은 출력을 내는 것을 확인한 뒤 아래를 고쳤다.
  역 DWT 밴드 순서 버그(PSNR 33 → 43dB), uint8 랩어라운드 버그, 크롭 내성을 위한 주기 격자
  배치, 희소 칸 선택, 그리고 전수 탐색을 감당하게 하는 벡터화.
- `watermark.py` — 키·삽입·추출·서명·지각해시·벤치마크·`mask_faces()` 얼굴 가리기
- `server.py` — 로컬 웹 API (127.0.0.1 전용, Host/Origin 검사, 유휴 시 자동 종료,
  `/lib` 로 `site/` 를 한 번 더 서빙해 얼굴 탐지 JS 를 웹앱과 공유)
- `static/index.html` — UI
- `test_watermark.py` — 자체 점검. `.venv/bin/python test_watermark.py`
- `test_face_mask.py` — 얼굴 가리기 자체 점검. `.venv/bin/python test_face_mask.py`
- `실행.command` — 파인더에서 더블클릭하는 실행기. 첫 실행 때 준비까지 알아서 한다.
- `site/` — **브라우저에서 도는 웹 앱** (정적 파일). Vercel 등에 그대로 올린다.
  서버로 사진을 보내지 않는다 — 계산이 전부 탭 안에서 일어나므로 "사진이 내 기기를
  벗어나지 않는다"가 그대로 유지된다.
  - `wm-core.js` 수학(DWT·DCT·최대특이삼중항) · `wm.js` 워터마크·문장·지각해시
  - `app-core.js` 키·서명·보호/검증 · `app.js` 화면 · `index.html`
  - `face.js` 얼굴 탐지·가리기 · `face-ui.js` 선택 패널 (데스크톱과 공유)
  - `vendor/` face-api.js 와 모델 가중치 (출처·해시는 `NOTICE` 참조)

## 웹 앱과 데스크톱 앱의 호환

같은 형식을 쓴다. 브라우저에서 심은 워터마크를 파이썬 앱이 읽고, 그 반대도 된다.
이식이 정확한지 기계적으로 대조했다.

| 항목 | 결과 |
|---|---|
| DCT 행렬 · 4x4 DCT · Haar DWT 2단계 | 오차 1e-13 |
| 최대 특이삼중항 | 오차 3e-13 |
| 작성자 태그 · 칸 선택 | 완전 일치 |
| 태그 삽입 결과 픽셀 | 98,304개 전부 동일 |
| CRC32 · 문장 프레임 · 용량 계산 | 완전 일치 |
| 지각 해시 | 같은 파일에서 완전 일치 (구현 차이 최대 2비트, 허용 10) |
| **브라우저가 심음 → 파이썬이 읽음** | **태그 48/48 · 문장 정확 · 선언 확인** |
| **브라우저 서명 → 파이썬이 검증** | **유효** |

이식 과정에서 세 가지를 맞춰야 했다.

- **칸 선택을 해시 기반으로 바꿨다.** 원래 `numpy.default_rng().choice()` 를 썼는데
  난수 알고리즘을 다른 언어에서 똑같이 재현하는 것은 취약하다. 지금은
  `SHA256(seed || 칸번호)` 순위로만 정한다.
- **정규 JSON 을 파이썬에 맞췄다.** 파이썬 `json.dumps` 는 비ASCII 를 `\uXXXX` 로
  escape 한다. JS 는 원문을 내보내므로 그대로 두면 서명 대상 바이트가 달라져
  서명 검증이 실패했다.
- **지각 해시의 축소를 OpenCV 와 맞췄다.** 면적 가중 평균에 정수 반올림까지 해야 한다
  (빼먹으면 평균 8비트 어긋났다).

## 라이선스

MIT. `dwtdctsvd.py` 는 [invisible-watermark](https://github.com/ShieldMnt/invisible-watermark)
(MIT, Copyright (c) 2021 ShieldMnt)의 dwtDctSvd 구현에서 출발했다. 원문 라이선스는 `NOTICE` 참조.
