# 얼굴 모자이크 — 설계

날짜: 2026-09-13
상태: 승인됨

## 왜

사진을 SNS에 올릴 때 함께 찍힌 **타인의 초상권**을 지킨다. 지금 이 도구는
올리는 사람의 저작권만 다룬다 — 찍힌 사람 쪽은 비어 있다.

## 무엇을 만드나

사진에서 얼굴을 자동으로 찾아 번호를 붙이고, 사용자가 고른 얼굴만 모자이크한다.
모자이크된 이미지가 기존 워터마크 파이프라인으로 흘러간다.

### 범위 안

- 얼굴 자동 탐지 (사진 한 장 안에서)
- 얼굴마다 번호 + 미리보기 썸네일, 개별 체크
- **수동으로 박스 추가 / 삭제** — 선택 기능이 아니라 필수 (아래 "탐지 한계" 참조)
- 타원 모자이크
- 웹 앱과 데스크톱 앱 양쪽

### 범위 밖 (지금은)

- **사람을 기억해서 자동 선택** — 얼굴 특징값 저장, 사진 간 동일인 매칭.
  인식 모델 ~7MB가 더 필요하고 타인의 생체 데이터를 기기에 쌓게 된다.
  `face.js` 의 박스 표현에 자리는 비워두되 만들지 않는다.
- **대체 사진 합성** — 사용자가 보류. 나중에 생성형 AI로 저작권·초상권이
  깨끗한 인물 사진을 만들어 합성하는 방향으로 갈 예정. 지금은 얼굴 항목에
  `mode` 필드만 두고 `'mosaic'` 한 가지만 구현한다. 그 확장이 들어올 자리다.
- 동영상, 얼굴 외의 개인정보(번호판, 명찰, 문신)

## 기술 선택 — 브라우저 얼굴 탐지

브라우저 내장 `FaceDetector` 는 **없다.** 배포 사이트에서 확인했다
(`typeof FaceDetector === 'undefined'`, `BarcodeDetector` 만 존재).
OpenCV 5.0 도 번들 Haar cascade 를 뺐다. 어느 쪽이든 모델 파일을 저장소에 넣어야 한다.

| 후보 | 용량 | CSP | 판단 |
|---|---|---|---|
| **face-api.js TinyFaceDetector** | 664KB + 모델 193KB | 그대로 | **채택** |
| MediaPipe tasks-vision | 9.5MB wasm + 230KB | `wasm-unsafe-eval` 추가 | 11배 무겁고 CSP 완화 |
| OpenCV YuNet + onnxruntime-web | 232KB + 10MB wasm | `wasm-unsafe-eval` 추가 | 위와 같음 |

face-api.js 를 채택한다. 11배 가볍고 `script-src 'self'` 를 건드리지 않는다.

판본은 유지보수되는 포크 `@vladmandic/face-api` (MIT) 의 ESM 빌드를 우선하고,
문제가 있으면 원본 `face-api.js@0.22.2` UMD 로 내린다.
정확한 버전과 파일 sha256 을 `NOTICE` 에 적는다.

### 검증해야 할 위험 — 구현 첫 단계

face-api 가 품고 있는 tfjs 가 `script-src 'self'` 아래서 도는지 **먼저 확인한다.**
tfjs WebGL 백엔드는 셰이더를 컴파일할 뿐 JS `eval` 을 쓰지 않으므로 통과할 것으로
보지만, 확인 전에는 가정이다.

확인 항목:

- `script-src 'self'` 에서 로드·추론 성공 (CSP 위반 콘솔 오류 0건)
- 모델 `.bin` / `-weights_manifest.json` fetch — `connect-src 'self'` 로 허용됨
- Web Worker 를 쓰지 않을 것 — 현재 CSP 는 `default-src 'none'` 이라 워커가 막힌다

하나라도 실패하면 MediaPipe + `wasm-unsafe-eval` 로 선회하고, CSP 완화를
`vercel.json` 주석과 README 에 근거와 함께 남긴다.

### 탐지 한계 — 설계에 반영해야 하는 사실

TinyFaceDetector 는 **옆얼굴, 작은 얼굴, 가려진 얼굴을 놓친다.** 오탐도 낸다.

초상권 보호에서 놓침은 곧 실제 피해다. 따라서 자동 탐지를 **최종 결과로 쓰지 않는다.**
"자동으로 찾고 사람이 확인한다" 구조로 만든다:

- 탐지된 얼굴은 **기본 전부 체크** (안전한 쪽으로 기울인다)
- 수동 박스 추가와 삭제를 1급 기능으로 제공
- UI 에 한계를 명시한다 — "놓친 얼굴은 직접 끌어서 칠하세요"

## 아키텍처

### 파이프라인

```
사진 선택
  → 축소본(≤640px)에서 얼굴 탐지        [브라우저]
  → 사용자가 확인·수정                   [브라우저]
  → 플랫폼 크기로 축소                   [기존]
  → 얼굴 모자이크                        [신규]
  → 워터마크 · 서명 · 사이드카           [기존]
```

모자이크는 **워터마크보다 먼저** 들어간다. 순서가 반대면 모자이크가 그 영역의
워터마크를 부순다.

부수 효과로 얻는 것: 서명되는 `phash` 와 `sha256_original` 이 **가려진 이미지**
기준이 된다. 공개용 증명서인 사이드카에 원래 얼굴의 흔적이 남지 않는다.

### 좌표 표현 — 정규화

얼굴 박스는 **0~1 정규화 좌표**로 다룬다.

```
{ x, y, w, h, mode }     // 모두 0~1, 이미지 좌상단 기준
```

이유:

- 탐지는 640px 축소본에서, 적용은 플랫폼 크기에서 한다 — 해상도가 다르다
- 사용자가 얼굴을 고른 뒤 플랫폼을 바꿔도 좌표가 그대로 유효하다
- 파이썬과 자바스크립트가 같은 숫자를 주고받는다

### 모듈 경계

```
site/face.js          탐지 + 모자이크 (JS)
site/face-ui.js       선택 패널 — DOM, 캔버스 오버레이, 드래그
watermark.py          mask_faces()  — 모자이크 (numpy, 데스크톱용)
```

`face.js` 는 DOM 을 모르고, `face-ui.js` 는 모델을 모른다.

인터페이스:

```js
// site/face.js
export async function loadDetector()          // 모델을 한 번만 읽는다. 두 번째 호출은 캐시
export async function detectFaces(bitmap)     // → [{x,y,w,h,score}]  정규화 좌표
                                              //   내부에서 긴 변 640px 로 줄여 추론한다
export function maskFaces(ctx, w, h, faces)   // 캔버스를 제자리에서 고친다

// site/face-ui.js
export function createFacePanel(hostEl) → {
  async show(bitmap),   // 탐지 후 패널을 그린다. 얼굴 0개면 안내만 남기고 접는다
  selected(),           // → [{x,y,w,h,mode}]  체크된 것만
  clear(),
}
```

```python
# watermark.py
def mask_faces(img, faces):   # img: BGR ndarray, faces: [{"x","y","w","h","mode"}]
```

### 웹과 데스크톱을 한 벌로

데스크톱 UI 도 결국 브라우저에서 돈다. 탐지 JS 와 선택 패널을 **양쪽이 같은
파일로 쓴다.** `server.py` 가 `site/` 를 `/lib` 로 한 번 더 서빙하면 복사본이 없다.

모자이크를 실제로 칠하는 곳만 다르다:

- **웹**: `protect()` 안에서 `maskFaces()` — 캔버스 위에서 바로
- **데스크톱**: 정규화 박스를 폼 필드로 서버에 보내고 `wm.mask_faces()` 가 칠한다

> 설계 검토 중 변경: 처음에는 "브라우저가 칠해서 PNG 로 업로드" 로 잡았으나,
> 좌표를 정규화하고 나니 **박스 좌표만 보내는 쪽**이 더 단순하다. 이미지 업로드
> 경로가 그대로고, PNG 재인코딩이 사라지고, 파이썬 쪽 추가분이 `mask_faces()`
> 15줄로 끝난다. 화질도 이쪽이 낫다.

### 데이터 흐름

**웹앱**

```
app.js  파일 선택
        → facePanel.show(bitmap)
        → 사용자 확인
        → protect(file, platform, { faces: panel.selected(), ... })
              fitCanvas → maskFaces → embedTag → embedMessage → JPEG → 서명
```

`protect()` 시그니처에 `faces` 옵션 하나가 는다. 캔버스를 주고받지 않는다.

**데스크톱**

```
static/index.html  파일 선택
                   → /lib/face-ui.js 의 패널로 확인
                   → POST /api/protect  (원본 파일 + faces=<JSON>)
server.py          faces 를 파싱해 wm.protect 로 넘김
watermark.py       resize → mask_faces → 기존 그대로
```

`/api/protect` 에 `faces: str = Form("")` 하나가 는다. 원본 파일 업로드 경로는 그대로다.

## 모자이크 방식

박스 하나를 칠하는 절차. 두 구현이 같은 결과를 내도록 여기서 못박는다.

1. 정규화 박스를 **1.3배로 확장** (중심 고정) — 탐지 박스는 턱과 머리카락을 자른다
2. 픽셀 좌표로 바꾸고 이미지 경계에서 자른다
3. 블록 크기 `b = max(8, round(폭 / 8))`
4. 사각 영역을 블록 단위 평균으로 픽셀화 — 가장자리 블록은 남은 만큼만
5. 사각형에 내접하는 **타원 안쪽만** 픽셀화 결과로 덮는다. 바깥은 원본 그대로

블록을 굵게(얼굴 폭의 1/8) 잡는 이유는 약한 픽셀화가 ML 복원에 취약하기 때문이다.
흐림(blur)은 쓰지 않는다 — 복원 가능성이 픽셀화보다 높다.

`mode` 는 지금 `'mosaic'` 하나뿐이다. 분기 지점만 만들어 두고 다른 값은 넣지 않는다.

## 오류 처리

| 상황 | 동작 |
|---|---|
| 모델 로드 실패 (오프라인 첫 방문, CSP 차단) | 패널에 오류를 적고 **수동 박스 그리기만** 남긴다. 보호하기는 계속 쓸 수 있다 |
| 얼굴 0개 탐지 | "얼굴을 못 찾았습니다 — 가릴 곳이 있으면 직접 끌어서 칠하세요" |
| WebGL 없음 | tfjs 가 CPU 백엔드로 내려간다. 느릴 뿐 동작한다. 3초 넘으면 진행 표시 |
| 박스가 이미지 밖 | 경계에서 자른다. 잘라서 넓이가 0이면 버린다 |
| `faces` JSON 이 깨짐 (데스크톱) | 400. 조용히 무시하지 않는다 — 가려질 줄 알았던 얼굴이 그냥 올라가면 안 된다 |

마지막 항목이 이 기능의 안전 원칙이다: **가리기가 실패하면 소리 없이 넘어가지 않는다.**

## 테스트

`test_face_mask.py` 하나. assert 기반, 프레임워크 없음.

1. **가려진다** — 타원 안쪽의 고주파 에너지(라플라시안 분산)가 원본의 5% 미만
2. **바깥은 안 변한다** — 확장 사각형 밖 픽셀이 한 개도 다르지 않음
3. **경계** — 이미지 모서리에 걸친 박스, 이미지보다 큰 박스, 넓이 0 박스
4. **워터마크가 산다** — 모자이크된 이미지에 워터마크를 심고 다시 읽어 48/48

JS 쪽은 `site/face.js` 의 `maskFaces` 가 같은 규칙을 따르는지 브라우저에서
한 번 확인한다 (동일 입력에 대해 파이썬 결과와 픽셀 비교, 반올림 오차 ±1 허용).

## 파일

```
신규  site/face.js                      탐지 + 모자이크
신규  site/face-ui.js                   선택 패널
신규  site/vendor/face-api.min.js       ~664KB, MIT
신규  site/vendor/models/               tiny_face_detector ~196KB, MIT
신규  test_face_mask.py
수정  site/app.js                       패널을 보호하기 흐름에 끼움
수정  site/app-core.js                  protect() 에 faces 옵션
수정  site/index.html                   패널 자리, 한계 안내
수정  static/index.html                 같은 패널 사용
수정  server.py                         /lib 로 site/ 서빙, /api/protect 에 faces
수정  watermark.py                      mask_faces()
수정  NOTICE                            face-api.js 저작권·라이선스
수정  README.md                         영문 먼저, 국문 아래
```

## 남는 위험

- **놓친 얼굴.** 탐지는 완벽하지 않고 완벽해질 수 없다. UI 문구와 수동 도구로
  덜어내되, README 의 "이 도구가 못 하는 것" 에도 적는다.
- **저장소 용량 +860KB.** 지금 저장소 대비 큰 증가다. 웹앱 첫 방문이 그만큼 느려진다.
  모델은 얼굴 패널을 쓸 때 처음 불러온다 (지연 로딩).
- **face-api.js 원본은 2020년 이후 정지.** 유지보수 포크를 쓰되, 버전을 고정하고
  파일 해시를 `NOTICE` 에 남겨 조용한 교체를 막는다.
