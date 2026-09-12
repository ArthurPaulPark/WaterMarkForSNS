"""보이지 않는 워터마크 + Ed25519 서명. 전부 로컬에서만 동작한다."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import zlib
import warnings
from pathlib import Path

import cv2
import numpy as np

warnings.filterwarnings("ignore")
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from dwtdctsvd import DwtDctSvd  # noqa: E402


def _false_positive(matched: int, total: int, trials: int) -> float:
    """워터마크가 없는 이미지에서 우연히 이 정도로 맞을 확률."""
    tail = sum(math.comb(total, k) for k in range(matched, total + 1)) / 2**total
    p = min(1.0, 2 * tail)  # 극성 반전도 매치로 인정하므로 2배
    return 1.0 - (1.0 - p) ** trials

# 48비트. 비트당 표본은 32비트보다 1.5배 적지만, 증거 예산이 훨씬 커진다.
# 32비트로 크롭을 탐색하면 32/32 완전 일치를 요구받지만, 48비트는 46/48 로 충분하다.
# 그 여유 덕에 색보정 후보를 훨씬 촘촘히 훑을 수 있다.
NBITS = 48
# 원 라이브러리 기본값은 [0,36,0](크로마 U). JPEG 4:2:0이 크로마를 절반으로 깎아 최악 21/32까지 떨어졌다.
# 루마(Y)로 옮겨 모든 SNS 공격에서 32/32가 됐다. 강도를 36 위로 올리는 건 소용없다 —
# 양자화가 어두운 블록의 특이값을 몇 배로 부풀려 오히려 나빠진다. 대신 DWT 레벨을 올렸다.
# 워터마크를 거친 저주파 구조에 실어야 디노이즈·리사이즈 같은 제거 시도를 견딘다.
#
# 레벨 2 + 희소 배치를 쓴다. 격자 12x16=192칸 중 공개키가 고른 48칸에만 심는다.
# 키가 다르면 고르는 칸도 달라서, 남이 덮어써도 겹치지 않은 칸에 내 비트가 남는다
# (실측: 덮어쓴 뒤에도 41~44/48 로 검출). 조밀하게 심으면 나중에 쓴 사람이 전부 가져간다.
#
# 레벨 3 조밀 배치도 검토했지만 블록이 726개뿐이라 희소하게 나눌 여유가 없었고,
# 레벨 3 조밀 + 레벨 2 희소를 겹치는 방법은 두 층이 서로를 죽여 실패했다
# (생존층을 남의 레벨 3 쓰기를 버틸 만큼 키우면 내 레벨 3 태그가 30/48 로 무너진다).
SCALES = [180, 0, 0]
LEVEL = 2
TILE = (12, 16)  # 격자 칸 수. NBITS 보다 커야 희소 배치가 된다.
# 판정은 고정 비트수가 아니라 "우연히 이만큼 맞을 확률"로 한다.
# 크롭 탐색은 후보를 3만 개 넘게 보므로, 같은 32/32라도 증거력이 전혀 다르기 때문이다.
# 크롭 없이 읽히면 보통 1e-9 수준, 크롭 탐색으로 찾으면 1e-5 수준이 나온다.
MAX_FALSE_POSITIVE = 1e-4

# 양자화가 절대값 기준이라 이득이 곱해지면 어긋난다 (인스타 필터, 색보정 등).
# 되돌려 볼 후보들. 원래 값(1.0, 0)을 먼저 보고, 실패할 때만 나머지를 훑는다.
GAINS = (0.8, 0.85, 0.9, 1.0, 1.1, 1.15, 1.25, 1.4)
OFFSETS = (-24, -12, 0, 12, 24)

# 크롭 탐색에서도 같은 격자를 쓴다. 48비트라 200만 후보를 봐도 46/48 이면 오탐 1.6e-5 로
# 예산 안에 들어온다 (32비트였다면 완전 일치를 요구받아 이 조합은 못 찾았다).
# 보정 없음을 맨 앞에 둬서, 단순 크롭이면 첫 시도에 끝난다.
# 못 찾는 경우 40가지 색보정 x 크롭 전수탐색을 끝까지 도는데, 큰 사진이면 몇 분이 걸린다.
# 찾을 때는 조기 종료라 빠르지만 못 찾을 때가 문제다. 그래서 벽시계 예산을 둔다.
# 오탐 확률은 '실제로 본 후보 수'로 계산하므로 중간에 멈춰도 판정은 그대로 유효하다.
DEEP_BUDGET = 12.0  # 초 (2단계와 3단계를 합쳐서)

CROP_GAINS = ((1.0, 0),) + tuple(
    (a, b) for a in GAINS for b in OFFSETS if (a, b) != (1.0, 0)
)
KEY_PATH = Path(os.environ.get("WATERMARK_KEY", Path.home() / ".watermark" / "key.pem"))
JPEG_Q = 95

# ── 문장 레이어 ────────────────────────────────────────────────────────────
# 작성자 태그(48비트)에는 문장이 안 들어간다. 한글 한 문장이면 700비트가 넘는다.
# 그래서 별도 층을 쓴다. DWT 레벨 1은 블록이 8px이라 용량이 24배지만 그만큼 약하다.
# 반드시 태그를 먼저 심고 문장을 나중에 심어야 한다. 순서를 바꾸면 레벨 3 삽입이
# 레벨 1 계수를 흔들어 문장이 뭉개진다 (실측 768비트 중 230비트 손상).
MSG_LEVEL = 1
# 36이면 문장이 더 질기지만 태그의 크롭 복구가 무너진다 (48/48 → 37/48).
# 24는 문장이 업로드 경로를 무손실로 통과하면서 태그도 46/48 이상 유지한다.
MSG_SCALE = 24
# 비트당 최소 표본. 12 미만이면 스크린샷에서 깨지기 시작한다.
MSG_SAMPLES = 16
MSG_HEADER = 2  # 길이
MSG_CRC = 4     # 제대로 읽혔는지 판별용. 없으면 깨진 글자를 진짜로 착각한다.

# ── AI 학습 거부 선언 ───────────────────────────────────────────────────────
# IPTC Photo Metadata 2023.1 이 PLUS 어휘를 받아들여 표준화한 항목이다.
# 크롤러가 읽는 곳은 파일 메타데이터(XMP)이지 우리 워터마크가 아니므로 여기에 쓴다.
# 강제력은 없다 — 크롤러가 지켜줘야 의미가 있는 '의사 표시'다.
# 그리고 인스타·X 는 업로드 때 메타데이터를 지우므로, SNS 를 거치면 사라진다.
# 직접 배포하는 파일(포트폴리오·메일·다운로드)에서만 살아남는다.
PLUS_NS = "http://ns.useplus.org/ldf/xmp/1.0/"
DMI_PROHIBIT_AI = "http://ns.useplus.org/ldf/vocab/DMI-PROHIBITED-AIMLTRAINING"
XMP_NS = b"http://ns.adobe.com/xap/1.0/\x00"

# 지각 해시 판정 기준. 무관한 사진 20종을 재보니 최소 22비트 차이였다.
# 다만 표본이 합성 이미지라, 실제로 비슷한 풍경 사진끼리는 더 가까울 수 있다.
# 그래서 여유를 크게 두고, 판정도 "증거 보강"으로만 쓴다 (확증은 서명 쪽이다).
PHASH_SAME = 10    # 이하면 사실상 같은 사진 (재압축·축소·덮어쓰기가 2~8)
PHASH_EDITED = 16  # 이하면 편집된 같은 사진으로 볼 만하다

# 문장 없이 태그만 심은 사진을 알아보기 위한 흔적 탐지 임계값.
# 사진 수백 장으로 보정했다. 없는 사진 최대 +0.37, 있는 사진 최소 +0.67 → 0.6 에서 오탐 0건.
TRACE_THRESHOLD = 0.6
# 크롭된 사진은 정렬을 1024가지 훑어야 하는데, 그러면 없는 사진의 값도 올라간다
# (강한 줄무늬 패턴이 +0.70 까지 나왔다). 그래서 탐색 경로는 기준을 따로 높인다.
TRACE_SEARCH_THRESHOLD = 0.75
MAX_UPLOAD = 50 * 1024 * 1024
MIN_SIDE = 256

# 각 플랫폼이 최종적으로 내보내는 해상도. 여기에 미리 맞춰서 심어야 워터마크가 살아남는다.
PLATFORMS = {
    "instagram": {"label": "Instagram", "max_w": 1080, "max_h": 1350, "note": "피드 최대 1080px"},
    "x": {"label": "X (Twitter)", "max_w": 2048, "max_h": 2048, "note": "대형 이미지 2048px"},
    "youtube": {"label": "YouTube 썸네일", "max_w": 1280, "max_h": 720, "note": "1280x720 이내"},
    "original": {"label": "원본 크기 유지", "max_w": None, "max_h": None, "note": "리사이즈 없음"},
}


# ---------------------------------------------------------------- 키

def pub_hex(pub: Ed25519PublicKey) -> str:
    return pub.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()


def generate_key(passphrase: str | None = None, path: Path = KEY_PATH) -> str:
    """새 신원을 만든다. 기존 키를 덮어쓰면 과거에 심은 워터마크를 전부 증명 못 하게 되므로 거부한다."""
    if path.exists():
        raise FileExistsError(f"이미 키가 있습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    enc = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return pub_hex(key.public_key())


def load_key(passphrase: str | None = None, path: Path = KEY_PATH) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(
        path.read_bytes(), passphrase.encode() if passphrase else None
    )


def key_is_encrypted(path: Path = KEY_PATH) -> bool:
    return b"ENCRYPTED" in path.read_bytes() if path.exists() else False


def author_cells(pub_hex_str: str) -> np.ndarray:
    """공개키로 정해지는 격자 칸 48개. 키가 다르면 겹치는 칸이 평균 12개뿐이다.

    공개키에서 유도하므로 누구나 검증할 수 있다. 뒤집어 말하면 내 공개키를 아는 상대가
    프로그램을 고치면 내 칸만 노릴 수 있다 — 표적 공격까지 막지는 못한다.
    """
    seed = int.from_bytes(
        hashlib.sha256(bytes.fromhex(pub_hex_str) + b"cells").digest()[:8], "big")
    return np.sort(np.random.default_rng(seed).choice(TILE[0] * TILE[1], NBITS, replace=False))


def _codec(pub: str) -> DwtDctSvd:
    return DwtDctSvd(author_tag(pub), NBITS, SCALES, level=LEVEL,
                     tile=TILE, cells=author_cells(pub))


def author_tag(pub_hex_str: str) -> list[int]:
    """공개키 → 32비트 태그. 결정적이라 별도 DB가 필요 없다."""
    digest = hashlib.sha256(bytes.fromhex(pub_hex_str)).digest()
    return [(digest[i // 8] >> (7 - i % 8)) & 1 for i in range(NBITS)]


class AlreadyWatermarked(Exception):
    """이미 워터마크가 있는 사진에 덮어쓰려 할 때.

    같은 프로그램을 쓰는 상대에게만 유효한 안전장치다. 도용자는 애초에 이 프로그램을
    쓰지 않거나 이 검사를 지우면 그만이다. 보안 통제가 아니라 실수 방지 장치로 본다.
    """

    def __init__(self, mine: bool, found: str | None):
        self.mine = mine          # 내 워터마크인가 (공개키 대조, 정확)
        self.found = found        # 읽어낸 기존 문장 (CRC 검증, 정확)
        super().__init__("이미 워터마크가 있는 사진입니다")


def message_capacity(width: int, height: int) -> int:
    """이 크기의 이미지에 담을 수 있는 문장의 바이트 수 (UTF-8, 한글은 글자당 3바이트)."""
    blocks = (height // 8) * (width // 8)  # 레벨 1 의 4x4 블록 수
    return max(0, blocks // MSG_SAMPLES // 8 - MSG_HEADER - MSG_CRC)


def capacity_for(image_bytes: bytes, platform: str) -> int:
    """이 사진을 이 플랫폼용으로 줄였을 때 담을 수 있는 문장 바이트 수."""
    spec = PLATFORMS[platform]
    base = _fit(_decode(image_bytes), spec["max_w"], spec["max_h"])
    return message_capacity(base.shape[1], base.shape[0])


def _frame(text: str, capacity: int) -> list[int]:
    body = len(text.encode()).to_bytes(MSG_HEADER, "big") + text.encode()
    body = body.ljust(MSG_HEADER + capacity, b"\0")
    frame = body + zlib.crc32(body).to_bytes(MSG_CRC, "big")
    return [(byte >> (7 - i)) & 1 for byte in frame for i in range(8)]


def _unframe(bits) -> str | None:
    """CRC 가 맞을 때만 문장을 돌려준다. 한 비트만 틀려도 글자가 깨지기 때문이다."""
    raw = np.packbits(np.asarray(bits, np.uint8)).tobytes()
    body, crc = raw[:-MSG_CRC], raw[-MSG_CRC:]
    if zlib.crc32(body).to_bytes(MSG_CRC, "big") != crc:
        return None
    length = int.from_bytes(body[:MSG_HEADER], "big")
    if length > len(body) - MSG_HEADER:
        return None
    try:
        return body[MSG_HEADER : MSG_HEADER + length].decode()
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------- 이미지

def _decode(image_bytes: bytes) -> np.ndarray:
    if len(image_bytes) > MAX_UPLOAD:
        raise ValueError("파일이 너무 큽니다 (최대 50MB)")
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("이미지를 읽을 수 없습니다")
    return img


def _fit(img: np.ndarray, max_w: int | None, max_h: int | None) -> np.ndarray:
    """비율을 유지하며 축소만 한다. 크롭도 확대도 하지 않는다."""
    if not max_w:
        return img
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale == 1.0:
        return img
    return cv2.resize(
        img, (max(1, round(w * scale)), max(1, round(h * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _canon(claim: dict) -> bytes:
    return json.dumps(claim, sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------- 삽입

def protect(image_bytes: bytes, platform: str, key: Ed25519PrivateKey | None = None,
            message: str = "", overwrite: bool = False, no_ai: bool = True) -> dict:
    """사진에 워터마크를 심는다.

    key 를 주면 작성자 태그(48비트)를 심고 사이드카에 서명한다.
    key 없이 message 만 주면 문장만 심는다 — 서명이 없으므로 원작자 증명은 되지 않는다.
    """
    if platform not in PLATFORMS:
        raise ValueError(f"알 수 없는 플랫폼: {platform}")
    if key is None and not message:
        raise ValueError("키 없이 심으려면 문장이 필요합니다")
    spec = PLATFORMS[platform]
    base = _fit(_decode(image_bytes), spec["max_w"], spec["max_h"])
    if min(base.shape[:2]) < MIN_SIDE:
        raise ValueError(f"이미지가 너무 작습니다 (짧은 변 최소 {MIN_SIDE}px)")

    pub = pub_hex(key.public_key()) if key else None
    # 삽입은 양자화라 이전 값을 지운다. 덮어쓰기 전에 기존 워터마크를 확인한다.
    #   * 내 태그는 공개키 대조로 정확히 잡힌다.
    #   * 남의 문장은 CRC 가 맞으면 확증이다.
    # 문장 없이 태그만 심은 사진은 알아낼 수 없다. 희소 배치라 블록의 1/4 에만 표시가
    # 있어서 통계로는 정상 사진과 구별되지 않았다 (문서형 이미지에서 오탐).
    # 대신 덮어써도 워터마크가 살아남으므로, 경고를 놓쳐도 피해가 크지 않다.
    already_mine = bool(pub) and check_watermark(image_bytes, pub, deep=False)["match"]
    found = read_message(image_bytes)
    already = already_mine or found is not None
    if already and not overwrite:
        raise AlreadyWatermarked(already_mine, found)

    # 키가 없으면 작성자 태그를 심을 게 없다. 문장 레이어만 쓴다.
    marked = _codec(pub).encode(base) if pub else base

    capacity = message_capacity(marked.shape[1], marked.shape[0])
    if message:
        if len(message.encode()) > capacity:
            raise ValueError(
                f"문장이 너무 깁니다 — {len(message.encode())}바이트, "
                f"이 크기에는 {capacity}바이트까지"
            )
        bits = _frame(message, capacity)
        marked = DwtDctSvd(bits, len(bits), [MSG_SCALE, 0, 0],
                           level=MSG_LEVEL, layout="raster").encode(marked)

    ok, buf = cv2.imencode(".jpg", marked, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    if not ok:
        raise RuntimeError("JPEG 인코딩 실패")
    out = buf.tobytes()
    # 서명이 선언까지 덮도록 해시를 내기 전에 넣는다.
    if no_ai:
        out = add_declaration(out)

    if key is None:
        # 서명할 키가 없으므로 사이드카도 없다. 서명 없는 기록은 누구나 위조할 수 있어
        # 증명에 쓸 수 없고, 있으면 오히려 증거인 줄 착각하게 만든다.
        return {
            "image": out,
            "sidecar": None,
            "psnr": round(float(cv2.PSNR(base, marked)), 1),
            "size": [int(marked.shape[1]), int(marked.shape[0])],
            "capacity": capacity,
            "already_marked": already,
            "no_ai": no_ai,
        }

    claim = {
        "v": 1,
        "alg": "dwtDctSvd+ed25519",
        "bits": NBITS,
        "pub": pub,
        "platform": platform,
        "size": [int(marked.shape[1]), int(marked.shape[0])],
        # 문장도 서명이 덮는다. 편집으로 이미지 속 문장이 깨져도 원래 문구를 증명할 수 있다.
        "message": message,
        "data_mining": DMI_PROHIBIT_AI if no_ai else None,
        # 원본 사진의 모양. 워터마크가 지워져도 파생 관계를 보일 수 있다.
        "phash": perceptual_hash(_decode(image_bytes)),
        "sha256_original": hashlib.sha256(image_bytes).hexdigest(),
        "sha256_protected": hashlib.sha256(out).hexdigest(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return {
        "image": out,
        "sidecar": {"claim": claim, "sig": key.sign(_canon(claim)).hex()},
        "psnr": round(float(cv2.PSNR(base, marked)), 1),
        "size": claim["size"],
        "capacity": capacity,
        "already_marked": already,
        "no_ai": no_ai,
    }


# ---------------------------------------------------------------- 검증

def _regain(img: np.ndarray, alpha: float, beta: float) -> np.ndarray:
    """밝기/대비 조작을 되돌린다."""
    return np.clip((img.astype(np.float32) - beta) / alpha, 0, 255).astype(np.uint8)


def check_watermark(
    image_bytes: bytes,
    claimed_pub: str,
    ref_size: list[int] | None = None,
    deep: bool = True,
) -> dict:
    """워터마크가 claimed_pub 소유자를 가리키는지 본다.

    변형마다 되돌리는 방법이 다르므로 단계적으로 넓혀 간다. 싼 것부터 보고,
    앞 단계에서 확실히 나오면 거기서 멈춘다.
      1. 그대로 읽기 (+ 리사이즈됐다면 원래 크기로 되돌린 것도)
      2. 밝기/대비를 되돌려 가며 읽기
      3. 크롭 정렬 전수 탐색 (밝기/대비 후보마다)

    후보를 많이 볼수록 우연히 맞을 확률이 올라가므로, 판정은 지금까지 본 후보 수를
    반영한 오탐 확률로 한다. 크롭까지 가면 32/32 여야만 통과한다.
    """
    img = _decode(image_bytes)
    candidates = [img]
    if ref_size and (img.shape[1], img.shape[0]) != tuple(ref_size):
        candidates.append(cv2.resize(img, tuple(ref_size), interpolation=cv2.INTER_LANCZOS4))

    target = np.array(author_tag(claimed_pub))
    codec = _codec(claimed_pub)
    best = {"accuracy": 0.0, "inverted": False, "offset": (0, 0), "gain": None}
    trials = 0

    def consider(bits, gain=None, offset=(0, 0)) -> None:
        nonlocal best
        raw = float(np.mean(np.asarray(bits) == target))
        # 디코더가 고정 임계값을 쓰는 탓에 밝기가 흔들리면 32비트가 통째로 반전된다.
        # 특정 패턴이 정확히 뒤집혀 나올 확률은 무시할 수준이라, 반전도 매치로 인정한다.
        if max(raw, 1.0 - raw) > best["accuracy"]:
            best = {"accuracy": max(raw, 1.0 - raw), "inverted": raw < 0.5,
                    "offset": tuple(offset), "gain": gain}

    def verdict() -> float:
        return _false_positive(round(best["accuracy"] * NBITS), NBITS, trials)

    for cand in candidates:
        consider(codec.decode(cand))
        trials += 1

    deadline = time.monotonic() + DEEP_BUDGET
    if verdict() > MAX_FALSE_POSITIVE and deep:
        for cand in candidates:
            for alpha in GAINS:
                if time.monotonic() > deadline:
                    break
                for beta in OFFSETS:
                    if (alpha, beta) == (1.0, 0):
                        continue
                    consider(codec.decode(_regain(cand, alpha, beta)), gain=(alpha, beta))
                    trials += 1

    searched = truncated = False
    if verdict() > MAX_FALSE_POSITIVE and deep:
        before, mark = verdict(), dict(best)
        # 크롭됐다면 원래 크기로 늘린 후보는 의미가 없으므로 원본만 본다.
        for alpha, beta in CROP_GAINS:
            plain = (alpha, beta) == (1.0, 0)
            hit = codec.detect(img if plain else _regain(img, alpha, beta), target,
                               deadline=deadline)
            trials += hit["trials"]
            if hit["accuracy"] > best["accuracy"]:
                best = {"accuracy": hit["accuracy"], "inverted": hit["inverted"],
                        "offset": tuple(hit["offset"]),
                        "gain": None if plain else (alpha, beta)}
            if verdict() <= MAX_FALSE_POSITIVE:
                break  # 증거가 충분해졌으면 남은 후보는 볼 필요가 없다
            if time.monotonic() > deadline:
                truncated = True
                break
        truncated = truncated or time.monotonic() > deadline
        if verdict() < before:
            searched = True
        else:
            best = mark  # 탐색이 도움이 안 됐으면 이전 판단을 남긴다

    fp = verdict()
    return {
        "accuracy": round(best["accuracy"], 4),
        "matched_bits": int(round(best["accuracy"] * NBITS)),
        "total_bits": NBITS,
        "inverted": bool(best["inverted"]),
        "regained": best["gain"] is not None,
        "cropped": searched and tuple(best["offset"]) != (0, 0),
        "searched": searched,
        "truncated": truncated,
        "trials": trials,
        "false_positive": fp,
        "match": fp <= MAX_FALSE_POSITIVE,
    }


def _xmp_packet(value: str) -> bytes:
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f'<rdf:Description rdf:about="" xmlns:plus="{PLUS_NS}">'
        f"<plus:DataMining>{value}</plus:DataMining>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    ).encode()


def add_declaration(jpeg: bytes, value: str = DMI_PROHIBIT_AI) -> bytes:
    """JPEG 에 XMP 로 학습 거부 선언을 넣는다 (APP1 세그먼트)."""
    payload = XMP_NS + _xmp_packet(value)
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("XMP 가 너무 큽니다")
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return jpeg[:2] + segment + jpeg[2:]  # SOI 바로 뒤


def read_declaration(jpeg: bytes) -> str | None:
    """파일에 적힌 데이터 마이닝 선언. 없으면 None."""
    i = 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF:
        marker = jpeg[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:  # 이미지 데이터 시작 — 뒤로는 메타데이터가 없다
            break
        size = int.from_bytes(jpeg[i + 2 : i + 4], "big")
        body = jpeg[i + 4 : i + 2 + size]
        if marker == 0xE1 and body.startswith(XMP_NS):
            found = re.search(rb"<plus:DataMining>([^<]*)</plus:DataMining>", body)
            if found:
                return found.group(1).decode(errors="replace")
        i += 2 + size
    return None


def perceptual_hash(img: np.ndarray) -> str:
    """사진의 '모양'을 63비트로 요약한다.

    워터마크는 덮어쓸 수 있지만 사진 자체는 못 지운다. 그래서 원본의 이 값을 사이드카에
    서명해 두면, 워터마크가 통째로 지워진 뒤에도 "이 사진은 내 원본에서 나왔다"를 보일 수 있다.

    실측(63비트 중 다른 비트 수): 워터마크를 덮어쓴 도용본 4, 재압축·축소 2, 밝기 조작 8,
    가장자리 크롭 16, 무관한 사진 30~32. 정중앙 50% 크롭은 26이라 애매하다.
    """
    small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (32, 32),
                       interpolation=cv2.INTER_AREA)
    low = cv2.dct(small.astype(np.float64))[:8, :8].flatten()[1:]  # DC 는 밝기라 뺀다
    bits = low > np.median(low)
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def phash_distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def read_message(image_bytes: bytes, ref_size: list[int] | None = None) -> str | None:
    """이미지에 심긴 문장을 읽는다. 확실하지 않으면 아무것도 돌려주지 않는다.

    래스터 배치라 크롭 내성이 없고, 원래 크기가 아니면 읽히지 않는다.
    ref_size 를 주면 그 크기로 되돌린 뒤 읽는다.
    """
    img = _decode(image_bytes)
    if ref_size and (img.shape[1], img.shape[0]) != tuple(ref_size):
        img = cv2.resize(img, tuple(ref_size), interpolation=cv2.INTER_LANCZOS4)
    capacity = message_capacity(img.shape[1], img.shape[0])
    if capacity <= 0:
        return None
    nbits = (capacity + MSG_HEADER + MSG_CRC) * 8
    bits = DwtDctSvd(wm_len=nbits, scales=[MSG_SCALE, 0, 0],
                     level=MSG_LEVEL, layout="raster").decode(img)
    # 밝기가 흔들리면 통째로 반전돼 나올 수 있다. CRC 가 지켜주므로 뒤집어도 한 번 본다.
    return _unframe(bits) or _unframe(~np.asarray(bits, bool))


def check_sidecar(image_bytes: bytes, sidecar: dict) -> dict:
    """사이드카 서명을 검증한다. 이게 통과하면 개인키 보유자만이 이 주장을 만들 수 있었다."""
    claim = sidecar["claim"]
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(claim["pub"])).verify(
            bytes.fromhex(sidecar["sig"]), _canon(claim)
        )
        signature_valid = True
    except (InvalidSignature, ValueError, KeyError):
        signature_valid = False
    digest = hashlib.sha256(image_bytes).hexdigest()
    out = {
        "signature_valid": signature_valid,
        "is_protected_file": digest == claim.get("sha256_protected"),
        "is_original_file": digest == claim.get("sha256_original"),
        "claim": claim,
    }
    # 워터마크가 지워졌어도 사진의 모양은 남는다. 서명된 원본과 얼마나 닮았는지 본다.
    signed = claim.get("phash")
    if signed:
        try:
            gap = phash_distance(signed, perceptual_hash(_decode(image_bytes)))
        except (ValueError, KeyError):
            gap = None
        if gap is not None:
            out["phash_distance"] = gap
            out["phash_verdict"] = ("same" if gap <= PHASH_SAME
                                    else "edited" if gap <= PHASH_EDITED else "different")
    return out


# ---------------------------------------------------------------- 강인성 벤치마크

def _jpeg(img: np.ndarray, q: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _attacks(platform: str) -> dict:
    q = 80 if platform == "instagram" else 85

    def scaled(im, f):
        return cv2.resize(im, (int(im.shape[1] * f), int(im.shape[0] * f)),
                          interpolation=cv2.INTER_AREA)

    def middle(im, f):
        h, w = im.shape[:2]
        y, x = int(h * (1 - f) / 2), int(w * (1 - f) / 2)
        return im[y : y + int(h * f), x : x + int(w * f)]

    def denoise(im):
        return cv2.fastNlMeansDenoisingColored(im, None, 10, 10, 7, 21)

    return {
        # 실제 SNS 업로드 경로
        "업로드 재압축": lambda im: _jpeg(im, q),
        "강한 재압축 (q60)": lambda im: _jpeg(_jpeg(im, q), 60),
        "스크린샷 캡처": lambda im: cv2.GaussianBlur(_jpeg(im, 70), (3, 3), 0.5),
        "저장 후 50% 축소": lambda im: scaled(_jpeg(im, q), 0.5),
        # 도용자가 흔히 하는 편집
        "가장자리 10% 크롭": lambda im: _jpeg(middle(im, 0.9), q),
        "정중앙 40%만 남김": lambda im: _jpeg(middle(im, 0.4), q),
        # 작정한 제거 시도
        "노이즈 제거 (제거 시도)": lambda im: _jpeg(denoise(im), q),
        "노이즈 제거 + 강한 재압축": lambda im: _jpeg(denoise(im), 40),
        "12%까지 축소 후 복원": lambda im: cv2.resize(
            scaled(im, 0.12), (im.shape[1], im.shape[0]), interpolation=cv2.INTER_LANCZOS4),
        # 탐색 격자에 없는 값을 일부러 쓴다. 자기 답안지로 채점하지 않기 위해서다.
        "밝기/대비 조작": lambda im: _jpeg(cv2.convertScaleAbs(im, alpha=1.19, beta=7), q),
        "크롭 + 밝기/대비": lambda im: _jpeg(cv2.convertScaleAbs(
            middle(im, 0.5), alpha=1.19, beta=7), q),
    }


def benchmark(image_bytes: bytes, platform: str, key: Ed25519PrivateKey) -> list[dict]:
    """실제로 SNS를 거치고 편집을 당한 뒤에도 읽히는지 측정한다.

    공격받은 사진의 화질도 함께 잰다. 워터마크를 지울 만큼 센 공격은 사진 자체를
    망가뜨린다는 것이 이 도구가 기댈 수 있는 유일한 보장이기 때문이다.
    """
    result = protect(image_bytes, platform, key)
    pub = pub_hex(key.public_key())
    ref = result["size"]
    clean = _decode(result["image"])
    rows = []
    for name, attack in _attacks(platform).items():
        attacked = attack(clean)
        ok, buf = cv2.imencode(".png", attacked)
        check = check_watermark(buf.tobytes(), pub, ref_size=ref)
        same = attacked if attacked.shape == clean.shape else cv2.resize(
            attacked, (clean.shape[1], clean.shape[0]), interpolation=cv2.INTER_LANCZOS4)
        rows.append({
            "attack": name,
            "damage_psnr": round(float(cv2.PSNR(clean, same)), 1),
            **check,
        })
    return rows
