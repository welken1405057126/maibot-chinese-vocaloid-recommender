from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from bilibili_client import BilibiliNetworkError  # noqa: E402
from cover_cache import CachedCover, CoverDownloadError  # noqa: E402
from models import CoverStatus, NewTrack, RecommendStatus, VideoMetadata  # noqa: E402
from recommend_service import RecommendService, format_recommend_reply  # noqa: E402
from repository import TrackRepository  # noqa: E402


def make_track(
    number: int,
    *,
    cover_path: str | None = None,
    cover_status: CoverStatus = CoverStatus.MISSING,
) -> NewTrack:
    bvid = f"BV{number:010d}"
    return NewTrack(
        metadata=VideoMetadata(
            bvid=bvid,
            aid=10_000 + number,
            title=f"Track {number}",
            cover_url=f"https://i0.hdslb.com/{bvid}.jpg",
        ),
        canonical_url=f"https://www.bilibili.com/video/{bvid}",
        uploader_id="user-1",
        origin_group_id="group-1",
        origin_stream_id="group-1",
        cover_path=cover_path,
        cover_status=cover_status,
    )


class FakeBilibiliClient:
    def __init__(self, metadata: VideoMetadata | None = None, *, fail: bool = False) -> None:
        self.metadata = metadata
        self.fail = fail
        self.calls = 0

    async def fetch_video_metadata(self, url: str) -> VideoMetadata:
        self.calls += 1
        if self.fail:
            raise BilibiliNetworkError("offline")
        if self.metadata is None:
            raise AssertionError("unexpected metadata request")
        return self.metadata


class FakeCoverCache:
    def __init__(self, body: bytes | None = b"cover-bytes", *, fail: bool = False) -> None:
        self.body = body
        self.fail = fail
        self.cache_calls = 0
        self.read_calls = 0

    async def read_cover(self, relative_path: str) -> bytes | None:
        self.read_calls += 1
        return self.body

    async def cache_cover(self, bvid: str, cover_url: str) -> CachedCover:
        self.cache_calls += 1
        if self.fail:
            raise CoverDownloadError("offline")
        if self.body is None:
            self.body = b"downloaded-cover"
        return CachedCover(f"covers/{bvid}.jpg", "image/jpeg", len(self.body))


class RecommendServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = TrackRepository(Path(self.temp_dir.name) / "tracks.sqlite3")
        await self.repository.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_service(
        self,
        *,
        client: FakeBilibiliClient | None = None,
        cache: FakeCoverCache | None = None,
        recent_exclude_limit: int = 5,
        metadata_refresh_days: int = 9999,
        cooldown_seconds: int = 0,
    ) -> RecommendService:
        return RecommendService(
            self.repository,
            client or FakeBilibiliClient(),
            cache or FakeCoverCache(),
            recent_exclude_limit=recent_exclude_limit,
            metadata_refresh_days=metadata_refresh_days,
            cooldown_seconds=cooldown_seconds,
        )

    async def test_empty_library_uses_approved_reply(self) -> None:
        result = await self.make_service().recommend(stream_id="stream-a")

        self.assertEqual(result.status, RecommendStatus.EMPTY_LIBRARY)
        self.assertIsNone(result.track)
        self.assertEqual(format_recommend_reply(result), "曲库还没歌，先 /上传中v 传一首")

    async def test_recommends_track_records_history_and_prepares_image(self) -> None:
        track = (await self.repository.add_track(make_track(1))).track

        result = await self.make_service().recommend(stream_id="stream-a")

        self.assertEqual(result.status, RecommendStatus.FOUND)
        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertEqual(result.track.id, track.id)
        self.assertEqual(result.image_base64, base64.b64encode(b"cover-bytes").decode("ascii"))
        self.assertEqual(
            format_recommend_reply(result),
            "推荐\n《Track 1》\nhttps://www.bilibili.com/video/BV0000000001",
        )
        self.assertEqual(await self.repository.get_recent_recommendation_ids("stream-a", 1), [track.id])

    async def test_avoids_recent_track_when_another_is_available(self) -> None:
        first = (await self.repository.add_track(make_track(1))).track
        second = (await self.repository.add_track(make_track(2))).track
        await self.repository.record_recommendation("stream-a", first.id)

        result = await self.make_service(recent_exclude_limit=1).recommend(stream_id="stream-a")

        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertEqual(result.track.id, second.id)

    async def test_falls_back_when_every_track_is_recent(self) -> None:
        first = (await self.repository.add_track(make_track(1))).track
        second = (await self.repository.add_track(make_track(2))).track
        await self.repository.record_recommendation("stream-a", first.id)
        await self.repository.record_recommendation("stream-a", second.id)

        result = await self.make_service(recent_exclude_limit=2).recommend(stream_id="stream-a")

        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertIn(result.track.id, {first.id, second.id})

    async def test_cooldown_is_scoped_to_stream(self) -> None:
        now = datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)
        service = self.make_service(cooldown_seconds=5)
        first = await service.recommend(stream_id="stream-a", now=now)
        limited = await service.recommend(
            stream_id="stream-a",
            now=datetime(2026, 9, 23, 10, 0, 2, tzinfo=UTC),
        )
        other_stream = await service.recommend(
            stream_id="stream-b",
            now=datetime(2026, 9, 23, 10, 0, 2, tzinfo=UTC),
        )

        self.assertEqual(first.status, RecommendStatus.EMPTY_LIBRARY)
        self.assertEqual(limited.status, RecommendStatus.COOLDOWN)
        self.assertEqual(limited.retry_after_seconds, 3)
        self.assertEqual(format_recommend_reply(limited), "缓会，3秒后再推荐")
        self.assertEqual(other_stream.status, RecommendStatus.EMPTY_LIBRARY)

    async def test_stale_metadata_is_refreshed_before_reply(self) -> None:
        await self.repository.add_track(make_track(1), now="2026-09-20T10:00:00+00:00")
        metadata = VideoMetadata(
            bvid="BV0000000001",
            aid=10001,
            title="Updated Track",
            cover_url="https://i1.hdslb.com/updated.jpg",
            view_count=999,
        )
        client = FakeBilibiliClient(metadata)

        result = await self.make_service(client=client, metadata_refresh_days=2).recommend(
            stream_id="stream-a",
            now=datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(client.calls, 1)
        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertEqual(result.track.title, "Updated Track")
        self.assertEqual(result.track.view_count, 999)

    async def test_metadata_failure_uses_stored_track(self) -> None:
        await self.repository.add_track(make_track(1), now="2026-09-20T10:00:00+00:00")
        client = FakeBilibiliClient(fail=True)

        result = await self.make_service(client=client, metadata_refresh_days=2).recommend(
            stream_id="stream-a",
            now=datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(result.status, RecommendStatus.FOUND)
        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertEqual(result.track.title, "Track 1")
        self.assertEqual(client.calls, 1)

    async def test_valid_cached_cover_avoids_download_and_marks_access(self) -> None:
        path = "covers/BV0000000001.jpg"
        await self.repository.add_track(
            make_track(1, cover_path=path, cover_status=CoverStatus.CACHED),
            now="2026-09-23T10:00:00+00:00",
        )
        cache = FakeCoverCache(b"cached-cover")

        result = await self.make_service(cache=cache).recommend(
            stream_id="stream-a",
            now=datetime(2026, 9, 23, 10, 0, 1, tzinfo=UTC),
        )

        self.assertEqual(cache.cache_calls, 0)
        self.assertEqual(result.image_base64, base64.b64encode(b"cached-cover").decode("ascii"))
        stored = await self.repository.get_track_by_id(1)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.cover_last_accessed_at, "2026-09-23T10:00:01+00:00")

    async def test_cover_failure_degrades_to_text(self) -> None:
        await self.repository.add_track(make_track(1))
        cache = FakeCoverCache(body=None, fail=True)
        client = FakeBilibiliClient(fail=True)

        result = await self.make_service(client=client, cache=cache).recommend(stream_id="stream-a")

        self.assertEqual(result.status, RecommendStatus.FOUND)
        self.assertIsNone(result.image_base64)
        self.assertIn("Track 1", format_recommend_reply(result))
        stored = await self.repository.get_track_by_id(1)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.cover_status, CoverStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
