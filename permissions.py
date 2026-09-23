"""Deletion permission checks independent from the MaiBot command adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

try:
    from .models import Track
except ImportError:
    from models import Track  # type: ignore[no-redef]


GroupMemberLookup = Callable[[str, str], Awaitable[object]]
GROUP_ADMIN_ROLES = frozenset({"admin", "owner"})


class DeletePermissionChecker:
    """Apply uploader, configured admin, and source-group admin rules."""

    def __init__(
        self,
        *,
        global_admin_ids: Iterable[str] = (),
        allow_group_admin_delete: bool = True,
        group_member_lookup: GroupMemberLookup | None = None,
        lookup_timeout_seconds: float = 5,
    ) -> None:
        normalized_admin_ids = (_normalize_id(item) for item in global_admin_ids)
        self.global_admin_ids = frozenset(item for item in normalized_admin_ids if item)
        self.allow_group_admin_delete = bool(allow_group_admin_delete)
        self.group_member_lookup = group_member_lookup
        self.lookup_timeout_seconds = max(0.01, float(lookup_timeout_seconds))

    async def can_delete(self, track: Track, *, user_id: str, group_id: str | None) -> bool:
        normalized_user_id = _normalize_id(user_id)
        normalized_group_id = _normalize_id(group_id)
        if not normalized_user_id:
            return False
        if normalized_user_id == _normalize_id(track.uploader_id):
            return True
        if normalized_user_id in self.global_admin_ids:
            return True
        if not self.allow_group_admin_delete or self.group_member_lookup is None:
            return False
        if not normalized_group_id or normalized_group_id != _normalize_id(track.origin_group_id):
            return False

        try:
            async with asyncio.timeout(self.lookup_timeout_seconds):
                member_info = await self.group_member_lookup(normalized_group_id, normalized_user_id)
        except Exception:
            return False
        return _extract_role(member_info) in GROUP_ADMIN_ROLES


def _extract_role(member_info: object) -> str:
    if not isinstance(member_info, Mapping):
        return ""
    payload: Mapping[str, Any] = member_info
    if "success" in payload:
        if payload.get("success") is not True:
            return ""
        nested = payload.get("result")
        if not isinstance(nested, Mapping):
            return ""
        payload = nested
    return str(payload.get("role") or "").strip().casefold()


def _normalize_id(value: object) -> str:
    return str(value or "").strip()
