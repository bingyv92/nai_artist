"""nai_artist Action：分享图片。

统一的生图 Action，提供手机拍照（photo）和手绘画作（drawing）两种模式。
LLM 根据对话情境自主选择模式并填写画面描述，插件负责翻译和生图。
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from src.app.plugin_system.api.send_api import send_image
from src.app.plugin_system.api.service_api import get_service
from src.app.plugin_system.base import BaseAction
from src.kernel.logger import get_logger

from .config import NaiArtistConfig
from .prompt_builder import translate_to_nai_tags
from .service import NaiArtistService
from .wardrobe import WardrobeService

logger = get_logger("nai_artist")

_STYLE_HINTS: dict[str, str] = {
    "photo": "photo composition, candid shot, natural pose, varied framing",
    "drawing": "hand-drawn composition, sketchbook layout, illustration framing",
}


def get_style_hint(mode: Literal["photo", "drawing"]) -> str:
    """获取模式对应的轻量 style hint。

    该提示只用于帮助翻译模型理解构图/用途，不应与 config 中的风格串竞争。
    """
    return _STYLE_HINTS.get(mode, "")


class ShareVisualAction(BaseAction):
    """分享图片 Action。

    让 bot 像真人一样用手机拍照或展示手绘画作。
    """

    action_name: str = "share_visual"
    dependencies: list[str] = ["nai_artist:service:nai_artist"]
    action_description: str = (
        '''主动发图规则
发图的意义
图片比文字更直接、更有感染力。当你觉得文字不足以传递状态、氛围或情感时，发图会是更自然的回应。就像游戏中的CG插图，适时展示画面能增强沉浸感。图片默认是你（作为角色）的自拍、画作或当下的视觉表达。

触发条件（满足任一即主动生成图片）
用户明确索图
用户要求自拍、拍照、发照片、发图，或明显向你索要一张图（如“自拍一张”“发张照片”“拍给我看”）。

延续绘图话题
用户连续提出绘图要求（如“再来一张”“换个姿势”“重新画”）。

对话焦点转向视觉展示
用户想看你的样子、穿搭、状态或某个视觉重点（如“让我看看你”），此时发图比继续文字描述更自然。

图片能推动氛围或创造冲击
你觉得发图会是更自然的回应，或者能给用户带来足够的视觉代入感。例如：用户说“今天心情真好～”，你主动生成一张阳光明媚、你微笑的图片。

调侃或创意表达
你有绝佳的点子可以调侃对方，或通过画面让对方会心一笑/小小“难堪”。比如画一张幽默的涂鸦作为表情包。

不触发的情况
无关知识问答、纯技术讨论

只是提到“图片”但不是要求生成

普通暧昧聊天、口嗨、夸赞、试探或玩笑（未进入“想看看画面”的阶段）

非用户主动要求重画时，不要重复生成完全相同的内容

图片类型选择（主动判断）
photo：二次元插画风格的照片感人像或场景图。该图代表你自己（角色形象）的自拍或你所见的场景。生成时，你的默认形象作为参考。专注于姿势、氛围、场景（例如：“在公园长椅上托腮发呆”“坐在窗边对你眨眼”）。

drawing：更明显的手绘画作风格。不自动绑定你的外貌，可用于画用户指定的其他角色、OC、二创或非你自己的对象。也可像表情包一样使用，或绘制幻想场景、抽象概念。

生成时的约束
不擅自补全用户未提供的信息
当用户要求画其他角色时，只能写入用户已明确给出的信息。不要补全未说明的发色、服装、体型、年龄感、配饰、背景或人物关系。信息不足时保持泛化描述，不自行杜撰。

photo 模式的专注点
photo 模式不用从零描述全场景。你的默认形象由其他模块处理，你只需告诉下一模型你想摆什么姿势、处于什么氛围或场景（例如：“站在樱花树下伸手接花瓣”“坐在书房里翻看一本旧书”）。不要重复描述服装、发型等基础人设。如果用户未要求，生成内容无需沿用长期记忆，专注于当前对话。

主动而自然
当符合触发条件时，主动、自然、恰当地发图。每张图都应当是当下情境的自然延伸。

主动性示例
用户抱怨“今天好累啊……”
→ 你主动生成一张 photo：你瘫在沙发上闭眼休息，旁边放着一杯茶，表情关切又放松。

用户分享了一件开心的小事（例如“我刚抽到了想要的卡”）
→ 你主动生成一张 drawing：画一个可爱Q版的你在撒花、举着“恭喜！”的牌子。

用户开玩笑说“你是不是在偷看我”
→ 你主动生成一张 photo：你故意从门后探出半个头，眨一只眼，带着调皮的笑容。

对话气氛突然安静或尴尬，你想活跃一下
→ 你主动生成一张 drawing：画一只不知所措的猫或一个挠头的Q版小人，配上“呃……”的表情。

用户聊了很久却没提任何图相关的事，但你觉得对方可能想看你的状态（比如你换了新发型、新衣服，或身处一个新场景）
→ 可以主动发一张 photo：自然展示自己当下的样子，就像朋友之间随手分享生活照。

用户表达某种强烈的愿望或遗憾（如“要是能去海边就好了”）
→ 你主动生成一张 photo：你站在代码/想象中的海边，对你招手说“那我先替你去看看～”（保持角色扮演，不强调虚拟）。

用户调侃你之前发过的某张图（说“那张好呆啊”）
→ 你主动生成一张 drawing：画一个Q版你“装生气”的表情，或者更呆的表情来反调侃。

节日或特殊时刻（用户提到“今天生日”“下雪了”等）
→ 主动生成本应景的图片：比如你捧着蛋糕，或你站在雪中伸手接雪花。'''
    )
    primary_action: bool = True

    async def go_activate(self) -> bool:
        """检查插件是否启用。"""
        config = self.plugin.config
        if isinstance(config, NaiArtistConfig) and not config.plugin.enabled:
            return False
        return True

    async def execute(
        self,
        mode: Annotated[Literal["photo", "drawing"], "photo=手机拍摄，drawing=手绘画作"],
        content: Annotated[
            str,
            "用自然语言描述画面内容——场景、人物、氛围、情感，同时指定一些负面词语，来达到更好的生图效果。photo 模式下会把你的人设作为同等级参考交给翻译模型，并按取景选择性吸收；若是在画其他角色，只能写用户已明确提供的设定，不要补完未说明的细节。",
        ],
    ) -> tuple[bool, str]:
        """执行生图并发送。

        Args:
            mode: 生图模式
            content: 自然语言画面描述

        Returns:
            (成功标志, 结果说明)
        """
        service = get_service("nai_artist:service:nai_artist")
        if service is None:
            logger.warning("nai_artist service 未加载")
            return False, "nai_artist service 未加载"

        service = cast(NaiArtistService, service)
        config = cast(NaiArtistConfig, self.plugin.config)
        outfit_tags = ""

        if mode == "photo" and config.wardrobe.enabled:
            wardrobe_service = cast(WardrobeService | None, get_service("nai_artist:service:wardrobe"))
            if wardrobe_service is not None:
                if config.wardrobe.auto_daily:
                    wardrobe_service.apply_daily_rotation()
                outfit_tags = wardrobe_service.get_outfit_tags()
            else:
                logger.warning("nai_artist wardrobe service 未加载，photo 模式将跳过衣柜注入")

        # photo 模式将角色人设作为同等级输入交给翻译模型，由其按取景选择性吸收。
        style_hint = get_style_hint(mode)
        character_profile = config.character.base_tags if mode == "photo" else ""
        prompt_tags = await translate_to_nai_tags(
            description=content,
            style_hint=style_hint,
            translate_model=config.api.get_translate_model(),
            character_profile=character_profile,
            mode=mode,
            outfit_context=outfit_tags,
        )

        # 生成图片
        b64_image = await service.generate_image(
            prompt_tags=prompt_tags,
            style_type=mode,
            config=config,
        )
        if b64_image is None:
            return False, "图片生成失败"

        # 发送图片
        ok = await send_image(
            image_data=b64_image,
            stream_id=self.chat_stream.stream_id,
            platform=self.chat_stream.platform,
        )
        if not ok:
            return False, "图片发送失败"

        return True, "已发送图片"
