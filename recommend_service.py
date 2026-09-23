"""Random recommendation workflow independent from the MaiBot command adapter."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

try:
    from .bilibili_client import BilibiliClient, BilibiliClientError
    from .cover_cache import CoverCache, CoverCacheError
    from .models import CoverStatus, RecommendResult, RecommendStatus, Track
    from .repository import TrackRepository
except ImportError:
    from bilibili_client import BilibiliClient, BilibiliClientError  # type: ignore[no-redef]
    from cover_cache import CoverCache, CoverCacheError  # type: ignore[no-redef]
    from models import CoverStatus, RecommendResult, RecommendStatus, Track  # type: ignore[no-redef]
    from repository import TrackRepository  # type: ignore[no-redef]


class RecommendService:
    """Select, refresh, and prepare one track for text or hybrid sending."""

    def __init__(
        self,
        repository: TrackRepository,
        bilibili_client: BilibiliClient,
        cover_cache: CoverCache,
        *,
        recent_exclude_limit: int = 5,
        metadata_refresh_days: int = 2,
        cooldown_seconds: int = 5,
    ) -> None:
        self.repository = repository
        self.bilibili_client = bilibili_client
        self.cover_cache = cover_cache
        self.recent_exclude_limit = max(0, int(recent_exclude_limit))
        self.metadata_refresh_days = max(0, int(metadata_refresh_days))
        self.cooldown_seconds = max(0, int(cooldown_seconds))

    async def recommend(
        self,
        *,
        stream_id: str,
        now: datetime | None = None,
    ) -> RecommendResult:
        current_time = _as_utc(now or datetime.now(UTC))
        retry_after = await self.repository.reserve_recommendation_attempt(
            stream_id=stream_id,
            cooldown_seconds=self.cooldown_seconds,
            now=current_time,
        )
        if retry_after > 0:
            return RecommendResult(RecommendStatus.COOLDOWN, retry_after_seconds=retry_after)

        old_ids = await self.repository.get_recent_recommendation_ids(stream_id, self.recent_exclude_limit)
        track = await self.repository.get_random_active(exclude_ids=old_ids)
        if track is None:
            return RecommendResult(RecommendStatus.EMPTY_LIBRARY)

        timestamp = current_time.isoformat(timespec="seconds")
        await self.repository.record_recommendation(stream_id, track.id, now=timestamp)

        metadata_attempted = False
        cover_changed = False
        if self.repository.metadata_is_stale(
            track,
            max_age_days=self.metadata_refresh_days,
            now=current_time,
        ):
            metadata_attempted = True
            refreshed = await self._refresh_metadata(track, timestamp)
            if refreshed is not None:
                cover_changed = refreshed.cover_url != track.cover_url
                track = refreshed

        track, image_base64 = await self._load_cover(
            track,
            timestamp=timestamp,
            prefer_cached=not cover_changed,
        )
        if image_base64 is None and not metadata_attempted:
            refreshed = await self._refresh_metadata(track, timestamp)
            if refreshed is not None:
                track = refreshed
                track, image_base64 = await self._load_cover(
                    track,
                    timestamp=timestamp,
                    prefer_cached=False,
                )

        return RecommendResult(RecommendStatus.FOUND, track, image_base64=image_base64)

    async def _refresh_metadata(self, track: Track, timestamp: str) -> Track | None:
        try:
            metadata = await self.bilibili_client.fetch_video_metadata(track.canonical_url)
        except BilibiliClientError:
            return None
        return await self.repository.update_video_metadata(track.id, metadata, now=timestamp)

    async def _load_cover(
        self,
        track: Track,
        *,
        timestamp: str,
        prefer_cached: bool,
    ) -> tuple[Track, str | None]:
        body: bytes | None = None
        if prefer_cached and track.cover_path is not None:
            body = await self.cover_cache.read_cover(track.cover_path)

        if body is None:
            try:
                cached = await self.cover_cache.cache_cover(track.bvid, track.cover_url)
                updated = await self.repository.update_cover_cache(
                    track.id,
                    cover_path=cached.relative_path,
                    cover_status=CoverStatus.CACHED,
                    now=timestamp,
                )
                if updated is not None:
                    track = updated
                body = await self.cover_cache.read_cover(cached.relative_path)
            except CoverCacheError:
                body = None

        if body is None:
            updated = await self.repository.update_cover_cache(
                track.id,
                cover_path=None,
                cover_status=CoverStatus.FAILED,
                now=timestamp,
            )
            return updated or track, None

        await self.repository.mark_cover_accessed(track.id, now=timestamp)
        return track, base64.b64encode(body).decode("ascii")


def format_recommend_reply(result: RecommendResult) -> str:
    if result.status is RecommendStatus.FOUND and result.track is not None:
        track = result.track
        return f"推荐\n《{track.title}》\n{track.canonical_url}"
    if result.status is RecommendStatus.EMPTY_LIBRARY:
        return "曲库还没歌，先 /上传中v 传一首"
    if result.status is RecommendStatus.COOLDOWN:
        return f"缓会，{max(1, result.retry_after_seconds)}秒后再推荐"
    return "推荐失败了，待会再试"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
