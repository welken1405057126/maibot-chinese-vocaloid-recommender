"""Authorized soft-deletion workflow."""

from __future__ import annotations

try:
    from .models import DeleteRequestResult, DeleteRequestStatus, DeleteStatus, TrackStatus
    from .permissions import DeletePermissionChecker
    from .repository import TrackRepository
except ImportError:
    from models import DeleteRequestResult, DeleteRequestStatus, DeleteStatus, TrackStatus  # type: ignore[no-redef]
    from permissions import DeletePermissionChecker  # type: ignore[no-redef]
    from repository import TrackRepository  # type: ignore[no-redef]


class DeleteService:
    """Find, authorize, and soft-delete one track."""

    def __init__(self, repository: TrackRepository, permission_checker: DeletePermissionChecker) -> None:
        self.repository = repository
        self.permission_checker = permission_checker

    async def delete(self, track_id: int, *, user_id: str, group_id: str | None) -> DeleteRequestResult:
        normalized_track_id = int(track_id)
        track = await self.repository.get_track_by_id(normalized_track_id)
        if track is None or track.status is TrackStatus.DELETED:
            return DeleteRequestResult(DeleteRequestStatus.NOT_FOUND, normalized_track_id)

        allowed = await self.permission_checker.can_delete(track, user_id=user_id, group_id=group_id)
        if not allowed:
            return DeleteRequestResult(DeleteRequestStatus.FORBIDDEN, normalized_track_id)

        deleted = await self.repository.soft_delete(normalized_track_id, deleted_by=str(user_id).strip())
        status = (
            DeleteRequestStatus.DELETED
            if deleted.status is DeleteStatus.DELETED
            else DeleteRequestStatus.NOT_FOUND
        )
        return DeleteRequestResult(status, normalized_track_id)


def format_delete_reply(result: DeleteRequestResult) -> str:
    """Turn a deletion result into the approved short group-chat wording."""

    if result.status is DeleteRequestStatus.DELETED:
        return f"已删除曲目 {result.track_id}"
    if result.status is DeleteRequestStatus.FORBIDDEN:
        return "你无权限删除"
    return "曲库没这ID"
