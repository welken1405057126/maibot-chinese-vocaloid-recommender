"""SQLite persistence for the shared Chinese Vocaloid track library."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

try:
    from .models import (
        AddTrackResult,
        CoverStatus,
        DeleteStatus,
        DeleteTrackResult,
        NewTrack,
        Track,
        TrackStatus,
    )
except ImportError:
    from models import (  # type: ignore[no-redef]
        AddTrackResult,
        CoverStatus,
        DeleteStatus,
        DeleteTrackResult,
        NewTrack,
        Track,
        TrackStatus,
    )


SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TrackRepository:
    """Owns the plugin database and serializes writes within one process."""

    def __init__(self, db_path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    async def initialize(self) -> None:
        async with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS tracks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bvid TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        aid INTEGER NOT NULL UNIQUE,
                        canonical_url TEXT NOT NULL,
                        title TEXT NOT NULL,
                        cover_url TEXT NOT NULL,
                        cover_path TEXT,
                        cover_status TEXT NOT NULL
                            CHECK (cover_status IN ('cached', 'missing', 'failed')),
                        cover_cached_at TEXT,
                        cover_last_accessed_at TEXT,
                        video_owner_mid TEXT NOT NULL,
                        video_owner_name TEXT NOT NULL,
                        duration INTEGER NOT NULL DEFAULT 0,
                        video_state INTEGER NOT NULL DEFAULT 0,
                        view_count INTEGER NOT NULL DEFAULT 0,
                        like_count INTEGER NOT NULL DEFAULT 0,
                        metadata_refreshed_at TEXT NOT NULL,
                        uploader_id TEXT NOT NULL,
                        uploader_name TEXT NOT NULL,
                        origin_group_id TEXT,
                        origin_stream_id TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'deleted')),
                        created_at TEXT NOT NULL,
                        deleted_at TEXT,
                        deleted_by TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_tracks_status
                        ON tracks(status);
                    CREATE INDEX IF NOT EXISTS idx_tracks_origin_group
                        ON tracks(origin_group_id);

                    CREATE TABLE IF NOT EXISTS recommendation_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        stream_id TEXT NOT NULL,
                        track_id INTEGER NOT NULL,
                        recommended_at TEXT NOT NULL,
                        FOREIGN KEY(track_id) REFERENCES tracks(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_recommendation_history_stream
                        ON recommendation_history(stream_id, id DESC);

                    CREATE TABLE IF NOT EXISTS command_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        stream_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_command_events_rate_limit
                        ON command_events(event_type, user_id, created_at);
                    """
                )
                connection.execute(
                    """
                    INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(SCHEMA_VERSION),),
                )
                connection.commit()
            finally:
                connection.close()

    async def get_schema_version(self) -> int:
        async with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
                return int(row["value"]) if row is not None else 0
            finally:
                connection.close()

    async def add_track(self, new_track: NewTrack, *, now: str | None = None) -> AddTrackResult:
        timestamp = now or utc_now_iso()
        metadata = new_track.metadata
        async with self._lock:
            connection = self._connect()
            try:
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO tracks (
                            bvid, aid, canonical_url, title, cover_url, cover_path,
                            cover_status, cover_cached_at, video_owner_mid,
                            video_owner_name, duration, video_state, view_count,
                            like_count, metadata_refreshed_at, uploader_id,
                            uploader_name, origin_group_id, origin_stream_id,
                            status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            metadata.bvid,
                            metadata.aid,
                            new_track.canonical_url,
                            metadata.title,
                            metadata.cover_url,
                            new_track.cover_path,
                            new_track.cover_status.value,
                            timestamp if new_track.cover_status is CoverStatus.CACHED else None,
                            metadata.owner_mid,
                            metadata.owner_name,
                            metadata.duration,
                            metadata.state,
                            metadata.view_count,
                            metadata.like_count,
                            timestamp,
                            new_track.uploader_id,
                            new_track.uploader_name,
                            new_track.origin_group_id,
                            new_track.origin_stream_id,
                            TrackStatus.ACTIVE.value,
                            timestamp,
                        ),
                    )
                    connection.commit()
                    row = connection.execute(
                        "SELECT * FROM tracks WHERE id = ?",
                        (int(cursor.lastrowid),),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("inserted track could not be read back")
                    return AddTrackResult(created=True, track=self._row_to_track(row))
                except sqlite3.IntegrityError:
                    connection.rollback()
                    row = connection.execute(
                        "SELECT * FROM tracks WHERE bvid = ? COLLATE NOCASE OR aid = ? LIMIT 1",
                        (metadata.bvid, metadata.aid),
                    ).fetchone()
                    if row is None:
                        raise
                    return AddTrackResult(created=False, track=self._row_to_track(row))
            finally:
                connection.close()

    async def get_track_by_id(self, track_id: int) -> Track | None:
        return await self._get_one("SELECT * FROM tracks WHERE id = ?", (int(track_id),))

    async def get_track_by_bvid(self, bvid: str) -> Track | None:
        return await self._get_one(
            "SELECT * FROM tracks WHERE bvid = ? COLLATE NOCASE",
            (bvid,),
        )

    async def get_track_by_aid(self, aid: int) -> Track | None:
        return await self._get_one("SELECT * FROM tracks WHERE aid = ?", (int(aid),))

    async def count_active(self) -> int:
        async with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM tracks WHERE status = ?",
                    (TrackStatus.ACTIVE.value,),
                ).fetchone()
                return int(row["count"]) if row is not None else 0
            finally:
                connection.close()

    async def get_recent_recommendation_ids(self, stream_id: str, limit: int) -> list[int]:
        if limit <= 0:
            return []
        async with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT track_id FROM recommendation_history
                    WHERE stream_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (stream_id, int(limit)),
                ).fetchall()
                return [int(row["track_id"]) for row in rows]
            finally:
                connection.close()

    async def get_random_active(
        self,
        *,
        exclude_ids: Sequence[int] = (),
        fallback_to_any: bool = True,
    ) -> Track | None:
        normalized_ids = tuple(dict.fromkeys(int(item) for item in exclude_ids if int(item) > 0))
        async with self._lock:
            connection = self._connect()
            try:
                row: sqlite3.Row | None = None
                if normalized_ids:
                    placeholders = ", ".join("?" for _ in normalized_ids)
                    row = connection.execute(
                        f"""
                        SELECT * FROM tracks
                        WHERE status = ? AND id NOT IN ({placeholders})
                        ORDER BY RANDOM()
                        LIMIT 1
                        """,
                        (TrackStatus.ACTIVE.value, *normalized_ids),
                    ).fetchone()
                if row is None and (fallback_to_any or not normalized_ids):
                    row = connection.execute(
                        """
                        SELECT * FROM tracks
                        WHERE status = ?
                        ORDER BY RANDOM()
                        LIMIT 1
                        """,
                        (TrackStatus.ACTIVE.value,),
                    ).fetchone()
                return self._row_to_track(row) if row is not None else None
            finally:
                connection.close()

    async def record_recommendation(
        self,
        stream_id: str,
        track_id: int,
        *,
        now: str | None = None,
    ) -> None:
        timestamp = now or utc_now_iso()
        async with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO recommendation_history(stream_id, track_id, recommended_at)
                    VALUES (?, ?, ?)
                    """,
                    (stream_id, int(track_id), timestamp),
                )
                connection.commit()
            finally:
                connection.close()

    async def soft_delete(
        self,
        track_id: int,
        *,
        deleted_by: str,
        now: str | None = None,
    ) -> DeleteTrackResult:
        timestamp = now or utc_now_iso()
        async with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT * FROM tracks WHERE id = ?",
                    (int(track_id),),
                ).fetchone()
                if row is None:
                    return DeleteTrackResult(DeleteStatus.NOT_FOUND, None)
                track = self._row_to_track(row)
                if track.status is TrackStatus.DELETED:
                    return DeleteTrackResult(DeleteStatus.ALREADY_DELETED, track)

                connection.execute(
                    """
                    UPDATE tracks
                    SET status = ?, deleted_at = ?, deleted_by = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        TrackStatus.DELETED.value,
                        timestamp,
                        deleted_by,
                        int(track_id),
                        TrackStatus.ACTIVE.value,
                    ),
                )
                connection.commit()
                updated_row = connection.execute(
                    "SELECT * FROM tracks WHERE id = ?",
                    (int(track_id),),
                ).fetchone()
                if updated_row is None:
                    raise RuntimeError("deleted track could not be read back")
                return DeleteTrackResult(
                    DeleteStatus.DELETED,
                    self._row_to_track(updated_row),
                )
            finally:
                connection.close()

    async def update_cover_cache(
        self,
        track_id: int,
        *,
        cover_path: str | None,
        cover_status: CoverStatus,
        now: str | None = None,
    ) -> Track | None:
        timestamp = now or utc_now_iso()
        async with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    UPDATE tracks
                    SET cover_path = ?, cover_status = ?, cover_cached_at = ?
                    WHERE id = ?
                    """,
                    (
                        cover_path,
                        cover_status.value,
                        timestamp if cover_status is CoverStatus.CACHED else None,
                        int(track_id),
                    ),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM tracks WHERE id = ?",
                    (int(track_id),),
                ).fetchone()
                return self._row_to_track(row) if row is not None else None
            finally:
                connection.close()

    async def mark_cover_accessed(self, track_id: int, *, now: str | None = None) -> None:
        async with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    "UPDATE tracks SET cover_last_accessed_at = ? WHERE id = ?",
                    (now or utc_now_iso(), int(track_id)),
                )
                connection.commit()
            finally:
                connection.close()

    async def _get_one(self, query: str, params: tuple[object, ...]) -> Track | None:
        async with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(query, params).fetchone()
                return self._row_to_track(row) if row is not None else None
            finally:
                connection.close()

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> Track:
        return Track(
            id=int(row["id"]),
            bvid=str(row["bvid"]),
            aid=int(row["aid"]),
            canonical_url=str(row["canonical_url"]),
            title=str(row["title"]),
            cover_url=str(row["cover_url"]),
            cover_path=str(row["cover_path"]) if row["cover_path"] is not None else None,
            cover_status=CoverStatus(str(row["cover_status"])),
            cover_cached_at=(str(row["cover_cached_at"]) if row["cover_cached_at"] is not None else None),
            cover_last_accessed_at=(
                str(row["cover_last_accessed_at"]) if row["cover_last_accessed_at"] is not None else None
            ),
            video_owner_mid=str(row["video_owner_mid"]),
            video_owner_name=str(row["video_owner_name"]),
            duration=int(row["duration"]),
            video_state=int(row["video_state"]),
            view_count=int(row["view_count"]),
            like_count=int(row["like_count"]),
            metadata_refreshed_at=str(row["metadata_refreshed_at"]),
            uploader_id=str(row["uploader_id"]),
            uploader_name=str(row["uploader_name"]),
            origin_group_id=(str(row["origin_group_id"]) if row["origin_group_id"] is not None else None),
            origin_stream_id=str(row["origin_stream_id"]),
            status=TrackStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            deleted_at=str(row["deleted_at"]) if row["deleted_at"] is not None else None,
            deleted_by=str(row["deleted_by"]) if row["deleted_by"] is not None else None,
        )
