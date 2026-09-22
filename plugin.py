"""
中V推荐插件
"""

import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase

DATE_FORMAT = "%Y-%m-%d"


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

    allowed_group_ids: list[str] = Field(default_factory=list, description="允许使用的群号，空群号表示无限制")
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
    """推荐配置"""

    __ui_label__ = "推荐"
    __ui_icon__ = "music"
    __ui_order__ = 3

    recent_exclusion_count: int = Field(default=5, description="推荐时避开最近 N 首")
    metadata_refresh_days: int = Field(default=2, description="视频信息超过 N 天后按需刷新")



class NetworkSectionConfig(PluginConfigBase):
    """B 站只读请求配置。"""

    __ui_label__ = "网络"
    __ui_icon__ = "network"
    __ui_order__ = 4

    request_timeout_seconds: float = Field(default=12, description="单次命令的 B 站请求总超时秒数")
    max_redirects: int = Field(default=3, description="b23.tv 短链最多跳转次数")
    metadata_max_bytes: int = Field(default=2 * 1024 * 1024, description="元数据响应最大字节数")


class CoverCacheSectionConfig(PluginConfigBase):
    """封面缓存配置。"""
    __ui_label__ = "封面缓存"
    __ui_icon__ = "image"
    __ui_order__ = 5

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
    network: NetworkSectionConfig = Field(default_factory=NetworkSectionConfig)
    cover_cache: CoverCacheSectionConfig = Field(default_factory=CoverCacheSectionConfig)


class ChineseVocaloidRecommenderPlugin(MaiBotPlugin):
    """中V推荐插件"""

    config_model = ChineseVocaloidRecommenderPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._records_file: Path | None = None

    async def on_load(self) -> None:
        """处理插件加载。"""
        """创建数据目录、读取持久化文件、初始化资源、打日志"""


    async def on_unload(self) -> None:
        """处理插件卸载。"""

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """处理配置热重载事件。"""

        del scope, config_data, version

    async def _get_video_base64(self) -> str|None:

        return None




    # ===== Command 组件 =====

    @Command(
        "recommend_chinese_vocaloid",
        description="推荐一首中v，发布中v的b站链接",
        pattern=r"(?i)^/(?:中v推荐|随机中v)\s*$",
    )
    async def recommend_chinese_vocaloid(
        self, stream_id: str = "", user_id: str = "", group_id: str = "", **kwargs: Any
    ):
        """处理 /中v推荐 和 /随机中v。"""
        del user_id, group_id, kwargs
        return True, "", True


    @Command(
        "update_chinese_vocaloid",
        description="群友上传中v链接到库，参数 <B站链接>",
        pattern=r"(?i)^/上传中v\s+(?P<url>https?://\S+)\s*$",
    )
    async def update_chinese_vocaloid(
        self, stream_id: str = "", user_id: str = "", group_id: str = "", **kwargs: Any
    ):
        del user_id, group_id, kwargs
        return True, "==", True

    @Command(
        "to_delete_chinese_vocaloid",
        description="管理员或原上传者删除，参数 <ID>",
        pattern=r"(?i)^/中v删除\s+(?P<track_id>\d+)\s*$",
    )
    async def to_delete_chinese_vocaloid(
        self, stream_id: str = "", user_id: str = "", group_id: str = "", **kwargs: Any
    ):
        del user_id, group_id, kwargs
        return True, "==", True

    @Command(
        "help_chinese_vocaloid",
        description="中v插件有关帮助",
        pattern=r"(?i)^/中v帮助\s*$",
    )
    async def help_chinese_vocaloid(
        self, stream_id: str = "", user_id: str = "", group_id: str = "", **kwargs: Any
    ):
        del user_id, group_id, kwargs

        text = (
            " == 中v推荐 功能 == \n"
            "/中v推荐：随机一首中v\n"
            "/中v推荐+ID：推荐特定ID的中v\n"
            "/上传中v <B站链接>：收录曲目\n"
            "/中v删除 <ID>：删除自己上传的曲目"
        )
        await self.ctx.send.text(text, stream_id)
        return True, "已显示帮助", True



def create_plugin() -> ChineseVocaloidRecommenderPlugin:
    """创建插件实例。"""

    return ChineseVocaloidRecommenderPlugin()
