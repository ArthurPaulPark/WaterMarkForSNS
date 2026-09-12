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
