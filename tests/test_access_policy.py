"""Локальные проверки границы доступа без запуска сервера и сетевых сокетов."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]


class _PeerConnection:
    created = 0

    def __init__(self):
        type(self).created += 1


def _load_server_module():
    """Подменяет только тяжёлые media-модули, не участвующие в auth checks."""
    mss_module = types.ModuleType("mss")
    mss_module.MSS = object

    aiortc_module = types.ModuleType("aiortc")
    aiortc_module.RTCPeerConnection = _PeerConnection
    aiortc_module.RTCSessionDescription = object
    aiortc_module.VideoStreamTrack = object

    codecs_module = types.ModuleType("aiortc.codecs")
    h264_module = types.ModuleType("aiortc.codecs.h264")
    codecs_module.h264 = h264_module

    av_module = types.ModuleType("av")
    av_module.VideoFrame = object

    stubs = {
        "mss": mss_module,
        "aiortc": aiortc_module,
        "aiortc.codecs": codecs_module,
        "aiortc.codecs.h264": h264_module,
        "av": av_module,
    }
    spec = importlib.util.spec_from_file_location("vrbox_server_for_test", ROOT / "pc" / "server.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, stubs), mock.patch.object(
        Path, "read_text", return_value="expected-token"
    ):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    module.TOKEN = "expected-token"
    return module


class AccessPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _load_server_module()

    def setUp(self):
        _PeerConnection.created = 0

    def test_vr_page_rejects_missing_and_wrong_token(self):
        for query in ({}, {"k": "wrong-token"}):
            response = asyncio.run(self.server.vr_page(SimpleNamespace(query=query)))
            self.assertEqual(response.status, 403)

    def test_vr_page_accepts_expected_token(self):
        response = asyncio.run(
            self.server.vr_page(SimpleNamespace(query={"k": "expected-token"}))
        )
        self.assertEqual(response.status, 200)

    def test_offer_rejects_before_peer_or_mouse_channel_exists(self):
        response = asyncio.run(
            self.server.offer(SimpleNamespace(query={"k": "wrong-token"}))
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(_PeerConnection.created, 0)

    def test_monitors_rejects_before_screen_layout_is_read(self):
        response = asyncio.run(self.server.monitors(SimpleNamespace(query={})))
        self.assertEqual(response.status, 403)

    def test_https_root_only_redirects_with_expected_token(self):
        denied = asyncio.run(
            self.server.index(SimpleNamespace(query={}, secure=True))
        )
        self.assertEqual(denied.status, 403)

        with self.assertRaises(web.HTTPFound) as raised:
            asyncio.run(
                self.server.index(
                    SimpleNamespace(query={"k": "expected-token"}, secure=True)
                )
            )
        self.assertEqual(raised.exception.location, "/vr?k=expected-token")

    def test_security_middleware_adds_non_leaking_headers(self):
        async def handler(_request):
            return web.Response(text="ok")

        response = asyncio.run(self.server._security_headers(None, handler))
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_security_middleware_also_covers_redirects(self):
        async def handler(_request):
            raise web.HTTPFound("/target")

        response = asyncio.run(self.server._security_headers(None, handler))
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")


if __name__ == "__main__":
    unittest.main()
