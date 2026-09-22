from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from models import NewTrack, RecommendStatus, VideoMetadata  # noqa: E402
from recommend_service import RecommendService, format_recommend_reply  # noqa: E402
from repository import TrackRepository  # noqa: E402


def make_track(number: int) -> NewTrack:
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
    )


class RecommendServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = TrackRepository(Path(self.temp_dir.name) / "tracks.sqlite3")
        await self.repository.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_empty_library_uses_approved_reply(self) -> None:
        result = await RecommendService(self.repository).recommend(stream_id="stream-a")

        self.assertEqual(result.status, RecommendStatus.EMPTY_LIBRARY)
        self.assertIsNone(result.track)
        self.assertEqual(format_recommend_reply(result), "曲库还没歌，先 /上传中v 传一首")

    async def test_recommends_track_and_records_history(self) -> None:
        track = (await self.repository.add_track(make_track(1))).track

        result = await RecommendService(self.repository).recommend(stream_id="stream-a")

        self.assertEqual(result.status, RecommendStatus.FOUND)
        self.assertEqual(result.track, track)
        self.assertEqual(
            format_recommend_reply(result),
            "推荐\n《Track 1》\nhttps://www.bilibili.com/video/BV0000000001",
        )
        self.assertEqual(await self.repository.get_recent_recommendation_ids("stream-a", 1), [track.id])

    async def test_avoids_recent_track_when_another_is_available(self) -> None:
        first = (await self.repository.add_track(make_track(1))).track
        second = (await self.repository.add_track(make_track(2))).track
        await self.repository.record_recommendation("stream-a", first.id)

        result = await RecommendService(self.repository, recent_exclude_limit=1).recommend(stream_id="stream-a")

        self.assertEqual(result.status, RecommendStatus.FOUND)
        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertEqual(result.track.id, second.id)

    async def test_falls_back_when_every_track_is_recent(self) -> None:
        first = (await self.repository.add_track(make_track(1))).track
        second = (await self.repository.add_track(make_track(2))).track
        await self.repository.record_recommendation("stream-a", first.id)
        await self.repository.record_recommendation("stream-a", second.id)

        result = await RecommendService(self.repository, recent_exclude_limit=2).recommend(stream_id="stream-a")

        self.assertEqual(result.status, RecommendStatus.FOUND)
        self.assertIsNotNone(result.track)
        assert result.track is not None
        self.assertIn(result.track.id, {first.id, second.id})


if __name__ == "__main__":
    unittest.main()
