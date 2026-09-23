from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from bilibili_client import BilibiliNetworkError  # noqa: E402
from cover_cache import CachedCover, CoverCacheError  # noqa: E402
from models import CoverStatus, UploadResult, UploadStatus, VideoMetadata  # noqa: E402
from repository import TrackRepository  # noqa: E402
from upload_service import UploadService, format_upload_reply  # noqa: E402


VIDEO_URL = "https://www.bilibili.com/video/BV0000000001"


class FakeBilibiliClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def fetch_video_metadata(self, url: str) -> VideoMetadata:
        self.calls += 1
        if self.fail:
            raise BilibiliNetworkError("offline")
        return VideoMetadata(
            bvid="BV0000000001",
            aid=10001,
            title="测试曲目",
            cover_url="https://i0.hdslb.com/test.jpg",
            view_count=123,
        )


class FakeCoverCache:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.cache_calls = 0

    async def cache_cover(self, bvid: str, cover_url: str) -> CachedCover:
        self.cache_calls += 1
        if self.fail:
            raise CoverCacheError("disk unavailable")
        return CachedCover(f"covers/{bvid}.jpg", "image/jpeg", 100)

    async def cleanup_if_due(self, tracks):
        return None


class UploadServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = TrackRepository(Path(self.temp_dir.name) / "tracks.sqlite3")
        await self.repository.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_service(self, *, api_fail: bool = False, cover_fail: bool = False) -> UploadService:
        return UploadService(
            self.repository,
            FakeBilibiliClient(fail=api_fail),
            FakeCoverCache(fail=cover_fail),
            cooldown_seconds=0,
            daily_limit=0,
            stream_attempts_per_minute=0,
        )

    async def test_success_caches_cover_and_uses_approved_wording(self) -> None:
        result = await self.make_service().upload(
            VIDEO_URL,
            stream_id="group-1",
            user_id="user-1",
            group_id="group-1",
            now=datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
        )

        self.assertEqual(result.status, UploadStatus.CREATED)
        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertEqual(result.track.cover_status, CoverStatus.CACHED)
        self.assertEqual(format_upload_reply(result), "收录成功，ID 1\n《测试曲目》")

    async def test_accepts_bare_bvid(self) -> None:
        result = await self.make_service().upload(
            "BV0000000001",
            stream_id="group-1",
            user_id="user-1",
            group_id="group-1",
        )

        self.assertEqual(result.status, UploadStatus.CREATED)

    async def test_cover_failure_silently_keeps_collected_track(self) -> None:
        result = await self.make_service(cover_fail=True).upload(
            VIDEO_URL,
            stream_id="group-1",
            user_id="user-1",
            group_id="group-1",
        )

        self.assertEqual(result.status, UploadStatus.CREATED)
        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertEqual(result.track.cover_status, CoverStatus.MISSING)
        self.assertEqual(format_upload_reply(result), "收录成功，ID 1\n《测试曲目》")

    async def test_duplicate_and_deleted_track_have_distinct_replies(self) -> None:
        service = self.make_service()
        first = await service.upload(VIDEO_URL, stream_id="group-1", user_id="user-1", group_id="group-1")
        duplicate = await service.upload(VIDEO_URL, stream_id="group-1", user_id="user-2", group_id="group-1")
        assert first.track is not None
        await self.repository.soft_delete(first.track.id, deleted_by="user-1")
        deleted = await service.upload(VIDEO_URL, stream_id="group-1", user_id="user-3", group_id="group-1")

        self.assertEqual(duplicate.status, UploadStatus.DUPLICATE)
        self.assertEqual(format_upload_reply(duplicate), "已在曲库中，ID 1")
        self.assertEqual(deleted.status, UploadStatus.PREVIOUSLY_DELETED)
        self.assertEqual(format_upload_reply(deleted), "这首以前收过，不过现在是删除状态，ID 1")

    async def test_invalid_link_does_not_call_api(self) -> None:
        client = FakeBilibiliClient()
        service = UploadService(
            self.repository,
            client,
            FakeCoverCache(),
            cooldown_seconds=0,
            daily_limit=0,
            stream_attempts_per_minute=0,
        )

        result = await service.upload(
            "https://example.com/video/BV0000000001",
            stream_id="group-1",
            user_id="user-1",
            group_id="group-1",
        )

        self.assertEqual(result.status, UploadStatus.INVALID_LINK)
        self.assertEqual(client.calls, 0)
        self.assertEqual(format_upload_reply(result), "链接不对，只收B站视频链接")

    async def test_api_failure_uses_short_retry_reply(self) -> None:
        result = await self.make_service(api_fail=True).upload(
            VIDEO_URL,
            stream_id="group-1",
            user_id="user-1",
            group_id="group-1",
        )

        self.assertEqual(result.status, UploadStatus.API_ERROR)
        self.assertEqual(format_upload_reply(result), "B站没回应，待会再试")

    async def test_concurrent_duplicate_creates_only_one_track(self) -> None:
        service = self.make_service()
        results = await asyncio.gather(
            service.upload(VIDEO_URL, stream_id="group-1", user_id="user-1", group_id="group-1"),
            service.upload(VIDEO_URL, stream_id="group-1", user_id="user-2", group_id="group-1"),
        )

        self.assertEqual([result.status for result in results].count(UploadStatus.CREATED), 1)
        self.assertEqual([result.status for result in results].count(UploadStatus.DUPLICATE), 1)
        self.assertEqual(await self.repository.count_active(), 1)

    def test_all_rate_limit_replies_match_approved_wording(self) -> None:
        self.assertEqual(
            format_upload_reply(UploadResult(UploadStatus.COOLDOWN, retry_after_seconds=12)),
            "缓会，12秒后再传",
        )
        self.assertEqual(
            format_upload_reply(UploadResult(UploadStatus.DAILY_LIMIT)),
            "今天已传够多了，明天再来",
        )


if __name__ == "__main__":
    unittest.main()
