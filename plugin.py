"""MaiBot command adapter for the Chinese Vocaloid recommender."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase

try:
    from .bilibili_client import BilibiliClient
    from .cover_cache import CoverCache
    from .repository import TrackRepository
    from .upload_service import UploadService, format_upload_reply
except ImportError:
    from bilibili_client import BilibiliClient  # type: ignore[no-redef]
    from cover_cache import CoverCache  # type: ignore[no-redef]
    from repository import TrackRepository  # type: ignore[no-redef]
    from upload_service import UploadService, format_upload_reply  # type: ignore[no-redef]


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="0.1.0", description="配置版本")


class ScopeSectionConfig(PluginConfigBase):
    """作用范围配置。"""

    __ui_label__ = "范围"
    __ui_icon__ = "users"
    __ui_order__ = 1

    allowed_group_ids: list[str] = Field(default_factory=list, description="允许使用的群号，空列表表示无限制")
    allow_private: bool = Field(default=True, description="是否允许私聊使用")
    library_mode: str = Field(default="global", description="曲库模式，首版固定 global")


class PermissionSectionConfig(PluginConfigBase):
    """权限配置。"""

    __ui_label__ = "权限"
    __ui_icon__ = "shield"
    __ui_order__ = 2

    global_admin_ids: list[str] = Field(default_factory=list, description="全局管理员 QQ 号")
    allow_group_admin_delete: bool = Field(default=True, description="是否允许群管理员删除本群投稿")


class RecommendationSectionConfig(PluginConfigBase):
    """推荐配置。"""

    __ui_label__ = "推荐"
    __ui_icon__ = "music"
    __ui_order__ = 3

    recent_exclusion_count: int = Field(default=5, description="推荐时避开最近 N 首")
    metadata_refresh_days: int = Field(default=2, description="视频信息超过 N 天后按需刷新")


class RateLimitSectionConfig(PluginConfigBase):
    """上传和推荐限流配置。"""

    __ui_label__ = "限流"
    __ui_icon__ = "timer"
    __ui_order__ = 4

    upload_cooldown_seconds: int = Field(default=60, description="同一用户两次上传尝试的间隔秒数")
    upload_daily_limit: int = Field(default=10, description="同一用户每天成功上传上限")
    stream_upload_attempts_per_minute: int = Field(default=20, description="同一聊天流每分钟上传尝试上限")
    recommend_cooldown_seconds: int = Field(default=5, description="同一用户推荐命令冷却秒数")


class NetworkSectionConfig(PluginConfigBase):
    """B 站只读请求配置。"""

    __ui_label__ = "网络"
    __ui_icon__ = "network"
    __ui_order__ = 5

    request_timeout_seconds: float = Field(default=12, description="单次命令的 B 站请求总超时秒数")
    max_redirects: int = Field(default=3, description="b23.tv 短链最多跳转次数")
    metadata_max_bytes: int = Field(default=2 * 1024 * 1024, description="元数据响应最大字节数")
    max_cover_bytes: int = Field(default=8 * 1024 * 1024, description="单张封面响应最大字节数")


class CoverCacheSectionConfig(PluginConfigBase):
    """封面缓存配置。"""

    __ui_label__ = "封面缓存"
    __ui_icon__ = "image"
    __ui_order__ = 6

    cleanup_interval_hours: int = Field(default=24, description="惰性清理间隔小时")
    deleted_retention_days: int = Field(default=30, description="软删除曲目封面保留天数")
    orphan_retention_days: int = Field(default=7, description="孤儿封面保留天数")
    temp_retention_hours: int = Field(default=24, description="临时文件保留小时")
    max_cache_mib: int = Field(default=256, description="封面缓存上限 MiB")


class ChineseVocaloidRecommenderPluginConfig(PluginConfigBase):
    """插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    scope: ScopeSectionConfig = Field(default_factory=ScopeSectionConfig)
    permission: PermissionSectionConfig = Field(default_factory=PermissionSectionConfig)
    recommendation: RecommendationSectionConfig = Field(default_factory=RecommendationSectionConfig)
    rate_limit: RateLimitSectionConfig = Field(default_factory=RateLimitSectionConfig)
    network: NetworkSectionConfig = Field(default_factory=NetworkSectionConfig)
    cover_cache: CoverCacheSectionConfig = Field(default_factory=CoverCacheSectionConfig)


class ChineseVocaloidRecommenderPlugin(MaiBotPlugin):
    """中 V 推荐插件。"""

    config_model = ChineseVocaloidRecommenderPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._data_dir: Path | None = None
        self._repository: TrackRepository | None = None
        self._upload_service: UploadService | None = None

    async def on_load(self) -> None:
        data_dir = self.ctx.paths.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = data_dir
        self._repository = TrackRepository(data_dir / "tracks.sqlite3")
        await self._repository.initialize()
        self._configure_services()

    async def on_unload(self) -> None:
        self._upload_service = None
        self._repository = None
        self._data_dir = None

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        del scope, config_data, version
        self._configure_services()

    def _configure_services(self) -> None:
        if self._repository is None or self._data_dir is None:
            return
        network = self.config.network
        cache = self.config.cover_cache
        limits = self.config.rate_limit
        bilibili_client = BilibiliClient(
            request_timeout_seconds=network.request_timeout_seconds,
            max_response_bytes=network.metadata_max_bytes,
            max_redirects=network.max_redirects,
        )
        cover_cache = CoverCache(
            self._data_dir,
            request_timeout_seconds=network.request_timeout_seconds,
            max_cover_bytes=network.max_cover_bytes,
            max_cache_mib=cache.max_cache_mib,
            cleanup_interval_hours=cache.cleanup_interval_hours,
            deleted_retention_days=cache.deleted_retention_days,
            orphan_retention_days=cache.orphan_retention_days,
            temp_retention_hours=cache.temp_retention_hours,
        )
        self._upload_service = UploadService(
            self._repository,
            bilibili_client,
            cover_cache,
            cooldown_seconds=limits.upload_cooldown_seconds,
            daily_limit=limits.upload_daily_limit,
            stream_attempts_per_minute=limits.stream_upload_attempts_per_minute,
        )

    def _in_scope(self, group_id: str) -> bool:
        if not self.config.plugin.enabled:
            return False
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return self.config.scope.allow_private
        allowed = {str(item).strip() for item in self.config.scope.allowed_group_ids if str(item).strip()}
        return not allowed or normalized_group_id in allowed

    @Command(
        "recommend_chinese_vocaloid",
        description="推荐一首中v，发布中v的b站链接",
        pattern=r"(?i)^/(?:中v推荐|随机中v)\s*$",
    )
    async def recommend_chinese_vocaloid(
        self, stream_id: str = "", user_id: str = "", group_id: str = "", **kwargs: Any
    ):
        del user_id, kwargs
        if not self._in_scope(group_id):
            return True, "", True
        return True, "", True

    @Command(
        "update_chinese_vocaloid",
        description="群友上传中v链接到库，参数 <B站链接>",
        pattern=r"(?i)^/上传中v\s+(?P<url>\S+)\s*$",
    )
    async def update_chinese_vocaloid(
        self, stream_id: str = "", user_id: str = "", group_id: str = "", **kwargs: Any
    ):
        if not self._in_scope(group_id):
            return True, "", True
        matched_groups = kwargs.get("matched_groups")
        url = str(matched_groups.get("url") or "").strip() if isinstance(matched_groups, dict) else ""
        if not url:
            reply = "链接不对，只收B站视频链接"
        elif not stream_id or not user_id or self._upload_service is None:
            reply = "B站没回应，待会再试"
        else:
            result = await self._upload_service.upload(
                url,
                stream_id=str(stream_id),
                user_id=str(user_id),
                group_id=str(group_id).strip() or None,
            )
            reply = format_upload_reply(result)
        await self.ctx.send.text(reply, stream_id)
        return True, reply, True

    @Command(
        "to_delete_chinese_vocaloid",
        description="管理员或原上传者删除，参数 <ID>",
        pattern=r"(?i)^/中v删除\s+(?P<track_id>\d+)\s*$",
    )
    async def to_delete_chinese_vocaloid(
        self, stream_id: str = "", user_id: str = "", group_id: str = "", **kwargs: Any
    ):
        del user_id, kwargs
        if not self._in_scope(group_id):
            return True, "", True
        return True, "", True

    @Command(
        "help_chinese_vocaloid",
        description="中v插件有关帮助",
        pattern=r"(?i)^/中v帮助\s*$",
    )
    async def help_chinese_vocaloid(
        self, stream_id: str = "", user_id: str = "", group_id: str = "", **kwargs: Any
    ):
        del user_id, kwargs
        if not self._in_scope(group_id):
            return True, "", True
        text = (
            "== 中v推荐 ==\n"
            "/中v推荐：随机一首中v\n"
            "/随机中v：随机一首中v\n"
            "/上传中v <B站链接>：收录曲目\n"
            "/中v删除 <ID>：删除自己上传的曲目\n"
            "/中v帮助：显示本说明"
        )
        await self.ctx.send.text(text, stream_id)
        return True, text, True


def create_plugin() -> ChineseVocaloidRecommenderPlugin:
    return ChineseVocaloidRecommenderPlugin()
