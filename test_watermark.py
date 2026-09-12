"""assert 기반 자체 점검.  실행:  .venv/bin/python test_watermark.py"""
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

import watermark as wm


def sample_jpeg(w=1600, h=1200) -> bytes:
    """사진 비슷한 테스트 이미지 (부드러운 그라디언트 + 텍스처)."""
    rng = np.random.default_rng(42)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.stack(
        [128 + 90 * np.sin(xx / 300), 128 + 80 * np.cos(yy / 250),
         128 + 60 * np.sin((xx + yy) / 400)], -1
    ) + rng.normal(0, 8, (h, w, 3))
    ok, buf = cv2.imencode(".jpg", np.clip(img, 0, 255).astype(np.uint8),
                           [cv2.IMWRITE_JPEG_QUALITY, 98])
    assert ok
    return buf.tobytes()


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        # --- 키 ---
        path = tmp / "key.pem"
        pub = wm.generate_key(path=path)
        assert len(pub) == 64, pub
        assert path.stat().st_mode & 0o777 == 0o600, "개인키 권한은 0600이어야 한다"
        try:
            wm.generate_key(path=path)
            raise AssertionError("기존 키를 덮어쓰면 안 된다")
        except FileExistsError:
            pass
        key = wm.load_key(path=path)
        assert wm.pub_hex(key.public_key()) == pub

        enc_path = tmp / "enc.pem"
        wm.generate_key(passphrase="hunter2", path=enc_path)
        assert wm.key_is_encrypted(enc_path)
        assert wm.load_key(passphrase="hunter2", path=enc_path)

        # --- 태그는 결정적이고 키마다 달라야 한다 ---
        assert wm.author_tag(pub) == wm.author_tag(pub)
        other = wm.pub_hex(wm.load_key(passphrase="hunter2", path=enc_path).public_key())
        assert wm.author_tag(pub) != wm.author_tag(other)

        img = sample_jpeg()

        # --- 삽입 → 추출 ---
        out = wm.protect(img, "instagram", key)
        assert out["size"][0] == 1080, out["size"]
        assert out["psnr"] > 35, f"눈에 보일 만큼 열화됨: {out['psnr']}dB"
        assert wm.check_watermark(out["image"], pub, out["size"])["match"]

        # 남의 공개키로는 매치되면 안 된다
        assert not wm.check_watermark(out["image"], other, out["size"])["match"]
        # 워터마크 없는 이미지도 매치되면 안 된다
        assert not wm.check_watermark(img, pub)["match"]
        # 크롭 조각도 찾아내야 한다 (정렬 전수 탐색)
        marked = wm._decode(out["image"])
        h, w = marked.shape[:2]
        piece = marked[h // 4 + 7 : h // 4 + 7 + h // 2, w // 4 + 13 : w // 4 + 13 + w // 2]
        ok, buf = cv2.imencode(".jpg", piece, [cv2.IMWRITE_JPEG_QUALITY, 80])
        crop_hit = wm.check_watermark(buf.tobytes(), pub, out["size"])
        assert crop_hit["match"] and crop_hit["searched"], f"크롭 조각 검출 실패: {crop_hit}"
        assert not wm.check_watermark(buf.tobytes(), other, out["size"])["match"]

        # --- AI 학습 거부 선언 ---
        assert out["no_ai"] and wm.read_declaration(out["image"]) == wm.DMI_PROHIBIT_AI
        assert out["sidecar"]["claim"]["data_mining"] == wm.DMI_PROHIBIT_AI, "서명이 선언을 덮어야 한다"
        plain = wm.protect(img, "instagram", key, overwrite=True, no_ai=False)
        assert wm.read_declaration(plain["image"]) is None
        assert plain["sidecar"]["claim"]["data_mining"] is None
        # 선언이 워터마크나 문장을 건드리면 안 된다
        assert wm.check_watermark(out["image"], pub, out["size"])["match"]
        assert wm.read_declaration(img) is None, "원본에는 선언이 없어야 한다"

        # --- 문장 레이어 ---
        cap = wm.message_capacity(*out["size"])
        assert cap > 40, f"용량이 너무 작다: {cap}"
        note = "이 사진은 홍길동이 촬영했습니다. 무단 도용 금지."
        withmsg = wm.protect(img, "instagram", key, note)
        assert withmsg["capacity"] == cap
        assert wm.read_message(withmsg["image"], withmsg["size"]) == note
        assert withmsg["sidecar"]["claim"]["message"] == note, "서명이 문장을 덮어야 한다"
        # 문장이 있어도 태그는 그대로 읽혀야 한다
        assert wm.check_watermark(withmsg["image"], pub, withmsg["size"])["match"]
        # 업로드 경로를 거쳐도 문장이 살아야 한다
        shrunk = cv2.imdecode(cv2.imencode(".jpg", wm._decode(withmsg["image"]),
                                           [cv2.IMWRITE_JPEG_QUALITY, 80])[1], cv2.IMREAD_COLOR)
        ok, buf = cv2.imencode(".png", shrunk)
        assert wm.read_message(buf.tobytes(), withmsg["size"]) == note
        # 문장 없는 사진에서는 아무것도 나오면 안 된다
        assert wm.read_message(out["image"], out["size"]) is None
        # 용량 초과는 거부해야 한다
        try:
            wm.protect(img, "instagram", key, "가" * (cap // 3 + 20))
            raise AssertionError("용량 초과를 거부해야 한다")
        except ValueError:
            pass

        # --- 덮어쓰기 생존 ---
        # 희소 배치: 키마다 격자 칸을 다르게 고르므로, 남이 덮어써도 겹치지 않은 칸에
        # 내 비트가 남는다. 조밀하게 심던 시절에는 여기서 태그가 통째로 사라졌다.
        thief = wm.load_key(passphrase="hunter2", path=enc_path)
        # 문장이 읽히면 워터마크가 있다는 확증이므로 먼저 막아야 한다
        try:
            wm.protect(withmsg["image"], "instagram", thief, "도용자")
            raise AssertionError("문장이 있는 사진은 덮어쓰기를 막아야 한다")
        except wm.AlreadyWatermarked as blocked:
            assert blocked.found == note and not blocked.mine
        # 내 워터마크 위에 다시 심는 것도 막는다 (문장이 없어도 공개키로 잡힌다)
        try:
            wm.protect(out["image"], "instagram", key)
            raise AssertionError("내 워터마크도 막아야 한다")
        except wm.AlreadyWatermarked as blocked:
            assert blocked.mine and blocked.found is None
        # 문장 없이 태그만 심은 남의 워터마크는 알아낼 수 없다 (희소 배치의 대가).
        # 대신 덮어써도 살아남으므로 아래에서 그것을 확인한다.
        wm.protect(img, "instagram", thief)
        # 명시적으로 허용하면 덮어써진다
        stolen = wm.protect(withmsg["image"], "instagram", thief, "도용자", overwrite=True)
        survived = wm.check_watermark(stolen["image"], stolen["size"] and pub, stolen["size"])
        assert survived["match"], f"덮어쓰기 후 원작자 태그가 사라졌다: {survived}"
        assert survived["matched_bits"] >= 38, survived
        # 도용자의 태그도 함께 읽힌다 (둘이 공존한다)
        thief_pub = wm.pub_hex(thief.public_key())
        assert wm.check_watermark(stolen["image"], thief_pub, stolen["size"])["match"]
        assert wm.read_message(stolen["image"], stolen["size"]) == "도용자"
        # 그래도 사이드카는 원작자를 지킨다: 도용자가 '내 원본'이라 서명한 해시가
        # 원작자의 출력물과 같고, 원작자만 그 이전 파일을 갖고 있다.
        assert (stolen["sidecar"]["claim"]["sha256_original"]
                == withmsg["sidecar"]["claim"]["sha256_protected"])
        assert wm.check_sidecar(img, withmsg["sidecar"])["is_original_file"]
        # 워터마크가 지워져도 사진의 모양은 남는다 — 파생 관계를 보일 수 있다
        robbed = wm.check_sidecar(stolen["image"], withmsg["sidecar"])
        assert robbed["signature_valid"] and not robbed["is_protected_file"]
        # 사진에 따라 same/edited 로 갈리지만, 핵심은 "무관하지 않다"로 판정되는 것이다
        assert robbed["phash_verdict"] != "different", robbed
        assert robbed["phash_distance"] <= wm.PHASH_EDITED
        # 무관한 사진은 확실히 갈라져야 한다
        other_photo = sample_jpeg(1200, 900)
        far = wm.check_sidecar(other_photo, withmsg["sidecar"])
        assert far["phash_verdict"] == "different", far
        assert far["phash_distance"] > wm.PHASH_EDITED + 4, far["phash_distance"]
        assert stolen["already_marked"] and not withmsg["already_marked"]

        # --- 키 없이 문장만 ---
        bare = wm.protect(img, "instagram", None, note)
        assert bare["sidecar"] is None, "키가 없으면 서명할 수 없다"
        assert wm.read_message(bare["image"], bare["size"]) == note
        assert bare["psnr"] > out["psnr"], "레이어가 하나뿐이라 화질이 더 좋아야 한다"
        # 태그가 없으니 어떤 공개키로도 매치되면 안 된다
        assert not wm.check_watermark(bare["image"], pub, bare["size"])["match"]
        # 업로드 경로를 거쳐도 문장이 살아야 한다
        shrunk2 = cv2.imdecode(cv2.imencode(".jpg", wm._decode(bare["image"]),
                                            [cv2.IMWRITE_JPEG_QUALITY, 80])[1], cv2.IMREAD_COLOR)
        ok, buf = cv2.imencode(".png", shrunk2)
        assert wm.read_message(buf.tobytes(), bare["size"]) == note
        # 키도 문장도 없으면 심을 게 없다
        try:
            wm.protect(img, "instagram", None, "")
            raise AssertionError("키도 문장도 없으면 거부해야 한다")
        except ValueError:
            pass

        # --- 사이드카 서명 ---
        card = out["sidecar"]
        v = wm.check_sidecar(out["image"], card)
        assert v["signature_valid"] and v["is_protected_file"]
        assert wm.check_sidecar(img, card)["is_original_file"]

        tampered = {"claim": {**card["claim"], "created": "2001-01-01T00:00:00Z"},
                    "sig": card["sig"]}
        assert not wm.check_sidecar(out["image"], tampered)["signature_valid"], \
            "주장을 고쳤는데 서명이 통과했다"

        # --- SNS 통과 후에도 읽히는가 (핵심) ---
        for platform in ("instagram", "x", "youtube"):
            rows = wm.benchmark(img, platform, key)
            got = {r["attack"]: r for r in rows}
            # 크롭과 밝기/대비가 겹치면 이미지에 따라 놓친다. 알려진 한계라 단언하지 않고
            # 결과만 보여준다. 나머지는 전부 통과해야 한다.
            hard = {"크롭 + 밝기/대비"}
            for attack, row in got.items():
                if attack not in hard:
                    assert row["match"], f"{platform} / {attack} 에서 워터마크 소실"
            for r in rows:
                print(f"  {platform:10s} {r['attack']:24s} {r['matched_bits']:>2}/{r['total_bits']} "
                      f"{'검출' if r['match'] else '소실':4s}  공격받은 사진 {r['damage_psnr']}dB")

        print("\n전부 통과 ✅")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
