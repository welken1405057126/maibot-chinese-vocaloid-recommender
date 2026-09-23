from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from delete_service import DeleteService, format_delete_reply  # noqa: E402
from models import DeleteRequestStatus, NewTrack, TrackStatus, VideoMetadata  # noqa: E402
from permissions import DeletePermissionChecker  # noqa: E402
from repository import TrackRepository  # noqa: E402


def make_track(*, uploader_id: str = "uploader", group_id: str | None = "group-1") -> NewTrack:
    return NewTrack(
        metadata=VideoMetadata(
            bvid="BV0000000001",
            aid=10001,
            title="测试曲目",
            cover_url="https://i0.hdslb.com/test.jpg",
        ),
        canonical_url="https://www.bilibili.com/video/BV0000000001",
        uploader_id=uploader_id,
        origin_group_id=group_id,
        origin_stream_id=group_id or "private-uploader",
    )


class DeleteServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = TrackRepository(Path(self.temp_dir.name) / "tracks.sqlite3")
        await self.repository.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def add_track(self, *, uploader_id: str = "uploader", group_id: str | None = "group-1") -> int:
        return (await self.repository.add_track(make_track(uploader_id=uploader_id, group_id=group_id))).track.id

    async def test_uploader_can_delete_and_reply_uses_approved_wording(self) -> None:
        track_id = await self.add_track()
        service = DeleteService(self.repository, DeletePermissionChecker())

        result = await service.delete(track_id, user_id="uploader", group_id=None)

        self.assertEqual(result.status, DeleteRequestStatus.DELETED)
        self.assertEqual(format_delete_reply(result), f"已删除曲目 {track_id}")
        deleted = await self.repository.get_track_by_id(track_id)
        assert deleted is not None
        self.assertEqual(deleted.status, TrackStatus.DELETED)
        self.assertEqual(deleted.deleted_by, "uploader")

    async def test_global_admin_can_delete_any_track(self) -> None:
        track_id = await self.add_track(group_id=None)
        checker = DeletePermissionChecker(global_admin_ids=["global-admin"])
        service = DeleteService(self.repository, checker)

        result = await service.delete(track_id, user_id="global-admin", group_id=None)

        self.assertEqual(result.status, DeleteRequestStatus.DELETED)

    async def test_source_group_admin_can_delete_but_cross_group_admin_cannot(self) -> None:
        calls: list[tuple[str, str]] = []

        async def lookup(group_id: str, user_id: str) -> object:
            calls.append((group_id, user_id))
            return {"role": "admin"}

        checker = DeletePermissionChecker(group_member_lookup=lookup)
        service = DeleteService(self.repository, checker)
        track_id = await self.add_track()

        cross_group = await service.delete(track_id, user_id="moderator", group_id="group-2")
        source_group = await service.delete(track_id, user_id="moderator", group_id="group-1")

        self.assertEqual(cross_group.status, DeleteRequestStatus.FORBIDDEN)
        self.assertEqual(format_delete_reply(cross_group), "你无权限删除")
        self.assertEqual(source_group.status, DeleteRequestStatus.DELETED)
        self.assertEqual(calls, [("group-1", "moderator")])

    async def test_normal_member_is_denied_without_changing_track(self) -> None:
        async def lookup(group_id: str, user_id: str) -> object:
            return {"role": "member"}

        track_id = await self.add_track()
        service = DeleteService(self.repository, DeletePermissionChecker(group_member_lookup=lookup))

        result = await service.delete(track_id, user_id="member", group_id="group-1")

        self.assertEqual(result.status, DeleteRequestStatus.FORBIDDEN)
        track = await self.repository.get_track_by_id(track_id)
        assert track is not None
        self.assertEqual(track.status, TrackStatus.ACTIVE)

    async def test_missing_and_already_deleted_ids_share_not_found_reply(self) -> None:
        track_id = await self.add_track()
        service = DeleteService(self.repository, DeletePermissionChecker())
        await service.delete(track_id, user_id="uploader", group_id="group-1")

        already_deleted = await service.delete(track_id, user_id="uploader", group_id="group-1")
        missing = await service.delete(999, user_id="uploader", group_id="group-1")

        self.assertEqual(already_deleted.status, DeleteRequestStatus.NOT_FOUND)
        self.assertEqual(missing.status, DeleteRequestStatus.NOT_FOUND)
        self.assertEqual(format_delete_reply(already_deleted), "曲库没这ID")
        self.assertEqual(format_delete_reply(missing), "曲库没这ID")


if __name__ == "__main__":
    unittest.main()
