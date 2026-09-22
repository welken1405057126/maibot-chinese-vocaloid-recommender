from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from cover_cache import (  # noqa: E402
    CoverCache,
    CoverDownloadError,
    InvalidCoverImage,
)
from models import CoverStatus, Track, TrackStatus  # noqa: E402


JPEG = b"\xff\xd8\xff" + b"jpeg-data"
PNG = b"\x89PNG\r\n\x1a\n" + b"png-data"
GIF = b"GIF89a" + b"gif-data"
WEBP = b"RIFF\x04\x00\x00\x00WEBP" + b"webp-data"


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size: int):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(body)

    async def __aenter__(self):
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
            raise AssertionError("unexpected cover request")
        return self.responses.pop(0)


def make_track(
    track_id: int,
    cover_path: str,
    *,
    status: TrackStatus = TrackStatus.ACTIVE,
    deleted_at: str | None = None,
    last_accessed_at: str | None = "2026-09-20T00:00:00+00:00",
) -> Track:
    bvid = f"BV{track_id:010d}"
    return Track(
        id=track_id,
        bvid=bvid,
        aid=10_000 + track_id,
        canonical_url=f"https://www.bilibili.com/video/{bvid}",
        title=f"Track {track_id}",
        cover_url=f"https://i0.hdslb.com/{bvid}.jpg",
        cover_path=cover_path,
        cover_status=CoverStatus.CACHED,
        cover_cached_at="2026-09-19T00:00:00+00:00",
        cover_last_accessed_at=last_accessed_at,
        video_state=0,
        view_count=100,
        metadata_refreshed_at="2026-09-20T00:00:00+00:00",
        uploader_id="10001",
        origin_group_id="20001",
        origin_stream_id="20001",
        status=status,
        created_at="2026-09-18T00:00:00+00:00",
        deleted_at=deleted_at,
        deleted_by="admin" if deleted_at else None,
    )


class CoverDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.plugin_dir = Path(self.temp_dir.name)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_downloads_valid_image_atomically_and_reads_it_back(self) -> None:
        session = FakeSession([FakeResponse(200, JPEG)])
        cache = CoverCache(self.plugin_dir, session=session)

        result = await cache.cache_cover("BV1234567890", "http://i0.hdslb.com/cover.jpg")

        self.assertEqual(result.relative_path, "covers/BV1234567890.jpg")
        self.assertEqual(result.media_type, "image/jpeg")
        self.assertEqual(await cache.read_cover(result.relative_path), JPEG)
        self.assertFalse(any(path.suffix == ".tmp" for path in cache.cover_dir.iterdir()))
        self.assertEqual(session.calls[0][0], "https://i0.hdslb.com/cover.jpg")
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    async def test_rejects_unlisted_host_without_request(self) -> None:
        session = FakeSession([])
        cache = CoverCache(self.plugin_dir, session=session)

        with self.assertRaises(CoverDownloadError):
            await cache.cache_cover("BV1234567890", "https://example.com/cover.jpg")

        self.assertEqual(session.calls, [])

    async def test_rejects_html_disguised_as_image(self) -> None:
        session = FakeSession([FakeResponse(200, b"<html>not an image</html>")])
        cache = CoverCache(self.plugin_dir, session=session)

        with self.assertRaises(InvalidCoverImage):
            await cache.cache_cover("BV1234567890", "https://i0.hdslb.com/cover.jpg")

        self.assertFalse(cache.cover_dir.exists())

    async def test_rejects_oversized_response_and_path_traversal(self) -> None:
        session = FakeSession([FakeResponse(200, JPEG, {"Content-Length": "999"})])
        cache = CoverCache(self.plugin_dir, session=session, max_cover_bytes=32)

        with self.assertRaisesRegex(CoverDownloadError, "too large"):
            await cache.cache_cover("BV1234567890", "https://i0.hdslb.com/cover.jpg")
        self.assertIsNone(await cache.read_cover("../outside.jpg"))
        self.assertIsNone(await cache.read_cover("covers/../outside.jpg"))


class CoverCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.plugin_dir = Path(self.temp_dir.name)
        self.cover_dir = self.plugin_dir / "covers"
        self.cover_dir.mkdir()
        self.now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_file(self, name: str, body: bytes, *, age: timedelta = timedelta(0)) -> Path:
        path = self.cover_dir / name
        path.write_bytes(body)
        modified = (self.now - age).timestamp()
        os.utime(path, (modified, modified))
        return path

    async def test_cleanup_obeys_retention_rules(self) -> None:
        self.write_file("BV0000000001.jpg", JPEG)
        self.write_file("BV0000000002.png", PNG)
        self.write_file("orphan.webp", WEBP, age=timedelta(days=8))
        self.write_file("recent-orphan.gif", GIF, age=timedelta(days=1))
        self.write_file("stale.tmp", b"partial", age=timedelta(hours=25))
        tracks = [
            make_track(1, "covers/BV0000000001.jpg"),
            make_track(
                2,
                "covers/BV0000000002.png",
                status=TrackStatus.DELETED,
                deleted_at="2026-08-20T00:00:00+00:00",
            ),
        ]
        cache = CoverCache(self.plugin_dir)

        report = await cache.cleanup(tracks, now=self.now)

        self.assertTrue((self.cover_dir / "BV0000000001.jpg").exists())
        self.assertTrue((self.cover_dir / "recent-orphan.gif").exists())
        self.assertFalse((self.cover_dir / "BV0000000002.png").exists())
        self.assertFalse((self.cover_dir / "orphan.webp").exists())
        self.assertFalse((self.cover_dir / "stale.tmp").exists())
        self.assertEqual(report.affected_track_ids, (2,))
        self.assertEqual(report.failed_paths, ())

    async def test_capacity_prefers_deleted_track_before_active_track(self) -> None:
        deleted_body = PNG + b"d" * 700_000
        active_body = JPEG + b"a" * 700_000
        self.write_file("BV0000000001.jpg", active_body)
        self.write_file("BV0000000002.png", deleted_body)
        tracks = [
            make_track(1, "covers/BV0000000001.jpg"),
            make_track(
                2,
                "covers/BV0000000002.png",
                status=TrackStatus.DELETED,
                deleted_at="2026-09-22T11:00:00+00:00",
            ),
        ]
        cache = CoverCache(self.plugin_dir, max_cache_mib=1)

        report = await cache.cleanup(tracks, now=self.now)

        self.assertTrue((self.cover_dir / "BV0000000001.jpg").exists())
        self.assertFalse((self.cover_dir / "BV0000000002.png").exists())
        self.assertEqual(report.affected_track_ids, (2,))
        self.assertLessEqual(report.remaining_bytes, 1024 * 1024)

    async def test_lazy_cleanup_runs_once_per_interval(self) -> None:
        cache = CoverCache(self.plugin_dir, cleanup_interval_hours=24)

        first = await cache.cleanup_if_due([], now=self.now)
        second = await cache.cleanup_if_due([], now=self.now + timedelta(hours=1))

        self.assertIsNotNone(first)
        self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
