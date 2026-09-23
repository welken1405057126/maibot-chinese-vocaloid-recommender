"""Safe Bilibili link parsing and video metadata requests."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

try:
    import aiohttp
except ImportError:  # The MaiBot runtime provides aiohttp; parser-only tests do not need it.
    aiohttp = None  # type: ignore[assignment]

try:
    from .models import VideoMetadata
except ImportError:
    from models import VideoMetadata  # type: ignore[no-redef]


VIEW_API_URL = "https://api.bilibili.com/x/web-interface/view"
VIDEO_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})
SHORT_LINK_HOSTS = frozenset({"b23.tv"})
COVER_HOSTS = frozenset({"i0.hdslb.com", "i1.hdslb.com", "i2.hdslb.com", "archive.biliimg.com"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEFAULT_USER_AGENT = "MaiBot-ChineseVocaloidRecommender/0.1"
DEFAULT_REFERER = "https://www.bilibili.com/"

_BVID_RE = re.compile(r"BV[A-Za-z0-9]{10}")
_BARE_VIDEO_ID_RE = re.compile(r"(?P<video_id>BV[A-Za-z0-9]{10}|av[1-9][0-9]*)", re.IGNORECASE)
_VIDEO_PATH_RE = re.compile(r"^/video/(?P<video_id>BV[A-Za-z0-9]{10}|av[1-9][0-9]*)/?$", re.IGNORECASE)
_SHORT_PATH_RE = re.compile(r"^/[A-Za-z0-9_-]{2,64}/?$")


class BilibiliClientError(RuntimeError):
    """Base error for safe user-facing request failures."""


class BilibiliLinkError(BilibiliClientError):
    """Raised before requesting an unsupported or unsafe user URL."""


class BilibiliNetworkError(BilibiliClientError):
    """Raised for timeouts, connection errors, and unexpected HTTP status codes."""


class BilibiliResponseError(BilibiliClientError, ValueError):
    """Raised when the view API response cannot produce safe metadata."""


class BilibiliResponseTooLarge(BilibiliResponseError):
    """Raised when a metadata response exceeds the configured byte limit."""


@dataclass(frozen=True, slots=True)
class VideoReference:
    """One locally parsed Bilibili identifier."""

    bvid: str | None = None
    aid: int | None = None

    def __post_init__(self) -> None:
        if (self.bvid is None) == (self.aid is None):
            raise ValueError("exactly one of bvid or aid is required")

    @property
    def query_params(self) -> dict[str, str]:
        if self.bvid is not None:
            return {"bvid": self.bvid}
        assert self.aid is not None
        return {"aid": str(self.aid)}


def parse_video_reference(url: str) -> VideoReference:
    """Parse a Bilibili video identifier or ordinary URL without a request."""

    kind, value = _classify_user_url(url)
    if kind == "short":
        raise BilibiliLinkError("b23.tv short links must be resolved by BilibiliClient")
    assert isinstance(value, VideoReference)
    return value


def validate_user_video_url(url: str) -> None:
    """Validate a Bilibili identifier, ordinary URL, or short URL locally."""

    _classify_user_url(url)


def canonical_video_url(metadata: VideoMetadata) -> str:
    return f"https://www.bilibili.com/video/{metadata.bvid}"


def validate_cover_url(url: str) -> str:
    """Normalize and validate a cover URL before every image request."""

    normalized = url.strip()
    if normalized.startswith("http://"):
        normalized = "https://" + normalized.removeprefix("http://")
    _validate_https_url(normalized, COVER_HOSTS, field="cover_url")
    return normalized


class BilibiliClient:
    """Fetch video metadata from one fixed API with strict network boundaries."""

    def __init__(
        self,
        *,
        request_timeout_seconds: float = 12,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_redirects: int = 3,
        session: Any | None = None,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self.max_redirects = int(max_redirects)
        self._session = session
        self._headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": DEFAULT_REFERER,
            "Accept": "application/json",
        }

    async def fetch_video_metadata(self, user_url: str) -> VideoMetadata:
        """Resolve one whitelisted link and retrieve normalized metadata."""

        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                async with self._session_scope() as session:
                    reference = await self._resolve_reference(session, user_url)
                    payload = await self._request_view_payload(session, reference)
                    metadata = parse_video_metadata(payload)
                    _verify_api_identifier(reference, metadata)
                    return metadata
        except TimeoutError as exc:
            raise BilibiliNetworkError("Bilibili request timed out") from exc
        except BilibiliClientError:
            raise
        except Exception as exc:
            if _is_aiohttp_client_error(exc):
                raise BilibiliNetworkError("Bilibili connection failed") from exc
            raise

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[Any]:
        if self._session is not None:
            yield self._session
            return
        if aiohttp is None:
            raise BilibiliNetworkError("aiohttp is unavailable in the MaiBot runtime")

        timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)
        async with aiohttp.ClientSession(
            timeout=timeout,
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
        ) as session:
            yield session

    async def _resolve_reference(self, session: Any, user_url: str) -> VideoReference:
        kind, value = _classify_user_url(user_url)
        if kind == "video":
            assert isinstance(value, VideoReference)
            return value
        assert isinstance(value, str)
        return await self._resolve_short_link(session, value)

    async def _resolve_short_link(self, session: Any, short_url: str) -> VideoReference:
        current_url = short_url
        for redirects_followed in range(self.max_redirects + 1):
            async with session.get(
                current_url,
                headers=self._headers,
                allow_redirects=False,
            ) as response:
                if response.status not in REDIRECT_STATUSES:
                    raise BilibiliNetworkError(f"b23.tv returned unexpected HTTP {response.status}")
                location = response.headers.get("Location")
                if not location:
                    raise BilibiliResponseError("b23.tv redirect did not include Location")

            if redirects_followed >= self.max_redirects:
                raise BilibiliLinkError("b23.tv exceeded the redirect limit")
            target_url = urljoin(current_url, location)
            kind, value = _classify_user_url(target_url)
            if kind == "video":
                assert isinstance(value, VideoReference)
                return value
            assert isinstance(value, str)
            current_url = value

        raise BilibiliLinkError("b23.tv exceeded the redirect limit")

    async def _request_view_payload(self, session: Any, reference: VideoReference) -> Mapping[str, Any]:
        async with session.get(
            VIEW_API_URL,
            params=reference.query_params,
            headers=self._headers,
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise BilibiliNetworkError(f"Bilibili API returned HTTP {response.status}")
            raw = await _read_limited(response, self.max_response_bytes)

        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BilibiliResponseError("Bilibili API did not return valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise BilibiliResponseError("Bilibili API JSON root must be an object")
        return payload


async def _read_limited(response: Any, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise BilibiliResponseTooLarge("Bilibili API response is too large")
        except ValueError:
            pass

    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise BilibiliResponseTooLarge("Bilibili API response is too large")
    return bytes(body)


def parse_video_metadata(payload: Mapping[str, Any]) -> VideoMetadata:
    """Convert one successful Bilibili view response to the internal model."""

    code = payload.get("code")
    if not _is_int(code) or code != 0:
        message = payload.get("message", "unknown error")
        raise BilibiliResponseError(f"Bilibili API failed: code={code!r}, message={message!r}")

    data = _require_mapping(payload.get("data"), "data")
    stats = _require_mapping(data.get("stat"), "data.stat")
    bvid = _require_str(data.get("bvid"), "data.bvid")
    if _BVID_RE.fullmatch(bvid) is None:
        raise BilibiliResponseError("data.bvid has an invalid format")

    cover_url = validate_cover_url(_require_str(data.get("pic"), "data.pic"))

    view_count = _require_int(stats.get("view"), "data.stat.view")
    if view_count < 0:
        raise BilibiliResponseError("data.stat.view cannot be negative")

    return VideoMetadata(
        bvid=bvid,
        aid=_require_int(data.get("aid"), "data.aid"),
        title=_sanitize_title(_require_str(data.get("title"), "data.title")),
        cover_url=cover_url,
        state=_require_int(data.get("state"), "data.state"),
        view_count=view_count,
    )


def _classify_user_url(url: str) -> tuple[str, VideoReference | str]:
    if not isinstance(url, str) or not url.strip():
        raise BilibiliLinkError("Bilibili link cannot be empty")
    if len(url) > 2048:
        raise BilibiliLinkError("Bilibili link is too long")

    normalized_input = url.strip()
    bare_match = _BARE_VIDEO_ID_RE.fullmatch(normalized_input)
    if bare_match is not None:
        return "video", _video_reference_from_id(bare_match.group("video_id"))

    parts, host = _split_safe_https_url(normalized_input)
    normalized_url = urlunsplit(("https", host, parts.path, parts.query, ""))
    if host in SHORT_LINK_HOSTS:
        if _SHORT_PATH_RE.fullmatch(parts.path) is None:
            raise BilibiliLinkError("invalid b23.tv short-link path")
        return "short", normalized_url
    if host not in VIDEO_HOSTS:
        raise BilibiliLinkError("link host is not in the Bilibili whitelist")

    match = _VIDEO_PATH_RE.fullmatch(parts.path)
    if match is None:
        raise BilibiliLinkError("link must point to one Bilibili video")
    return "video", _video_reference_from_id(match.group("video_id"))


def _video_reference_from_id(video_id: str) -> VideoReference:
    if video_id[:2].casefold() == "bv":
        return VideoReference(bvid="BV" + video_id[2:])
    return VideoReference(aid=int(video_id[2:]))


def _split_safe_https_url(url: str) -> tuple[Any, str]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BilibiliLinkError("link has an invalid host or port") from exc
    if parts.scheme.casefold() != "https":
        raise BilibiliLinkError("only HTTPS Bilibili links are accepted")
    if parts.username is not None or parts.password is not None:
        raise BilibiliLinkError("link must not contain user information")
    if port is not None:
        raise BilibiliLinkError("link must not contain an explicit port")
    host = parts.hostname
    if host is None or host.endswith("."):
        raise BilibiliLinkError("link host is invalid")
    return parts, host.casefold()


def _validate_https_url(url: str, allowed_hosts: frozenset[str], *, field: str) -> None:
    try:
        _, host = _split_safe_https_url(url)
    except BilibiliLinkError as exc:
        raise BilibiliResponseError(f"{field} is not a safe HTTPS URL") from exc
    if host not in allowed_hosts:
        raise BilibiliResponseError(f"{field} host is not allowed")


def _verify_api_identifier(reference: VideoReference, metadata: VideoMetadata) -> None:
    if reference.bvid is not None and reference.bvid.casefold() != metadata.bvid.casefold():
        raise BilibiliResponseError("Bilibili API returned a different BVID")
    if reference.aid is not None and reference.aid != metadata.aid:
        raise BilibiliResponseError("Bilibili API returned a different AID")


def _sanitize_title(title: str) -> str:
    cleaned = " ".join("".join(character if character.isprintable() else " " for character in title).split())
    if not cleaned:
        raise BilibiliResponseError("data.title is empty after sanitization")
    return cleaned[:200]


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BilibiliResponseError(f"{field} must be an object")
    return value


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BilibiliResponseError(f"{field} must be a non-empty string")
    return value.strip()


def _require_int(value: object, field: str) -> int:
    if not _is_int(value):
        raise BilibiliResponseError(f"{field} must be an integer")
    return int(value)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_aiohttp_client_error(exc: Exception) -> bool:
    return aiohttp is not None and isinstance(exc, aiohttp.ClientError)


# Manual examples supplied by welken:
# https://api.bilibili.com/x/web-interface/view?bvid=BV1vFxXzWELU
# https://api.bilibili.com/x/web-interface/view?bvid=BV1PAYS6cE4B
