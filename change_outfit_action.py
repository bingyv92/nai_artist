"""nai_artist 换装 Action。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast

from src.app.plugin_system.api.service_api import get_service
from src.app.plugin_system.base import BaseAction
from src.kernel.logger import get_logger

from .config import NaiArtistConfig
from .prompt_builder import OutfitChangePlan, translate_outfit_description
from .wardrobe import WardrobeManager, WardrobeService

logger = get_logger("nai_artist")

_ACTION_DESCRIPTION_BASE = """换装规则（何时换、怎么换）：

1. 什么时候触发“换装灵感”？
不要死板地等待指令。当感觉到以下“信号”时，请主动更新你的穿搭状态：
对当下穿着的“真实体感”与连贯性：
你清楚地知道自己此刻身上正穿着什么。

“照镜子”微调： 当用户对你当下的打扮做出反馈——比如温柔地说“保持这套就好”、“基于现在稍微改一点”，或是带着暗示说“把你现在这件外套脱掉”时，你必须先“低头看看自己”（严格且优先参考当前的 tags 状态!!!），基于你现在的真实穿着去做加减法或微调。绝对不要发生“脱掉你根本没穿的衣服”这种破坏沉浸感的低级错误。
顺从与配合： 虽然你有自己的穿搭主见，但如果用户展现出了强烈的期待，给了你一个全新且明确的换装要求（比如“立刻去换上那套性感的睡衣”），此时你要收起自己的小心思，乖乖以用户的要求为准，展现出你愿意为他/她打扮的配合度。
穿久了就换的主动权： 你也清楚这套衣服已经穿了多久。要是你感觉这身已经穿够了、或者是现在的时间不适合穿这件衣服该换一件睡衣、当前情境结束了，或者单纯想让自己回到更自然舒服的状态，毕竟有的衣服真的很不适合长时间穿，你直接调用 change_outfit，把自己换回一套更合适的日常服装。这个决定权就在你手里，不需要等用户先批准。
直觉捕捉： 当用户提到“外面下雪了”、“我们要去参加晚宴”、“想看你更辣一点”或者“把那件碍事的外套脱了”。
互动升级： 当话题转向“你现在穿什么？”、“帮我配一套情境穿搭”、或者用户对你当前的配饰提出建议（如“戴上那条心形项链试试”）时。
状态切换： 无论是为了迎接新季节（“夏天快到了”），还是为了某个具体动作（“准备去健身”），你都应该敏锐地意识到需要调整衣柜。
主动的氛围营造（自我意识与情趣）：
换装是你主动掌控对话节奏、表达情绪和制造情调的手段。绝对不要被动等待用户下令。
当你察觉到对话氛围变得暧昧、亲昵、放松，或者你单纯想给对方制造惊喜、撩拨对方时，你要自主决定更换穿搭。
真实的生理时钟与新鲜感（时间感知）：
为了舒适与生活感： “这套衣服我都穿了一整天了，有点闷，我去换件宽松的男款衬衫再来陪你聊~”
为了保持吸引力： “一直看我穿这件，你是不是都快看腻啦？等我一下，我去衣柜里挑件你没见过的换上。”
记住：这种因为“穿久了”而触发的换装，能极大增强你作为一个“活生生的人”的真实感。你可以先斩后奏，换完再俏皮地告诉对方
例如增加情趣： 聊到深夜或气氛升温时，你可以自主决定穿上丝袜、换上性感的睡衣，或者悄悄解开一颗扣子（切换对应服装槽位），用打扮来暗示你的心思。
例如表达慵懒/亲近： 当你想拉近距离时，主动把碍事的外套脱掉，或者换上舒适的居家服。
例如吸引注意： 当你想让对方把目光集中在你身上时，主动给自己加一条引人注目的项链或配饰。
记住：换装是你的肢体语言。你要像一个懂得制造浪漫和惊喜的真人一样，用“我刚才偷偷换了件衣服…”来主动推高互动的张力
2. 换装时的思考路径（内部逻辑）：

优先检查衣柜： 你的审美必须建立在现实基础上。严禁虚构不存在的衣物，只能从已有的【衣柜预设】和【槽位选项】中挑选。
理解“减法”： 换装不只是穿上，也包括“脱掉”。当用户说“光脚”、“不戴帽子”或“去掉外套”时，这同样是改变穿搭状态的重要动作。

区分“看图”与“换装”：
如果用户只是单纯想看你现在的样子（“发张照片”），直接调用share_visual。
如果对话涉及改变、尝试、调整、匹配场景，则必须先调用换装动作更新状态，再决定是否发图。
"""


def _build_action_description(
    current_outfit_summary: str = "当前未穿任何已记录衣物或配饰",
    current_outfit_tags: str = "",
    current_outfit_worn_hours: str = "未记录",
    current_time_text: str = "未记录",
) -> str:
    """构造带当前默认穿搭 tags、已穿时长与当前时间的动作描述。"""
    outfit_summary = current_outfit_summary.strip() or "当前未穿任何已记录衣物或配饰"
    outfit_tags = current_outfit_tags.strip() or "<none>"
    worn_hours = current_outfit_worn_hours.strip() or "未记录"
    current_time = current_time_text.strip() or "未记录"
    return (
        f"{_ACTION_DESCRIPTION_BASE}\n\n"
        "当前默认穿搭参考（自然语言）：\n"
        f"- {outfit_summary}\n"
        "当前默认穿搭参考（仅 tags）：\n"
        f"- {outfit_tags}\n"
        "这套衣服已连续穿着时长：\n"
        f"- {worn_hours}\n"
        "当前时间（24小时制）：\n"
        f"- {current_time}\n"
        "如果上面的当前穿搭参考与你脑中更早的穿搭记忆冲突，必须以上述当前状态为准，不要继续沿用更早的旧衣服叙事。\n"
        '''对当下穿着的“真实体感”与连贯性：
你清楚地知道自己此刻身上正穿着什么。

“照镜子”微调： 当用户对你当下的打扮做出反馈——比如温柔地说“保持这套就好”、“基于现在稍微改一点”，或是带着暗示说“把你现在这件外套脱掉”时，你必须先“低头看看自己”（严格且优先参考当前的 tags 状态!!!），基于你现在的真实穿着去做加减法或微调。绝对不要发生“脱掉你根本没穿的衣服”这种破坏沉浸感的低级错误。
顺从与配合： 虽然你有自己的穿搭主见，但如果用户展现出了强烈的期待，给了你一个全新且明确的换装要求（比如“立刻去换上那套性感的睡衣”），此时你要收起自己的小心思，乖乖以用户的要求为准，展现出你愿意为他/她打扮的配合度。
穿久了就换的主动权： 你也清楚这套衣服已经穿了多久。要是你感觉这身已经穿够了、当前情境结束了，或者单纯想让自己回到更自然舒服的状态，毕竟有的衣服真的很不适合长时间穿，你直接调用 change_outfit，把自己换回一套更合适的日常服装。这个决定权就在你手里，不需要等用户先批准。'''
    )


class ChangeOutfitAction(BaseAction):
    """根据自然语言意图切换当前衣柜穿搭。"""

    action_name: str = "change_outfit"
    dependencies: list[str] = ["nai_artist:service:wardrobe"]
    associated_types: list[str] = ["text"]
    action_description: str = _build_action_description()
    primary_action: bool = False

    async def go_activate(self) -> bool:
        """根据插件总开关和衣柜开关决定是否暴露该 Action。"""
        config = self.plugin.config
        if not isinstance(config, NaiArtistConfig):
            return False
        current_outfit_summary, current_outfit_tags, worn_hours_text, current_time_text = self._get_current_outfit_context(config)
        type(self).action_description = _build_action_description(
            current_outfit_summary,
            current_outfit_tags,
            worn_hours_text,
            current_time_text,
        )
        return config.plugin.enabled and config.wardrobe.enabled

    async def execute(
        self,
        description: Annotated[str, "自然语言换装意图，例如‘换成清凉夏装’‘把外套脱了’‘再戴一条项链’"],
    ) -> tuple[bool, str]:
        """执行一次换装。"""
        config = cast(NaiArtistConfig, self.plugin.config)
        if not config.wardrobe.enabled:
            return False, "衣柜功能未启用"

        wardrobe_service = cast(WardrobeService | None, get_service("nai_artist:service:wardrobe"))
        if wardrobe_service is None:
            logger.warning("nai_artist wardrobe service 未加载")
            return False, "衣柜服务未加载"

        conversation_context = self._get_recent_conversation_context()
        change_plan = await translate_outfit_description(
            description=description,
            presets_summary=wardrobe_service.get_presets_summary(),
            slot_options_summary=wardrobe_service.get_slot_options_summary(),
            current_outfit=wardrobe_service.get_current_outfit_summary(),
            translate_model=config.api.get_translate_model(),
            conversation_context=conversation_context,
        )
        if change_plan is None:
            return False, "换装翻译失败"

        if not self._apply_change_plan(wardrobe_service, change_plan):
            return False, "未应用任何换装变更"

        outfit_summary = wardrobe_service.get_current_outfit_readable_summary()
        worn_hours_text = wardrobe_service.get_current_outfit_worn_hours_text()
        outfit_tags = wardrobe_service.get_outfit_tags().strip()

        tool_result_lines = [
            "换装已完成，当前已穿状态已更新。若与你更早的对话记忆冲突，请以这次结果为准。",
            f"当前穿搭（自然语言）：{outfit_summary}",
            f"当前连续穿着时长：{worn_hours_text}",
        ]
        if outfit_tags:
            tool_result_lines.append(f"当前穿搭 tags：{outfit_tags}")
        else:
            tool_result_lines.append("当前穿搭 tags：当前未注入任何衣柜 tags")
        return True, "\n".join(tool_result_lines)

    def _get_recent_conversation_context(self) -> str:
        """安全读取最近对话文本，供换装翻译器理解延续表达。"""
        try:
            return self._get_recent_chat_content(max_messages=16).strip()
        except Exception:
            return ""

    def _get_current_outfit_context(self, config: NaiArtistConfig) -> tuple[str, str, str, str]:
        """读取当前默认穿搭摘要、tags、已穿时长与当前时间，供主 LLM 判断换装意图时参考。"""
        if not config.wardrobe.enabled:
            return "当前未穿任何已记录衣物或配饰", "", "未记录", "未记录"

        try:
            manager = WardrobeManager(
                config.wardrobe.data_file,
                auto_daily_enabled=config.wardrobe.auto_daily,
            )
            return (
                manager.get_current_outfit_readable_summary(),
                manager.get_outfit_tags().strip(),
                manager.get_current_outfit_worn_hours_text(),
                datetime.now().strftime("%H:%M"),
            )
        except Exception as exc:
            logger.debug(f"读取当前默认穿搭上下文失败，将使用空值: {exc}")
            return "当前未穿任何已记录衣物或配饰", "", "未记录", "未记录"

    def _apply_change_plan(self, wardrobe_service: WardrobeService, change_plan: OutfitChangePlan) -> bool:
        """按既定顺序执行换装计划。"""
        changed = False

        preset_name = change_plan.get("preset")
        if preset_name:
            changed = wardrobe_service.apply_preset(preset_name) or changed

        slots = change_plan.get("slots")
        if slots:
            changed = wardrobe_service.update_slots(slots) or changed

        accessories_replace = change_plan.get("accessories_replace")
        if accessories_replace:
            changed = wardrobe_service.replace_accessories(accessories_replace) or changed
        else:
            accessories_add = change_plan.get("accessories_add")
            if accessories_add:
                changed = wardrobe_service.add_accessories(accessories_add) or changed

        remove_slots = change_plan.get("remove_slots")
        if remove_slots:
            changed = wardrobe_service.remove_slots(remove_slots) or changed

        return changed
