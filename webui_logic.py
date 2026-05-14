"""nai_artist WebUI 的后端编排逻辑。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, TypedDict, cast

from src.core.config import init_core_config, init_model_config
from src.kernel.config.core import _render_toml_with_signature

from .action import get_style_hint
from .config import NaiArtistConfig
from .prompt_builder import translate_to_nai_tags
from .service import NaiArtistService, build_final_prompt
from .wardrobe import DeletionImpact, SlotName, WardrobeData, WardrobeManager


DEFAULT_PLUGIN_CONFIG_PATH = Path("config/plugins/nai_artist/config.toml")
DEFAULT_CORE_CONFIG_PATH = Path("config/core.toml")
DEFAULT_MODEL_CONFIG_PATH = Path("config/model.toml")


class ConfigOverrideData(TypedDict, total=False):
    """WebUI 允许编辑的配置覆盖字段。"""

    base_tags: str
    fixed_tags: str
    negative_tags: str
    photo_style_tags: str
    photo_style_tags_append: str
    photo_width: int
    photo_height: int
    photo_steps: int
    drawing_style_tags: str
    drawing_style_tags_append: str
    drawing_width: int
    drawing_height: int
    drawing_steps: int
    wardrobe_enabled: bool
    wardrobe_auto_daily: bool
    wardrobe_data_file: str


def _merge_comma_text(*parts: str) -> str:
    """按顺序拼接逗号分隔文本，忽略空项并去重。"""
    merged: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = part.replace("，", ",").replace("、", ",")
        for raw_tag in normalized.split(","):
            tag = raw_tag.strip()
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(tag)
    return ", ".join(merged)


def initialize_webui_runtime(
    core_config_path: str | Path = DEFAULT_CORE_CONFIG_PATH,
    model_config_path: str | Path = DEFAULT_MODEL_CONFIG_PATH,
) -> None:
    """初始化 WebUI 所需的最小配置运行时。"""
    init_core_config(str(core_config_path))
    init_model_config(str(model_config_path))


def load_nai_artist_config(config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH) -> NaiArtistConfig:
    """加载当前 nai_artist 配置。"""
    return NaiArtistConfig.load(Path(config_path), auto_update=True)


def apply_config_overrides(config: NaiArtistConfig, overrides: ConfigOverrideData | None) -> NaiArtistConfig:
    """将白名单字段覆盖到配置实例。"""
    if not overrides:
        return config

    if "base_tags" in overrides:
        config.character.base_tags = overrides["base_tags"]
    if "fixed_tags" in overrides:
        config.character.fixed_tags = overrides["fixed_tags"]
    if "negative_tags" in overrides:
        config.character.negative_tags = overrides["negative_tags"]

    if "photo_style_tags" in overrides:
        config.photo.style_tags = overrides["photo_style_tags"]
    if "photo_style_tags_append" in overrides:
        config.photo.style_tags = _merge_comma_text(
            config.photo.style_tags,
            overrides["photo_style_tags_append"],
        )
    if "photo_width" in overrides:
        config.photo.width = int(overrides["photo_width"])
    if "photo_height" in overrides:
        config.photo.height = int(overrides["photo_height"])
    if "photo_steps" in overrides:
        config.photo.steps = int(overrides["photo_steps"])

    if "drawing_style_tags" in overrides:
        config.drawing.style_tags = overrides["drawing_style_tags"]
    if "drawing_style_tags_append" in overrides:
        config.drawing.style_tags = _merge_comma_text(
            config.drawing.style_tags,
            overrides["drawing_style_tags_append"],
        )
    if "drawing_width" in overrides:
        config.drawing.width = int(overrides["drawing_width"])
    if "drawing_height" in overrides:
        config.drawing.height = int(overrides["drawing_height"])
    if "drawing_steps" in overrides:
        config.drawing.steps = int(overrides["drawing_steps"])

    if "wardrobe_enabled" in overrides:
        config.wardrobe.enabled = bool(overrides["wardrobe_enabled"])
    if "wardrobe_auto_daily" in overrides:
        config.wardrobe.auto_daily = bool(overrides["wardrobe_auto_daily"])
    if "wardrobe_data_file" in overrides:
        config.wardrobe.data_file = overrides["wardrobe_data_file"]

    return config


def config_to_editor_payload(config: NaiArtistConfig, config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH) -> dict[str, Any]:
    """将配置实例转换为前端编辑所需字段。"""
    return {
        "configPath": str(Path(config_path)),
        "plugin": {"enabled": config.plugin.enabled},
        "api": {
            "model": config.api.model,
            "translateModel": config.api.get_translate_model(),
        },
        "character": {
            "baseTags": config.character.base_tags,
            "fixedTags": config.character.fixed_tags,
            "negativeTags": config.character.negative_tags,
        },
        "photo": {
            "styleTags": config.photo.style_tags,
            "width": config.photo.width,
            "height": config.photo.height,
            "steps": config.photo.steps,
        },
        "drawing": {
            "styleTags": config.drawing.style_tags,
            "width": config.drawing.width,
            "height": config.drawing.height,
            "steps": config.drawing.steps,
        },
        "wardrobe": {
            "enabled": config.wardrobe.enabled,
            "autoDaily": config.wardrobe.auto_daily,
            "dataFile": config.wardrobe.data_file,
        },
    }


def save_nai_artist_config(
    overrides: ConfigOverrideData,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> NaiArtistConfig:
    """按白名单字段保存配置到 TOML。"""
    path = Path(config_path)
    config = load_nai_artist_config(path)
    apply_config_overrides(config, overrides)
    rendered = _render_toml_with_signature(NaiArtistConfig, config.model_dump(mode="python"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return config


def _make_service(config: NaiArtistConfig) -> NaiArtistService:
    plugin = SimpleNamespace(config=config)
    return NaiArtistService(plugin=cast(Any, plugin))


def _get_wardrobe_manager(config: NaiArtistConfig) -> WardrobeManager:
    """基于当前配置创建衣柜管理器。"""
    return WardrobeManager(
        config.wardrobe.data_file,
        auto_daily_enabled=config.wardrobe.auto_daily,
    )


def _resolve_outfit_tags(
    config: NaiArtistConfig,
    mode: Literal["photo", "drawing"],
    preset_name: str | None = None,
) -> str:
    """解析预览流程里应注入的衣柜 tags。"""
    if mode != "photo" or not config.wardrobe.enabled:
        return ""

    wardrobe = _get_wardrobe_manager(config)
    if preset_name:
        return wardrobe.get_preset_tags(preset_name)
    return wardrobe.get_outfit_tags()


async def _build_preview_payload(
    *,
    description: str,
    mode: Literal["photo", "drawing"],
    config: NaiArtistConfig,
    outfit_tags: str,
    config_path: str | Path,
    generate_image: bool,
) -> dict[str, Any]:
    """统一构建翻译/预览结果 payload。"""
    character_profile = config.character.base_tags if mode == "photo" else ""
    translated_tags = ""
    if description.strip():
        translated_tags = await translate_to_nai_tags(
            description=description,
            style_hint=get_style_hint(mode),
            translate_model=config.api.get_translate_model(),
            character_profile=character_profile,
            mode=mode,
            outfit_context=outfit_tags,
        )

    final_prompt = build_final_prompt(
        translated_tags,
        mode,
        config,
    )

    image_b64: str | None = None
    if generate_image:
        image_b64 = await _make_service(config).generate_image(
            prompt_tags=translated_tags,
            style_type=mode,
            config=config,
        )

    return {
        "mode": mode,
        "translatedTags": translated_tags,
        "finalPrompt": final_prompt,
        "outfitTags": outfit_tags,
        "imageDataUrl": f"data:image/png;base64,{image_b64}" if image_b64 else None,
        "config": config_to_editor_payload(config, config_path),
    }


async def preview_translation(
    *,
    description: str,
    mode: Literal["photo", "drawing"],
    overrides: ConfigOverrideData | None = None,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> dict[str, Any]:
    """仅翻译并返回最终 prompt，不调用生图。"""
    config = apply_config_overrides(load_nai_artist_config(config_path), overrides)
    outfit_tags = _resolve_outfit_tags(config, mode)
    return await _build_preview_payload(
        description=description,
        mode=mode,
        config=config,
        outfit_tags=outfit_tags,
        config_path=config_path,
        generate_image=False,
    )


async def generate_preview(
    *,
    description: str,
    mode: Literal["photo", "drawing"],
    overrides: ConfigOverrideData | None = None,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> dict[str, Any]:
    """翻译并出图，返回最终 prompt 和 data URI。"""
    config = apply_config_overrides(load_nai_artist_config(config_path), overrides)
    outfit_tags = _resolve_outfit_tags(config, mode)
    return await _build_preview_payload(
        description=description,
        mode=mode,
        config=config,
        outfit_tags=outfit_tags,
        config_path=config_path,
        generate_image=True,
    )


def get_wardrobe_data(
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
    wardrobe_data_file: str | Path | None = None,
) -> WardrobeData:
    """读取当前衣柜 JSON。"""
    config = load_nai_artist_config(config_path)
    if wardrobe_data_file is not None:
        config.wardrobe.data_file = str(wardrobe_data_file)
    return _get_wardrobe_manager(config).get_data()


def save_slot_option(
    *,
    slot: SlotName,
    name: str,
    description: str = "",
    tags: str = "",
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> WardrobeData:
    """新增或覆盖槽位选项。"""
    config = load_nai_artist_config(config_path)
    wardrobe = _get_wardrobe_manager(config)
    wardrobe.add_slot_option(slot, name, description, tags)
    return wardrobe.get_data()


def update_slot_option(
    *,
    slot: SlotName,
    name: str,
    description: str | None = None,
    tags: str | None = None,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> WardrobeData:
    """更新已有槽位选项。"""
    config = load_nai_artist_config(config_path)
    wardrobe = _get_wardrobe_manager(config)
    if not wardrobe.update_slot_option(slot, name, description=description, tags=tags):
        raise KeyError(name)
    return wardrobe.get_data()


def check_deletion_impact(
    *,
    slot: SlotName,
    name: str,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> DeletionImpact:
    """查询删除槽位选项时会影响哪些预设和当前状态。"""
    config = load_nai_artist_config(config_path)
    wardrobe = _get_wardrobe_manager(config)
    if wardrobe.find_slot_option(slot, name) is None:
        raise KeyError(name)
    return wardrobe.find_affected_by_deletion(slot, name)


def delete_slot_option(
    *,
    slot: SlotName,
    name: str,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> WardrobeData:
    """删除槽位选项并返回最新衣柜数据。"""
    config = load_nai_artist_config(config_path)
    wardrobe = _get_wardrobe_manager(config)
    if not wardrobe.delete_slot_option(slot, name):
        raise KeyError(name)
    return wardrobe.get_data()


def save_preset(
    *,
    name: str,
    description: str = "",
    top: str | None = None,
    bottom: str | None = None,
    outerwear: str | None = None,
    shoes: str | None = None,
    accessories: list[str] | None = None,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> WardrobeData:
    """新增或覆盖一个整套预设。"""
    config = load_nai_artist_config(config_path)
    wardrobe = _get_wardrobe_manager(config)
    wardrobe.add_preset(
        name,
        description,
        top=top,
        bottom=bottom,
        outerwear=outerwear,
        shoes=shoes,
        accessories=accessories,
    )
    return wardrobe.get_data()


def delete_preset(
    name: str,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> WardrobeData:
    """删除整套预设。"""
    config = load_nai_artist_config(config_path)
    wardrobe = _get_wardrobe_manager(config)
    if not wardrobe.delete_preset(name):
        raise KeyError(name)
    return wardrobe.get_data()


def update_daily_pool(
    preset_names: list[str],
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> WardrobeData:
    """更新每日自动换装候选池。"""
    config = load_nai_artist_config(config_path)
    wardrobe = _get_wardrobe_manager(config)
    wardrobe.set_daily_pool(preset_names)
    return wardrobe.get_data()


def update_wardrobe_state(
    *,
    preset_name: str | None = None,
    preset_name_provided: bool = False,
    slots: dict[str, str | None] | None = None,
    accessories: list[str] | None = None,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
    wardrobe_data_file: str | Path | None = None,
) -> WardrobeData:
    """手动应用当前穿搭状态。"""
    config = load_nai_artist_config(config_path)
    if wardrobe_data_file is not None:
        config.wardrobe.data_file = str(wardrobe_data_file)
    wardrobe = _get_wardrobe_manager(config)
    normalized_preset_name = preset_name.strip() if isinstance(preset_name, str) and preset_name.strip() else None
    if preset_name_provided:
        if normalized_preset_name is None:
            wardrobe.clear_active_preset()
        elif not wardrobe.apply_preset(normalized_preset_name):
            raise KeyError(normalized_preset_name)
    if slots:
        wardrobe.update_slots(slots)
    if accessories is not None:
        wardrobe.replace_accessories(accessories)
    return wardrobe.get_data()


async def preview_with_preset(
    *,
    description: str,
    mode: Literal["photo", "drawing"] = "photo",
    preset_name: str | None = None,
    overrides: ConfigOverrideData | None = None,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> dict[str, Any]:
    """使用指定预设或当前 state 做临时出图，不修改持久状态。"""
    config = apply_config_overrides(load_nai_artist_config(config_path), overrides)
    wardrobe = _get_wardrobe_manager(config)
    if preset_name and preset_name not in wardrobe.get_data()["presets"]:
        raise KeyError(preset_name)

    outfit_tags = _resolve_outfit_tags(config, mode, preset_name=preset_name)
    return await _build_preview_payload(
        description=description,
        mode=mode,
        config=config,
        outfit_tags=outfit_tags,
        config_path=config_path,
        generate_image=True,
    )