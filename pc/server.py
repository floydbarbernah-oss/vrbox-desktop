"""VRBox Desktop — сервер.
Захват экрана ПК -> WebRTC -> Safari на телефоне (стерео/gaze/мультиэкран — на клиенте).
Отдаёт статику, WebRTC-offer и data-channel управления мышью. См. README.md.
"""
import asyncio
import ctypes
import json
import secrets
import socket
import ssl
import threading
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
import mss
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.codecs import h264 as _h264
from av import VideoFrame

# aiortc по умолчанию душит H264 (target 1 Mbps, потолок 3) -> на 3840×1080 текст превращается
# в мыло (реально доезжало ~300 kbps). Поднимаем битрейт. Декод на телефоне зависит от
# разрешения/fps, а не от битрейта, поэтому нагрев почти не растёт, а читаемость — сильно.
_h264.MIN_BITRATE = 3_000_000
_h264.DEFAULT_BITRATE = 5_000_000
_h264.MAX_BITRATE = 8_000_000

WEB = Path(__file__).resolve().parent.parent / "web"
CERTS = Path(__file__).resolve().parent.parent / "certs"
TOKEN_FILE = Path(__file__).resolve().parent.parent / ".token"
FPS = 24  # 24 вместо 30: меньше декода/заливок текстуры/рендера на телефоне -> меньше нагрев
MAX_W = 3840  # захватываем ОБЪЕДИНЁННЫЙ рабочий стол (оба монитора рядом)


def _load_token() -> str:
    """Постоянный токен доступа: без него /offer не даст управлять мышью (любой в LAN мог бы)."""
    try:
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(9)
    TOKEN_FILE.write_text(t, encoding="utf-8")
    return t


TOKEN = _load_token()

# режим стрима, управляется командой с телефона (data-channel):
#   focus -> грабим только один монитор (1920×1080, чётко),  dual -> объединённый рабочий стол (3840)
stream_mode = "focus"   # "focus" | "dual"
stream_mon = 0          # индекс реального монитора для focus (0-based)


class Capture(threading.Thread):
    """Отдельный поток непрерывно грабит экран в self.latest (RGB ndarray)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.latest = None
        self._run = True

    def run(self):
        # неубиваемый цикл: если grab упадёт (UAC/секьюр-десктоп/смена режима),
        # пересоздаём mss и продолжаем — иначе поток застынет на последнем кадре
        while self._run:
            try:
                with mss.MSS() as sct:
                    while self._run:
                        if not pcs:                # телефон не подключён — не грабим, не греем ПК
                            time.sleep(0.2)
                            continue
                        mons = sct.monitors        # [0] = объединённый, [1:] = реальные мониторы
                        if stream_mode == "focus" and len(mons) > 1:
                            i = min(max(0, stream_mon), len(mons) - 2)
                            region = mons[i + 1]   # один монитор (1920) -> чётче текст, легче декод
                        else:
                            region = mons[0]       # объединённый рабочий стол (3840)
                        stride = max(1, -(-region["width"] // MAX_W))  # ceil-деление
                        raw = np.asarray(sct.grab(region))       # BGRA, C-contiguous
                        if stride > 1:
                            raw = np.ascontiguousarray(raw[::stride, ::stride])
                        # отдаём BGRA как есть: конверсию в YUV сделает libav на C
                        # (раньше тут были две numpy-копии на кадр — срез каналов + разворот)
                        self.latest = raw
                        time.sleep(1 / (FPS + 5))
            except Exception as e:
                print("capture error, retry:", e)
                time.sleep(0.5)

    def stop(self):
        self._run = False


class ScreenTrack(VideoStreamTrack):
    def __init__(self, cap: Capture):
        super().__init__()
        self.cap = cap
        self._n = 0
        self._t0 = time.time()

    async def recv(self):
        target = self._t0 + self._n / FPS
        now = time.time()
        if target > now:
            await asyncio.sleep(target - now)
        self._n += 1
        arr = self.cap.latest
        if arr is None:
            arr = np.zeros((720, MAX_W, 4), dtype=np.uint8)
        frame = VideoFrame.from_ndarray(arr, format="bgra")
        frame.pts = self._n * (90000 // FPS)
        frame.time_base = Fraction(1, 90000)
        return frame


pcs: set[RTCPeerConnection] = set()
cap = Capture()

# --- ввод: движение курсора по нормированным координатам экрана ---
_user32 = ctypes.windll.user32


def _handle_control(raw: str):
    global stream_mode, stream_mon
    try:
        d = json.loads(raw)
        t = d.get("t")
        if t == "move":
            _user32.SetCursorPos(int(d["X"]), int(d["Y"]))  # абсолютные координаты вирт. рабочего стола
        elif t == "stream":
            m = d.get("mode")
            if m in ("focus", "dual"):
                stream_mode = m
            if "mon" in d:
                stream_mon = int(d["mon"])
        elif t == "click":
            _user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            _user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    except Exception:
        pass


_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate"}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def _authorized(request) -> bool:
    """Единая проверка токена для всех закрытых HTTP-маршрутов."""
    supplied = request.query.get("k")
    return isinstance(supplied, str) and secrets.compare_digest(supplied, TOKEN)


@web.middleware
async def _security_headers(request, handler):
    """Добавляет совместимые защитные заголовки, в том числе к static routes."""
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


async def index(request):
    # HTTPS (8443) -> в приложение, но ТОЛЬКО если токен уже на руках.
    # Раньше здесь стоял безусловный редирект на /vr?k=TOKEN — он раздавал токен любому,
    # кто просто открыл https://IP:8443/, то есть защита /offer была декоративной.
    if request.secure:
        if _authorized(request):
            raise web.HTTPFound(f"/vr?k={TOKEN}", headers=_NOCACHE)
        return web.Response(
            status=403,
            text="Нужна полная ссылка с токеном — она напечатана в консоли сервера.",
            content_type="text/plain",
            charset="utf-8",
        )
    # HTTP (8080) оставляем как страницу установки сертификата
    return web.FileResponse(WEB / "index.html", headers=_NOCACHE)


async def vr_page(request):
    if not _authorized(request):
        return web.Response(
            status=403,
            text="Нужна полная ссылка с токеном — она напечатана в консоли сервера.",
            content_type="text/plain",
            charset="utf-8",
            headers=_NOCACHE,
        )
    return web.FileResponse(WEB / "vr.html", headers=_NOCACHE)


async def monitors(request):
    if not _authorized(request):  # раскладка мониторов — тоже приватная деталь
        return web.json_response({"error": "unauthorized"}, status=403)
    with mss.MSS() as sct:
        mons = sct.monitors
        c = mons[0]  # объединённый bounding box
        real = mons[1:]
        data = {
            "combined": {"left": c["left"], "top": c["top"], "width": c["width"], "height": c["height"]},
            "monitors": [
                {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
                for m in real
            ],
        }
    return web.json_response(data)


async def cert(_request):
    return web.FileResponse(
        CERTS / "cert.pem",
        headers={
            "Content-Type": "application/x-x509-ca-cert",
            "Content-Disposition": "attachment; filename=vrbox.crt",
        },
    )


async def offer(request):
    if not _authorized(request):      # без валидного токена — не пускаем к управлению мышью
        return web.json_response({"error": "unauthorized"}, status=403)
    params = await request.json()
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("datachannel")
    def _on_datachannel(channel):
        @channel.on("message")
        def _on_message(message):
            _handle_control(message)

    @pc.on("connectionstatechange")
    async def _on_state():
        print("  peer:", pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    pc.addTrack(ScreenTrack(cap))
    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def on_shutdown(_app):
    await asyncio.gather(*[pc.close() for pc in pcs], return_exceptions=True)
    pcs.clear()
    cap.stop()


async def start():
    cap.start()
    app = web.Application(middlewares=[_security_headers])
    app.router.add_get("/", index)
    app.router.add_get("/vr", vr_page)
    app.router.add_get("/monitors", monitors)
    app.router.add_get("/cert", cert)
    app.router.add_get("/vrbox.crt", cert)
    app.router.add_post("/offer", offer)
    app.router.add_static("/vendor", WEB / "vendor")
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    # HTTP (8080) — отдаёт сертификат + подсказку. HTTPS (8443) — само приложение (нужно для датчиков).
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERTS / "cert.pem", CERTS / "key.pem")
    await web.TCPSite(runner, "0.0.0.0", 8443, ssl_context=ctx).start()

    ip = lan_ip()
    print("=" * 64)
    print("  VRBox Desktop — сервер запущен")
    print(f"  Сертификат (один раз):   http://{ip}:8080")
    print("     -> «Установить сертификат», доверить в")
    print("        Настройки > Осн. > Об устройстве > Доверие сертификатам")
    print(f"  ПРИЛОЖЕНИЕ (сохрани на раб.стол):")
    print(f"     https://{ip}:8443/vr?k={TOKEN}")
    print("  Ctrl+C — остановить")
    print("=" * 64)
    while True:
        await asyncio.sleep(3600)


def main():
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
