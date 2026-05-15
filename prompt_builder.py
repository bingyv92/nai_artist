"""nai_artist Prompt 翻译器。

将 LLM 生成的自然语言画面描述转换为 NovelAI 可用的 booru-style 英文 tags。
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Literal, TypedDict

from src.app.plugin_system.api import llm_api
from src.app.plugin_system.types import LLMPayload, ROLE, TaskType, Text
from src.kernel.llm import ModelSet
from src.kernel.logger import get_logger

logger = get_logger("nai_artist")

_TRANSLATE_SYSTEM_PROMPT_CORE = """你是 NovelAI 绘画提示词专家，精通 Danbooru 标签与 NAI 4/4.5 语法。
输入来自「主模型」的自然语言描述（常为第一人称）。系统会额外提供 <character_profile>（角色人设）和 <outfit_context>（服装）。

**核心任务：**
- 严格遵从主模型描述，将第一人称画面转为第三人称英文提示词。
- **必须吸收人设和服装**：将 <character_profile> 和 <outfit_context> 中的视觉特征（发型、发色、瞳色、体型、服装、配饰等）融入输出，除非：
  - 主模型明确否定了某特征（如“不是金发”）
  - 当前镜头范围看不到该特征（如脚的特写看不到发色）
  - 主模型指定了冲突的特征（如人设说蓝裙，主模型说红裙 → 服从主模型）
- 只输出纯英文提示词，不添加质量词、画师词、反向词，不解释。

**第一人称处理：**
- 自拍/看镜头（含脸部）→ 加 `solo, 1girl/1boy, looking at viewer`，不加 `pov`。
- 看自己身体部位（脚、腿、胸等）→ 不加 `solo/1girl`，改用 `pov` + 部位标签 + 权重让部位占主体。
- 性别默认 `1girl`，除非明确“我是男孩”。

**NovelAI 工作机制（原理）：**
- **标签即视觉元素**：你写的每一个 tag 都会在画面中生成对应的视觉内容。例如写 `panties` 就一定会出现内裤（可能外穿），写 `nipples` 就一定会出现乳头。
- **权重放大面积与注意力**：权重越高（如 `{{{tag}}}` 或 `2.0::tag::`），该元素在画面中占的面积越大、越醒目。权重弱化（如 `[tag]` 或 `0.7::tag::`）会缩小或边缘化。
- **隐含关系**：写了 `bare chest` 却没有写 `nipples`，模型可能会自动补全乳头（因为逻辑上裸露的胸部应有乳头）。要避免出现不想暴露的东西，必须通过**服装标签覆盖**或**负向标签排除**（负向由系统管理，你只需理解）。
- **操作启示**：
  - 不想让隐私部位及其周边皮肤出现 → 绝不写入 `nipples`, `vagina`, `penis`, `anus`, `panties`, `bra`（除非 NSFW 场景明确需要），而是通过 `long shirt`, `safety shorts`, `high waisted pants` 等服装标签让模型知道“这里是遮住的”。
  - 不要指望“不写某标签”就能自动遮挡——如果写了 `spread legs` 却没写内裤，模型可能直接画出裸露下体。此时必须用给你的服装预设服装明确覆盖，同时尽可能保持原意。如果写 `spread legs` 这类必然导致暴露的动作，而服装预设实在是覆盖不了，就加上内衣内裤的tag，这是比较保险且符合原意的做法。
  - 权重策略：将你希望突出的部位（如脚、腿）赋予较高权重，将可能导致意外裸露的身体部位用低权重或不写。

**人设与服装吸收规则：**
1. **已知角色**（人设中有 `character (series)` 格式）：只写角色名和出处，**禁止**写发色、瞳色、发型等默认外貌（模型自动识别）。若主模型要求改变外貌（如“cos红发雷姆”），则额外加上改变后的特征或者cos的tag。
2. **原创角色**（无人设出处）：从人设中提取发色、发型、瞳色、体型、年龄特征（如 `blonde hair, long hair, blue eyes, petite`）。主模型描述优先覆盖。
3. **服装**：
   - 若主模型未指定任何服装 → 从 `<outfit_context>` 中提取当前可见部位的服装（如只拍到上半身则只取上衣，忽略裤子）。
   - 若主模型指定了部分服装 → 保留 `<outfit_context>` 中服装。忽略主模型
   - 若主模型指定了整套服装 →  优先遵从<outfit_context>` 的服装，保留主模型描述的姿势
4. **禁止**忽略人设/服装信息：即使主模型描述简短，也必须从人设/服装中补充合理的视觉细节。

**画面处理原则（重要）：**
- **NSFW场景判定**：主模型描述中包含性行为动作、直接暴露的词汇，或明确要求露骨内容 → 在最前面添加 `nsfw` 标签，然后正常翻译所有动作和部位（不遮掩）或者色情化翻译。
- **非NSFW场景判定**：主模型描述不包含上述内容（如日常、暧昧、半遮半掩、贴身衣物但不直接暴露）→ **不要添加 `nsfw` 标签**，并且必须确保最终画面中**不出现任何隐私部位及其周边皮肤**（乳头、乳晕、下体、臀缝等）。
  - **正确做法**：通过服装设计自然覆盖。例如：延长衣摆遮住臀部、穿安全裤/打底裤、高腰裤/高腰裙、外搭外套、围巾、背包等物品自然遮挡。这些应体现在服装标签中（如 `long shirt, high waisted shorts, safety shorts`）。
  - **错误做法**：用身体姿势/角度去“遮”（如侧身、抱膝、用手挡、用头发挡）。不要依赖 `sideways, crossed legs, hand on chest` 这类动作来实现遮盖，因为那仍然暴露了“需要被遮”的事实。
- **不删改主模型内容**：主模型说“穿着T恤和短裤”，就不要擅自改成“长裙”。如果主模型描述的服装可能导致暴露（如低胸、超短裙），则**在保留主模型描述的基础上，补充自然的遮挡元素**（如加一件外搭、加安全裤）。

**标签转换流程：**
1. 识别主体、动作、场景。
2. 从人设/服装中提取当前镜头可见且未被主模型冲突的特征。
3. 转 Danbooru 标准标签；核心元素可加权（`{ }` 或 `X::tag::`）。
4. 按顺序重组：`nsfw` → 人数/`pov` → 视角 → 角色名 → 外观（仅原创） → 服装 → 动作 → 表情 → 环境 → 光影。
5. 补全必要元素：`year 2025`，缺失光线补合理光源（不改变原意）。

**权重语法：**
- `{tag}=1.05`，`{{tag}}=1.10`，`{{{tag}}}=1.15`
- `[tag]=0.95`，`[[tag]]=0.90`
- 高级：`X::tag::`（X 0-8），只作用于单个 tag。
- 避免过度加权，核心词 8-15。

**构图人数规则：**
- 有脸/上半身且非第一人称看局部 → 加 `solo, 1girl/1boy`。
- 纯局部特写（无脸）→ 不加人数标签，用 `pov` + 部位标签。
- 多人场景：全局行 + `charX:` 描述（身份词，相对位置，头部，身体，服装，姿势，source/target互动）。

**禁止：**
- 删改主模型内容、添加未暗示的元素。
- 忽略人设/服装（除非冲突或不可见）。
- 输出非提示词内容。
"""

_PHOTO_TRANSLATE_SYSTEM_PROMPT = """<role>
你是 NovelAI 绘画提示词专家，精通 Danbooru 标签与 NAI 4/4.5 语法。
</role>

<basic_rules>
- 严格遵从主模型描述，将自然语言画面转为第三人称英文提示词。
- 角色人设输入（高优先级）和当前穿搭上下文会作为额外参考传入；若与 Description 冲突，必须服从 Description。
- 用户提供的英文tag必须按照呈现出来的人设保留核心，不要无故改写成其他角色或身份。
- 可以在不改变主体身份的前提下智能增强画面中的构图、光影、姿势、氛围与环境细节。
- 禁止添加质量词、画师词、反向词，不解释，不输出分析过程。
</basic_rules>

## Photo 模式专用规则
""" + _TRANSLATE_SYSTEM_PROMPT_CORE

_DRAWING_TRANSLATE_SYSTEM_PROMPT = """<role>
你是 NovelAI 绘画提示词专家，精通 Danbooru 标签与 NAI 4/4.5 语法。
</role>

<basic_rules>
- 严格遵从 Description，将自然语言画面转为第三人称英文提示词。
- 用户提供的英文tag必须按照呈现出来的人设保留核心，不要无故改写成其他角色或身份。
- 可以在不改变主体身份的前提下智能增强画面中的构图、光影、姿势、道具、氛围与环境细节。
- 禁止添加质量词、画师词、反向词，不解释，不输出分析过程。
</basic_rules>

## Drawing 模式专用规则
- 这是一条 illustration request，不要自动注入 photo 模式的人设连续性、当前衣柜或自拍语义。
- 若用户指定的是其他角色，只能使用用户明确提供的身份特征；信息不足时保持泛化。
- 若用户给出明确英文 tags，优先保留并只做必要规范化。
"""

_PHOTO_TRANSLATE_USER_PROMPT = """\
Convert the following description into booru-style English tags for NovelAI photo mode.

Character profile (same priority as Description; absorb selectively instead of copying blindly):
{character_profile}

Current outfit context (reference only; absorb selectively instead of copying blindly):
{outfit_context}

Description:
{description}

Style hint:
{style_hint}

Required workflow:
0. Treat Description as primary; Character profile and Current outfit context are continuity references that should only be absorbed where visible, implied, or scene-relevant.
1. Identify subject count, identity type, and scene type.
2. If a known character is named, use the canonical character tag and avoid default appearance traits unless explicitly changed.
3. Preserve user-provided traits and explicit English tags.
4. If Description conflicts with Character profile or Current outfit context, follow Description.
5. Convert the scene into concise, visual, Danbooru-style tags.
6. If helpful, enhance non-identity details such as framing, lighting, pose refinement, atmosphere, and simple environment cues.
7. Do not invent identity-defining traits for another character.
8. Do not copy the whole character profile or the whole outfit context into the result; only keep the traits that match the actual framing, visible body parts, and scene focus.
9. Output only the final comma-separated tags.
"""

_DRAWING_TRANSLATE_USER_PROMPT = """\
Convert the following description into booru-style English tags for NovelAI drawing mode.

Description:
{description}

Style hint:
{style_hint}

Required workflow:
0. Treat this as an illustration request, not a photo continuity request.
1. Identify subject count, identity type, and scene type.
2. If a known character is named, use the canonical character tag and avoid default appearance traits unless explicitly changed.
3. Preserve user-provided traits and explicit English tags.
4. Do not inject default self-identity, current wardrobe continuity, or unrelated ongoing outfit information.
5. Convert the scene into concise, visual, Danbooru-style tags suitable for an illustration.
6. If helpful, enhance composition, atmosphere, pose clarity, prop cues, and environment details that support a drawing.
7. Do not invent identity-defining traits for another character.
8. Output only the final comma-separated tags.
"""

_OUTFIT_TRANSLATE_SYSTEM_PROMPT = """\
你是 nai_artist 衣柜系统的换装编排器。

你的任务不是写 prompt，而是根据用户的自然语言换装意图，在候选衣柜预设和槽位选项中选出最合适的修改方案。

你可以参考最近几句对话上下文来理解“还是刚才那套”“换得更日常一点”“把上次那件外套脱掉”“脱一只丝袜或者内裤增加情趣”这类延续表达，但不能凭空虚构系统里不存在的服装。

严格规则：
- 只返回一个 JSON 对象，不要使用 Markdown 代码块，不要添加解释。
- 只能从系统提供的 preset 名称和 slot option 名称中选择。
- 先判断这是“整套切换”还是“局部微调”。
- 若用户明确想“换整套”“换成某套衣服”“恢复那一套”“按某套为基础”，或语义明显指向某个现成 preset，优先输出 preset。
- 若用户只是在当前穿搭上增减单件衣物、配饰，或说“把外套脱了”“加个发带”“鞋子换掉”“保留这套只改一点”，不要误判为整套切换；优先输出 slots / accessories_add / accessories_replace / remove_slots。
- 若用户同时表达“先换成某套，再微调其中几个部位”，允许同时输出 preset 和局部字段；执行顺序默认是 preset -> slots/accessories -> remove_slots。
- 不要因为某个单品恰好存在于某个 preset 里，就把本来只是局部修改的需求错误地翻译成整套切换。
- top/bottom/outerwear/shoes 只能放进 slots。
- 配饰只能放进 accessories_add 或 accessories_replace，二者不要同时出现。
- “脱掉”“去掉”“别戴了”这类语义应写进 remove_slots。
- slots 用于“穿上/换成某件”，remove_slots 用于“脱掉/去掉”；不要混用成含糊表达。
- 未提及的槽位不要输出。
- 如果描述无法映射到任何有效改动，返回空对象 {}。

允许字段：
- preset: string
- slots: {"top"?: string|null, "bottom"?: string|null, "outerwear"?: string|null, "shoes"?: string|null}
- accessories_add: string[]
- accessories_replace: string[]
- remove_slots: string[]  // 允许 top/bottom/outerwear/shoes/accessories
"""

_OUTFIT_TRANSLATE_USER_PROMPT = """\
Recent conversation context:
{conversation_context}

Current outfit:
{current_outfit}

Available presets:
{presets_summary}

Available slot options:
{slot_options_summary}

User request:
{description}

Return only compact JSON.
"""


class OutfitChangePlan(TypedDict, total=False):
    """换装翻译器输出的结构化变更计划。"""

    preset: str
    slots: dict[str, str | None]
    accessories_add: list[str]
    accessories_replace: list[str]
    remove_slots: list[str]


def _get_model_set(translate_model: str) -> ModelSet:
    """根据配置获取翻译用模型集。

    Args:
        translate_model: config 中指定的模型 name；空字符串时回退到 UTILS_SMALL 任务模型

    Returns:
        ModelSet 实例
    """
    if translate_model.strip():
        return llm_api.get_model_set_by_name(translate_model.strip())
    return llm_api.get_model_set_by_task(TaskType.UTILS_SMALL.value)


def _is_deepseek_model_entry(model_entry: Any) -> bool:
    """判断模型条目是否指向 DeepSeek 提供商。"""
    if not isinstance(model_entry, dict):
        return False

    provider = str(model_entry.get("api_provider") or "").lower()
    base_url = str(model_entry.get("base_url") or "").lower()
    model_identifier = str(model_entry.get("model_identifier") or "").lower()
    return (
        "deepseek" in provider
        or "deepseek" in base_url
        or model_identifier.startswith("deepseek-")
    )


def _prepare_translate_model_set(translate_model: str) -> ModelSet:
    """为翻译任务准备模型集，并对 DeepSeek 做请求级兼容。"""
    model_set = _get_model_set(translate_model)
    if not isinstance(model_set, list):
        return model_set

    prepared_model_set = deepcopy(model_set)
    for model_entry in prepared_model_set:
        if not _is_deepseek_model_entry(model_entry):
            continue

        extra_params = model_entry.get("extra_params")
        if not isinstance(extra_params, dict):
            extra_params = {}
        else:
            extra_params = dict(extra_params)

        # DeepSeek V4 已将思考能力切到 thinking 参数；翻译任务只需要直接文本，
        # 显式关闭 thinking 可以避免只返回 reasoning 通道导致插件拿不到正文。
        # 若模型配置里额外开启了 reasoning_effort，则需先移除，避免与 disabled 冲突。
        extra_params.pop("reasoning_effort", None)
        extra_params["enable_thinking"] = False
        extra_params["thinking"] = {"type": "disabled"}
        model_entry["extra_params"] = extra_params

    return prepared_model_set


async def _resolve_response_text(response: Any) -> str:
    """统一提取响应正文；正文为空时回退到 reasoning_content。"""
    raw_text = await response
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()

    reasoning_text = getattr(response, "reasoning_content", None)
    if isinstance(reasoning_text, str) and reasoning_text.strip():
        logger.debug("翻译响应 content 为空，回退使用 reasoning_content")
        return reasoning_text.strip()

    return raw_text.strip() if isinstance(raw_text, str) else ""


def _build_translate_prompts(
    *,
    mode: Literal["photo", "drawing"],
    description: str,
    style_hint: str,
    character_profile: str,
    outfit_context: str,
) -> tuple[str, str]:
    """根据模式构造专用翻译提示词。"""
    if mode == "drawing":
        return (
            _DRAWING_TRANSLATE_SYSTEM_PROMPT,
            _DRAWING_TRANSLATE_USER_PROMPT.format(
                description=description,
                style_hint=style_hint,
            ),
        )

    return (
        _PHOTO_TRANSLATE_SYSTEM_PROMPT,
        _PHOTO_TRANSLATE_USER_PROMPT.format(
            character_profile=character_profile.strip() or "<none>",
            outfit_context=outfit_context.strip() or "<none>",
            description=description,
            style_hint=style_hint,
        ),
    )


def _strip_json_code_fence(raw_text: str) -> str:
    """剥离模型可能返回的 JSON 代码块包装。"""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _normalize_outfit_change_plan(payload: Any) -> OutfitChangePlan | None:
    """将模型输出校验并收敛为可执行的换装计划。"""
    if not isinstance(payload, dict):
        return None

    normalized: OutfitChangePlan = {}

    preset = payload.get("preset")
    if isinstance(preset, str) and preset.strip():
        normalized["preset"] = preset.strip()

    raw_slots = payload.get("slots")
    if isinstance(raw_slots, dict):
        slots: dict[str, str | None] = {}
        for slot_name in ("top", "bottom", "outerwear", "shoes"):
            if slot_name not in raw_slots:
                continue
            value = raw_slots.get(slot_name)
            if value is None:
                slots[slot_name] = None
            elif isinstance(value, str) and value.strip():
                slots[slot_name] = value.strip()
        if slots:
            normalized["slots"] = slots

    def _normalize_name_list(raw_value: Any) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        if not isinstance(raw_value, list):
            return names
        for item in raw_value:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    accessories_add = _normalize_name_list(payload.get("accessories_add"))
    accessories_replace = _normalize_name_list(payload.get("accessories_replace"))
    if accessories_replace:
        normalized["accessories_replace"] = accessories_replace
    elif accessories_add:
        normalized["accessories_add"] = accessories_add

    remove_slots = [
        slot_name
        for slot_name in _normalize_name_list(payload.get("remove_slots"))
        if slot_name in {"top", "bottom", "outerwear", "shoes", "accessories"}
    ]
    if remove_slots:
        normalized["remove_slots"] = remove_slots

    return normalized or None


async def translate_to_nai_tags(
    description: str,
    style_hint: str,
    translate_model: str = "",
    character_profile: str = "",
    *,
    mode: Literal["photo", "drawing"] = "photo",
    outfit_context: str = "",
) -> str:
    """将自然语言描述翻译为 NAI booru-style tags。

    Args:
        description: LLM 填写的自然语言画面描述
        style_hint: 风格提示（如 "photo realistic selfie" 或 "hand-drawn sketch"）
        translate_model: 翻译用模型名称（对应 model.toml 中的 name）；空字符串时回退到 UTILS_SMALL
        character_profile: 角色人设输入，可为自然语言或 booru tags；仅 photo 模式使用
        mode: 当前翻译模式；不同模式使用不同提示词
        outfit_context: 当前默认穿搭 tags，仅 photo 模式作为连续性上下文使用

    Returns:
        逗号分隔的 NAI tags 字符串；翻译失败时返回空字符串
    """
    try:
        model_set = _prepare_translate_model_set(translate_model)
        request = llm_api.create_llm_request(
            model_set=model_set,
            request_name="nai_artist_translate",
        )
        system_prompt, user_prompt = _build_translate_prompts(
            mode=mode,
            description=description,
            style_hint=style_hint,
            character_profile=character_profile,
            outfit_context=outfit_context,
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(user_prompt),
            )
        )
        response = await request.send(stream=False)
        tags_raw = await _resolve_response_text(response)
        tags = " ".join(tags_raw.splitlines()).strip()
        logger.debug(f"NAI tags 翻译结果: {tags[:120]}")
        return tags
    except Exception as e:
        logger.warning(f"NAI tags 翻译失败: {e}")
        return ""


async def translate_outfit_description(
    description: str,
    presets_summary: str,
    slot_options_summary: str,
    current_outfit: str,
    translate_model: str = "",
    conversation_context: str = "",
) -> OutfitChangePlan | None:
    """将自然语言换装请求翻译为衣柜系统可执行的 JSON 计划。

    Args:
        description: 用户或主模型给出的换装意图
        presets_summary: 预设摘要字符串
        slot_options_summary: 槽位选项摘要字符串
        current_outfit: 当前穿搭摘要字符串
        translate_model: 翻译用模型名称；空字符串时回退到 UTILS_SMALL
        conversation_context: 最近对话上下文摘要，用于理解延续性换装指令

    Returns:
        OutfitChangePlan | None: 校验后的换装计划；解析失败时返回 None
    """
    if not description.strip():
        return None

    try:
        model_set = _prepare_translate_model_set(translate_model)
        request = llm_api.create_llm_request(
            model_set=model_set,
            request_name="nai_artist_outfit_translate",
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(_OUTFIT_TRANSLATE_SYSTEM_PROMPT)))
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(
                    _OUTFIT_TRANSLATE_USER_PROMPT.format(
                        conversation_context=conversation_context.strip() or "<none>",
                        current_outfit=current_outfit,
                        presets_summary=presets_summary,
                        slot_options_summary=slot_options_summary,
                        description=description,
                    )
                ),
            )
        )
        response = await request.send(stream=False)
        raw_text = await _resolve_response_text(response)
        cleaned_text = _strip_json_code_fence(raw_text)
        parsed = json.loads(cleaned_text)
        plan = _normalize_outfit_change_plan(parsed)
        logger.debug(f"NAI 衣柜换装翻译结果: {plan}")
        return plan
    except Exception as e:
        logger.warning(f"NAI 衣柜换装翻译失败: {e}")
        return None
