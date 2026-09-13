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
    # 가린 색과 바깥 색 사이의 중간값이 하나라도 있으면 페더링이 있다는 뜻이다.
    # 예전엔 out.size*0.02(9,600화소)까지 봐줬는데, 실측 중간값 화소는 0개라 2px
    # 페더링(약 3,000화소)이 들어와도 그 문턱을 통과했다 — 시험이 이름값을 못 했다.
    mid = ((out > 60) & (out < 180)).sum()
    assert mid == 0, f"경계가 번졌다 — 중간값 화소 {mid}개"


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

    난수가 실제로 들어갔다는 뜻이고, 결정론적 역산의 전제를 깬다. solid 든
    mosaic 이든 마찬가지다 — mosaic 이 재식별을 못 막는다고 이 성질까지 없는
    것은 아니다.
    """
    src = photo()
    face = {"x": .3, "y": .3, "w": .3, "h": .3, "grow": 1.0}
    for mode in (wm.MASK_SOLID, wm.MASK_MOSAIC):
        a = wm.mask_faces(src.copy(), [dict(face, mode=mode)])
        b = wm.mask_faces(src.copy(), [dict(face, mode=mode)])
        assert not np.array_equal(a, b), f"{mode}: 두 번 돌려도 결과가 같다"


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
    """가려진 출력에서 원본을 골라낼 수 없다 — 그건 solid 얘기고, mosaic 은 아니다.

    후보 200개 중 하나를 고르는 문제라 우연은 0.5%다. 실측 2.0%는 정확히
    우연은 아니다 — 양자화된 중앙값 색(SOLID_LEVELS 16단계)이 설계상 그대로
    남기 때문이다.

    mosaic 에는 통과선을 두지 않는다. 기본 상수(블록4/지터12)에서 재식별은
    100.0%다 — 지터를 24로 올려도 100.0%, 블록을 3으로 줄여도 100.0%로,
    통과할 만한 조합이 없다(전체 측정표: docs/superpowers/specs/2026-09-13-face-masking-design.md).
    통과 기준을 mosaic 이 넘을 수 있는 값으로 낮추면 "가려준다"는 거짓말이
    된다. 그래서 여기서는 실제로 지켜야 할 불변식만 확인한다: 기본값(solid)이
    선택지(mosaic)보다 압도적으로 강해야 한다는 것.
    """
    chance = 1 / 200
    solid = _reid_top1(wm.MASK_SOLID)
    mosaic = _reid_top1(wm.MASK_MOSAIC)
    print(f"    재식별 top-1 — 단색 {solid:.1%}, 모자이크 {mosaic:.1%} (우연 {chance:.1%})")
    assert solid <= 0.02, f"단색인데 재식별이 된다: {solid:.1%}"
    assert solid < mosaic / 10, (
        f"기본값(solid)이 선택지(mosaic)보다 압도적으로 강해야 한다: "
        f"solid {solid:.1%} vs mosaic {mosaic:.1%}"
    )


def test_remaining_samples_are_counted():
    """가린 영역에 남은 독립 표본 수를 세어 기록하고, 실제로 그 안에 갇히는지 잰다.

    복원 가능성은 이 숫자가 결정한다. mosaic 쪽은 통과선이 없다 — 정보용으로만
    찍는다(위 재식별 시험 참고).

    예전 판은 `solid_samples = 3` 을 상수로 박아두고 `assert solid_samples == 3` 만
    했다 — `_fill_solid` 를 어떻게 고쳐도(중앙값 대신 평균을 쓰든, 양자화를
    빼먹든, `SOLID_LEVELS` 를 256 으로 올리든) 통과하는 항진명제였다. 여기서는
    실제로 `_fill_solid` 를 돌려 채널마다 몇 가지 기준색이 나오는지 재고, 그 값이
    `SOLID_LEVELS` 를 넘지 않는지 확인한다 — 양자화 구현이 깨지면 이 시험이 잡는다.
    """
    step = 256.0 / wm.SOLID_LEVELS
    rng = np.random.default_rng(11)
    buckets = set()
    for level in range(0, 256, 3):                     # 채널값 전 구간을 촘촘히 훑는다
        patch = np.full((2, 2, 3), level, np.uint8)
        inside = np.ones((2, 2), bool)
        out = wm._fill_solid(patch, inside, rng)
        # 기준색은 항상 칸 한가운데(k*step + step/2)에 있고 잡음 폭(SOLID_NOISE)이
        # 칸 절반보다 작으므로, floor(값/step) 은 잡음에 흔들리지 않고 원래 칸
        # 번호를 그대로 복원한다 — round 를 쓰면 기준색이 반올림 경계 바로 위에
        # 앉아 있어 잡음의 부호에 따라 칸이 갈린다(측정 오차가 생긴다).
        for c in range(3):
            buckets.add(int(out[0, 0, c] // step))
    solid_samples = 3                                   # 양자화된 색 세 개(RGB 한 벌)
    mosaic_samples = wm.MOSAIC_BLOCKS ** 2 * 3           # 블록마다 색 세 개
    print(f"    남은 표본 — 단색 {solid_samples}개, 모자이크 {mosaic_samples}개"
          f" (관측된 기준색 종류: {len(buckets)}/{wm.SOLID_LEVELS})")
    assert len(buckets) <= wm.SOLID_LEVELS, (
        f"단색 채우기가 SOLID_LEVELS({wm.SOLID_LEVELS})보다 많은 기준색을 실제로 "
        f"낸다 — 관측 {len(buckets)}가지. _fill_solid 의 양자화가 깨졌다."
    )


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
    assert r["matched_bits"] == wm.NBITS, f"{r['matched_bits']}/{wm.NBITS}"


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


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print("  ✓", t.__name__)
    print(f"{len(TESTS)}개 통과")
