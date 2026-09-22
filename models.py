"""Domain models shared by the recommender plugin modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TrackStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class CoverStatus(StrEnum):
    CACHED = "cached"
    MISSING = "missing"
    FAILED = "failed"


class DeleteStatus(StrEnum):
    DELETED = "deleted"
    NOT_FOUND = "not_found"
    ALREADY_DELETED = "already_deleted"


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    """Normalized subset of the Bilibili view API response."""

    bvid: str
    aid: int
    title: str
    cover_url: str
    state: int = 0
    view_count: int = 0

    def __post_init__(self) -> None:
        if not self.bvid.strip():
            raise ValueError("bvid cannot be empty")
        if self.aid <= 0:
            raise ValueError("aid must be positive")
        if not self.title.strip():
            raise ValueError("title cannot be empty")
        if not self.cover_url.strip():
            raise ValueError("cover_url cannot be empty")


@dataclass(frozen=True, slots=True)
class NewTrack:
    """Information required to add one submitted video to the library."""

    metadata: VideoMetadata
    canonical_url: str
    uploader_id: str
    origin_stream_id: str
    origin_group_id: str | None = None
    cover_path: str | None = None
    cover_status: CoverStatus = CoverStatus.MISSING

    def __post_init__(self) -> None:
        if not self.canonical_url.strip():
            raise ValueError("canonical_url cannot be empty")
        if not self.uploader_id.strip():
            raise ValueError("uploader_id cannot be empty")
        if not self.origin_stream_id.strip():
            raise ValueError("origin_stream_id cannot be empty")


@dataclass(frozen=True, slots=True)
class Track:
    id: int
    bvid: str
    aid: int
    canonical_url: str
    title: str
    cover_url: str
    cover_path: str | None
    cover_status: CoverStatus
    cover_cached_at: str | None
    cover_last_accessed_at: str | None
    video_state: int
    view_count: int
    metadata_refreshed_at: str
    uploader_id: str
    origin_group_id: str | None
    origin_stream_id: str
    status: TrackStatus
    created_at: str
    deleted_at: str | None
    deleted_by: str | None


@dataclass(frozen=True, slots=True)
class AddTrackResult:
    created: bool
    track: Track


@dataclass(frozen=True, slots=True)
class DeleteTrackResult:
    status: DeleteStatus
    track: Track | None
