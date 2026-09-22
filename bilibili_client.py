"""Parse responses from Bilibili's fixed video view endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:
    from .models import VideoMetadata
except ImportError:
    from models import VideoMetadata  # type: ignore[no-redef]


class BilibiliResponseError(ValueError):
    """Raised when the view API response cannot produce safe metadata."""


def parse_video_metadata(payload: Mapping[str, Any]) -> VideoMetadata:
    """Convert one successful Bilibili view response to the internal model."""

    code = payload.get("code")
    if not _is_int(code) or code != 0:
        message = payload.get("message", "unknown error")
        raise BilibiliResponseError(f"Bilibili API failed: code={code!r}, message={message!r}")

    data = _require_mapping(payload.get("data"), "data")
    stats = _require_mapping(data.get("stat"), "data.stat")
    cover_url = _require_str(data.get("pic"), "data.pic")
    if cover_url.startswith("http://"):
        cover_url = "https://" + cover_url.removeprefix("http://")
    if not cover_url.startswith("https://"):
        raise BilibiliResponseError("data.pic must be an HTTP(S) URL")

    return VideoMetadata(
        bvid=_require_str(data.get("bvid"), "data.bvid"),
        aid=_require_int(data.get("aid"), "data.aid"),
        title=_require_str(data.get("title"), "data.title"),
        cover_url=cover_url,
        state=_require_int(data.get("state"), "data.state"),
        view_count=_require_int(stats.get("view"), "data.stat.view"),
    )


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


# Manual examples supplied by welken:
# https://api.bilibili.com/x/web-interface/view?bvid=BV1vFxXzWELU
# https://api.bilibili.com/x/web-interface/view?bvid=BV1PAYS6cE4B
