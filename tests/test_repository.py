from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from models import CoverStatus, DeleteStatus, NewTrack, TrackStatus, VideoMetadata  # noqa: E402
from repository import SCHEMA_VERSION, TrackRepository  # noqa: E402


def make_track(
    number: int,
    *,
    uploader_id: str = "10001",
    group_id: str | None = "20001",
) -> NewTrack:
    bvid = f"BV{number:010d}"
    return NewTrack(
        metadata=VideoMetadata(
            bvid=bvid,
            aid=10_000 + number,
            title=f"Track {number}",
            cover_url=f"https://i0.hdslb.com/{bvid}.jpg",
            view_count=number * 100,
        ),
        canonical_url=f"https://www.bilibili.com/video/{bvid}",
        uploader_id=uploader_id,
        origin_group_id=group_id,
        origin_stream_id=group_id or f"private-{uploader_id}",
    )


class TrackRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tracks.sqlite3"
        self.repository = TrackRepository(self.db_path)
        await self.repository.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_initializes_schema(self) -> None:
        self.assertTrue(self.db_path.is_file())
        self.assertEqual(await self.repository.get_schema_version(), SCHEMA_VERSION)
        self.assertEqual(await self.repository.count_active(), 0)
        connection = sqlite3.connect(self.db_path)
        try:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(tracks)")}
        finally:
            connection.close()
        self.assertTrue(
            {"bvid", "aid", "video_state", "view_count", "metadata_refreshed_at"} <= columns
        )
        self.assertTrue(
            {"duration", "video_owner_mid", "video_owner_name", "uploader_name", "like_count"}.isdisjoint(columns)
        )

    async def test_adds_and_persists_track(self) -> None:
        result = await self.repository.add_track(make_track(1), now="2026-09-22T10:00:00+00:00")

        self.assertTrue(result.created)
        self.assertEqual(result.track.id, 1)
        self.assertEqual(result.track.status, TrackStatus.ACTIVE)
        self.assertEqual(result.track.origin_group_id, "20001")

        reopened = TrackRepository(self.db_path)
        await reopened.initialize()
        stored = await reopened.get_track_by_bvid("bv0000000001")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.title, "Track 1")
        self.assertEqual(stored.view_count, 100)

    async def test_duplicate_bvid_or_aid_returns_existing_track(self) -> None:
        first = await self.repository.add_track(make_track(1))
        duplicate_bvid = NewTrack(
            metadata=VideoMetadata(
                bvid=first.track.bvid.lower(),
                aid=99999,
                title="Duplicate by BVID",
                cover_url="https://i0.hdslb.com/duplicate.jpg",
            ),
            canonical_url="https://www.bilibili.com/video/duplicate",
            uploader_id="2",
            origin_stream_id="stream-2",
        )
        duplicate_aid = make_track(2)
        duplicate_aid = NewTrack(
            metadata=VideoMetadata(
                bvid=duplicate_aid.metadata.bvid,
                aid=first.track.aid,
                title=duplicate_aid.metadata.title,
                cover_url=duplicate_aid.metadata.cover_url,
            ),
            canonical_url=duplicate_aid.canonical_url,
            uploader_id=duplicate_aid.uploader_id,
            origin_group_id=duplicate_aid.origin_group_id,
            origin_stream_id=duplicate_aid.origin_stream_id,
        )

        by_bvid = await self.repository.add_track(duplicate_bvid)
        by_aid = await self.repository.add_track(duplicate_aid)

        self.assertFalse(by_bvid.created)
        self.assertFalse(by_aid.created)
        self.assertEqual(by_bvid.track.id, first.track.id)
        self.assertEqual(by_aid.track.id, first.track.id)
        self.assertEqual(await self.repository.count_active(), 1)

    async def test_concurrent_duplicate_uploads_create_one_track(self) -> None:
        def upload_from_independent_connection():
            repository = TrackRepository(self.db_path)
            return asyncio.run(repository.add_track(make_track(1)))

        results = await asyncio.gather(*(asyncio.to_thread(upload_from_independent_connection) for _ in range(8)))

        self.assertEqual(sum(result.created for result in results), 1)
        self.assertEqual({result.track.id for result in results}, {1})
        self.assertEqual(await self.repository.count_active(), 1)

    async def test_random_selection_excludes_recent_then_falls_back(self) -> None:
        first = (await self.repository.add_track(make_track(1))).track
        second = (await self.repository.add_track(make_track(2))).track

        selected = await self.repository.get_random_active(exclude_ids=[first.id])
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.id, second.id)

        fallback = await self.repository.get_random_active(exclude_ids=[first.id, second.id])
        self.assertIsNotNone(fallback)
        assert fallback is not None
        self.assertIn(fallback.id, {first.id, second.id})

    async def test_recommendation_history_is_stream_scoped(self) -> None:
        first = (await self.repository.add_track(make_track(1))).track
        second = (await self.repository.add_track(make_track(2))).track
        await self.repository.record_recommendation("stream-a", first.id)
        await self.repository.record_recommendation("stream-a", second.id)
        await self.repository.record_recommendation("stream-b", first.id)

        self.assertEqual(
            await self.repository.get_recent_recommendation_ids("stream-a", 2),
            [second.id, first.id],
        )
        self.assertEqual(
            await self.repository.get_recent_recommendation_ids("stream-b", 2),
            [first.id],
        )

    async def test_soft_delete_removes_track_from_active_pool(self) -> None:
        track = (await self.repository.add_track(make_track(1))).track

        deleted = await self.repository.soft_delete(
            track.id,
            deleted_by="admin-1",
            now="2026-09-22T11:00:00+00:00",
        )
        deleted_again = await self.repository.soft_delete(track.id, deleted_by="admin-1")
        missing = await self.repository.soft_delete(999, deleted_by="admin-1")

        self.assertEqual(deleted.status, DeleteStatus.DELETED)
        self.assertIsNotNone(deleted.track)
        assert deleted.track is not None
        self.assertEqual(deleted.track.status, TrackStatus.DELETED)
        self.assertEqual(deleted.track.deleted_by, "admin-1")
        self.assertEqual(deleted_again.status, DeleteStatus.ALREADY_DELETED)
        self.assertEqual(missing.status, DeleteStatus.NOT_FOUND)
        self.assertEqual(await self.repository.count_active(), 0)
        self.assertIsNone(await self.repository.get_random_active())

    async def test_updates_cover_cache_state(self) -> None:
        track = (await self.repository.add_track(make_track(1))).track
        updated = await self.repository.update_cover_cache(
            track.id,
            cover_path="covers/BV0000000001.jpg",
            cover_status=CoverStatus.CACHED,
            now="2026-09-22T12:00:00+00:00",
        )
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.cover_status, CoverStatus.CACHED)
        self.assertEqual(updated.cover_path, "covers/BV0000000001.jpg")
        self.assertEqual(updated.cover_cached_at, "2026-09-22T12:00:00+00:00")
        with_cover = await self.repository.list_tracks_with_cover_paths()
        self.assertEqual([item.id for item in with_cover], [track.id])

    async def test_refreshes_stale_video_metadata(self) -> None:
        track = (
            await self.repository.add_track(
                make_track(1),
                now="2026-09-20T10:00:00+00:00",
            )
        ).track
        before_limit = datetime(2026, 9, 22, 9, 59, 59, tzinfo=UTC)
        at_limit = datetime(2026, 9, 22, 10, 0, 0, tzinfo=UTC)

        self.assertFalse(self.repository.metadata_is_stale(track, now=before_limit))
        self.assertTrue(self.repository.metadata_is_stale(track, now=at_limit))

        refreshed = await self.repository.update_video_metadata(
            track.id,
            VideoMetadata(
                bvid=track.bvid,
                aid=track.aid,
                title="Updated title",
                cover_url="https://i0.hdslb.com/updated.jpg",
                state=0,
                view_count=999,
            ),
            now="2026-09-22T10:00:00+00:00",
        )

        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.title, "Updated title")
        self.assertEqual(refreshed.view_count, 999)
        self.assertEqual(refreshed.metadata_refreshed_at, "2026-09-22T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
