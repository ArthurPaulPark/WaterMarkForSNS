"""로컬 전용 웹 UI.  실행:  .venv/bin/python server.py

사진도 개인키도 이 컴퓨터를 벗어나지 않는다. 서명은 전부 여기서만 이뤄지고,
127.0.0.1 에만 바인딩하므로 같은 네트워크의 다른 기기에서도 접근할 수 없다.

로컬 서버라고 안전한 게 아니라서 세 가지를 더 막는다.
  * 사진을 메모리에서만 다룬다 (기본값이면 1MB 넘는 업로드가 디스크 임시파일로 떨어진다)
  * Host/Origin 을 검사한다 (악성 웹페이지가 브라우저를 통해 이 서버를 부리는 것을 막는다)
  * 안 쓰면 스스로 꺼진다 (떠 있는 시간이 곧 노출 시간이다)
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser

import watermark as wm

PORT = 8765
STATIC = Path(__file__).parent / "static"
SITE = Path(__file__).parent / "site"

# 떠 있는 시간이 곧 공격 표면이므로, 쓰지 않으면 스스로 종료한다.
# 화면은 "탭이 보이고 + 최근에 조작이 있을 때"만 살아있다고 알려온다. 탭을 닫거나
# 열어둔 채 방치하면 신호가 끊기고, 아래 시간이 지나면 종료된다.
IDLE_EXIT = 10 * 60
IDLE_CHECK = 20

_last_seen = time.monotonic()
_server: "uvicorn.Server | None" = None


async def _exit_when_idle() -> None:
    while True:
        await asyncio.sleep(IDLE_CHECK)
        if time.monotonic() - _last_seen > IDLE_EXIT:
            print(f"  {IDLE_EXIT // 60}분간 사용이 없어 종료합니다.")
            if _server is not None:
                _server.should_exit = True
            return


@asynccontextmanager
async def lifespan(_app: FastAPI):
    watcher = asyncio.create_task(_exit_when_idle())
    yield
    watcher.cancel()


app = FastAPI(title="WaterMark", lifespan=lifespan)

# 기본값 1MB 를 넘으면 업로드가 디스크 임시파일로 새어나간다. 사진은 대부분 그보다 크다.
# 상한을 올려 메모리 안에서만 다룬다 (업로드 자체는 _read 에서 50MB 로 제한된다).
MultiPartParser.spool_max_size = wm.MAX_UPLOAD + 1

# 브라우저는 다른 사이트의 페이지에서도 127.0.0.1 로 요청을 보낼 수 있다. CORS 때문에
# 응답을 읽지는 못하지만 POST 는 그대로 도달한다. DNS 리바인딩이면 읽기까지 뚫린다.
# 사진을 보관하는 엔드포인트가 없어 사진 유출은 불가능하지만, 남이 이 사용자의 개인키로
# 서명을 시킬 수 있다 — 원작자 증명 도구에서는 그것만으로 치명적이다.
LOCAL_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}
LOCAL_ORIGINS = {f"http://{h}" for h in LOCAL_HOSTS}


@app.middleware("http")
async def local_only(request: Request, call_next):
    global _last_seen
    _last_seen = time.monotonic()
    if request.headers.get("host") not in LOCAL_HOSTS:
        return JSONResponse({"detail": "이 서버는 이 컴퓨터에서만 쓸 수 있습니다"}, 403)
    origin = request.headers.get("origin")
    if origin is not None and origin not in LOCAL_ORIGINS:
        return JSONResponse({"detail": "다른 사이트에서 온 요청은 받지 않습니다"}, 403)
    return await call_next(request)


def _key(passphrase: str | None):
    if not wm.KEY_PATH.exists():
        raise HTTPException(400, "먼저 키를 만들어 주세요")
    try:
        return wm.load_key(passphrase or None)
    except TypeError:
        raise HTTPException(401, "이 키는 암호로 잠겨 있습니다")
    except ValueError:
        raise HTTPException(401, "암호가 틀렸습니다")


async def _read(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일입니다")
    if len(data) > wm.MAX_UPLOAD:
        raise HTTPException(413, "파일이 너무 큽니다 (최대 50MB)")
    return data


# 데스크톱 UI 도 브라우저에서 돈다. 얼굴 탐지 JS 와 모델을 웹앱과 같은 파일로 쓴다 —
# 복사본을 두면 한쪽만 고쳐지는 날이 온다.
app.mount("/lib", StaticFiles(directory=SITE), name="lib")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/ping")
def ping():
    """화면이 살아있음을 알리는 신호. 도달한 것만으로 종료 타이머가 리셋된다."""
    return {"idle_exit": IDLE_EXIT}


@app.get("/api/status")
def status():
    exists = wm.KEY_PATH.exists()
    pub = wm.pub_hex(wm.load_key().public_key()) if exists and not wm.key_is_encrypted() else None
    return {
        "has_key": exists,
        "encrypted": wm.key_is_encrypted(),
        "pub": pub,
        "key_path": str(wm.KEY_PATH),
        "platforms": wm.PLATFORMS,
    }


@app.post("/api/key")
def create_key(passphrase: str = Form("")):
    try:
        return {"pub": wm.generate_key(passphrase or None)}
    except FileExistsError as e:
        raise HTTPException(409, str(e))


@app.post("/api/capacity")
async def capacity(file: UploadFile, platform: str = Form(...)):
    """문장을 몇 바이트까지 넣을 수 있는지. 줄인 뒤 크기에 따라 달라진다."""
    try:
        return {"capacity": wm.capacity_for(await _read(file), platform)}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/protect")
async def protect(file: UploadFile, platform: str = Form(...), passphrase: str = Form(""),
                  message: str = Form(""), overwrite: str = Form(""),
                  keyless: str = Form(""), no_ai: str = Form("1"),
                  faces: str = Form(""), clean_only: str = Form("")):
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

    if clean_only == "1":
        # 워터마크·서명과 완전히 분리된 경로다 — 키가 없어도, 문장이 없어도 된다.
        # protect() 와 달리 _key() 를 아예 부르지 않는다: 키가 없는 사용자에게
        # "먼저 키를 만들어 주세요" 오류를 낼 이유가 없다.
        try:
            out = wm.strip_metadata(await _read(file), platform, no_ai == "1", face_list)
        except ValueError as e:
            raise HTTPException(400, str(e))
        stem = Path(file.filename or "image").stem
        return {
            "image_b64": base64.b64encode(out["image"]).decode(),
            "filename": f"{stem}_clean.jpg",
            "no_ai": no_ai == "1",
            "size": out["size"],
            "faces_masked": out["faces_masked"],
        }

    key = None if keyless == "1" else _key(passphrase)
    try:
        out = wm.protect(await _read(file), platform, key,
                         message.strip(), overwrite == "1", no_ai == "1", face_list)
    except wm.AlreadyWatermarked as e:
        raise HTTPException(409, {"already": True, "mine": e.mine, "found": e.found,
                                  "msg": str(e)})
    except ValueError as e:
        raise HTTPException(400, str(e))
    stem = Path(file.filename or "image").stem
    return {
        "image_b64": base64.b64encode(out["image"]).decode(),
        "filename": f"{stem}_{platform}_protected.jpg",
        "sidecar": out["sidecar"],
        "sidecar_filename": f"{stem}_{platform}_protected.jpg.sig.json",
        "keyless": key is None,
        "no_ai": out["no_ai"],
        "psnr": out["psnr"],
        "size": out["size"],
        "capacity": out["capacity"],
        "message": message.strip(),
        "already_marked": out["already_marked"],
        "faces_masked": out["faces_masked"],
    }


@app.post("/api/verify")
async def verify(file: UploadFile, pub: str = Form(""), sidecar: str = Form("")):
    data = await _read(file)
    out: dict = {}
    claimed, ref = pub.strip(), None

    if sidecar.strip():
        try:
            card = json.loads(sidecar)
            out["sidecar"] = wm.check_sidecar(data, card)
            claimed = claimed or card["claim"]["pub"]
            ref = card["claim"].get("size")
        except (json.JSONDecodeError, KeyError, TypeError):
            raise HTTPException(400, "사이드카 파일을 읽을 수 없습니다")

    if claimed:
        try:
            bytes.fromhex(claimed)
        except ValueError:
            raise HTTPException(400, "공개키 형식이 올바르지 않습니다")
        try:
            out["watermark"] = wm.check_watermark(data, claimed, ref)
        except ValueError as e:
            raise HTTPException(400, str(e))
        out["checked_pub"] = claimed
    # 문장은 키가 없어도 읽힌다. 사진만 올려도 검증이 성립한다.
    try:
        out["message"] = wm.read_message(data, ref)
    except ValueError:
        out["message"] = None
    out["declaration"] = wm.read_declaration(data)
    return out


@app.post("/api/benchmark")
async def bench(file: UploadFile, platform: str = Form(...), passphrase: str = Form("")):
    try:
        return {"rows": wm.benchmark(await _read(file), platform, _key(passphrase))}
    except ValueError as e:
        raise HTTPException(400, str(e))


if __name__ == "__main__":
    print(f"  →  http://127.0.0.1:{PORT}   (사용이 없으면 {IDLE_EXIT // 60}분 뒤 자동 종료)")
    _server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    )
    _server.run()
