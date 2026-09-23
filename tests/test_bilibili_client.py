from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from bilibili_client import (  # noqa: E402
    VIEW_API_URL,
    BilibiliClient,
    BilibiliLinkError,
    BilibiliNetworkError,
    BilibiliResponseError,
    BilibiliResponseTooLarge,
    canonical_video_url,
    parse_video_metadata,
    parse_video_reference,
)


def make_payload() -> dict[str, object]:
    return {
        "code": 0,
        "message": "0",
        "data": {
            "bvid": "BV1vFxXzWELU",
            "aid": 123456,
            "title": " Test\n title ",
            "pic": "http://i0.hdslb.com/test.jpg",
            "state": 0,
            "stat": {"view": 9876},
        },
    }


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size: int):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        enter_delay: float = 0,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(body)
        self.enter_delay = enter_delay

    async def __aenter__(self):
        if self.enter_delay:
            await asyncio.sleep(self.enter_delay)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


class ParseVideoReferenceTests(unittest.TestCase):
    def test_parses_bvid_and_aid_links_locally(self) -> None:
        bvid = parse_video_reference("https://www.bilibili.com/video/BV1vFxXzWELU?p=1")
        aid = parse_video_reference("https://m.bilibili.com/video/av123456")

        self.assertEqual(bvid.bvid, "BV1vFxXzWELU")
        self.assertIsNone(bvid.aid)
        self.assertEqual(aid.aid, 123456)
        self.assertIsNone(aid.bvid)

    def test_parses_bare_bvid_and_aid_locally(self) -> None:
        bvid = parse_video_reference("bv1vFxXzWELU")
        aid = parse_video_reference("AV123456")

        self.assertEqual(bvid.bvid, "BV1vFxXzWELU")
        self.assertEqual(aid.aid, 123456)

    def test_rejects_malformed_bare_identifiers(self) -> None:
        invalid_identifiers = ("BV1vFxXzWEL", "av0", "BV1vFxXzWELU extra", "not-a-video")

        for identifier in invalid_identifiers:
            with self.subTest(identifier=identifier), self.assertRaises(BilibiliLinkError):
                parse_video_reference(identifier)

    def test_rejects_non_https_ports_userinfo_and_unlisted_hosts(self) -> None:
        unsafe_links = (
            "http://www.bilibili.com/video/BV1vFxXzWELU",
            "https://www.bilibili.com:443/video/BV1vFxXzWELU",
            "https://user@www.bilibili.com/video/BV1vFxXzWELU",
            "https://bilibili.com.evil.example/video/BV1vFxXzWELU",
            "https://127.0.0.1/video/BV1vFxXzWELU",
        )

        for link in unsafe_links:
            with self.subTest(link=link), self.assertRaises(BilibiliLinkError):
                parse_video_reference(link)


class ParseVideoMetadataTests(unittest.TestCase):
    def test_maps_required_fields_and_normalizes_values(self) -> None:
        metadata = parse_video_metadata(make_payload())

        self.assertEqual(metadata.bvid, "BV1vFxXzWELU")
        self.assertEqual(metadata.aid, 123456)
        self.assertEqual(metadata.title, "Test title")
        self.assertEqual(metadata.cover_url, "https://i0.hdslb.com/test.jpg")
        self.assertEqual(metadata.state, 0)
        self.assertEqual(metadata.view_count, 9876)
        self.assertEqual(canonical_video_url(metadata), "https://www.bilibili.com/video/BV1vFxXzWELU")

    def test_rejects_api_business_error(self) -> None:
        with self.assertRaisesRegex(BilibiliResponseError, "code=-400"):
            parse_video_metadata({"code": -400, "message": "请求错误", "data": None})

    def test_rejects_missing_nested_view_count(self) -> None:
        payload = make_payload()
        data = payload["data"]
        assert isinstance(data, dict)
        data["stat"] = {}

        with self.assertRaisesRegex(BilibiliResponseError, "data.stat.view"):
            parse_video_metadata(payload)

    def test_rejects_unlisted_cover_host(self) -> None:
        payload = make_payload()
        data = payload["data"]
        assert isinstance(data, dict)
        data["pic"] = "https://example.com/cover.jpg"

        with self.assertRaisesRegex(BilibiliResponseError, "host is not allowed"):
            parse_video_metadata(payload)


class BilibiliClientRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_link_calls_only_fixed_view_api(self) -> None:
        body = json.dumps(make_payload()).encode()
        session = FakeSession([FakeResponse(200, body=body)])
        client = BilibiliClient(session=session)

        metadata = await client.fetch_video_metadata("https://www.bilibili.com/video/BV1vFxXzWELU")

        self.assertEqual(metadata.view_count, 9876)
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, VIEW_API_URL)
        self.assertEqual(kwargs["params"], {"bvid": "BV1vFxXzWELU"})
        self.assertFalse(kwargs["allow_redirects"])

    async def test_bare_bvid_calls_only_fixed_view_api(self) -> None:
        body = json.dumps(make_payload()).encode()
        session = FakeSession([FakeResponse(200, body=body)])
        client = BilibiliClient(session=session)

        metadata = await client.fetch_video_metadata("BV1vFxXzWELU")

        self.assertEqual(metadata.bvid, "BV1vFxXzWELU")
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, VIEW_API_URL)
        self.assertEqual(kwargs["params"], {"bvid": "BV1vFxXzWELU"})

    async def test_short_link_checks_redirect_before_calling_api(self) -> None:
        body = json.dumps(make_payload()).encode()
        session = FakeSession(
            [
                FakeResponse(
                    302,
                    headers={"Location": "https://www.bilibili.com/video/BV1vFxXzWELU?share_source=copy"},
                ),
                FakeResponse(200, body=body),
            ]
        )
        client = BilibiliClient(session=session)

        await client.fetch_video_metadata("https://b23.tv/abc123")

        self.assertEqual([call[0] for call in session.calls], ["https://b23.tv/abc123", VIEW_API_URL])

    async def test_short_link_rejects_redirect_to_private_ip_before_request(self) -> None:
        session = FakeSession([FakeResponse(302, headers={"Location": "https://127.0.0.1/private"})])
        client = BilibiliClient(session=session)

        with self.assertRaises(BilibiliLinkError):
            await client.fetch_video_metadata("https://b23.tv/abc123")

        self.assertEqual(len(session.calls), 1)

    async def test_rejects_response_larger_than_limit(self) -> None:
        session = FakeSession([FakeResponse(200, body=b"12345")])
        client = BilibiliClient(session=session, max_response_bytes=4)

        with self.assertRaises(BilibiliResponseTooLarge):
            await client.fetch_video_metadata("https://www.bilibili.com/video/BV1vFxXzWELU")

    async def test_applies_total_timeout(self) -> None:
        session = FakeSession([FakeResponse(200, enter_delay=0.05)])
        client = BilibiliClient(session=session, request_timeout_seconds=0.01)

        with self.assertRaisesRegex(BilibiliNetworkError, "timed out"):
            await client.fetch_video_metadata("https://www.bilibili.com/video/BV1vFxXzWELU")


if __name__ == "__main__":
    unittest.main()
