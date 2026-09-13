# 얼굴 가리기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사진에서 얼굴을 찾아 사용자가 고른 것만 되돌릴 수 없게 가리고, 그 결과가 기존 워터마크 파이프라인으로 흘러가게 한다.

**Architecture:** 탐지는 브라우저에서(face-api.js TinyFaceDetector + 68점 랜드마크), 얼굴 박스는 0~1 정규화 좌표로 다룬다. 실제로 칠하는 일은 웹앱은 캔버스에서, 데스크톱은 파이썬에서 하되 같은 규칙을 따른다. 가리기는 워터마크와 지각 해시보다 **먼저** 들어간다.

**Tech Stack:** face-api.js(MIT, 반입) · OpenCV 5 + numpy(기존) · WebCrypto/`secrets` 난수 · 순수 ES 모듈, 빌드 도구 없음

**Spec:** `docs/superpowers/specs/2026-09-13-face-masking-design.md`

## Global Constraints

- **사진은 기기를 벗어나지 않는다.** 웹앱은 네트워크 호출 자체가 없고, 데스크톱은 127.0.0.1 뿐이다. 모델 파일은 같은 출처에서만 받는다 — CDN 금지.
- **CSP 를 바꾸지 않는다.** `script-src 'self'`, `connect-src 'self'`, `default-src 'none'` 유지. Web Worker 금지(`default-src 'none'` 이 막는다). Task 1 에서 못 지키면 즉시 중단하고 보고한다.
- **얼굴 좌표는 0~1 정규화.** 필드는 `{x, y, w, h, poly?, mode, grow}` — 파이썬과 자바스크립트가 같은 숫자를 쓴다.
- **기본 강도는 `"solid"`.** `"mosaic"` 은 사용자가 명시적으로 고를 때만.
- **경계에 페더링 없음.** 부드러운 경계는 그 폭만큼 원본을 흘린다. 캔버스 안티에일리어싱은 알파 128 에서 자른다.
- **난수는 암호학적으로.** 파이썬 `secrets`, JS `crypto.getRandomValues`. `Math.random` / `np.random` 기본 시드 금지 (테스트에서 시드를 주입할 때만 예외).
- **가리기 실패는 조용히 넘어가지 않는다.** `faces` 파싱 실패는 400. 가려질 줄 알았던 얼굴이 그냥 발행되는 것이 최악이다.
- 공통 상수는 두 언어에 같은 값으로 둔다: `MOSAIC_BLOCKS=4`, `MOSAIC_LEVELS=16`, `MOSAIC_JITTER=12`, `SOLID_LEVELS=16`, `SOLID_NOISE=6`, `GROW_POLY=1.08`, `GROW_BOX=1.25`, `GROW_MANUAL=1.0`, `DETECT_SIDE=640`.
- 파이썬 실행은 항상 `.venv/bin/python`. 테스트는 프레임워크 없이 `assert` 로.
- 주석과 UI 문구는 한국어. README 는 영문이 위, 국문이 아래.

---

### Task 1: 라이브러리 반입과 CSP 검증

이 과제가 실패하면 접근 자체를 바꿔야 하므로 맨 앞에 둔다. **CSP 위반이 하나라도 나오면 진행하지 말고 보고한다.**

**Files:**
- Create: `site/vendor/face-api.esm.js`
- Create: `site/vendor/models/tiny_face_detector_model-weights_manifest.json`
- Create: `site/vendor/models/tiny_face_detector_model.bin`
- Create: `site/vendor/models/face_landmark_68_tiny_model-weights_manifest.json`
- Create: `site/vendor/models/face_landmark_68_tiny_model.bin`
- Create: `site/vendor/README.md`
- Modify: `NOTICE`
- Temporary (deleted in Step 5): `site/_csp-check.html`, `site/_csp-check.js`

- [ ] **Step 1: 파일을 받아 반입한다**

`@vladmandic/face-api` (MIT, face-api.js 의 유지보수 포크) 를 쓴다. 저장소의 `master`
브랜치에서 그대로 받는다 — 릴리스 태그로 고정하지 않으므로 버전 번호가 아니라
Step 2 에서 남기는 파일 해시가 조용한 교체를 잡아내는 유일한 보증이다.

```bash
mkdir -p site/vendor/models
cd site/vendor
curl -fsSL -o face-api.esm.js \
  https://raw.githubusercontent.com/vladmandic/face-api/master/dist/face-api.esm.js
cd models
for f in tiny_face_detector_model-weights_manifest.json tiny_face_detector_model.bin \
         face_landmark_68_tiny_model-weights_manifest.json face_landmark_68_tiny_model.bin; do
  curl -fsSL -o "$f" "https://raw.githubusercontent.com/vladmandic/face-api/master/model/$f"
done
cd ../../..
ls -la site/vendor site/vendor/models
```

받은 파일이 HTML 오류 페이지가 아닌지 확인한다:

```bash
file site/vendor/face-api.esm.js site/vendor/models/*
head -c 200 site/vendor/face-api.esm.js
```

- [ ] **Step 2: 해시를 기록한다**

조용한 교체를 막기 위해 버전과 sha256 을 남긴다.

```bash
shasum -a 256 site/vendor/face-api.esm.js site/vendor/models/*
```

`NOTICE` 끝에 추가한다 (기존 ShieldMnt 항목 형식을 따른다):

```
face-api.js — 얼굴 탐지
  site/vendor/ 아래 파일들은 @vladmandic/face-api 저장소의 master 브랜치에서 가져왔다
  (릴리스 태그로 고정한 것이 아니다).
  https://github.com/vladmandic/face-api  (MIT)
  원본: face-api.js by Vincent Mühler  https://github.com/justadudewhohacks/face-api.js  (MIT)
  모델 가중치도 같은 저장소·같은 라이선스다.

  태그가 아니라서 버전 번호로는 다시 받아도 같은 내용이 온다는 보장이 없다. 대신
  파일 해시를 여기 남긴다 — 조용히 바뀌면 버전 번호가 아니라 이 해시로 알아챈다.
  <여기에 Step 2 의 shasum 출력을 붙인다>
```

`site/vendor/README.md`:

```markdown
# 반입한 것

여기 파일은 우리가 쓴 코드가 아니다. 출처·버전·라이선스·해시는 저장소 루트의 `NOTICE` 에 있다.

빌드 도구를 쓰지 않으므로 파일을 그대로 둔다. 갱신할 때는 `NOTICE` 의 버전과 해시도 같이 고친다.

CDN 에서 불러오지 않는다. 사진이 있는 페이지가 제3자 스크립트를 부르면 안 되고,
CSP 도 `script-src 'self'` 라 애초에 막힌다.
```

- [ ] **Step 3: 진짜 CSP 아래서 도는지 확인하는 임시 페이지**

`site/_csp-check.html` 과 `site/_csp-check.js` 를 만든다. **검증이 끝나면 둘 다 지운다 — 저장소에 남기지 않는다.**

검증 로직을 **별도 파일로 뺀다.** `script-src 'self'` 는 인라인 스크립트를 종류 불문
전부 막으므로(module 이어도 예외 없음), 인라인으로 두면 라이브러리를 시험하기도 전에
페이지가 죽어 아무 신호도 못 얻는다. 실제 사이트도 항상 외부 스크립트를 쓴다.

```html
<!doctype html><meta charset="utf-8"><title>CSP check</title>
<pre id="out">시작…</pre>
<script type="module" src="./_csp-check.js"></script>
```

`site/_csp-check.js`:

```js
const out = document.getElementById('out');
const log = (s) => { out.textContent += '\n' + s; };
window.onerror = (e) => log('❌ onerror: ' + e);
document.addEventListener('securitypolicyviolation',
  (e) => log('❌ CSP 위반: ' + e.violatedDirective + ' ← ' + e.blockedURI));
try {
  const fa = await import('./vendor/face-api.esm.js');
  log('✅ 모듈 로드');
  await fa.nets.tinyFaceDetector.loadFromUri('./vendor/models');
  await fa.nets.faceLandmark68TinyNet.loadFromUri('./vendor/models');
  log('✅ 모델 로드');
  log('   백엔드: ' + fa.tf.getBackend());
  // 얼굴이 없는 이미지라도, 추론이 끝까지 도는지가 목적이다
  const c = document.createElement('canvas');
  c.width = 416; c.height = 416;
  const cx = c.getContext('2d');
  cx.fillStyle = '#c09070'; cx.fillRect(0, 0, 416, 416);
  const t0 = performance.now();
  const r = await fa.detectAllFaces(c, new fa.TinyFaceDetectorOptions())
                  .withFaceLandmarks(true);
  log(`✅ 추론 완료 — ${r.length}개, ${Math.round(performance.now() - t0)}ms`);
  log('워커 사용 여부는 개발자도구 Network 의 Type=script 항목으로 확인');
} catch (e) { log('❌ ' + (e && e.stack || e)); }
```

- [ ] **Step 4: 실제 CSP 헤더를 붙여 띄우고 확인**

`vercel.json` 과 같은 헤더를 주는 임시 서버로 연다 (프로젝트에 남기지 않는다):

```bash
.venv/bin/python - <<'PY'
import http.server, functools
CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()
http.server.ThreadingHTTPServer(("127.0.0.1", 8777),
    functools.partial(H, directory="site")).serve_forever()
PY
```

브라우저로 `http://127.0.0.1:8777/_csp-check.html` 을 연다.

통과 기준 — **넷 다** 충족해야 한다:

1. `✅ 모듈 로드`, `✅ 모델 로드`, `✅ 추론 완료` 가 모두 찍힌다
2. `❌ CSP 위반` 이 한 줄도 없다
3. 콘솔에 CSP 관련 오류가 없다
4. Network 탭에 Worker 요청이 없다

**하나라도 실패하면 여기서 멈추고 보고한다.** 대안은 MediaPipe + `script-src` 에 `'wasm-unsafe-eval'` 추가인데, CSP 완화라 사용자 판단이 필요하다.

- [ ] **Step 5: 임시 파일을 지우고 커밋**

```bash
rm site/_csp-check.html site/_csp-check.js
git add site/vendor NOTICE
git commit -m "얼굴 탐지 라이브러리 반입 — CSP 를 건드리지 않고 도는 것을 확인했다"
```

---

### Task 2: `mask_faces()` — 파이썬 기준 구현

파이썬을 기준 구현으로 삼는다. 테스트하기 쉽고, 자바스크립트 쪽은 이걸 따라 맞춘다.

**Files:**
- Modify: `watermark.py` (상수는 `JPEG_Q` 근처, 함수는 `_fit` 다음)
- Test: `test_face_mask.py` (신규)

**Interfaces:**
- Consumes: 없음
- Produces:
  - `wm.mask_faces(img: np.ndarray, faces: list[dict], rng=None) -> np.ndarray` — BGR 이미지를 제자리에서 고치고 같은 배열을 돌려준다
  - `wm.MASK_SOLID = "solid"`, `wm.MASK_MOSAIC = "mosaic"`
  - 얼굴 dict: `{"x","y","w","h"}` 필수(0~1), `"poly"` 선택(`[[x,y],…]` 0~1), `"mode"` 선택(기본 `"solid"`), `"grow"` 선택(기본 `1.0`)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_face_mask.py` 를 새로 만든다:

```python
"""얼굴 가리기 자체 점검.  실행:  .venv/bin/python test_face_mask.py"""
import numpy as np

import watermark as wm


def photo(w=800, h=600, seed=1):
    """사진 비슷한 테스트 이미지."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.stack([128 + 90 * np.sin(xx / 90),
                    128 + 80 * np.cos(yy / 70),
                    128 + 60 * np.sin((xx + yy) / 110)], -1)
    img += rng.normal(0, 12, (h, w, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def test_outside_untouched():
    """가린 영역 밖은 한 화소도 변하지 않는다.

    사진의 나머지를 침해하지 않는다는 요구가 지켜지는지 보는 시험이다.
    """
    src = photo()
    face = {"x": .30, "y": .25, "w": .20, "h": .28, "grow": 1.0}
    out = wm.mask_faces(src.copy(), [face])

    # 가린 자리를 넉넉히 감싸는 사각형. 이 밖은 원본과 완전히 같아야 한다.
    h, w = src.shape[:2]
    x0, x1 = int(face["x"] * w) - 2, int((face["x"] + face["w"]) * w) + 2
    y0, y1 = int(face["y"] * h) - 2, int((face["y"] + face["h"]) * h) + 2
    outside = np.ones((h, w), bool)
    outside[y0:y1, x0:x1] = False
    assert np.array_equal(out[outside], src[outside]), "가린 영역 밖이 변했다"
    # 안쪽은 실제로 변했어야 한다
    assert not np.array_equal(out[y0:y1, x0:x1], src[y0:y1, x0:x1]), "가려지지 않았다"


def test_hard_edge():
    """경계가 부드럽지 않다. 페더링은 그 폭만큼 원본을 흘린다."""
    src = np.full((400, 400, 3), 200, np.uint8)
    src[130:270, 130:270] = 20                    # 타원 대부분을 채우는 어두운 덩어리
    #  중앙값이 어느 쪽인지 애매하면 안 된다 — 어두운 쪽이 확실히 우세하게 둔다
    face = {"x": .30, "y": .30, "w": .40, "h": .40, "grow": 1.0}
    out = wm.mask_faces(src.copy(), [face], rng=np.random.default_rng(0))
    # 가린 색과 바깥 색 사이의 중간값이 넓게 깔리면 페더링이 있다는 뜻이다
    mid = ((out > 60) & (out < 180)).sum()
    assert mid < out.size * 0.02, f"경계가 번졌다 — 중간값 화소 {mid}개"


def test_edges_and_degenerate():
    """모서리에 걸친 박스, 이미지보다 큰 박스, 넓이 0, 얼굴 없음."""
    src = photo(400, 300)
    cases = [
        {"x": -0.2, "y": -0.2, "w": 0.3, "h": 0.3},      # 좌상단 밖으로
        {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5},        # 우하단 밖으로
        {"x": -1.0, "y": -1.0, "w": 3.0, "h": 3.0},      # 이미지보다 큼
        {"x": 0.5, "y": 0.5, "w": 0.0, "h": 0.0},        # 넓이 0
        {"x": 5.0, "y": 5.0, "w": 0.1, "h": 0.1},        # 완전히 밖
    ]
    for f in cases:
        out = wm.mask_faces(src.copy(), [dict(f, grow=1.0)])
        assert out.shape == src.shape, f
        assert out.dtype == np.uint8, f
    assert np.array_equal(wm.mask_faces(src.copy(), []), src), "얼굴이 없으면 그대로"


def test_polygon_covers_less_than_ellipse():
    """폴리곤이 타원보다 적게 지운다 — 배경 침해가 줄어든다."""
    src = photo()
    box = {"x": .30, "y": .25, "w": .20, "h": .28}
    # 박스에 내접하는 마름모꼴 폴리곤(윤곽 대용)
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    poly = [[cx, box["y"]], [box["x"] + box["w"], cy],
            [cx, box["y"] + box["h"]], [box["x"], cy]]

    def changed(face):
        out = wm.mask_faces(src.copy(), [face])
        return int((out != src).any(-1).sum())

    n_ellipse = changed(dict(box, grow=1.0))
    n_poly = changed(dict(box, poly=poly, grow=1.0))
    assert 0 < n_poly < n_ellipse, f"폴리곤 {n_poly}, 타원 {n_ellipse}"


def test_nondeterministic():
    """같은 입력을 두 번 가리면 결과가 다르다.

    난수가 실제로 들어갔다는 뜻이고, 결정론적 역산의 전제를 깬다.
    """
    src = photo()
    face = {"x": .3, "y": .3, "w": .3, "h": .3, "grow": 1.0}
    for mode in (wm.MASK_SOLID, wm.MASK_MOSAIC):
        a = wm.mask_faces(src.copy(), [dict(face, mode=mode)])
        b = wm.mask_faces(src.copy(), [dict(face, mode=mode)])
        assert not np.array_equal(a, b), f"{mode}: 두 번 돌려도 결과가 같다"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in TESTS:
        t()
        print("  ✓", t.__name__)
    print(f"{len(TESTS)}개 통과")
```

- [ ] **Step 2: 실패를 확인한다**

```bash
.venv/bin/python test_face_mask.py
```

기대: `AttributeError: module 'watermark' has no attribute 'mask_faces'`

- [ ] **Step 3: `watermark.py` 에 상수를 넣는다**

`JPEG_Q = 95` 바로 아래에 붙인다:

```python
# ── 얼굴 가리기 ────────────────────────────────────────────────────
# 되돌릴 수 없게 만드는 것은 알고리즘의 복잡성이 아니라 "남기는 정보량"이다.
# 픽셀화는 결정론적 선형 연산이라 블록 평균이 곧 알려진 측정값이 되고,
# 얼굴 초해상도 모델은 그 측정값에 맞는 얼굴을 찾아낸다. 블록이 많을수록 잘 맞는다.
# 그래서 기본은 단색이다 — 출력이 양자화된 색 세 개를 통해서만 원본에 의존하므로
# 복원할 것이 남지 않는다. 휴리스틱이 아니라 정보이론이다.
MASK_SOLID = "solid"      # 기본
MASK_MOSAIC = "mosaic"    # 모자이크 모양을 원할 때. 강하지만 보장은 아니다

SOLID_LEVELS = 16         # 채우는 색의 양자화 단계
SOLID_NOISE = 6           # 평평한 색면이 JPEG 에서 띠를 만들지 않게 얹는 잡음
MOSAIC_BLOCKS = 4         # 얼굴 폭을 넷으로. 8 로 나누면 64표본이 남아 복원에 충분하다
MOSAIC_LEVELS = 16
MOSAIC_JITTER = 12        # 양자화 폭과 맞먹는 난수. 블록 평균 = 원본 평균이라는 전제를 깬다

GROW_POLY = 1.08          # 윤곽 폴리곤은 조금만 넓힌다
GROW_BOX = 1.25           # 랜드마크가 없어 타원을 쓸 때
GROW_MANUAL = 1.0         # 사용자가 직접 그린 박스는 그대로. 의도를 넓히지 않는다
# 이 값들은 site/face.js 에도 같은 값으로 있다. 한쪽만 고치지 말 것.
```

`import` 목록에 `secrets` 를 추가한다.

- [ ] **Step 4: 구현을 넣는다**

`_fit` 함수 바로 다음에 붙인다:

```python
def _mask_region(f: dict, w: int, h: int):
    """가릴 영역의 불리언 마스크와 그 경계 상자. 없으면 (None, None)."""
    grow = float(f.get("grow", 1.0))
    canvas = np.zeros((h, w), np.uint8)
    poly = f.get("poly")
    if poly:
        pts = np.asarray(poly, np.float64)
        if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3:
            return None, None
        centre = pts.mean(0)
        pts = (centre + (pts - centre) * grow) * [w, h]
        hull = cv2.convexHull(np.rint(pts).astype(np.int32))
        cv2.fillConvexPoly(canvas, hull, 1)
    else:
        cx = (float(f["x"]) + float(f["w"]) / 2) * w
        cy = (float(f["y"]) + float(f["h"]) / 2) * h
        ax = float(f["w"]) * w * grow / 2
        ay = float(f["h"]) * h * grow / 2
        if ax < 0.5 or ay < 0.5:
            return None, None
        cv2.ellipse(canvas, (int(round(cx)), int(round(cy))),
                    (int(round(ax)), int(round(ay))), 0, 0, 360, 1, -1)
    ys, xs = np.nonzero(canvas)
    if len(xs) == 0:
        return None, None
    return canvas.astype(bool), (int(xs.min()), int(ys.min()),
                                 int(xs.max()) + 1, int(ys.max()) + 1)


def _fill_solid(patch: np.ndarray, inside: np.ndarray, rng) -> np.ndarray:
    """영역을 그 영역의 중앙값 색으로 채운다.

    평균이 아니라 중앙값을 쓴다 — 배경이 조금 섞여도 색이 끌려가지 않는다.
    출력은 양자화된 색 세 개를 통해서만 원본에 의존한다.
    """
    med = np.median(patch[inside].astype(np.float64), axis=0)
    step = 256.0 / SOLID_LEVELS
    base = np.clip(np.floor(med / step) * step + step / 2, 0, 255)
    noise = rng.integers(-SOLID_NOISE, SOLID_NOISE + 1, patch.shape)
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def _fill_mosaic(patch: np.ndarray, rng) -> np.ndarray:
    """굵은 블록으로 픽셀화하고 블록마다 난수를 더한다."""
    ph, pw = patch.shape[:2]
    b = max(8, int(round(pw / MOSAIC_BLOCKS)))
    sw, sh = max(1, -(-pw // b)), max(1, -(-ph // b))
    small = cv2.resize(patch, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float64)
    step = 256.0 / MOSAIC_LEVELS
    small = np.floor(small / step) * step + step / 2
    small += rng.integers(-MOSAIC_JITTER, MOSAIC_JITTER + 1, small.shape)
    small = np.clip(small, 0, 255).astype(np.uint8)
    return cv2.resize(small, (pw, ph), interpolation=cv2.INTER_NEAREST)


def mask_faces(img: np.ndarray, faces: list[dict], rng=None) -> np.ndarray:
    """얼굴 영역을 되돌릴 수 없게 지운다. img(BGR)를 제자리에서 고치고 돌려준다.

    faces 의 좌표는 0~1 정규화다. 640px 축소본에서 찾은 것을 여기서 칠할 수 있고,
    자바스크립트 쪽과 같은 숫자를 주고받는다.

    난수는 저장하지 않는다. 같은 사진을 두 번 처리하면 다른 결과가 나온다.
    """
    if not faces:
        return img
    h, w = img.shape[:2]
    if rng is None:
        rng = np.random.default_rng(secrets.randbits(128))
    for f in faces:
        inside, box = _mask_region(f, w, h)
        if inside is None:
            continue
        x0, y0, x1, y1 = box
        patch = img[y0:y1, x0:x1]
        sub = inside[y0:y1, x0:x1]
        if not sub.any():
            continue
        if f.get("mode", MASK_SOLID) == MASK_MOSAIC:
            filled = _fill_mosaic(patch, rng)
        else:
            filled = _fill_solid(patch, sub, rng)
        patch[sub] = filled[sub]
    return img
```

- [ ] **Step 5: 통과를 확인한다**

```bash
.venv/bin/python test_face_mask.py
```

기대: `5개 통과`

기존 테스트가 깨지지 않았는지도 본다:

```bash
.venv/bin/python test_watermark.py
```

- [ ] **Step 6: 커밋**

```bash
git add watermark.py test_face_mask.py
git commit -m "얼굴 영역을 되돌릴 수 없게 지우는 mask_faces

기본은 단색 채움이다. 출력이 양자화된 색 세 개를 통해서만 원본에 의존하므로
복원할 정보가 남지 않는다. 좌표는 0~1 정규화라 640px 축소본에서 찾아
플랫폼 크기에서 칠할 수 있고 자바스크립트와 같은 숫자를 쓴다."
```

---

### Task 3: 얼마나 잘 가려지는지 재고, 모자이크 지터를 맞춘다

주장하지 말고 수치를 낸다. 이 과제의 산출물은 **측정 결과와 확정된 상수**다.

**Files:**
- Modify: `test_face_mask.py`
- Modify: `watermark.py` (측정 결과에 따라 상수만)

**Interfaces:**
- Consumes: `wm.mask_faces`, `wm.MASK_SOLID`, `wm.MASK_MOSAIC`, `wm.MOSAIC_JITTER`, `wm.MOSAIC_BLOCKS`
- Produces: 없음 (측정 전용)

- [ ] **Step 1: 정보이론 시험을 쓴다**

`test_face_mask.py` 의 `TESTS = ...` 줄 **위에** 붙인다:

```python
def test_solid_carries_only_the_median():
    """내용이 전혀 다른 두 이미지가, 중앙값만 같으면 똑같은 결과를 낸다.

    출력이 중앙값 외의 어떤 정보도 담지 않는다는 직접 증거다.
    복원할 것이 남지 않았다는 말의 의미가 이것이다.
    """
    rng_a = np.random.default_rng(7)
    a = np.full((200, 200, 3), 100, np.uint8)
    a[::2] = 140                                   # 줄무늬
    b = np.full((200, 200, 3), 100, np.uint8)
    b[:, ::2] = 140                                # 세로 줄무늬 — 구조가 완전히 다르다
    assert not np.array_equal(a, b)
    assert np.array_equal(np.median(a.reshape(-1, 3), 0),
                          np.median(b.reshape(-1, 3), 0)), "시험 전제: 중앙값이 같아야 한다"

    # 타원이 아니라 정사각 폴리곤으로 전체를 덮는다. 코너가 남으면 그쪽 원본이
    # 그대로 보여 "출력이 중앙값만 담는다"는 시험 자체가 성립하지 않는다.
    face = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "mode": wm.MASK_SOLID, "grow": 1.0,
            "poly": [[0, 0], [1, 0], [1, 1], [0, 1]]}
    out_a = wm.mask_faces(a.copy(), [face], rng=np.random.default_rng(7))
    out_b = wm.mask_faces(b.copy(), [face], rng=np.random.default_rng(7))

    inside, box = wm._mask_region(face, 200, 200)
    x0, y0, x1, y1 = box
    sub = inside[y0:y1, x0:x1]
    assert np.array_equal(out_a[y0:y1, x0:x1][sub], out_b[y0:y1, x0:x1][sub]), \
        "구조가 다른데 결과가 다르다 — 출력이 중앙값 외의 정보를 담고 있다"


def _skin_crops(n, size, rng):
    """피부톤은 같고 구조만 다른 크롭 n 개.

    색으로는 구별할 수 없게 만들었다. 맞히려면 얼굴 구조에 해당하는 정보가
    출력에 남아 있어야 한다. 6x6 저주파 구조는 4x4 모자이크가 잡아낼 수 있는
    바로 그 크기라 일부러 불리하게 고른 조건이다.
    """
    base = np.array([120.0, 150.0, 200.0])         # BGR 피부톤
    crops = []
    for _ in range(n):
        low = rng.normal(0, 25, (6, 6, 3))
        big = cv2.resize(low, (size, size), interpolation=cv2.INTER_CUBIC)
        crops.append(np.clip(base + big, 0, 255).astype(np.uint8))
    return crops


def _reid_top1(mode, n=200, size=96, seed=3):
    """가려진 출력만 보고 원본을 맞히는 공격의 top-1 정확도."""
    rng = np.random.default_rng(seed)
    crops = _skin_crops(n, size, rng)
    # 타원을 쓰면 네 귀퉁이가 안 가려진 채 남아 공격자가 그것만 보고 맞힌다.
    # 가리기 자체의 강도를 재는 시험이므로 영역 전체를 덮는다.
    face = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "mode": mode, "grow": 1.0,
            "poly": [[0, 0], [1, 0], [1, 1], [0, 1]]}
    masked = [wm.mask_faces(c.copy(), [face]) for c in crops]

    # 공격자는 모자이크가 남기는 바로 그 해상도에서 비교한다 — 가장 유리한 조건.
    def desc(im):
        return cv2.resize(im, (wm.MOSAIC_BLOCKS, wm.MOSAIC_BLOCKS),
                          interpolation=cv2.INTER_AREA).astype(np.float64).ravel()

    bank = np.array([desc(c) for c in crops])
    hits = sum(int(np.argmin(((bank - desc(m)) ** 2).sum(1))) == i
               for i, m in enumerate(masked))
    return hits / n


def test_reidentification_is_chance_level():
    """가려진 출력에서 원본을 골라낼 수 없다.

    후보 200개 중 하나를 고르는 문제라 우연은 0.5%다.
    """
    chance = 1 / 200
    solid = _reid_top1(wm.MASK_SOLID)
    assert solid <= 0.02, f"단색인데 재식별이 된다: {solid:.1%}"

    mosaic = _reid_top1(wm.MASK_MOSAIC)
    print(f"    재식별 top-1 — 단색 {solid:.1%}, 모자이크 {mosaic:.1%} (우연 {chance:.1%})")
    assert mosaic <= 0.05, (
        f"모자이크 재식별 {mosaic:.1%} — 너무 높다. "
        f"MOSAIC_JITTER 를 4씩 올려 24까지, 그래도 안 되면 MOSAIC_BLOCKS 를 3으로")


def test_remaining_samples_are_counted():
    """가린 영역에 남은 독립 표본 수를 세어 기록한다.

    복원 가능성은 이 숫자가 결정한다. 눈에 보이게 남겨둔다.
    """
    size = 96
    solid_samples = 3                                   # 양자화된 색 세 개
    blocks = -(-size // max(8, round(size / wm.MOSAIC_BLOCKS)))
    mosaic_samples = blocks * blocks * 3
    print(f"    남은 표본 — 단색 {solid_samples}개, 모자이크 {mosaic_samples}개")
    assert solid_samples == 3
    assert mosaic_samples <= 60, f"모자이크 표본 {mosaic_samples}개는 너무 많다"
```

- [ ] **Step 2: 돌려서 수치를 본다**

```bash
.venv/bin/python test_face_mask.py
```

- [ ] **Step 3: 실패하면 상수를 올린다**

`test_reidentification_is_chance_level` 이 실패하면 `watermark.py` 의 상수를 이 순서로 고치고 매번 다시 돌린다:

1. `MOSAIC_JITTER` 를 12 → 16 → 20 → 24
2. 24 에서도 실패하면 `MOSAIC_BLOCKS` 를 4 → 3
3. 그래도 실패하면 **멈추고 보고한다** — 모자이크 강도를 UI 에서 빼야 할 수 있다

`site/face.js` 는 아직 없다. 최종 값은 Task 5 에서 그대로 옮긴다.

- [ ] **Step 4: 측정 결과를 스펙에 기록한다**

`docs/superpowers/specs/2026-09-13-face-masking-design.md` 의 `### 얼마나 잘 가려지는지 — 측정` 절 끝에 실제 숫자를 붙인다:

```markdown
**측정 결과** (2026-09-13, `test_face_mask.py`)

| | 재식별 top-1 | 남은 표본 |
|---|---|---|
| 단색 (기본) | <실측>% | 3 |
| 모자이크 | <실측>% | <실측>개 |

우연 수준은 0.5% (후보 200개).
```

- [ ] **Step 5: 커밋**

```bash
git add test_face_mask.py watermark.py docs/superpowers/specs/2026-09-13-face-masking-design.md
git commit -m "가리기 강도를 재고 모자이크 지터를 확정

주장 대신 수치를 낸다. 단색은 구조가 다른 두 입력이 중앙값만 같으면 같은
결과를 내는 것으로 정보가 남지 않음을 직접 보였고, 모자이크는 피부톤이 같고
구조만 다른 크롭 200개로 재식별을 시도해 지터 폭을 정했다."
```

---

### Task 4: 파이썬 파이프라인 통합

가리기를 `protect()` 에 끼우고, 스펙이 짚은 통합 위험 셋을 함께 고친다.

**Files:**
- Modify: `watermark.py:273-360` (`protect`)
- Modify: `test_face_mask.py`

**Interfaces:**
- Consumes: `wm.mask_faces`
- Produces: `wm.protect(image_bytes, platform, key=None, message="", overwrite=False, no_ai=True, faces=None)` — `faces` 는 정규화 얼굴 dict 목록. 사이드카 claim 에 `faces_masked: int` 가 생기고, 얼굴을 하나라도 가리면 `sha256_original` 은 `None` 이 된다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_face_mask.py` 의 `TESTS = ...` 줄 위에 붙인다:

```python
def test_protect_masks_before_watermark():
    """가리기가 워터마크보다 먼저 들어가고, 워터마크는 그대로 살아남는다."""
    import test_watermark

    tmp = __import__("tempfile").mkdtemp()
    path = __import__("pathlib").Path(tmp) / "key.pem"
    pub = wm.generate_key(path=path)
    key = wm.load_key(path=path)

    src = test_watermark.sample_jpeg()
    faces = [{"x": .35, "y": .30, "w": .18, "h": .24, "grow": 1.0}]
    out = wm.protect(src, "instagram", key, faces=faces)

    r = wm.check_watermark(out["image"], pub)
    assert r["match"], f"가린 뒤에도 워터마크가 읽혀야 한다: {r}"
    assert r["matched"] == wm.NBITS, f"{r['matched']}/{wm.NBITS}"


def test_protect_signs_the_masked_image():
    """서명되는 지각 해시가 발행된(가려진) 이미지와 맞는다.

    가리기 전 원본에서 계산하면 검증이 어긋나고, 가려지지 않은 원본의 지문이
    공개 증명서에 남는다.
    """
    import test_watermark

    tmp = __import__("tempfile").mkdtemp()
    path = __import__("pathlib").Path(tmp) / "key.pem"
    wm.generate_key(path=path)
    key = wm.load_key(path=path)

    src = test_watermark.sample_jpeg()
    faces = [{"x": .20, "y": .15, "w": .45, "h": .55, "grow": 1.0}]   # 크게 가린다
    out = wm.protect(src, "instagram", key, faces=faces)

    chk = wm.check_sidecar(out["image"], out["sidecar"])
    assert chk["signature_valid"], "서명이 맞아야 한다"
    assert chk["phash_verdict"] == "same", \
        f"발행본과 서명된 해시가 어긋난다: 거리 {chk.get('phash_distance')}"


def test_protect_does_not_leak_the_unmasked_original():
    """얼굴을 가리면 원본 파일 해시를 공개 증명서에 넣지 않는다."""
    import test_watermark

    tmp = __import__("tempfile").mkdtemp()
    path = __import__("pathlib").Path(tmp) / "key.pem"
    wm.generate_key(path=path)
    key = wm.load_key(path=path)
    src = test_watermark.sample_jpeg()

    masked = wm.protect(src, "instagram", key,
                        faces=[{"x": .3, "y": .3, "w": .2, "h": .2, "grow": 1.0}])
    assert masked["sidecar"]["claim"]["sha256_original"] is None, \
        "가린 사진의 증명서에 원본 해시가 남았다"
    assert masked["sidecar"]["claim"]["faces_masked"] == 1

    plain = wm.protect(src, "instagram", key)
    assert plain["sidecar"]["claim"]["sha256_original"] is not None, \
        "가리지 않았으면 원본 해시는 그대로 있어야 한다"
    assert plain["sidecar"]["claim"]["faces_masked"] == 0


def test_psnr_measures_the_watermark_not_the_mask():
    """PSNR 은 워터마크가 준 손상만 재야 한다.

    가리기 전과 비교하면 모자이크 면적이 그대로 잡혀 수치가 무너진다.
    """
    import test_watermark

    tmp = __import__("tempfile").mkdtemp()
    path = __import__("pathlib").Path(tmp) / "key.pem"
    wm.generate_key(path=path)
    key = wm.load_key(path=path)
    src = test_watermark.sample_jpeg()

    plain = wm.protect(src, "instagram", key)
    masked = wm.protect(src, "instagram", key,
                        faces=[{"x": .2, "y": .2, "w": .4, "h": .4, "grow": 1.0}])
    assert masked["psnr"] > plain["psnr"] - 3, \
        f"가리기가 PSNR 에 섞였다: 가림 {masked['psnr']}, 안 가림 {plain['psnr']}"


def test_no_exif_thumbnail_survives():
    """EXIF 축소판에 가리기 전 얼굴이 남아 따라나가지 않는다.

    잘라낸 사진의 EXIF 축소판에 잘리기 전 원본이 남아 있던 사고가 여러 번 있었다.
    지금 파이프라인은 픽셀을 다시 인코딩하므로 EXIF 가 통째로 사라지지만,
    가정하지 않고 못박는다.
    """
    import test_watermark

    tmp = __import__("tempfile").mkdtemp()
    path = __import__("pathlib").Path(tmp) / "key.pem"
    wm.generate_key(path=path)
    key = wm.load_key(path=path)

    # 축소판이 든 EXIF 를 흉내 낸 APP1 세그먼트를 SOI 뒤에 끼운다
    plain = test_watermark.sample_jpeg()
    payload = b"Exif\x00\x00" + b"THUMBNAIL-SECRET" + b"\x00" * 64
    seg = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    src = plain[:2] + seg + plain[2:]
    assert b"THUMBNAIL-SECRET" in src, "시험 전제: 입력에 축소판이 있어야 한다"

    out = wm.protect(src, "instagram", key,
                     faces=[{"x": .3, "y": .3, "w": .2, "h": .2, "grow": 1.0}])["image"]
    assert b"THUMBNAIL-SECRET" not in out, "EXIF 축소판이 출력까지 따라나갔다"
    assert b"Exif\x00\x00" not in out, "출력에 EXIF 가 남았다"
    # APP1 은 우리가 넣는 XMP 뿐이어야 한다
    assert b"http://ns.adobe.com/xap/1.0/" in out, "XMP 선언은 있어야 한다"
```

- [ ] **Step 2: 실패를 확인한다**

```bash
.venv/bin/python test_face_mask.py
```

기대: `protect() got an unexpected keyword argument 'faces'`

- [ ] **Step 3: `protect()` 를 고친다**

시그니처에 `faces` 를 더한다 (`watermark.py:273` 근처):

```python
def protect(image_bytes: bytes, platform: str, key: Ed25519PrivateKey | None = None,
            message: str = "", overwrite: bool = False, no_ai: bool = True,
            faces: list[dict] | None = None) -> dict:
```

`base = _fit(...)` 와 `if min(base.shape[:2]) < MIN_SIDE:` 검사 **다음에** 넣는다:

```python
    # 가리기는 워터마크보다 먼저다. 순서가 반대면 가리기가 그 영역의 워터마크를 부순다.
    # 지각 해시도 이 뒤에 계산해야 발행본과 맞고, 가려지지 않은 원본의 지문이 남지 않는다.
    n_masked = len(faces or [])
    if faces:
        mask_faces(base, faces)
```

`claim` 의 두 줄을 바꾼다:

```python
        # 발행된(가려진) 이미지의 모양. 워터마크가 지워져도 파생 관계를 보일 수 있다.
        "phash": perceptual_hash(base),
        # 얼굴을 가렸으면 원본 파일 해시를 넣지 않는다. 되돌릴 수는 없지만 원본을
        # 가진 사람이 발행본과의 연결을 증명할 수 있고, 초상권을 지키려고 가린
        # 사진의 공개 증명서에 그 고리를 남길 이유가 없다.
        "sha256_original": None if n_masked else hashlib.sha256(image_bytes).hexdigest(),
        "faces_masked": n_masked,
```

`_fit` 는 새 배열을 돌려주므로 `base` 를 제자리에서 고쳐도 호출자의 바이트열은 안전하다. `cv2.PSNR(base, marked)` 은 두 곳 다 이미 가린 뒤 `base` 를 쓰므로 손댈 필요가 없다 — Step 4 의 테스트로 확인한다.

> **의도된 동작 변경**: `perceptual_hash(_decode(image_bytes))` (원본 크기) 에서
> `perceptual_hash(base)` (플랫폼 크기) 로 바뀐다. 얼굴을 가리지 않는 경우에도 값이
> 몇 비트 달라질 수 있다. 이건 겸사겸사 고치는 불일치다 — 웹앱은 이미 축소한 뒤에
> 계산하고 있어서(`app-core.js` 의 `before`) 두 구현이 서로 다른 해시를 서명하고
> 있었다. 지각 해시는 어차피 32×32 로 줄여서 내므로 차이는 작고,
> `PHASH_SAME = 10` 안에 들어온다. Step 4 에서 기존 테스트로 확인한다.

- [ ] **Step 4: 통과를 확인한다**

```bash
.venv/bin/python test_face_mask.py && .venv/bin/python test_watermark.py
```

둘 다 통과해야 한다. `test_watermark.py` 가 `sha256_original` 이나 claim 키 개수를 검사하다 깨지면, 가리지 않은 경우의 동작은 바뀌지 않았음을 확인하고 테스트를 고친다.

- [ ] **Step 5: 커밋**

```bash
git add watermark.py test_face_mask.py
git commit -m "가리기를 protect 에 끼우고 서명 대상을 발행본으로 맞춘다

지각 해시를 가린 뒤에 계산한다. 가리기 전 원본에서 계산하고 있어서 그대로
두면 발행본 검증이 어긋나고 가려지지 않은 원본의 지문이 공개 증명서에 남는다.
같은 이유로 얼굴을 가리면 sha256_original 을 넣지 않고 가린 개수만 남긴다."
```

---

### Task 5: `site/face.js` — 브라우저 탐지와 가리기

**Files:**
- Create: `site/face.js`
- Create: `site/_face-check.html` (검증용, 이 과제 끝에 지운다)

**Interfaces:**
- Consumes: `site/vendor/face-api.esm.js`, `site/vendor/models/`
- Produces:
  - `loadDetector(): Promise<void>` — 모델을 한 번만 읽는다. 실패하면 다음 호출에서 다시 시도한다
  - `detectFaces(bitmap): Promise<Face[]>` — `Face = {x,y,w,h,poly?,score,mode,grow}`, 좌표 0~1
  - `maskFaces(ctx, w, h, faces): void` — 캔버스를 제자리에서 고친다
  - `MASK_SOLID`, `MASK_MOSAIC`, `GROW_MANUAL`

- [ ] **Step 1: `site/face.js` 를 쓴다**

```js
// 사진에서 얼굴을 찾아 되돌릴 수 없게 지운다. 사진은 이 탭 밖으로 나가지 않는다.
//
// 되돌릴 수 없게 만드는 것은 알고리즘이 아니라 남기는 정보량이다. 자세한 근거는
// watermark.py 의 같은 주석에 있다. 아래 상수는 watermark.py 와 같은 값이어야 한다 —
// 한쪽만 고치지 말 것.
import * as faceapi from './vendor/face-api.esm.js';

export const MASK_SOLID = 'solid';
export const MASK_MOSAIC = 'mosaic';

const SOLID_LEVELS = 16, SOLID_NOISE = 6;
const MOSAIC_BLOCKS = 4, MOSAIC_LEVELS = 16, MOSAIC_JITTER = 12;
export const GROW_POLY = 1.08, GROW_BOX = 1.25, GROW_MANUAL = 1.0;
const DETECT_SIDE = 640;

// ── 탐지 ───────────────────────────────────────────────────────────
let ready = null;
export function loadDetector() {
  if (!ready) {
    ready = (async () => {
      await faceapi.nets.tinyFaceDetector.loadFromUri('./vendor/models');
      await faceapi.nets.faceLandmark68TinyNet.loadFromUri('./vendor/models');
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
function randBytes(n) {
  const a = new Uint8Array(n);
  crypto.getRandomValues(a);     // Math.random 을 쓰지 않는다
  return a;
}

// 캔버스가 폴리곤과 타원을 직접 칠해 준다. 직접 래스터라이즈하지 않는다.
// 다만 안티에일리어싱이 경계를 번지게 하므로 알파 128 에서 자른다 — 페더링 금지.
function regionMask(w, h, f) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const cx = c.getContext('2d', { willReadFrequently: true });
  const grow = f.grow ?? 1.0;
  cx.fillStyle = '#fff';
  cx.beginPath();
  if (f.poly && f.poly.length >= 3) {
    const mx = f.poly.reduce((s, p) => s + p[0], 0) / f.poly.length;
    const my = f.poly.reduce((s, p) => s + p[1], 0) / f.poly.length;
    f.poly.forEach(([px, py], i) => {
      const X = (mx + (px - mx) * grow) * w, Y = (my + (py - my) * grow) * h;
      i ? cx.lineTo(X, Y) : cx.moveTo(X, Y);
    });
    cx.closePath();
  } else {
    const cxp = (f.x + f.w / 2) * w, cyp = (f.y + f.h / 2) * h;
    const ax = f.w * w * grow / 2, ay = f.h * h * grow / 2;
    if (ax < 0.5 || ay < 0.5) return null;
    cx.ellipse(cxp, cyp, ax, ay, 0, 0, Math.PI * 2);
  }
  cx.fill();

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
  // 평균이 아니라 중앙값 — 배경이 조금 섞여도 색이 끌려가지 않는다
  const base = chans.map((v) => {
    v.sort((a, b) => a - b);
    const m = v[v.length >> 1];
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

function fillMosaic(img, w, inside, box) {
  const [x0, y0, x1, y1] = box;
  const pw = x1 - x0, ph = y1 - y0;
  const b = Math.max(8, Math.round(pw / MOSAIC_BLOCKS));
  const sw = Math.max(1, Math.ceil(pw / b)), sh = Math.max(1, Math.ceil(ph / b));
  const step = 256 / MOSAIC_LEVELS;
  const noise = randBytes(sw * sh * 3);
  const cell = new Int32Array(sw * sh * 3), count = new Int32Array(sw * sh);

  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const s = Math.min(sh - 1, ((y - y0) / b) | 0) * sw + Math.min(sw - 1, ((x - x0) / b) | 0);
      const p = (y * w + x) * 4;
      cell[s * 3] += img[p]; cell[s * 3 + 1] += img[p + 1]; cell[s * 3 + 2] += img[p + 2];
      count[s]++;
    }
  const val = new Uint8Array(sw * sh * 3);
  for (let s = 0; s < sw * sh; s++)
    for (let k = 0; k < 3; k++) {
      const mean = count[s] ? cell[s * 3 + k] / count[s] : 128;
      // 지터는 양자화 폭과 맞먹는다 — 블록 값이 곧 원본 평균이라는 역산의 전제를 깬다
      const j = (noise[s * 3 + k] % (2 * MOSAIC_JITTER + 1)) - MOSAIC_JITTER;
      val[s * 3 + k] = Math.max(0, Math.min(255,
        Math.round(Math.floor(mean / step) * step + step / 2 + j)));
    }
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const i = y * w + x;
      if (!inside[i]) continue;
      const s = Math.min(sh - 1, ((y - y0) / b) | 0) * sw + Math.min(sw - 1, ((x - x0) / b) | 0);
      const p = i * 4;
      for (let k = 0; k < 3; k++) img[p + k] = val[s * 3 + k];
    }
}

export function maskFaces(ctx, w, h, faces) {
  if (!faces || !faces.length) return;
  const image = ctx.getImageData(0, 0, w, h);
  for (const f of faces) {
    const r = regionMask(w, h, f);
    if (!r) continue;
    if ((f.mode ?? MASK_SOLID) === MASK_MOSAIC) fillMosaic(image.data, w, r.inside, r.box);
    else fillSolid(image.data, w, r.inside, r.box);
  }
  ctx.putImageData(image, 0, 0);
}
```

- [ ] **Step 2: 파이썬과 맞는지 확인하는 임시 페이지**

`site/_face-check.html`:

```html
<!doctype html><meta charset="utf-8"><title>face.js check</title>
<pre id="out">…</pre>
<script type="module">
import * as F from './face.js';
const out = document.getElementById('out');
const log = (s) => { out.textContent += s + '\n'; };
out.textContent = '';
try {
  // 200x200, 왼쪽 절반 100 / 오른쪽 절반 140 — 파이썬 시험과 같은 구성
  const c = document.createElement('canvas'); c.width = c.height = 200;
  const cx = c.getContext('2d', { willReadFrequently: true });
  cx.fillStyle = 'rgb(100,100,100)'; cx.fillRect(0, 0, 200, 200);
  cx.fillStyle = 'rgb(140,140,140)'; cx.fillRect(0, 0, 200, 100);

  const face = { x: .1, y: .1, w: .8, h: .8, mode: F.MASK_SOLID, grow: 1.0 };
  const before = cx.getImageData(0, 0, 200, 200).data.slice();
  F.maskFaces(cx, 200, 200, [face]);
  const after = cx.getImageData(0, 0, 200, 200).data;

  // 1. 타원 밖은 안 변한다
  let outsideChanged = 0;
  for (let i = 0; i < after.length; i += 4) {
    const x = (i / 4) % 200, y = ((i / 4) / 200) | 0;
    const dx = (x - 100) / 80, dy = (y - 100) / 80;
    if (dx * dx + dy * dy > 1.2 && after[i] !== before[i]) outsideChanged++;
  }
  log(`타원 밖 변한 화소: ${outsideChanged}  (0 이어야 함)`);

  // 2. 채운 색 — 파이썬의 양자화 기준색과 같아야 한다
  const p = (100 * 200 + 100) * 4;
  log(`중앙 색: ${after[p]},${after[p+1]},${after[p+2]}  (양자화 기준 120 ± 잡음 6)`);

  // 3. 비결정성
  cx.putImageData(new ImageData(new Uint8ClampedArray(before), 200, 200), 0, 0);
  F.maskFaces(cx, 200, 200, [face]);
  const again = cx.getImageData(0, 0, 200, 200).data;
  let same = true;
  for (let i = 0; i < after.length; i++) if (after[i] !== again[i]) { same = false; break; }
  log(`두 번 돌린 결과가 같은가: ${same}  (false 여야 함)`);

  await F.loadDetector();
  log('모델 로드 ✅');
} catch (e) { log('❌ ' + (e && e.stack || e)); }
</script>
```

- [ ] **Step 3: CSP 헤더를 붙여 띄우고 확인**

Task 1 Step 4 의 임시 서버를 다시 띄우고 `http://127.0.0.1:8777/_face-check.html` 을 연다.

통과 기준:

- `타원 밖 변한 화소: 0`
- `중앙 색` 세 채널이 모두 `114~126` 범위 (파이썬 기준색 120 = `floor(120/16)*16+8`, 잡음 ±6)
- `두 번 돌린 결과가 같은가: false`
- `모델 로드 ✅`
- CSP 위반 없음

**파이썬 대조**: 같은 입력으로 파이썬이 무엇을 내는지 확인한다.

```bash
.venv/bin/python -c "
import numpy as np, watermark as wm
a = np.full((200,200,3), 100, np.uint8); a[:100] = 140
f = {'x':.1,'y':.1,'w':.8,'h':.8,'mode':wm.MASK_SOLID,'grow':1.0}
out = wm.mask_faces(a.copy(), [f], rng=np.random.default_rng(0))
print('중앙 색', out[100,100])
"
```

두 값의 **양자화 기준색**(잡음 제외)이 같아야 한다. 잡음 때문에 픽셀이 정확히 같을 수는 없으므로 ±6 안에 들면 통과다. 벗어나면 중앙값 계산이나 양자화 식이 어긋난 것이다.

- [ ] **Step 4: 임시 파일을 지우고 커밋**

```bash
rm site/_face-check.html
git add site/face.js
git commit -m "브라우저에서 얼굴을 찾아 지우는 face.js

68점 랜드마크의 턱선과 이마 추정선으로 윤곽 폴리곤을 만들어 사각형이나
타원보다 배경을 덜 지운다. 경계는 캔버스 안티에일리어싱을 알파 128 에서
잘라 하드하게 둔다 — 부드러운 경계는 그 폭만큼 원본을 흘린다.
상수와 계산식은 watermark.py 와 같은 값이다."
```

---

### Task 6: `site/face-ui.js` — 선택 패널

**Files:**
- Create: `site/face-ui.js`

**Interfaces:**
- Consumes: `./face.js` 의 `detectFaces`, `maskFaces`, `MASK_SOLID`, `MASK_MOSAIC`, `GROW_MANUAL`
- Produces: `createFacePanel(hostEl) -> { show(bitmap), selected(), clear(), count() }`
  - `show(bitmap)`: 탐지하고 패널을 그린다. 실패해도 던지지 않는다 — 패널 안에 오류를 적고 수동 도구만 남긴다
  - `selected()`: `Face[]` — 체크된 것만. `mode` 와 `grow` 가 채워져 있다
  - `count()`: `{ found, checked }`

- [ ] **Step 1: `site/face-ui.js` 를 쓴다**

```js
// 찾은 얼굴을 보여주고 사용자가 확인·수정하게 한다.
//
// 탐지는 옆얼굴과 작은 얼굴을 놓치고 오탐도 낸다. 초상권에서 놓침은 실제 피해라
// 자동 결과를 최종으로 쓰지 않는다 — 기본 전부 체크, 수동 추가·삭제가 1급 기능이다.
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
  const modeWrap = el('label', 'display:flex;align-items:center;gap:6px;font-size:12.5px;cursor:pointer');
  const modeBox = el('input');
  modeBox.type = 'checkbox';
  modeWrap.append(modeBox, document.createTextNode('모자이크 모양으로 (보장은 약해집니다)'));
  head.append(title, status, el('div', 'flex:1'), modeWrap);

  const stage = el('div', 'position:relative;line-height:0;user-select:none;touch-action:none');
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

  modeBox.onchange = () => { /* selected() 에서 읽는다 */ };

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
      const mode = modeBox.checked ? F.MASK_MOSAIC : F.MASK_SOLID;
      return faces.filter((f) => f.on !== false).map((f) => ({
        x: f.x, y: f.y, w: f.w, h: f.h,
        ...(f.poly ? { poly: f.poly } : {}),
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
```

- [ ] **Step 2: 커밋**

```bash
git add site/face-ui.js
git commit -m "얼굴 선택 패널 — 자동으로 찾고 사람이 확인한다

탐지가 옆얼굴과 작은 얼굴을 놓치므로 자동 결과를 최종으로 쓰지 않는다.
기본을 전부 체크로 두어 안전한 쪽으로 기울이고, 끌어서 박스를 추가하는
길을 1급으로 둔다. 모델을 못 불러와도 수동 도구는 남아 기능이 죽지 않는다."
```

---

### Task 7: 웹앱 통합

**Files:**
- Modify: `site/app-core.js:205-262` (`protect`, `measurePsnr`)
- Modify: `site/app.js`
- Modify: `site/index.html`

**Interfaces:**
- Consumes: `./face.js` 의 `maskFaces`, `./face-ui.js` 의 `createFacePanel`
- Produces: `A.protect(file, platform, { message, noAi, keyless, faces })` — `faces` 는 정규화 얼굴 목록. 사이드카 claim 에 `faces_masked` 가 생기고, 가리면 `sha256_original` 은 `null`

- [ ] **Step 1: `app-core.js` 의 `protect()` 를 고친다**

맨 위 import 옆에 추가:

```js
import { maskFaces } from './face.js';
```

시그니처와 앞부분을 바꾼다:

```js
export async function protect(file, platform, { message = '', noAi = true, keyless = false,
                                                faces = [] } = {}) {
  const spec = PLATFORMS[platform];
  const bmp = await loadImage(file);
  const { canvas, ctx, w, h } = fitCanvas(bmp, spec.maxW, spec.maxH);
  if (Math.min(w, h) < MIN_SIDE) throw new Error(`이미지가 너무 작습니다 (짧은 변 최소 ${MIN_SIDE}px)`);

  // 가리기는 워터마크보다 먼저다. 순서가 반대면 가리기가 그 영역의 워터마크를 부순다.
  // 지각 해시도 이 뒤라야 발행본과 맞고, 가려지지 않은 원본의 지문이 남지 않는다.
  if (faces.length) maskFaces(ctx, w, h, faces);
```

`claim` 의 두 줄을 바꾼다:

```js
      phash: before,
      // 얼굴을 가렸으면 원본 파일 해시를 넣지 않는다. 원본을 가진 사람이 발행본과의
      // 연결을 증명할 수 있어, 초상권을 지키려고 가린 사진의 공개 증명서에 남길 이유가 없다.
      sha256_original: faces.length ? null : await sha256Hex(origBytes),
      faces_masked: faces.length,
```

`measurePsnr` 이 가리기를 워터마크 손상으로 잘못 재지 않게 고친다. 호출부를

```js
  const psnr = await measurePsnr(bmp, spec, jpeg);
```

에서

```js
  const psnr = await measurePsnr(bmp, spec, jpeg, faces);
```

로 바꾸고 함수 앞부분을:

```js
async function measurePsnr(bmp, spec, jpeg, faces = []) {
  const a = fitCanvas(bmp, spec.maxW, spec.maxH);
  // 가리기가 아니라 워터마크가 준 손상을 재는 값이다. 기준도 가린 뒤여야 한다.
  if (faces.length) maskFaces(a.ctx, a.w, a.h, faces);
  const pa = getPixels(a.ctx, a.w, a.h).data;
```

> 가리기에 난수가 들어가므로 기준과 결과의 난수가 서로 다르다. 단색은 ±6, 모자이크는 ±12 폭이라 PSNR 에 주는 영향은 얼굴 면적에 비례해 작다. 정확한 값이 필요한 게 아니라 "워터마크가 눈에 띄는가"를 보는 값이므로 이 정도면 충분하다.

- [ ] **Step 2: `app.js` 에 패널을 끼운다**

import 에 추가:

```js
import { createFacePanel } from './face-ui.js';
```

`dropzone($('#pdrop'), ...)` 줄 **위에** 패널을 만든다:

```js
const facePanel = createFacePanel($('#faces'));
```

`pImg` 를 받는 dropzone 콜백을 바꾼다:

```js
dropzone($('#pdrop'), $('#pfile'), async (f) => {
  pImg = f; sync(); askCap();
  try { await facePanel.show(await A.loadImage(f)); }
  catch (e) { console.warn('얼굴 패널', e); }   // 패널이 죽어도 보호하기는 살아야 한다
});
```

`$('#pgo').onclick` 안의 `A.protect(...)` 호출에 `faces` 를 넘긴다:

```js
    const r = await A.protect(pImg, $('#platform').value, {
      ...
      faces: facePanel.selected(),
    });
```

결과 카드에 가린 수를 적는다. `$('#pres').innerHTML` 을 만드는 곳의 다른 `.kv` 줄 옆에 붙인다:

```js
${r.sidecar?.claim?.faces_masked
  ? `<div class="kv"><span>가린 얼굴</span><span>${r.sidecar.claim.faces_masked}명 — 되돌릴 수 없습니다</span></div>`
  : ''}
```

- [ ] **Step 3: `site/index.html` 에 자리를 만든다**

`<div class="row" style="align-items:flex-start">` (`#pmsg` 가 든 줄) **바로 위에** 넣는다:

```html
  <div class="card hide" id="facesCard" style="margin-top:14px">
    <div id="faces"></div>
  </div>
```

패널이 스스로 `display` 를 켜므로 감싼 카드도 같이 켠다. `app.js` 의 dropzone 콜백에서 `facePanel.show()` 뒤에 한 줄 더한다:

```js
  show($('#facesCard'), true);
```

그리고 "이 도구가 못 하는 것" 목록에 항목을 더한다:

```html
    <li><b>얼굴 자동 찾기는 완벽하지 않습니다.</b> 옆얼굴·작은 얼굴·가려진 얼굴을
      놓칩니다. 올리기 전에 눈으로 확인하고, 놓친 얼굴은 직접 칠하세요.</li>
```

- [ ] **Step 4: 브라우저에서 확인한다**

Task 1 Step 4 의 CSP 서버로 `http://127.0.0.1:8777/` 을 연다.

확인 항목:

1. 사진을 넣으면 얼굴 패널이 뜨고 상태 문구가 바뀐다
2. 얼굴이 없는 사진에서도 오류 없이 "얼굴을 못 찾았습니다" 가 뜬다
3. 사진 위를 끌면 박스가 생기고, 칩을 눌러 켜고 끌 수 있고, × 로 지워진다
4. [보호하기] 를 누르면 내려받은 이미지의 해당 영역이 실제로 지워져 있다
5. 그 이미지를 [검증하기] 에 넣으면 태그가 48/48 로 읽히고 서명이 통과한다
6. 사이드카 JSON 에 `"sha256_original": null` 과 `"faces_masked": <개수>` 가 있다
7. CSP 위반이 없다

- [ ] **Step 5: 커밋**

```bash
git add site/app-core.js site/app.js site/index.html
git commit -m "웹앱에 얼굴 가리기를 끼운다

가리기를 워터마크와 지각 해시보다 먼저 넣고, 서명 대상을 발행본으로 맞춘다.
PSNR 도 가린 뒤를 기준으로 재야 워터마크 품질이 계속 보인다."
```

---

### Task 8: 데스크톱 통합

**Files:**
- Modify: `server.py`
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `wm.protect(..., faces=...)`, `site/face-ui.js`, `site/face.js`
- Produces: `POST /api/protect` 에 `faces` 폼 필드(JSON 배열 문자열). `GET /lib/<path>` 가 `site/` 를 서빙

- [ ] **Step 1: `server.py` 가 `site/` 를 `/lib` 로 서빙하게 한다**

데스크톱 UI 도 브라우저에서 돌므로 탐지 JS 를 그대로 쓴다. 복사본을 만들지 않는다.

`STATIC = Path(__file__).parent / "static"` 아래에 더한다:

```python
SITE = Path(__file__).parent / "site"
```

`@app.get("/")` 위에 붙인다:

```python
# 데스크톱 UI 도 브라우저에서 돈다. 얼굴 탐지 JS 와 모델을 웹앱과 같은 파일로 쓴다 —
# 복사본을 두면 한쪽만 고쳐지는 날이 온다.
from fastapi.staticfiles import StaticFiles

app.mount("/lib", StaticFiles(directory=SITE), name="lib")
```

`import` 는 파일 맨 위 다른 fastapi import 옆으로 옮긴다.

- [ ] **Step 2: `/api/protect` 가 `faces` 를 받게 한다**

```python
@app.post("/api/protect")
async def protect(file: UploadFile, platform: str = Form(...), passphrase: str = Form(""),
                  message: str = Form(""), overwrite: str = Form(""),
                  keyless: str = Form(""), no_ai: str = Form("1"),
                  faces: str = Form("")):
    key = None if keyless == "1" else _key(passphrase)
    # 가리기가 실패하면 조용히 넘어가지 않는다. 가려질 줄 알았던 얼굴이 그냥
    # 발행되는 것이 최악이라, 파싱이 안 되면 아무것도 하지 않고 거절한다.
    try:
        face_list = json.loads(faces) if faces.strip() else []
        if not isinstance(face_list, list):
            raise ValueError("배열이어야 합니다")
        for f in face_list:
            if not isinstance(f, dict) or not all(k in f for k in ("x", "y", "w", "h")):
                raise ValueError("x, y, w, h 가 있어야 합니다")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"얼굴 정보를 읽지 못했습니다 — 가리기를 하지 않았습니다: {e}")
    try:
        out = wm.protect(await _read(file), platform, key,
                         message.strip(), overwrite == "1", no_ai == "1", face_list)
```

`import json` 이 없으면 맨 위에 추가한다.

응답 dict 에 한 줄 더한다:

```python
        "faces_masked": len(face_list),
```

- [ ] **Step 3: `static/index.html` 에 패널을 붙인다**

`<script>` 를 `<script type="module">` 로 바꾸고 맨 위에 import 를 넣는다:

```html
<script type="module">
import { createFacePanel } from '/lib/face-ui.js';
```

> `type="module"` 로 바꾸면 스크립트가 지연 실행된다. 이 파일은 이미 모든 핸들러를 `$('#…').onclick = …` 로 붙이므로 DOM 이 준비된 뒤 도는 편이 오히려 안전하다. 바꾼 뒤 Step 5 에서 모든 버튼을 눌러 확인한다.

`#pmsg` 가 든 줄 위에 자리를 만든다:

```html
  <div class="card hide" id="facesCard" style="margin-top:14px"><div id="faces"></div></div>
```

패널을 만들고 파일 선택에 연결한다 (`pImg` 를 받는 dropzone 콜백을 찾아 고친다):

```js
const facePanel = createFacePanel($('#faces'));
```

```js
  pImg = f;
  createImageBitmap(f)
    .then((b) => { show($('#facesCard'), true); return facePanel.show(b); })
    .catch((e) => console.warn('얼굴 패널', e));
```

`body()` 에 얼굴을 실어 보낸다:

```js
function body(f, overwrite){
  const fd = new FormData();
  fd.append('file', f); fd.append('platform', $('#platform').value);
  fd.append('passphrase', $('#ppass').value || '');
  fd.append('message', $('#pmsg').value.trim());
  if (overwrite) fd.append('overwrite', '1');
  if (keyless()) fd.append('keyless', '1');
  fd.append('no_ai', $('#pnoai').checked ? '1' : '0');
  const faces = facePanel.selected();
  if (faces.length) fd.append('faces', JSON.stringify(faces));
  return fd;
}
```

- [ ] **Step 4: face.js 의 모델 경로를 양쪽에서 맞춘다**

`face.js` 는 `'./vendor/models'` 라는 상대 경로를 쓴다. `/lib/face.js` 로 불러오면 `/lib/vendor/models` 가 되어 그대로 맞는다. 상대 경로를 절대 경로로 바꾸지 말 것 — 웹앱이 깨진다.

- [ ] **Step 5: 데스크톱 앱을 띄워 확인한다**

```bash
./실행.command
```

확인 항목:

1. 기존 버튼이 전부 그대로 동작한다 (`type="module"` 로 바꾼 영향 확인 — 도장 만들기, 보호하기, 검증, 벤치마크)
2. 사진을 넣으면 얼굴 패널이 뜨고 `/lib/face.js` 와 모델이 200 으로 내려온다 (개발자도구 Network)
3. 얼굴을 체크하고 [보호하기] → 결과 이미지의 해당 영역이 지워져 있다
4. 사이드카에 `"sha256_original": null`, `"faces_masked": <개수>`
5. 얼굴 없이 보호하면 예전과 똑같이 동작한다

깨진 `faces` 를 거절하는지 확인한다:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/api/protect \
  -H 'Origin: http://127.0.0.1:8000' \
  -F file=@<아무_사진.jpg> -F platform=instagram -F keyless=1 -F message=hi \
  -F 'faces=not-json'
```

기대: `400`

- [ ] **Step 6: 커밋**

```bash
git add server.py static/index.html
git commit -m "데스크톱에도 얼굴 가리기 — 웹앱과 같은 JS 를 쓴다

데스크톱 UI 도 브라우저에서 도므로 site/ 를 /lib 로 한 번 더 서빙해
탐지 JS 와 모델을 공유한다. 복사본을 두면 한쪽만 고쳐지는 날이 온다.
얼굴 정보가 깨지면 400 으로 거절한다 — 가려질 줄 알았던 얼굴이 그냥
발행되는 것이 최악이다."
```

---

### Task 9: 문서

**Files:**
- Modify: `README.md`
- Modify: `site/index.html` (이미 Task 7 에서 한계 항목 추가 — 여기서는 확인만)

- [ ] **Step 1: README 에 기능을 적는다**

영문이 위, 국문이 아래라는 기존 구조를 지킨다. 기능 목록과 "이 도구가 못 하는 것" 양쪽에 넣는다.

영문 쪽 기능 목록에:

```markdown
- **Face masking** — finds faces and irreversibly erases the ones you pick, before the
  watermark goes in. The default fills the region with its own median colour, so the
  output depends on the original through three quantised numbers and nothing else:
  there is nothing left for a de-mosaicing model to recover. A coarse-mosaic look is
  available and is deliberately labelled as the weaker choice. Detection misses profile
  and small faces, so you can drag boxes yourself — check before you post.
```

영문 "What it cannot do" 에:

```markdown
- **Automatic face detection is not reliable.** It misses profile, small, and occluded
  faces. Look at the preview before posting and paint over anything it missed.
```

국문 쪽 기능 목록에:

```markdown
- **얼굴 가리기** — 사진에서 얼굴을 찾아 고른 것만 되돌릴 수 없게 지웁니다.
  워터마크보다 먼저 들어갑니다. 기본은 그 영역의 중앙값 색으로 채우는 방식이라,
  출력이 양자화된 색 세 개를 통해서만 원본에 의존합니다 — 모자이크 제거 모델이
  복원할 것이 남지 않습니다. 모자이크 모양도 고를 수 있지만 보장이 약해진다고
  적어 두었습니다. 자동 찾기는 옆얼굴과 작은 얼굴을 놓치므로 직접 끌어서 칠할 수
  있고, 올리기 전에 눈으로 확인하세요.
```

국문 "이 도구가 못 하는 것" 에:

```markdown
- **얼굴 자동 찾기는 완벽하지 않습니다.** 옆얼굴·작은 얼굴·가려진 얼굴을 놓칩니다.
  미리보기를 눈으로 확인하고 놓친 얼굴은 직접 칠하세요.
```

- [ ] **Step 2: 측정 결과를 README 에 넣는다**

Task 3 에서 잰 숫자를 기존 측정치 표 옆에 놓는다 (영문·국문 양쪽):

```markdown
| Masking | Re-identification top-1 | Samples left |
|---|---|---|
| Solid (default) | <실측>% | 3 |
| Mosaic | <실측>% | <실측> |

Chance is 0.5% (200 candidates).
```

- [ ] **Step 3: 전체 테스트를 돌린다**

```bash
.venv/bin/python test_watermark.py && .venv/bin/python test_face_mask.py
```

- [ ] **Step 4: 개인정보가 섞이지 않았는지 확인한다**

공개 저장소다. 새로 넣은 것 중에 사용자 정보가 없는지 본다.

```bash
git diff --stat main@{u}..HEAD
git log -p main@{u}..HEAD | grep -inE "geniuspyopro|@gmail|/Users/arthur|박천표" || echo "깨끗함"
```

- [ ] **Step 5: 커밋하고 밀어 올린다**

```bash
git add README.md
git commit -m "얼굴 가리기를 문서에 적는다 — 무엇을 보장하고 무엇을 못 하는지"
git push
```

Vercel 이 배포를 집어가면 배포된 사이트에서 Task 7 Step 4 의 확인 항목을 한 번 더 돌린다.

---

## 자기 점검 기록

**스펙 대응**

| 스펙 요구 | 과제 |
|---|---|
| CSP 검증 | 1 |
| 라이브러리·모델 반입, NOTICE | 1 |
| `mask_faces` 단색·모자이크 | 2 |
| 윤곽 폴리곤, 확장 배율 3종, 하드 엣지 | 2 (파이썬), 5 (JS) |
| 재식별 시험, 표본 수, 비결정성, 침해 범위 | 2, 3 |
| phash 순서 / `sha256_original` / PSNR | 4 (파이썬), 7 (JS) |
| EXIF 축소판 | 4 |
| 탐지 + 랜드마크 → 폴리곤 | 5 |
| 선택 패널, 수동 추가·삭제, 기본 전부 체크 | 6 |
| 모델 로드 실패 시 수동만 남김 | 6 |
| 웹앱 통합 | 7 |
| `/lib` 공유, `faces` 폼 필드, 400 거절 | 8 |
| README 한계 | 7 (UI), 9 (README) |

**이름 일관성** — `mask_faces`/`maskFaces`, `MASK_SOLID`/`MASK_MOSAIC`, `faces_masked`, `grow`, `poly`, `createFacePanel`, `selected()` 를 정의한 곳과 쓰는 곳에서 대조했다.

**남는 판단** — Task 3 의 재식별 결과에 따라 `MOSAIC_JITTER` 나 `MOSAIC_BLOCKS` 가 바뀔 수 있다. 바뀌면 Task 5 의 `site/face.js` 상수도 같이 고친다. 두 파일에 서로를 가리키는 주석을 남겼다.
