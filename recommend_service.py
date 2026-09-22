"""Random recommendation independent from the MaiBot command adapter."""

from __future__ import annotations

try:
    from .models import (
        RecommendResult,
        RecommendStatus,
    )
    from .repository import TrackRepository
except ImportError:
    from models import (  # type: ignore[no-redef]
        RecommendResult,
        RecommendStatus,
    )
    from repository import TrackRepository  # type: ignore[no-redef]


class RecommendService:
    """Select active tracks while avoiding recent items in one chat stream."""

    def __init__(
        self,
        repository: TrackRepository,
        recent_exclude_limit: int = 5,
    ) -> None:
        self.repository = repository
        self.recent_exclude_limit = max(0, int(recent_exclude_limit))

    async def recommend(
        self,
        *,
        stream_id: str,
    ) -> RecommendResult:
        old_ids = await self.repository.get_recent_recommendation_ids(stream_id, self.recent_exclude_limit)
        track = await self.repository.get_random_active(exclude_ids=old_ids)
        if track is None:
            return RecommendResult(RecommendStatus.EMPTY_LIBRARY)

        await self.repository.record_recommendation(stream_id, track.id)
        return RecommendResult(RecommendStatus.FOUND, track)


def format_recommend_reply(result: RecommendResult) -> str:
    if result.status is RecommendStatus.FOUND and result.track is not None:
        track = result.track
        return f"推荐\n《{track.title}》\n{track.canonical_url}"
    if result.status is RecommendStatus.EMPTY_LIBRARY:
        return "曲库还没歌，先 /上传中v 传一首"
    return "推荐失败了，待会再试"
