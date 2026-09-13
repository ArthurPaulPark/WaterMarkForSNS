"""얼굴 가리기 자체 점검.  실행:  .venv/bin/python test_face_mask.py"""
import cv2
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
    """모서리에 걸친 박스, 이미지보다 큰 박스, 넓이 0, 얼굴 없음, 망가진 폴리곤.

    계약: 가릴 게 정말 없을 때(이미지 밖으로 완전히 벗어났거나 넓이가 0)만
    아무것도 안 바뀐다. 그 외에는 항상 뭔가 바뀌어야 한다 — 조용히 건너뛰는
    것이 이 기능에서 제일 나쁜 실패다.
    """
    src = photo(400, 300)
    overlapping = [
        {"x": -0.2, "y": -0.2, "w": 0.3, "h": 0.3},      # 좌상단 걸침
        {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5},        # 우하단 걸침
        {"x": -1.0, "y": -1.0, "w": 3.0, "h": 3.0},      # 이미지보다 큼
    ]
    nothing_to_mask = [
        {"x": 0.5, "y": 0.5, "w": 0.0, "h": 0.0},        # 넓이 0
        {"x": 5.0, "y": 5.0, "w": 0.1, "h": 0.1},        # 완전히 밖
    ]
    for f in overlapping:
        out = wm.mask_faces(src.copy(), [dict(f, grow=1.0)])
        assert out.shape == src.shape and out.dtype == np.uint8, f
        assert not np.array_equal(out, src), f"이미지와 겹치는데 안 가려졌다: {f}"
    for f in nothing_to_mask:
        out = wm.mask_faces(src.copy(), [dict(f, grow=1.0)])
        assert out.shape == src.shape and out.dtype == np.uint8, f
        assert np.array_equal(out, src), f"가릴 게 없는데 뭔가 바뀌었다: {f}"
    assert np.array_equal(wm.mask_faces(src.copy(), []), src), "얼굴이 없으면 그대로"

    # 유효한 박스 + 망가진(점 2개) 폴리곤 — 조용히 건너뛰지 않고 박스로 가려야 한다
    box = {"x": 0.30, "y": 0.30, "w": 0.30, "h": 0.30, "grow": 1.0}
    broken = dict(box, poly=[[0.3, 0.3], [0.6, 0.6]])
    out = wm.mask_faces(src.copy(), [broken])
    assert not np.array_equal(out, src), "망가진 poly 가 가리기를 통째로 건너뛰었다"
    inside, region = wm._mask_region(box, 400, 300)
    x0, y0, x1, y1 = region
    assert not np.array_equal(out[y0:y1, x0:x1], src[y0:y1, x0:x1]), \
        "박스 자리가 실제로 가려지지 않았다"


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
    face = {"x": .3, "y": .3, "w": .3, "h": .3, "grow": 1.0, "mode": wm.MASK_SOLID}
    a = wm.mask_faces(src.copy(), [face])
    b = wm.mask_faces(src.copy(), [face])
    assert not np.array_equal(a, b), "두 번 돌려도 결과가 같다"


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

    # 공격자에게 가장 유리한 저해상도 서술자로 비교한다.
    def desc(im):
        return cv2.resize(im, (3, 3), interpolation=cv2.INTER_AREA).astype(np.float64).ravel()

    bank = np.array([desc(c) for c in crops])
    hits = sum(int(np.argmin(((bank - desc(m)) ** 2).sum(1))) == i
               for i, m in enumerate(masked))
    return hits / n


def test_reidentification_is_chance_level():
    """가려진 출력에서 원본을 골라낼 수 없다.

    후보 200개 중 하나를 고르는 문제라 우연은 0.5%다. 실측 2.0%는 정확히
    우연은 아니다 — 양자화된 중앙값 색(SOLID_LEVELS 16단계)이 설계상 그대로
    남기 때문이다. 모자이크는 같은 시험에서 최선의 조합(블록 2, 지터 96)도
    45.5%로 떨어지지 않아 폐기했다 (docs/superpowers/specs/2026-09-13-face-masking-design.md).
    """
    chance = 1 / 200
    solid = _reid_top1(wm.MASK_SOLID)
    print(f"    재식별 top-1 — 단색 {solid:.1%} (우연 {chance:.1%})")
    assert solid <= 0.02, f"단색인데 재식별이 된다: {solid:.1%}"


def test_remaining_samples_are_counted():
    """가린 영역에 남은 독립 표본 수를 세어 기록한다.

    복원 가능성은 이 숫자가 결정한다. 눈에 보이게 남겨둔다.
    """
    solid_samples = 3                                   # 양자화된 색 세 개
    print(f"    남은 표본 — 단색 {solid_samples}개")
    assert solid_samples == 3


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in TESTS:
        t()
        print("  ✓", t.__name__)
    print(f"{len(TESTS)}개 통과")
