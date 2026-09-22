"""Validated local cache for recoverable Bilibili cover images."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import aiohttp
except ImportError:  # The MaiBot runtime provides aiohttp; injected sessions keep tests standalone.
    aiohttp = None  # type: ignore[assignment]

try:
    from .bilibili_client import BilibiliClientError, validate_cover_url
    from .models import Track, TrackStatus
except ImportError:
    from bilibili_client import BilibiliClientError, validate_cover_url  # type: ignore[no-redef]
    from models import Track, TrackStatus  # type: ignore[no-redef]


DEFAULT_MAX_COVER_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_CACHE_MIB = 256
IMAGE_EXTENSIONS = frozenset({".jpg", ".png", ".gif", ".webp"})
_BVID_RE = re.compile(r"BV[A-Za-z0-9]{10}")


class CoverCacheError(RuntimeError):
    """Base error for cover download and persistence failures."""


class CoverDownloadError(CoverCacheError):
    """Raised for blocked URLs, network failures, and oversized responses."""


class InvalidCoverImage(CoverCacheError):
    """Raised when downloaded bytes are not a supported raster image."""


@dataclass(frozen=True, slots=True)
class CachedCover:
    relative_path: str
    media_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CleanupReport:
    removed_paths: tuple[str, ...]
    affected_track_ids: tuple[int, ...]
    failed_paths: tuple[str, ...]
    bytes_freed: int
    remaining_bytes: int


@dataclass(frozen=True, slots=True)
class _CacheFile:
    path: Path
    size: int
    modified_at: datetime
    track: Track | None


class CoverCache:
    """Download, validate, read, and lazily clean local cover files."""

    def __init__(
        self,
        plugin_dir: Path,
        *,
        request_timeout_seconds: float = 12,
        max_cover_bytes: int = DEFAULT_MAX_COVER_BYTES,
        max_cache_mib: int = DEFAULT_MAX_CACHE_MIB,
        cleanup_interval_hours: int = 24,
        deleted_retention_days: int = 30,
        orphan_retention_days: int = 7,
        temp_retention_hours: int = 24,
        session: Any | None = None,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if max_cover_bytes <= 0:
            raise ValueError("max_cover_bytes must be positive")
        if max_cache_mib < 0:
            raise ValueError("max_cache_mib cannot be negative")
        if min(cleanup_interval_hours, deleted_retention_days, orphan_retention_days, temp_retention_hours) < 0:
            raise ValueError("cache retention settings cannot be negative")

        self.plugin_dir = Path(plugin_dir).resolve()
        self.cover_dir = self.plugin_dir / "covers"
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.max_cover_bytes = int(max_cover_bytes)
        self.max_cache_bytes = int(max_cache_mib) * 1024 * 1024
        self.cleanup_interval = timedelta(hours=int(cleanup_interval_hours))
        self.deleted_retention = timedelta(days=int(deleted_retention_days))
        self.orphan_retention = timedelta(days=int(orphan_retention_days))
        self.temp_retention = timedelta(hours=int(temp_retention_hours))
        self._session = session
        self._file_lock = asyncio.Lock()
        self._last_cleanup_at: datetime | None = None
        self._headers = {
            "User-Agent": "MaiBot-ChineseVocaloidRecommender/0.1",
            "Referer": "https://www.bilibili.com/",
            "Accept": "image/jpeg,image/png,image/gif,image/webp",
        }

    async def cache_cover(self, bvid: str, cover_url: str) -> CachedCover:
        """Download a cover and atomically store it under a BVID-based name."""

        if _BVID_RE.fullmatch(bvid) is None:
            raise CoverCacheError("invalid BVID for cover filename")
        try:
            safe_url = validate_cover_url(cover_url)
        except BilibiliClientError as exc:
            raise CoverDownloadError("cover URL is not allowed") from exc

        body = await self._download_cover(safe_url)
        extension, media_type = detect_image_format(body)
        target = self.cover_dir / f"{bvid}{extension}"

        async with self._file_lock:
            try:
                await asyncio.to_thread(self._atomic_store, target, body, bvid)
            except OSError as exc:
                raise CoverCacheError("could not store cover image") from exc

        return CachedCover(
            relative_path=f"covers/{target.name}",
            media_type=media_type,
            size_bytes=len(body),
        )

    async def read_cover(self, relative_path: str) -> bytes | None:
        """Read a valid cached cover, returning None for missing or corrupt files."""

        path = self._resolve_relative_path(relative_path)
        if path is None:
            return None
        return await asyncio.to_thread(self._read_valid_file, path)

    async def delete_cover(self, relative_path: str) -> bool:
        path = self._resolve_relative_path(relative_path)
        if path is None:
            return False
        async with self._file_lock:
            return await asyncio.to_thread(_unlink_file, path)

    async def cleanup_if_due(
        self,
        tracks: Sequence[Track],
        *,
        now: datetime | None = None,
    ) -> CleanupReport | None:
        """Run lazy cleanup at most once per configured interval."""

        current_time = _as_utc(now or datetime.now(UTC))
        async with self._file_lock:
            if self._last_cleanup_at is not None and current_time - self._last_cleanup_at < self.cleanup_interval:
                return None
            report = await asyncio.to_thread(self._cleanup_sync, tracks, current_time)
            self._last_cleanup_at = current_time
            return report

    async def cleanup(
        self,
        tracks: Sequence[Track],
        *,
        now: datetime | None = None,
    ) -> CleanupReport:
        """Force cleanup, primarily for maintenance and deterministic tests."""

        current_time = _as_utc(now or datetime.now(UTC))
        async with self._file_lock:
            report = await asyncio.to_thread(self._cleanup_sync, tracks, current_time)
            self._last_cleanup_at = current_time
            return report

    async def _download_cover(self, cover_url: str) -> bytes:
        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                async with self._session_scope() as session:
                    async with session.get(
                        cover_url,
                        headers=self._headers,
                        allow_redirects=False,
                    ) as response:
                        if response.status != 200:
                            raise CoverDownloadError(f"cover server returned HTTP {response.status}")
                        return await _read_limited(response, self.max_cover_bytes)
        except TimeoutError as exc:
            raise CoverDownloadError("cover request timed out") from exc
        except CoverCacheError:
            raise
        except Exception as exc:
            if _is_aiohttp_client_error(exc):
                raise CoverDownloadError("cover connection failed") from exc
            raise

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[Any]:
        if self._session is not None:
            yield self._session
            return
        if aiohttp is None:
            raise CoverDownloadError("aiohttp is unavailable in the MaiBot runtime")

        timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)
        async with aiohttp.ClientSession(
            timeout=timeout,
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
        ) as session:
            yield session

    def _atomic_store(self, target: Path, body: bytes, bvid: str) -> None:
        self.cover_dir.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self.cover_dir,
            prefix=f".{bvid}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(body)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, target)
            for extension in IMAGE_EXTENSIONS:
                stale_path = self.cover_dir / f"{bvid}{extension}"
                if stale_path != target:
                    _unlink_file(stale_path)
        except Exception:
            _unlink_file(temporary_path)
            raise

    def _read_valid_file(self, path: Path) -> bytes | None:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > self.max_cover_bytes:
                return None
            body = path.read_bytes()
            extension, _ = detect_image_format(body)
            if path.suffix.casefold() != extension:
                return None
            return body
        except (OSError, InvalidCoverImage):
            return None

    def _resolve_relative_path(self, relative_path: str) -> Path | None:
        candidate = Path(relative_path)
        if candidate.is_absolute() or len(candidate.parts) != 2 or candidate.parts[0] != "covers":
            return None
        path = self.cover_dir / candidate.parts[1]
        if path.parent != self.cover_dir or path.suffix.casefold() not in IMAGE_EXTENSIONS:
            return None
        return path

    def _cleanup_sync(self, tracks: Sequence[Track], now: datetime) -> CleanupReport:
        self.cover_dir.mkdir(parents=True, exist_ok=True)
        references: dict[str, Track] = {}
        for track in tracks:
            if track.cover_path is None:
                continue
            path = self._resolve_relative_path(track.cover_path)
            if path is not None:
                references[path.name] = track

        removed_paths: list[str] = []
        affected_track_ids: set[int] = set()
        failed_paths: list[str] = []
        bytes_freed = 0

        def remove(file_info: _CacheFile) -> bool:
            nonlocal bytes_freed
            relative_path = f"covers/{file_info.path.name}"
            if not _unlink_file(file_info.path):
                if file_info.path.exists():
                    failed_paths.append(relative_path)
                return False
            removed_paths.append(relative_path)
            bytes_freed += file_info.size
            if file_info.track is not None:
                affected_track_ids.add(file_info.track.id)
            return True

        remaining: list[_CacheFile] = []
        for path in self.cover_dir.iterdir():
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                failed_paths.append(f"covers/{path.name}")
                continue

            file_info = _CacheFile(
                path=path,
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                track=references.get(path.name),
            )
            if path.suffix.casefold() == ".tmp":
                if now - file_info.modified_at >= self.temp_retention:
                    remove(file_info)
                continue
            if path.suffix.casefold() not in IMAGE_EXTENSIONS:
                continue
            if file_info.track is None and now - file_info.modified_at >= self.orphan_retention:
                remove(file_info)
                continue
            if (
                file_info.track is not None
                and file_info.track.status is TrackStatus.DELETED
                and _age_since(file_info.track.deleted_at, now) >= self.deleted_retention
            ):
                remove(file_info)
                continue
            remaining.append(file_info)

        total_bytes = sum(file_info.size for file_info in remaining)
        if total_bytes > self.max_cache_bytes:
            candidates = sorted(remaining, key=lambda item: self._eviction_key(item, now))
            for file_info in candidates:
                if total_bytes <= self.max_cache_bytes:
                    break
                if remove(file_info):
                    total_bytes -= file_info.size

        return CleanupReport(
            removed_paths=tuple(sorted(removed_paths)),
            affected_track_ids=tuple(sorted(affected_track_ids)),
            failed_paths=tuple(sorted(set(failed_paths))),
            bytes_freed=bytes_freed,
            remaining_bytes=total_bytes,
        )

    @staticmethod
    def _eviction_key(file_info: _CacheFile, now: datetime) -> tuple[int, datetime]:
        track = file_info.track
        if track is None:
            return 0, file_info.modified_at
        if track.status is TrackStatus.DELETED:
            return 1, _parse_datetime(track.deleted_at) or file_info.modified_at
        last_used = (
            _parse_datetime(track.cover_last_accessed_at)
            or _parse_datetime(track.cover_cached_at)
            or _parse_datetime(track.created_at)
            or now
        )
        return 2, last_used


async def _read_limited(response: Any, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise CoverDownloadError("cover response is too large")
        except ValueError:
            pass

    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise CoverDownloadError("cover response is too large")
    return bytes(body)


def detect_image_format(body: bytes) -> tuple[str, str]:
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise InvalidCoverImage("cover is not JPEG, PNG, GIF, or WEBP")


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _age_since(value: str | None, now: datetime) -> timedelta:
    parsed = _parse_datetime(value)
    if parsed is None:
        return timedelta(0)
    return max(timedelta(0), now - parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _unlink_file(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _is_aiohttp_client_error(exc: Exception) -> bool:
    return aiohttp is not None and isinstance(exc, aiohttp.ClientError)
