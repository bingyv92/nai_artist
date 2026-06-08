"""nai_artist WebUI Router。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI

from src.app.plugin_system.base import BaseRouter
from src.kernel.logger import get_logger

from .config import NaiArtistConfig
from .webui_app import create_app
from .webui_logic import initialize_webui_runtime


logger = get_logger("nai_artist.router")


class NaiArtistWebUIRouter(BaseRouter):
    """将 nai_artist WebUI 挂载到主程序 HTTP 服务器。"""

    router_name = "webui"
    router_description = "NAI Artist WebUI"

    def __init__(self, plugin: Any) -> None:
        config = getattr(plugin, "config", None)
        if isinstance(config, NaiArtistConfig):
            route_path = config.webui.route_path.strip()
            self.custom_route_path = route_path or "/plugins/nai-artist"
        self._sub_app: FastAPI | None = None
        super().__init__(plugin)

    def register_endpoints(self) -> None:
        """挂载现有 WebUI 子应用。"""
        config_path = Path("config/plugins/nai_artist/config.toml")
        self._sub_app = create_app(config_path=config_path, initialize_runtime=False)
        self.app.mount("/", self._sub_app)

    async def startup(self) -> None:
        """初始化 WebUI 所需的最小运行时。"""
        initialize_webui_runtime()
        logger.info(
            f"nai_artist WebUI 已挂载到主程序 HTTP 路径: {self.get_route_path()}"
        )

    async def shutdown(self) -> None:
        """清理预览临时目录。"""
        if self._sub_app is None:
            return
        temp_dir = getattr(self._sub_app.state, "webui_preview_temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()
            self._sub_app.state.webui_preview_temp_dir = None
