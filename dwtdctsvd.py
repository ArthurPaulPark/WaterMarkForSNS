"""DWT-DCT-SVD 워터마크 (크롭 내성 + 벡터화).

invisible-watermark(ShieldMnt, MIT)의 dwtDctSvd에서 출발했다. 패키지 전체를 의존하면
쓰지도 않는 rivaGan 탓에 PyTorch 400MB가 딸려와서 알고리즘만 가져왔다.
이식 시점에 원본과 바이트 단위로 같은 출력을 내는 것을 확인했고, 이후 아래를 바꿨다.

  * 역 DWT의 디테일 밴드 순서 수정 (원본은 cH/cV를 뒤바꿔 넣어 PSNR을 9dB 손해봤다)
  * 비트 배정을 래스터 순서 → 주기 격자로 변경. 크롭돼도 오프셋만 찾으면 복구된다.
    (원래의 래스터 배치도 layout="raster" 로 남겨뒀다. 크롭 내성을 포기하는 대신
     임의 길이 페이로드를 담을 수 있어 문장 레이어가 쓴다.)
  * 블록 DCT/SVD 벡터화. 크롭 오프셋 64가지를 전수 탐색해도 몇 초면 끝난다.
  * 역변환 결과를 uint8 에 넣을 때 클리핑. 원본은 랩어라운드돼서 강도를 올릴 수 없었다.
  * DWT 1레벨 → 2레벨. 같은 화질(PSNR 43dB)에서 디노이즈 공격 21/32 → 32/32 로 바뀐다.
    거친 구조에 실리므로, 지우려면 사진의 저주파를 건드려야 해서 눈에 보이게 망가진다.
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np
import pywt

BLOCK = 4

# ── 지각 마스킹 ────────────────────────────────────────────────────────────
# 하늘·벽처럼 평평한 곳에서는 블록마다의 양자화 오프셋을 가려줄 무늬가 없어서
# 규칙적인 격자로 보인다. 강도를 낮추는 것으로는 해결되지 않았다 (180→80 으로
# 반토막 내도 하늘 최대변화 9→5 인데 벤치마크 최악은 41→33 으로 무너진다).
# 그래서 무늬가 있는 곳에는 전력으로 심고, 없는 곳에는 아예 심지 않는다.
# MASK_K 스윕(0.35/0.50/0.65/0.85) 결과: 0.35~0.65 는 완전히 동일했다 — 평평한
# 블록은 AC 에너지가 거의 0 이라 문턱을 조금만 올려도 다 걸러진다. 0.85 에서만
# 문턱이 진짜 무늬가 있는 블록까지 파고들어 과하게 마스킹했고, 그 대가로 실제
# 사진 실패가 1/11 → 4/11 로 늘었다. 그래서 동일 구간의 중앙값인 0.5 를 쓴다 —
# 위아래 경계 모두에서 떨어져 있어 여유가 있다. site/wm.js 의 MASK_K 와 반드시
# 같은 값이어야 한다 (달라지면 브라우저·데스크톱이 서로 다른 워터마크를 심는다).
MASK_K = 0.5       # ||AC|| < MASK_K * scale 인 블록은 건너뛴다 (무차원)
MASK_FLOOR = 0.35  # 그래도 활동도 상위 이 비율은 반드시 심는다 (평평한 사진 대비)
MASK_READ = 0.10   # 추출: 절대 기준을 넘는 블록이 이만큼도 없을 때만 바닥으로 내린다


def _dct_matrix(n: int = BLOCK) -> np.ndarray:
    """cv2.dct 와 동일한 정규 직교 DCT-II 행렬.  cv2.dct(X) == D @ X @ D.T"""
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n)) * np.sqrt(2 / n)
    m[0] /= np.sqrt(2)
    return m


_D = _dct_matrix()


def _ac_norm(dct: np.ndarray) -> np.ndarray:
    """블록별 AC 노름 = √(전체 에너지 − DC²). 정규직교 DCT 라 블록 표준편차의 4배다."""
    return np.sqrt(np.maximum(np.einsum("kij,kij->k", dct, dct) - dct[:, 0, 0] ** 2, 0))


def _bar(ac: np.ndarray, scale: float, trip: float) -> float:
    """마스킹 기준선. 절대 기준 MASK_K*scale 을 넘는 블록이 trip 비율에 못 미칠 때만
    '상위 MASK_FLOOR 비율'까지 내린다.

    삽입과 추출이 trip 만 다르다. 삽입은 trip=MASK_FLOOR — 무늬가 모자라면 바닥까지
    내려서라도 심어야 하기 때문이다. 추출은 trip=MASK_READ 로 훨씬 낮다 — 추출에는
    바닥이 필요 없고, 오히려 해가 된다. 크롭 조각이 하늘만 담고 있으면 그 조각의
    분위수는 하늘 한복판에 떨어져, 인코더가 일부러 건너뛴 블록을 도로 세게 된다
    (실측: 하늘 60% 사진의 정중앙 40% 크롭에서 이것 때문에 표가 묻혔다).
    """
    hard = MASK_K * scale
    if not len(ac) or float(np.mean(ac >= hard)) >= trip:
        return hard
    return float(np.quantile(ac, 1.0 - MASK_FLOOR))


def _period(n: int) -> tuple[int, int]:
    """비트를 담을 주기 격자 크기. 32 → (4, 8): 4x8 타일마다 32비트가 정확히 한 번씩."""
    ph = math.isqrt(n)
    while n % ph:
        ph -= 1
    return ph, n // ph


class DwtDctSvd:
    def __init__(self, bits=None, wm_len: int = 32, scales=None,
                 block: int = BLOCK, level: int = 2, layout: str = "tile",
                 tile=None, cells=None):
        self.bits = list(bits or [])
        self.wm_len = wm_len
        self.scales = list(scales or [0, 72, 0])
        self.block = block
        self.level = level
        self.layout = layout
        # 주기 격자는 tile 배치에서만 의미가 있다 (크롭 오프셋 탐색의 근거).
        self.ph, self.pw = (tile if tile else
                            _period(wm_len) if layout == "tile" else (1, wm_len))
        # 격자 칸보다 비트가 적으면 일부 칸만 쓴다 (희소 배치). 키마다 다른 칸을 고르면
        # 남이 덮어써도 겹치지 않은 칸에 내 비트가 남는다.
        self.cells = (np.arange(self.wm_len) if cells is None
                      else np.asarray(cells, int))
        assert len(self.cells) == self.wm_len

    def _index(self, gh: int, gw: int) -> np.ndarray:
        """블록마다 어느 비트를 싣는지. -1 이면 건드리지 않는다."""
        if self.layout == "raster":
            return (np.arange(gh * gw) % self.wm_len).reshape(gh, gw)
        i = np.arange(gh)[:, None] % self.ph
        j = np.arange(gw)[None, :] % self.pw
        cell = i * self.pw + j
        lookup = np.full(self.ph * self.pw, -1)
        lookup[self.cells] = np.arange(self.wm_len)
        return lookup[cell]

    @property
    def _span(self) -> int:
        """블록 하나가 덮는 원본 픽셀 수. 크롭 오프셋 탐색 범위가 된다."""
        return self.block * 2**self.level

    # ------------------------------------------------------------ 내부
    def _split(self, ca: np.ndarray):
        b = self.block
        gh, gw = ca.shape[0] // b, ca.shape[1] // b
        blocks = ca[: gh * b, : gw * b].reshape(gh, b, gw, b).swapaxes(1, 2)
        return blocks.reshape(-1, b, b), gh, gw

    def _crop(self, n: int) -> int:
        """DWT 레벨 수만큼 나누어떨어지게 자른다."""
        unit = 2**self.level
        return n // unit * unit

    # ------------------------------------------------------------ 삽입
    def encode(self, bgr: np.ndarray) -> np.ndarray:
        h, w = self._crop(bgr.shape[0]), self._crop(bgr.shape[1])
        yuv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV)
        for ch in range(3):
            scale = self.scales[ch]
            if scale <= 0:
                continue
            coeffs = pywt.wavedec2(yuv[:h, :w, ch].astype(np.float64), "haar",
                                   level=self.level)
            ca = np.ascontiguousarray(coeffs[0])
            self._embed(ca, scale)
            coeffs[0] = ca
            restored = pywt.waverec2(coeffs, "haar")[:h, :w]
            # uint8 배열에 그냥 대입하면 255를 넘는 값이 클리핑이 아니라 랩어라운드된다
            # (260 → 4). 원본 라이브러리의 버그로, 강도를 올릴수록 워터마크가 무너졌다.
            yuv[:h, :w, ch] = np.clip(np.rint(restored), 0, 255)
        return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)

    def _mask(self, dct: np.ndarray, used: np.ndarray, scale: float) -> np.ndarray:
        """무늬가 없어 삽입을 숨길 수 없는 블록을 골라 뺀다 (지각 마스킹).

        활동도는 DCT 의 AC 에너지로 잰다 — 어차피 삽입에 필요해서 이미 계산해 둔
        값이라 공짜이고, 정규직교 DCT 라 블록 분산의 16배와 정확히 같은 양이다.
        (지역 분산을 따로 구하는 것과 같은 수치를 얻으면서 연산이 늘지 않는다.)

        기준을 scale 에 비례시키는 이유: 삽입이 만드는 픽셀 변화의 rms 는
        Δs0/(4·2^level) 이고, AC 노름을 픽셀로 옮길 때도 같은 1/(4·2^level) 이
        걸린다. 두 양의 비 ||AC||/scale 은 레벨과 강도에 무관한 무차원 수라,
        태그 층(180/레벨2)과 문장 층(24/레벨1)에 같은 상수 하나로 통한다.

        절대 기준만 쓰면 평평한 사진이 통째로 비어 검출이 불가능해진다. 그래서
        활동도 상위 FLOOR 비율은 기준에 못 미쳐도 반드시 심는다 — 그 사진에서는
        보이는 대가를 치르더라도 표식이 남는 쪽을 고른다.

        추출(_read)도 같은 활동도를 쓴다. 받은 이미지에서 다시 계산하는 값이라
        부가 정보가 아니고, 사이드카 형식도 그대로다.
        """
        ac = _ac_norm(dct)
        return used & (ac >= _bar(ac[used], scale, MASK_FLOOR))

    def _embed(self, ca: np.ndarray, scale: float) -> None:
        b = self.block
        blocks, gh, gw = self._split(ca)
        idx = self._index(gh, gw).ravel()
        used = idx >= 0
        wanted = np.zeros(len(idx))
        wanted[used] = np.asarray(self.bits, float)[idx[used]]

        dct = _D @ blocks @ _D.T
        used = self._mask(dct, used, scale)
        u, s, vt = np.linalg.svd(dct)
        # 원래는 s0 가 있던 격자 칸 안의 점으로만 옮겼다 (s0 // scale). 그런데 같은 비트를
        # 뜻하는 점은 scale 마다 하나씩 있으므로, 가장 가까운 것을 고르면 이동량이 최대
        # scale → scale/2 로 줄어든다. 판독은 s0 % scale 만 보므로 여유는 한 톨도 줄지
        # 않는다 (실측: 벤치마크 동일, 하늘 최대변화 9→6, PSNR 41.0→43.8dB).
        off = 0.25 + 0.5 * wanted
        s[:, 0] = np.where(used, (np.rint(s[:, 0] / scale - off) + off) * scale, s[:, 0])
        out = _D.T @ ((u * s[:, None, :]) @ vt) @ _D
        ca[: gh * b, : gw * b] = (
            out.reshape(gh, gw, b, b).swapaxes(1, 2).reshape(gh * b, gw * b)
        )

    # ------------------------------------------------------------ 추출
    def _ca(self, bgr: np.ndarray) -> list[tuple[np.ndarray, float]] | None:
        """워터마크를 심은 채널의 DWT 근사계수. 탐색에서 가장 비싼 단계라 재사용한다."""
        h, w = self._crop(bgr.shape[0]), self._crop(bgr.shape[1])
        if min(h, w) < self._span:
            return None
        yuv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV)
        out = []
        for ch in range(3):
            if self.scales[ch] <= 0:
                continue
            ca = pywt.wavedec2(yuv[:h, :w, ch].astype(np.float64), "haar", level=self.level)[0]
            out.append((np.ascontiguousarray(ca), self.scales[ch]))
        return out or None

    def _read(self, blocks: np.ndarray, scale: float):
        """블록별 (표, 가중치). 가중치는 삽입 때와 같은 활동도 판정이다.

        건너뛴 블록도 다수결에 섞으면 흡수될 줄 알았는데 아니었다 — 삽입 비율이 f 면
        표의 여유가 f 배로 줄고, 공격을 받으면 그 여유가 표본 잡음에 잡아먹힌다
        (실측: f=0.35 에서 벤치마크 최악 44/48 → 32/48). 그래서 추출도 같은 기준으로
        평탄한 블록을 빼고 센다. 받은 이미지에서 다시 계산하는 값이라 부가 정보가
        아니고, 사이드카 형식도 그대로다. 공격으로 기준선이 어긋나도 손해는 완만하다 —
        너무 낮게 잡히면 잡음 블록이 섞일 뿐이고, 너무 높게 잡히면 표본이 줄 뿐이다.
        """
        dct = _D @ blocks @ _D.T
        s = np.linalg.svd(dct, compute_uv=False)[:, 0]
        ac = _ac_norm(dct)
        return ((s % scale) > scale * 0.5).astype(np.float64), (ac >= _bar(ac, scale, MASK_READ))

    def _cells_from(self, cas, my: int = 0, mx: int = 0) -> np.ndarray | None:
        """근사계수를 (my, mx) 만큼 밀어 블록을 자르고, 주기 격자 칸별 평균을 낸다."""
        acc = []
        for ca, scale in cas:
            blocks, gh, gw = self._split(np.ascontiguousarray(ca[my:, mx:]))
            if gh < self.ph or gw < self.pw:
                return None
            score, keep = self._read(blocks, scale)
            hh, ww = gh // self.ph * self.ph, gw // self.pw * self.pw

            def fold(v):
                return (v.reshape(gh, gw)[:hh, :ww]
                        .reshape(hh // self.ph, self.ph, ww // self.pw, self.pw)
                        .sum(axis=(0, 2)))

            w = fold(keep.astype(np.float64))
            # 표본이 하나도 없는 칸은 0.5 (중립) — 어느 쪽으로도 기울이지 않는다.
            acc.append(np.where(w > 0, fold(score * keep) / np.maximum(w, 1), 0.5))
        return np.mean(acc, axis=0)

    def _pick(self, cells: np.ndarray, shift=(0, 0)) -> np.ndarray:
        """격자 칸 표에서 내 비트가 실린 칸만 골라낸다."""
        return np.roll(cells, shift, axis=(0, 1)).ravel()[self.cells]

    def _cells(self, bgr: np.ndarray, offset=(0, 0)) -> np.ndarray | None:
        cas = self._ca(bgr[offset[0] :, offset[1] :])
        return None if cas is None else self._cells_from(cas)

    def _bits(self, cells: np.ndarray, shift=(0, 0)) -> np.ndarray:
        return self._pick(cells, shift) * 255 > 127

    def decode(self, bgr: np.ndarray) -> np.ndarray:
        """빠른 경로: 크롭되지 않았다고 보고 그대로 읽는다."""
        if self.layout == "raster":
            return self._votes(bgr)
        cells = self._cells(bgr)
        return np.zeros(self.wm_len, bool) if cells is None else self._bits(cells)

    def _votes(self, bgr: np.ndarray) -> np.ndarray:
        """래스터 배치용. 같은 비트를 실은 블록끼리 평균낸다."""
        cas = self._ca(bgr)
        if cas is None:
            return np.zeros(self.wm_len, bool)
        acc = []
        for ca, scale in cas:
            blocks, gh, gw = self._split(ca)
            score, keep = self._read(blocks, scale)
            idx = self._index(gh, gw).ravel()
            used = (idx >= 0) & keep
            total = np.bincount(idx[used], weights=score[used], minlength=self.wm_len)
            count = np.bincount(idx[used], minlength=self.wm_len)
            acc.append(np.where(count > 0, total / np.maximum(count, 1), 0.5))
        return np.mean(acc, axis=0) * 255 > 127

    def detect(self, bgr: np.ndarray, target, deadline: float | None = None) -> dict:
        """크롭 대응: 정렬을 전수 탐색해 target 과 가장 맞는 것을 찾는다.

        픽셀 오프셋이 2**level 만큼 차이나면 근사계수가 그만큼 밀린 것과 같다.
        그래서 DWT 는 2**level 가지만 계산하고, 나머지는 계수를 잘라 재사용한다.

        시도 횟수를 함께 돌려준다 — 후보를 많이 볼수록 우연히 맞을 확률이 올라가므로
        판정 임계값은 호출부에서 시도 횟수를 반영해 정해야 한다.
        """
        target = np.asarray(target, bool)
        best = {"accuracy": 0.0, "offset": (0, 0), "shift": (0, 0), "inverted": False}
        trials = 0
        unit = 2**self.level
        for ry in range(unit):
            # 큰 사진에서는 정렬 하나를 도는 데도 시간이 걸린다. 바깥에서 준 기한을 지킨다.
            if deadline and time.monotonic() > deadline:
                break
            for rx in range(unit):
                cas = self._ca(bgr[ry:, rx:])
                if cas is None:
                    continue
                for my in range(self.block):
                    for mx in range(self.block):
                        cells = self._cells_from(cas, my, mx)
                        if cells is None:
                            continue
                        for bi in range(self.ph):
                            for bj in range(self.pw):
                                raw = float((self._bits(cells, (bi, bj)) == target).mean())
                                trials += 1
                                acc = max(raw, 1.0 - raw)
                                if acc > best["accuracy"]:
                                    best = {
                                        "accuracy": acc,
                                        "offset": (ry + my * unit, rx + mx * unit),
                                        "shift": (bi, bj),
                                        "inverted": raw < 0.5,
                                    }
        best["trials"] = trials
        return best
