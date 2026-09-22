"""Upload workflow independent from the MaiBot command adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

try:
    from .bilibili_client import (
        BilibiliClient,
        BilibiliClientError,
        BilibiliLinkError,
        canonical_video_url,
        validate_user_video_url,
    )
    from .cover_cache import CoverCache, CoverCacheError
    from .models import (
        CoverStatus,
        NewTrack,
        TrackStatus,
        UploadLimitStatus,
        UploadResult,
        UploadStatus,
    )
    from .repository import TrackRepository
except ImportError:
    from bilibili_client import (  # type: ignore[no-redef]
        BilibiliClient,
        BilibiliClientError,
        BilibiliLinkError,
        canonical_video_url,
        validate_user_video_url,
    )
    from cover_cache import CoverCache, CoverCacheError  # type: ignore[no-redef]
    from models import (  # type: ignore[no-redef]
        CoverStatus,
        NewTrack,
        TrackStatus,
        UploadLimitStatus,
        UploadResult,
        UploadStatus,
    )
    from repository import TrackRepository  # type: ignore[no-redef]


SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


class UploadService:
    """Validate, rate-limit, persist, and optionally cache one submission."""

    def __init__(
        self,
        repository: TrackRepository,
        bilibili_client: BilibiliClient,
        cover_cache: CoverCache,
        *,
        cooldown_seconds: int = 60,
        daily_limit: int = 10,
        stream_attempts_per_minute: int = 20,
    ) -> None:
        self.repository = repository
        self.bilibili_client = bilibili_client
        self.cover_cache = cover_cache
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self.daily_limit = max(0, int(daily_limit))
        self.stream_attempts_per_minute = max(0, int(stream_attempts_per_minute))

    async def upload(
        self,
        url: str,
        *,
        stream_id: str,
        user_id: str,
        group_id: str | None,
        now: datetime | None = None,
    ) -> UploadResult:
        try:
            validate_user_video_url(url)
        except BilibiliLinkError:
            return UploadResult(UploadStatus.INVALID_LINK)

        current_time = _as_utc(now or datetime.now(UTC))
        local_time = current_time.astimezone(SHANGHAI_TIMEZONE)
        day_started_at = local_time.replace(hour=0, minute=0, second=0, microsecond=0)
        limit = await self.repository.check_upload_rate_limit(
            stream_id=stream_id,
            user_id=user_id,
            cooldown_seconds=self.cooldown_seconds,
            daily_limit=self.daily_limit,
            stream_attempts_per_minute=self.stream_attempts_per_minute,
            now=current_time,
            day_started_at=day_started_at,
        )
        if limit.status is not UploadLimitStatus.ALLOWED:
            return UploadResult(
                UploadStatus(limit.status.value),
                retry_after_seconds=limit.retry_after_seconds,
            )

        try:
            metadata = await self.bilibili_client.fetch_video_metadata(url)
        except BilibiliLinkError:
            return UploadResult(UploadStatus.INVALID_LINK)
        except BilibiliClientError:
            return UploadResult(UploadStatus.API_ERROR)

        added = await self.repository.add_track(
            NewTrack(
                metadata=metadata,
                canonical_url=canonical_video_url(metadata),
                uploader_id=user_id,
                origin_group_id=group_id or None,
                origin_stream_id=stream_id,
            ),
            now=current_time.isoformat(timespec="seconds"),
        )
        if not added.created:
            status = (
                UploadStatus.PREVIOUSLY_DELETED
                if added.track.status is TrackStatus.DELETED
                else UploadStatus.DUPLICATE
            )
            return UploadResult(status, added.track)

        track = added.track
        try:
            cached = await self.cover_cache.cache_cover(metadata.bvid, metadata.cover_url)
            updated = await self.repository.update_cover_cache(
                track.id,
                cover_path=cached.relative_path,
                cover_status=CoverStatus.CACHED,
                now=current_time.isoformat(timespec="seconds"),
            )
            if updated is not None:
                track = updated
        except CoverCacheError:
            pass

        await self.repository.record_upload_success(
            stream_id=stream_id,
            user_id=user_id,
            now=current_time,
        )
        await self._run_lazy_cleanup()
        refreshed = await self.repository.get_track_by_id(track.id)
        return UploadResult(UploadStatus.CREATED, refreshed or track)

    async def _run_lazy_cleanup(self) -> None:
        try:
            tracks = await self.repository.list_tracks_with_cover_paths()
            report = await self.cover_cache.cleanup_if_due(tracks)
        except (CoverCacheError, OSError):
            return
        if report is None:
            return
        for track_id in report.affected_track_ids:
            await self.repository.update_cover_cache(
                track_id,
                cover_path=None,
                cover_status=CoverStatus.MISSING,
            )


def format_upload_reply(result: UploadResult) -> str:
    """Turn an upload result into the approved short group-chat wording."""

    if result.status is UploadStatus.CREATED and result.track is not None:
        return f"收录成功，ID {result.track.id}\n《{result.track.title}》"
    if result.status is UploadStatus.DUPLICATE and result.track is not None:
        return f"已在曲库中，ID {result.track.id}"
    if result.status is UploadStatus.PREVIOUSLY_DELETED and result.track is not None:
        return f"这首以前收过，不过现在是删除状态，ID {result.track.id}"
    if result.status is UploadStatus.INVALID_LINK:
        return "链接不对，只收B站视频链接"
    if result.status is UploadStatus.DAILY_LIMIT:
        return "今天已传够多了，明天再来"
    if result.status in {UploadStatus.COOLDOWN, UploadStatus.STREAM_LIMIT}:
        return f"缓会，{max(1, result.retry_after_seconds)}秒后再传"
    return "B站没回应，待会再试"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
