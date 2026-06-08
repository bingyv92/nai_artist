"""nai_artist 插件入口。

加载后注册 system reminder 到 actor bucket，让 bot 获得手机拍照和画画的人设。
"""

from __future__ import annotations

import os

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .action import ShareVisualAction
from .change_outfit_action import ChangeOutfitAction
from .config import NaiArtistConfig
from .router import NaiArtistWebUIRouter
from .service import NaiArtistService
from .wardrobe import WardrobeManager, WardrobeService

logger = get_logger("nai_artist")

_REMINDER_BUCKET = "actor"
_REMINDER_NAME = "关于用生图在视觉上呈现自己给对方和画画"
_REMINDER_CONTENT = (
    "你有一个道具，可以随时随地记录自己的状态（表情、动作、场景）并生成图片展示给对方。不要只用文字描述“我在做什么/穿什么/什么表情”——优先考虑拍照分享。只要你觉得当前氛围适合展示一个画面（比如你换了衣服、看到有趣的东西、想用表情代替语言），就主动拍下来给对方看，这比文字更生动。"
    "同时你也会画画，心情好时可以答应别人的画图请求。更主动的是：即使对方没开口，你也能主动画些小涂鸦来调侃对方、回应情绪或活跃气氛——比如画个Q版自己撒花、画只呆猫盯着对方、或者画个太阳递过去。"
    "你还有一套可切换的衣柜，会根据对话氛围、场景或对方请求自然换穿搭。换好后用图片展示，而不是文字描述“我现在穿了什么”。"
    "当对方要求你画其他角色、OC 或二创对象时，不要擅自脑补和补全未被明确说明的人设细节；"
    "只能根据对方已提供的信息来画，不够明确时就先问或保持泛化。"
)


@register_plugin
class NaiArtistPlugin(BasePlugin):
    """NAI Artist 插件。"""

    plugin_name: str = "nai_artist"
    plugin_description: str = "让 bot 像真人一样用手机拍照或展示手绘画作"
    plugin_version: str = "1.0.3"

    configs: list[type] = [NaiArtistConfig]
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """返回插件提供的组件类列表。"""
        config = self.config if isinstance(self.config, NaiArtistConfig) else None
        if config is not None and not config.plugin.enabled:
            return []

        components: list[type] = [NaiArtistService, ShareVisualAction]
        if config is not None and config.wardrobe.enabled:
            components.extend([WardrobeService, ChangeOutfitAction])
        if config is not None and config.webui.mount_on_main_http:
            components.append(NaiArtistWebUIRouter)
        return components

    async def on_plugin_loaded(self) -> None:
        """插件加载时注册 system reminder 并确保缓存目录存在。"""
        if isinstance(self.config, NaiArtistConfig) and not self.config.plugin.enabled:
            logger.info("nai_artist 已通过配置禁用，跳过 reminder 注册")
            return

        prompt_api.add_system_reminder(
            bucket=_REMINDER_BUCKET,
            name=_REMINDER_NAME,
            content=_REMINDER_CONTENT,
        )
        logger.debug("nai_artist actor reminder 已注册")

        if isinstance(self.config, NaiArtistConfig):
            cache_dir = self.config.storage.cache_dir
            os.makedirs(cache_dir, exist_ok=True)
            logger.debug(f"nai_artist 缓存目录已确认: {cache_dir}")

            if self.config.wardrobe.enabled:
                wardrobe_manager = WardrobeManager(self.config.wardrobe.data_file)
                wardrobe_manager.load()
                logger.debug(f"nai_artist 衣柜数据已确认: {self.config.wardrobe.data_file}")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时移除 system reminder。"""
        from src.core.prompt import get_system_reminder_store

        get_system_reminder_store().delete(_REMINDER_BUCKET, _REMINDER_NAME)
        logger.debug("nai_artist actor reminder 已移除")
