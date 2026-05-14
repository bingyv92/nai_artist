"""nai_artist 插件配置。"""

from __future__ import annotations

from typing import ClassVar, Literal

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class NaiArtistConfig(BaseConfig):
    """nai_artist 插件配置类。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "NAI Artist 生图插件配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件全局开关。"""

        enabled: bool = Field(default=True, description="是否启用 share_visual 生图功能；关闭后 Action 不会被 LLM 调用，system reminder 也不会注入")

    @config_section("api")
    class ApiSection(SectionBase):
        """NAI 中转服务连接配置。"""

        provider: Literal["auto", "ikun", "idlecloud", "7877"] = Field(
            default="ikun",
            description="生图接口提供方。必须手动指定；支持 ikun、idlecloud、7877。legacy 值 auto 仅为兼容旧配置保留，运行时不会再自动识别。",
        )
        base_url: str = Field(
            default="https://your-ikun-gateway.example.com/v1",
            description="接口基础地址。ikun 可填写供应商给你的站点根地址或完整 /v1/chat/completions 地址；idlecloud 可填 https://api.idlecloud.cc（走自有轮询接口）或完整 https://api.idlecloud.cc/api/ai/generate-image（走官方兼容端点）；7877 也可填写站点根地址或完整 /v1/chat/completions 地址。",
        )
        api_key: str = Field(default="", description="接口访问令牌，统一使用 Bearer 认证")
        model: str = Field(default="nai-diffusion-4-5-full", description="要调用的 NAI 模型名称")
        timeout: float = Field(default=120.0, description="HTTP 请求超时时间（秒）")
        poll_interval: float = Field(default=5.0, description="IdleCloud 轮询生成结果的间隔（秒）")
        translate_model: str = Field(
            default="",
            description="统一用于场景描述翻译和衣柜换装选择的模型名称（对应 config/model.toml 中 models 列表里的 name）。留空时回退到 UTILS_SMALL 任务模型。",
        )

        def get_translate_model(self) -> str:
            """获取翻译链路实际应使用的模型名称。"""
            return self.translate_model.strip()

    @config_section("character")
    class CharacterSection(SectionBase):
        """角色默认人设输入与固定追加 tags。"""

        base_tags: str = Field(
            default="1girl",
            description="角色人设输入，支持自然语言或逗号分隔的 booru-style tags。photo 模式下会作为翻译模型的同等级参考输入，由模型按构图与主体选择性吸收；不再负责固定拼接到最终 prompt。drawing 模式默认不自动绑定。",
        )
        fixed_tags: str = Field(
            default="",
            description="固定追加到最终正向 prompt 的 tags（逗号分隔）。用于承接旧 base_tags 中需要稳定保留的一部分效果；仅 photo 模式固定拼接，不参与翻译模型的取景判断。",
        )
        negative_tags: str = Field(
            default="lowres, {bad}, {bad feet}, bad hands, error, fewer, extra, missing, worst quality, jpeg artifacts, bad quality, watermark,displeasing, signature, extra digits, artistic error, username, scan, [abstract], weibo watermark, chibi,blush,chibi inset, doll,stuffed toy,slimy,dripping,sweat,::artist collaboration::,::multiple views::,::thick outline::",
            description="负向 tags，排除不想要的画面",
        )

    @config_section("photo")
    class PhotoSection(SectionBase):
        """photo 模式风格预设参数。"""

        style_tags: str = Field(
            default="masterpiece, best quality, anime coloring, anime illustration, soft shading, clean lineart, detailed face, detailed eyes, natural blush, candid shot, photo composition",
            description="photo 模式附加风格 tags（与 character.fixed_tags 和翻译结果拼接）。角色人设不在这里硬拼接，而是在翻译阶段作为同等级上下文参与理解，整体倾向于照片感的人像或场景图，而不是固定自拍构图",
        )
        width: int = Field(default=832, description="图片宽度（像素，需为 64 的倍数）")
        height: int = Field(default=1216, description="图片高度（像素，需为 64 的倍数）")
        steps: int = Field(default=23, description="采样步数（免费限制 ≤28）")

    @config_section("drawing")
    class DrawingSection(SectionBase):
        """手绘画作风格预设参数。"""

        style_tags: str = Field(
            default="hand-drawn, sketch, rough lineart, visible brush strokes, colored pencil, watercolor texture, paper texture, sketchbook drawing, doodle, illustration",
            description="画作风格 tags（与翻译结果拼接）",
        )
        width: int = Field(default=832, description="图片宽度（像素，需为 64 的倍数）")
        height: int = Field(default=1216, description="图片高度（像素，需为 64 的倍数）")
        steps: int = Field(default=23, description="采样步数（免费限制 ≤28）")

    @config_section("storage")
    class StorageSection(SectionBase):
        """本地缓存配置。"""

        cache_dir: str = Field(default="data/media_cache/images/nai_artist", description="生成图片的本地缓存目录")
        max_cache: int = Field(default=100, description="最多缓存的图片数量，超出时自动删除最旧的文件")

    @config_section("wardrobe")
    class WardrobeSection(SectionBase):
        """衣柜系统配置。"""

        enabled: bool = Field(default=True, description="是否启用衣柜系统与换装 Action")
        auto_daily: bool = Field(default=True, description="是否在 photo 模式首次生图时按日期自动切换每日穿搭")
        data_file: str = Field(default="data/nai_artist/wardrobe.json", description="衣柜数据 JSON 文件路径")

    @config_section("webui")
    class WebUISection(SectionBase):
        """WebUI 对外暴露配置。"""

        mount_on_main_http: bool = Field(
            default=True,
            description=(
                "是否将 WebUI 挂载到主程序 HTTP 服务器。开启后随 main.py 一起启动；"
                "关闭后将不再暴露 WebUI 页面。"
            ),
        )
        route_path: str = Field(
            default="/plugins/nai-artist",
            description="挂载到主程序 HTTP 服务器时使用的子路径。",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    api: ApiSection = Field(default_factory=ApiSection)
    character: CharacterSection = Field(default_factory=CharacterSection)
    photo: PhotoSection = Field(default_factory=PhotoSection)
    drawing: DrawingSection = Field(default_factory=DrawingSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    wardrobe: WardrobeSection = Field(default_factory=WardrobeSection)
    webui: WebUISection = Field(default_factory=WebUISection)
