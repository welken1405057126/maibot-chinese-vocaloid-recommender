from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from models import CoverStatus, Track, TrackStatus  # noqa: E402
from permissions import DeletePermissionChecker  # noqa: E402


def make_track(*, uploader_id: str = "uploader", group_id: str | None = "group-1") -> Track:
    return Track(
        id=1,
        bvid="BV0000000001",
        aid=10001,
        canonical_url="https://www.bilibili.com/video/BV0000000001",
        title="测试曲目",
        cover_url="https://i0.hdslb.com/test.jpg",
        cover_path=None,
        cover_status=CoverStatus.MISSING,
        cover_cached_at=None,
        cover_last_accessed_at=None,
        video_state=0,
        view_count=0,
        metadata_refreshed_at="2026-09-23T00:00:00+00:00",
        uploader_id=uploader_id,
        origin_group_id=group_id,
        origin_stream_id=group_id or "private-uploader",
        status=TrackStatus.ACTIVE,
        created_at="2026-09-23T00:00:00+00:00",
        deleted_at=None,
        deleted_by=None,
    )


class DeletePermissionCheckerTests(unittest.IsolatedAsyncioTestCase):
    async def test_uploader_and_global_admin_do_not_query_napcat(self) -> None:
        calls: list[tuple[str, str]] = []

        async def lookup(group_id: str, user_id: str) -> object:
            calls.append((group_id, user_id))
            return {"role": "member"}

        checker = DeletePermissionChecker(global_admin_ids=["global-admin"], group_member_lookup=lookup)
        track = make_track()

        self.assertTrue(await checker.can_delete(track, user_id="uploader", group_id="other-group"))
        self.assertTrue(await checker.can_delete(track, user_id="global-admin", group_id=None))
        self.assertEqual(calls, [])

    async def test_source_group_owner_and_admin_are_allowed(self) -> None:
        for role in ("owner", "admin", "ADMIN"):
            async def lookup(group_id: str, user_id: str, role: str = role) -> object:
                return {"group_id": group_id, "user_id": user_id, "role": role}

            checker = DeletePermissionChecker(group_member_lookup=lookup)

            with self.subTest(role=role):
                self.assertTrue(await checker.can_delete(make_track(), user_id="moderator", group_id="group-1"))

    async def test_member_cross_group_private_and_disabled_cases_are_denied(self) -> None:
        calls = 0

        async def lookup(group_id: str, user_id: str) -> object:
            nonlocal calls
            calls += 1
            return {"role": "member"}

        checker = DeletePermissionChecker(group_member_lookup=lookup)
        self.assertFalse(await checker.can_delete(make_track(), user_id="member", group_id="group-1"))
        self.assertFalse(await checker.can_delete(make_track(), user_id="member", group_id="group-2"))
        self.assertFalse(await checker.can_delete(make_track(group_id=None), user_id="member", group_id=None))

        disabled = DeletePermissionChecker(allow_group_admin_delete=False, group_member_lookup=lookup)
        self.assertFalse(await disabled.can_delete(make_track(), user_id="moderator", group_id="group-1"))
        self.assertEqual(calls, 1)

    async def test_lookup_failure_timeout_and_malformed_response_are_denied(self) -> None:
        async def failing_lookup(group_id: str, user_id: str) -> object:
            raise RuntimeError(f"offline: {group_id}/{user_id}")

        async def slow_lookup(group_id: str, user_id: str) -> object:
            await asyncio.sleep(0.05)
            return {"role": "owner"}

        track = make_track()
        failing = DeletePermissionChecker(group_member_lookup=failing_lookup)
        slow = DeletePermissionChecker(group_member_lookup=slow_lookup, lookup_timeout_seconds=0.01)

        self.assertFalse(await failing.can_delete(track, user_id="moderator", group_id="group-1"))
        self.assertFalse(await slow.can_delete(track, user_id="moderator", group_id="group-1"))

        for response in (None, [], {}, {"success": False, "role": "owner"}, {"success": True, "result": None}):
            async def malformed_lookup(group_id: str, user_id: str, response: object = response) -> object:
                return response

            checker = DeletePermissionChecker(group_member_lookup=malformed_lookup)
            with self.subTest(response=response):
                self.assertFalse(await checker.can_delete(track, user_id="moderator", group_id="group-1"))

    async def test_accepts_host_wrapped_success_result(self) -> None:
        async def lookup(group_id: str, user_id: str) -> object:
            return {"success": True, "result": {"role": "admin"}}

        checker = DeletePermissionChecker(group_member_lookup=lookup)
        self.assertTrue(await checker.can_delete(make_track(), user_id="moderator", group_id="group-1"))


if __name__ == "__main__":
    unittest.main()
