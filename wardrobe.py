"""nai_artist 衣柜数据管理与 Service。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, cast

from typing_extensions import TypedDict

from src.app.plugin_system.base import BaseService
from src.kernel.logger import get_logger

from .config import NaiArtistConfig

logger = get_logger("nai_artist")

SingleSlotName = Literal["top", "bottom", "outerwear", "shoes"]
SlotName = Literal["top", "bottom", "outerwear", "shoes", "accessories"]

SINGLE_SLOT_NAMES: tuple[SingleSlotName, ...] = ("top", "bottom", "outerwear", "shoes")
ALL_SLOT_NAMES: tuple[SlotName, ...] = (*SINGLE_SLOT_NAMES, "accessories")
_SLOT_LABELS: dict[SlotName, str] = {
    "top": "上装",
    "bottom": "下装",
    "outerwear": "外套",
    "shoes": "鞋子",
    "accessories": "配饰",
}


class SlotOption(TypedDict):
    """衣柜单个槽位选项。"""

    name: str
    description: str
    tags: str


class PresetEntry(TypedDict):
    """整套穿搭预设。"""

    description: str
    top: str | None
    bottom: str | None
    outerwear: str | None
    shoes: str | None
    accessories: list[str]


class WardrobeSlots(TypedDict):
    """当前穿搭的槽位引用。"""

    top: str | None
    bottom: str | None
    outerwear: str | None
    shoes: str | None
    accessories: list[str]


class WardrobeState(TypedDict):
    """衣柜运行时状态。"""

    slots: WardrobeSlots
    active_preset: str | None
    last_auto_date: str
    worn_since: str


class WardrobeData(TypedDict):
    """衣柜数据文件的完整结构。"""

    slot_options: dict[str, list[SlotOption]]
    presets: dict[str, PresetEntry]
    daily_pool: list[str]
    state: WardrobeState


class DeletionImpact(TypedDict):
    """删除某个槽位选项时的受影响范围。"""

    presets: list[str]
    state_affected: bool


def _new_empty_slots() -> WardrobeSlots:
    """创建空的穿搭槽位结构。"""
    return {
        "top": None,
        "bottom": None,
        "outerwear": None,
        "shoes": None,
        "accessories": [],
    }


def _new_default_data() -> WardrobeData:
    """创建默认衣柜数据。"""
    return {
        "slot_options": {slot_name: [] for slot_name in ALL_SLOT_NAMES},
        "presets": {},
        "daily_pool": [],
        "state": {
            "slots": _new_empty_slots(),
            "active_preset": None,
            "last_auto_date": "",
            "worn_since": "",
        },
    }


def _current_timestamp() -> str:
    """返回当前本地时间的 ISO 时间戳。"""
    return datetime.now().isoformat(timespec="seconds")


def _has_any_outfit_item(slots: WardrobeSlots) -> bool:
    """判断当前槽位里是否至少穿着了一件衣物或配饰。"""
    return any(slots[slot_name] is not None for slot_name in SINGLE_SLOT_NAMES) or bool(slots["accessories"])


def _normalize_name_list(raw_values: Iterable[Any]) -> list[str]:
    """将名称列表清洗为保序去重的字符串列表。"""
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            continue
        value = raw_value.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _merge_tag_groups(*tag_groups: str) -> str:
    """按顺序合并多组 tags，保留前者优先级并去重。"""
    merged: list[str] = []
    seen: set[str] = set()
    for group in tag_groups:
        normalized = group.replace("，", ",").replace("、", ",")
        for part in normalized.split(","):
            tag = part.strip()
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(tag)
    return ", ".join(merged)


class WardrobeManager:
    """衣柜 JSON 数据管理器。"""

    def __init__(self, data_file: str | Path, auto_daily_enabled: bool = False) -> None:
        """初始化衣柜管理器。

        Args:
            data_file: 衣柜 JSON 数据文件路径
            auto_daily_enabled: 读取当前穿搭时是否自动按日期同步 daily rotation
        """
        self.data_file = Path(data_file)
        self.auto_daily_enabled = auto_daily_enabled
        self._data: WardrobeData | None = None
        self._data_mtime_ns: int | None = None
        self._data_serialized: str | None = None

    def _get_file_mtime_ns(self) -> int | None:
        """返回当前数据文件的修改时间戳；文件不存在时返回 None。"""
        try:
            return self.data_file.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _load_data_from_disk(self) -> tuple[WardrobeData, str]:
        """从磁盘读取并收敛衣柜数据。"""
        raw_text = self.data_file.read_text(encoding="utf-8")
        raw_data = json.loads(raw_text) if raw_text.strip() else {}
        return self._coerce_data(raw_data), raw_text

    def _refresh_cached_data_if_needed(self) -> WardrobeData | None:
        """当底层文件已被外部修改时，刷新当前实例缓存。"""
        if self._data is None:
            return None

        if not self.data_file.exists():
            self._data = None
            self._data_mtime_ns = None
            self._data_serialized = None
            return None

        raw_text = self.data_file.read_text(encoding="utf-8")
        if raw_text.strip() == (self._data_serialized or "").strip():
            self._data_mtime_ns = self._get_file_mtime_ns()
            return self._data

        raw_data = json.loads(raw_text) if raw_text.strip() else {}
        self._data = self._coerce_data(raw_data)
        normalized_text = json.dumps(self._data, ensure_ascii=False, indent=2)
        self._data_serialized = normalized_text
        self._data_mtime_ns = self._get_file_mtime_ns()
        if normalized_text.strip() != raw_text.strip():
            self.save()

        return self._data

    def _sync_daily_rotation_if_needed(self) -> None:
        """在启用 auto_daily 时，读取当前穿搭前先同步当日预设。"""
        if not self.auto_daily_enabled:
            return
        self.apply_daily_rotation()

    def load(self) -> WardrobeData:
        """加载衣柜数据，不存在时自动创建默认结构。"""
        refreshed = self._refresh_cached_data_if_needed()
        if refreshed is not None:
            return refreshed

        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.data_file.exists():
            self._data = _new_default_data()
            self.save()
            return self._data

        self._data, raw_text = self._load_data_from_disk()
        self._data_mtime_ns = self._get_file_mtime_ns()

        normalized_text = json.dumps(self._data, ensure_ascii=False, indent=2)
        self._data_serialized = normalized_text
        if normalized_text.strip() != raw_text.strip():
            self.save()

        return self._data

    def save(self) -> None:
        """保存当前衣柜数据到 JSON 文件。"""
        if self._data is None:
            self._data = _new_default_data()

        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.data_file.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._data_mtime_ns = self._get_file_mtime_ns()
        self._data_serialized = json.dumps(self._data, ensure_ascii=False, indent=2)

    def get_data(self) -> WardrobeData:
        """返回完整衣柜数据的深拷贝。"""
        self._sync_daily_rotation_if_needed()
        return deepcopy(self.load())

    def get_outfit_tags(self) -> str:
        """解析当前穿搭引用并合并为 tags 字符串。"""
        self._sync_daily_rotation_if_needed()
        data = self.load()
        return self._resolve_slots_to_tags(data["state"]["slots"])

    def get_preset_tags(self, name: str) -> str:
        """解析指定预设对应的完整穿搭 tags。"""
        data = self.load()
        preset = data["presets"].get(name)
        if preset is None:
            return ""
        slots: WardrobeSlots = {
            "top": preset["top"],
            "bottom": preset["bottom"],
            "outerwear": preset["outerwear"],
            "shoes": preset["shoes"],
            "accessories": list(preset["accessories"]),
        }
        return self._resolve_slots_to_tags(slots)

    def get_current_outfit_summary(self) -> str:
        """返回供换装翻译模型阅读的当前穿搭摘要。"""
        self._sync_daily_rotation_if_needed()
        data = self.load()
        slots = data["state"]["slots"]
        worn_hours = self.get_current_outfit_worn_hours()
        summary = {
            "active_preset": data["state"]["active_preset"],
            "worn_since": data["state"].get("worn_since") or None,
            "worn_hours": worn_hours,
            "slots": {
                slot_name: self._resolve_slot_snapshot(slot_name, slots[slot_name])
                for slot_name in SINGLE_SLOT_NAMES
            },
            "accessories": [
                self._resolve_slot_snapshot("accessories", accessory_name)
                for accessory_name in slots["accessories"]
            ],
            "resolved_tags": self.get_outfit_tags(),
        }
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def get_current_outfit_readable_summary(self) -> str:
        """返回供主 LLM 和工具结果阅读的当前穿搭自然语言摘要。"""
        self._sync_daily_rotation_if_needed()
        data = self.load()
        slots = data["state"]["slots"]

        if not _has_any_outfit_item(slots):
            return "当前未穿任何已记录衣物或配饰"

        parts: list[str] = []
        active_preset = data["state"]["active_preset"]
        if active_preset:
            preset = data["presets"].get(active_preset)
            preset_description = ""
            if preset and preset["description"]:
                preset_description = f"（{preset['description']}）"
            parts.append(f"当前预设：{active_preset}{preset_description}")

        for slot_name in SINGLE_SLOT_NAMES:
            snapshot = self._resolve_slot_snapshot(slot_name, slots[slot_name])
            display_name = snapshot["description"] or snapshot["name"] or "未穿"
            parts.append(f"{_SLOT_LABELS[slot_name]}：{display_name}")

        if slots["accessories"]:
            accessory_names = [
                self._resolve_slot_snapshot("accessories", accessory_name)["description"]
                or self._resolve_slot_snapshot("accessories", accessory_name)["name"]
                or accessory_name
                for accessory_name in slots["accessories"]
            ]
            parts.append(f"{_SLOT_LABELS['accessories']}：{'、'.join(accessory_names)}")
        else:
            parts.append(f"{_SLOT_LABELS['accessories']}：无")

        resolved_tags = self._resolve_slots_to_tags(slots).strip()
        if resolved_tags:
            parts.append(f"解析 tags：{resolved_tags}")

        return "；".join(parts)

    def apply_preset(self, name: str) -> bool:
        """将当前穿搭整体替换为指定预设。"""
        data = self.load()
        preset = data["presets"].get(name)
        if preset is None:
            return False

        data["state"]["slots"] = {
            "top": preset["top"],
            "bottom": preset["bottom"],
            "outerwear": preset["outerwear"],
            "shoes": preset["shoes"],
            "accessories": list(preset["accessories"]),
        }
        data["state"]["active_preset"] = name
        data["state"]["worn_since"] = _current_timestamp()
        self.save()
        return True

    def clear_active_preset(self) -> bool:
        """仅清除当前 state 的预设绑定，保留实际穿搭槽位不变。"""
        data = self.load()
        if data["state"]["active_preset"] is None:
            return False

        data["state"]["active_preset"] = None
        self.save()
        return True

    def update_slots(self, updates: dict[str, str | None]) -> bool:
        """按槽位局部更新当前穿搭。"""
        data = self.load()
        changed = False
        for slot_name, raw_value in updates.items():
            if slot_name not in SINGLE_SLOT_NAMES:
                continue
            value = raw_value.strip() if isinstance(raw_value, str) and raw_value.strip() else None
            current_value = data["state"]["slots"][cast(SingleSlotName, slot_name)]
            if current_value == value:
                continue
            data["state"]["slots"][cast(SingleSlotName, slot_name)] = value
            changed = True

        if changed:
            data["state"]["active_preset"] = None
            data["state"]["worn_since"] = _current_timestamp()
            self.save()
        return changed

    def add_accessories(self, names: list[str]) -> bool:
        """在当前穿搭中追加配饰。"""
        data = self.load()
        existing = list(data["state"]["slots"]["accessories"])
        merged = _normalize_name_list([*existing, *names])
        if merged == existing:
            return False

        data["state"]["slots"]["accessories"] = merged
        data["state"]["active_preset"] = None
        data["state"]["worn_since"] = _current_timestamp()
        self.save()
        return True

    def replace_accessories(self, names: list[str]) -> bool:
        """全量替换当前穿搭的配饰列表。"""
        data = self.load()
        merged = _normalize_name_list(names)
        if merged == data["state"]["slots"]["accessories"]:
            return False

        data["state"]["slots"]["accessories"] = merged
        data["state"]["active_preset"] = None
        data["state"]["worn_since"] = _current_timestamp()
        self.save()
        return True

    def remove_slots(self, slot_names: list[str]) -> bool:
        """移除指定槽位当前穿搭；配饰会整体清空。"""
        data = self.load()
        changed = False
        for slot_name in _normalize_name_list(slot_names):
            if slot_name == "accessories":
                if data["state"]["slots"]["accessories"]:
                    data["state"]["slots"]["accessories"] = []
                    changed = True
                continue
            if slot_name not in SINGLE_SLOT_NAMES:
                continue
            typed_slot = cast(SingleSlotName, slot_name)
            if data["state"]["slots"][typed_slot] is None:
                continue
            data["state"]["slots"][typed_slot] = None
            changed = True

        if changed:
            data["state"]["active_preset"] = None
            data["state"]["worn_since"] = _current_timestamp()
            self.save()
        return changed

    def apply_daily_rotation(self) -> bool:
        """按日期从每日预设池中选择一套穿搭，当天内保持稳定。"""
        data = self.load()
        today = date.today().isoformat()
        if data["state"]["last_auto_date"] == today:
            return False

        valid_pool = [name for name in data["daily_pool"] if name in data["presets"]]
        if not valid_pool:
            return False

        selected_name = valid_pool[date.today().toordinal() % len(valid_pool)]
        preset = data["presets"][selected_name]
        data["state"]["slots"] = {
            "top": preset["top"],
            "bottom": preset["bottom"],
            "outerwear": preset["outerwear"],
            "shoes": preset["shoes"],
            "accessories": list(preset["accessories"]),
        }
        data["state"]["active_preset"] = selected_name
        data["state"]["last_auto_date"] = today
        data["state"]["worn_since"] = _current_timestamp()
        self.save()
        return True

    def find_slot_option(self, slot: SlotName, name: str) -> SlotOption | None:
        """按槽位与名称查找单个选项。"""
        for option in self.load()["slot_options"].get(slot, []):
            if option["name"] == name:
                return deepcopy(option)
        return None

    def add_slot_option(
        self,
        slot: SlotName,
        name: str,
        description: str = "",
        tags: str = "",
    ) -> bool:
        """新增或覆盖一个槽位选项。"""
        data = self.load()
        clean_name = name.strip()
        if not clean_name:
            return False

        options = data["slot_options"].setdefault(slot, [])
        for option in options:
            if option["name"] == clean_name:
                option["description"] = description.strip()
                option["tags"] = tags.strip()
                self.save()
                return True

        options.append(
            {
                "name": clean_name,
                "description": description.strip(),
                "tags": tags.strip(),
            }
        )
        self.save()
        return True

    def update_slot_option(
        self,
        slot: SlotName,
        name: str,
        *,
        description: str | None = None,
        tags: str | None = None,
    ) -> bool:
        """更新已有槽位选项的 description 或 tags。"""
        data = self.load()
        for option in data["slot_options"].get(slot, []):
            if option["name"] != name:
                continue
            if description is not None:
                option["description"] = description.strip()
            if tags is not None:
                option["tags"] = tags.strip()
            self.save()
            return True
        return False

    def find_affected_by_deletion(self, slot: SlotName, name: str) -> DeletionImpact:
        """分析删除某个槽位选项后会影响哪些预设和当前状态。"""
        data = self.load()
        affected_presets: list[str] = []
        for preset_name, preset in data["presets"].items():
            if slot == "accessories":
                if name in preset["accessories"]:
                    affected_presets.append(preset_name)
            elif preset[cast(SingleSlotName, slot)] == name:
                affected_presets.append(preset_name)

        state_slots = data["state"]["slots"]
        if slot == "accessories":
            state_affected = name in state_slots["accessories"]
        else:
            state_affected = state_slots[cast(SingleSlotName, slot)] == name

        return {
            "presets": affected_presets,
            "state_affected": state_affected,
        }

    def delete_slot_option(self, slot: SlotName, name: str) -> bool:
        """删除槽位选项，并清理所有预设与当前状态中的引用。"""
        data = self.load()
        options = data["slot_options"].get(slot, [])
        new_options = [option for option in options if option["name"] != name]
        if len(new_options) == len(options):
            return False

        data["slot_options"][slot] = new_options

        for preset in data["presets"].values():
            if slot == "accessories":
                preset["accessories"] = [item for item in preset["accessories"] if item != name]
            elif preset[cast(SingleSlotName, slot)] == name:
                preset[cast(SingleSlotName, slot)] = None

        if slot == "accessories":
            data["state"]["slots"]["accessories"] = [
                item for item in data["state"]["slots"]["accessories"] if item != name
            ]
        elif data["state"]["slots"][cast(SingleSlotName, slot)] == name:
            data["state"]["slots"][cast(SingleSlotName, slot)] = None

        if _has_any_outfit_item(data["state"]["slots"]):
            data["state"]["worn_since"] = _current_timestamp()
        else:
            data["state"]["worn_since"] = ""

        self.save()
        return True

    def get_current_outfit_worn_hours(self) -> int | None:
        """返回当前穿搭已经连续穿着了多少整小时。"""
        self._sync_daily_rotation_if_needed()
        data = self.load()
        if not _has_any_outfit_item(data["state"]["slots"]):
            return None

        worn_since = data["state"].get("worn_since", "")
        if not isinstance(worn_since, str) or not worn_since.strip():
            return None

        try:
            started_at = datetime.fromisoformat(worn_since)
        except ValueError:
            return None

        elapsed_seconds = max((datetime.now() - started_at).total_seconds(), 0.0)
        return int(elapsed_seconds // 3600)

    def get_current_outfit_worn_hours_text(self) -> str:
        """返回供提示词直接使用的当前穿着时长描述。"""
        worn_hours = self.get_current_outfit_worn_hours()
        if worn_hours is None:
            return "未记录"
        if worn_hours <= 0:
            return "不到 1 小时"
        return f"约 {worn_hours} 小时"

    def add_preset(
        self,
        name: str,
        description: str = "",
        *,
        top: str | None = None,
        bottom: str | None = None,
        outerwear: str | None = None,
        shoes: str | None = None,
        accessories: list[str] | None = None,
    ) -> bool:
        """新增或覆盖一个整套穿搭预设。"""
        clean_name = name.strip()
        if not clean_name:
            return False

        data = self.load()
        data["presets"][clean_name] = {
            "description": description.strip(),
            "top": top.strip() if isinstance(top, str) and top.strip() else None,
            "bottom": bottom.strip() if isinstance(bottom, str) and bottom.strip() else None,
            "outerwear": outerwear.strip() if isinstance(outerwear, str) and outerwear.strip() else None,
            "shoes": shoes.strip() if isinstance(shoes, str) and shoes.strip() else None,
            "accessories": _normalize_name_list(accessories or []),
        }
        self.save()
        return True

    def delete_preset(self, name: str) -> bool:
        """删除预设，并同步清理 daily_pool 与 active_preset。"""
        data = self.load()
        if name not in data["presets"]:
            return False

        del data["presets"][name]
        data["daily_pool"] = [preset_name for preset_name in data["daily_pool"] if preset_name != name]
        if data["state"]["active_preset"] == name:
            data["state"]["active_preset"] = None
        self.save()
        return True

    def set_daily_pool(self, preset_names: list[str]) -> bool:
        """更新每日自动换装候选池。"""
        data = self.load()
        valid_names = [name for name in _normalize_name_list(preset_names) if name in data["presets"]]
        if valid_names == data["daily_pool"]:
            return False

        data["daily_pool"] = valid_names
        self.save()
        return True

    def get_presets_summary(self) -> str:
        """返回供换装翻译模型阅读的预设摘要。"""
        return json.dumps(self.load()["presets"], ensure_ascii=False, indent=2)

    def get_slot_options_summary(self) -> str:
        """返回供换装翻译模型阅读的槽位选项摘要。"""
        return json.dumps(self.load()["slot_options"], ensure_ascii=False, indent=2)

    def _coerce_data(self, raw_data: Any) -> WardrobeData:
        """将任意 JSON 数据收敛为合法衣柜结构。"""
        default_data = _new_default_data()
        if not isinstance(raw_data, dict):
            return default_data

        slot_options: dict[str, list[SlotOption]] = {slot_name: [] for slot_name in ALL_SLOT_NAMES}
        raw_slot_options = raw_data.get("slot_options")
        if isinstance(raw_slot_options, dict):
            for slot_name in ALL_SLOT_NAMES:
                raw_options = raw_slot_options.get(slot_name, [])
                if not isinstance(raw_options, list):
                    continue
                seen_names: set[str] = set()
                for raw_option in raw_options:
                    option = self._coerce_slot_option(raw_option)
                    if option is None or option["name"] in seen_names:
                        continue
                    seen_names.add(option["name"])
                    slot_options[slot_name].append(option)

        presets: dict[str, PresetEntry] = {}
        raw_presets = raw_data.get("presets")
        if isinstance(raw_presets, dict):
            for raw_name, raw_preset in raw_presets.items():
                if not isinstance(raw_name, str) or not raw_name.strip():
                    continue
                preset = self._coerce_preset(raw_preset)
                if preset is None:
                    continue
                presets[raw_name.strip()] = preset

        raw_state = raw_data.get("state") if isinstance(raw_data.get("state"), dict) else {}
        state_slots = self._coerce_slots(raw_state.get("slots") if isinstance(raw_state, dict) else None)
        active_preset = raw_state.get("active_preset") if isinstance(raw_state, dict) else None
        if not isinstance(active_preset, str) or not active_preset.strip() or active_preset not in presets:
            active_preset = None

        last_auto_date = raw_state.get("last_auto_date") if isinstance(raw_state, dict) else ""
        if not isinstance(last_auto_date, str):
            last_auto_date = ""

        worn_since = raw_state.get("worn_since") if isinstance(raw_state, dict) else ""
        if not isinstance(worn_since, str) or not worn_since.strip():
            worn_since = _current_timestamp() if _has_any_outfit_item(state_slots) else ""
        else:
            try:
                datetime.fromisoformat(worn_since)
            except ValueError:
                worn_since = _current_timestamp() if _has_any_outfit_item(state_slots) else ""

        daily_pool = [
            preset_name
            for preset_name in _normalize_name_list(raw_data.get("daily_pool", []))
            if preset_name in presets
        ]

        return {
            "slot_options": slot_options,
            "presets": presets,
            "daily_pool": daily_pool,
            "state": {
                "slots": state_slots,
                "active_preset": active_preset,
                "last_auto_date": last_auto_date,
                "worn_since": worn_since,
            },
        }

    def _coerce_slot_option(self, raw_option: Any) -> SlotOption | None:
        """将任意对象收敛为合法槽位选项。"""
        if not isinstance(raw_option, dict):
            return None
        name = raw_option.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        description = raw_option.get("description", "")
        tags = raw_option.get("tags", "")
        return {
            "name": name.strip(),
            "description": description.strip() if isinstance(description, str) else "",
            "tags": tags.strip() if isinstance(tags, str) else "",
        }

    def _coerce_preset(self, raw_preset: Any) -> PresetEntry | None:
        """将任意对象收敛为合法预设。"""
        if not isinstance(raw_preset, dict):
            return None
        description = raw_preset.get("description", "")
        return {
            "description": description.strip() if isinstance(description, str) else "",
            "top": self._coerce_optional_name(raw_preset.get("top")),
            "bottom": self._coerce_optional_name(raw_preset.get("bottom")),
            "outerwear": self._coerce_optional_name(raw_preset.get("outerwear")),
            "shoes": self._coerce_optional_name(raw_preset.get("shoes")),
            "accessories": _normalize_name_list(raw_preset.get("accessories", [])),
        }

    def _coerce_slots(self, raw_slots: Any) -> WardrobeSlots:
        """将任意对象收敛为当前穿搭槽位结构。"""
        if not isinstance(raw_slots, dict):
            return _new_empty_slots()
        return {
            "top": self._coerce_optional_name(raw_slots.get("top")),
            "bottom": self._coerce_optional_name(raw_slots.get("bottom")),
            "outerwear": self._coerce_optional_name(raw_slots.get("outerwear")),
            "shoes": self._coerce_optional_name(raw_slots.get("shoes")),
            "accessories": _normalize_name_list(raw_slots.get("accessories", [])),
        }

    def _coerce_optional_name(self, raw_value: Any) -> str | None:
        """将任意对象收敛为可选名称字符串。"""
        if not isinstance(raw_value, str) or not raw_value.strip():
            return None
        return raw_value.strip()

    def _resolve_slots_to_tags(self, slots: WardrobeSlots) -> str:
        """按当前 state/preset 中的名称引用解析所有 tags。"""
        tag_groups: list[str] = []
        for slot_name in SINGLE_SLOT_NAMES:
            option_name = slots[slot_name]
            if option_name is None:
                continue
            option = self.find_slot_option(slot_name, option_name)
            if option is None:
                continue
            tag_groups.append(option["tags"])

        for accessory_name in slots["accessories"]:
            option = self.find_slot_option("accessories", accessory_name)
            if option is None:
                continue
            tag_groups.append(option["tags"])

        return _merge_tag_groups(*tag_groups)

    def _resolve_slot_snapshot(self, slot: SlotName, option_name: str | None) -> dict[str, str | None]:
        """返回单个槽位引用的快照信息，供 WebUI 与翻译模型阅读。"""
        if option_name is None:
            return {"name": None, "description": None, "tags": None}
        option = self.find_slot_option(slot, option_name)
        if option is None:
            return {"name": option_name, "description": None, "tags": None}
        return {
            "name": option["name"],
            "description": option["description"] or None,
            "tags": option["tags"] or None,
        }


class WardrobeService(BaseService):
    """衣柜 Service，对外暴露衣柜管理能力。"""

    service_name: str = "wardrobe"
    service_description: str = "管理 nai_artist 的衣柜、预设与当前穿搭状态"
    version: str = "1.0.0"

    def __init__(self, plugin: Any) -> None:
        """初始化衣柜 Service。"""
        super().__init__(plugin)
        self._manager: WardrobeManager | None = None

    def get_manager(self) -> WardrobeManager:
        """延迟创建并返回 WardrobeManager。"""
        config = self.plugin.config
        if not isinstance(config, NaiArtistConfig):
            raise RuntimeError("nai_artist wardrobe service 缺少有效配置")

        if self._manager is None:
            self._manager = WardrobeManager(
                config.wardrobe.data_file,
                auto_daily_enabled=config.wardrobe.auto_daily,
            )
        else:
            self._manager.auto_daily_enabled = config.wardrobe.auto_daily
        return self._manager

    def get_data(self) -> WardrobeData:
        """获取完整衣柜数据。"""
        return self.get_manager().get_data()

    def get_outfit_tags(self) -> str:
        """获取当前穿搭解析后的 tags。"""
        return self.get_manager().get_outfit_tags()

    def get_preset_tags(self, name: str) -> str:
        """获取某个预设解析后的 tags。"""
        return self.get_manager().get_preset_tags(name)

    def get_current_outfit_summary(self) -> str:
        """获取当前穿搭摘要。"""
        return self.get_manager().get_current_outfit_summary()

    def get_current_outfit_readable_summary(self) -> str:
        """获取当前穿搭的自然语言摘要。"""
        return self.get_manager().get_current_outfit_readable_summary()

    def get_current_outfit_worn_hours(self) -> int | None:
        """获取当前穿搭已经连续穿着了多少整小时。"""
        return self.get_manager().get_current_outfit_worn_hours()

    def get_current_outfit_worn_hours_text(self) -> str:
        """获取当前穿搭时长的人类可读描述。"""
        return self.get_manager().get_current_outfit_worn_hours_text()

    def apply_preset(self, name: str) -> bool:
        """应用指定预设。"""
        return self.get_manager().apply_preset(name)

    def update_slots(self, updates: dict[str, str | None]) -> bool:
        """局部更新非配饰槽位。"""
        return self.get_manager().update_slots(updates)

    def add_accessories(self, names: list[str]) -> bool:
        """追加配饰到当前穿搭。"""
        return self.get_manager().add_accessories(names)

    def replace_accessories(self, names: list[str]) -> bool:
        """全量替换当前穿搭配饰。"""
        return self.get_manager().replace_accessories(names)

    def remove_slots(self, slot_names: list[str]) -> bool:
        """移除指定槽位。"""
        return self.get_manager().remove_slots(slot_names)

    def apply_daily_rotation(self) -> bool:
        """按日期切换每日预设。"""
        return self.get_manager().apply_daily_rotation()

    def add_slot_option(self, slot: SlotName, name: str, description: str = "", tags: str = "") -> bool:
        """新增或覆盖槽位选项。"""
        return self.get_manager().add_slot_option(slot, name, description, tags)

    def update_slot_option(
        self,
        slot: SlotName,
        name: str,
        *,
        description: str | None = None,
        tags: str | None = None,
    ) -> bool:
        """更新已有槽位选项。"""
        return self.get_manager().update_slot_option(slot, name, description=description, tags=tags)

    def find_slot_option(self, slot: SlotName, name: str) -> SlotOption | None:
        """查找单个槽位选项。"""
        return self.get_manager().find_slot_option(slot, name)

    def find_affected_by_deletion(self, slot: SlotName, name: str) -> DeletionImpact:
        """分析删除槽位选项后的影响范围。"""
        return self.get_manager().find_affected_by_deletion(slot, name)

    def delete_slot_option(self, slot: SlotName, name: str) -> bool:
        """删除槽位选项。"""
        return self.get_manager().delete_slot_option(slot, name)

    def add_preset(
        self,
        name: str,
        description: str = "",
        *,
        top: str | None = None,
        bottom: str | None = None,
        outerwear: str | None = None,
        shoes: str | None = None,
        accessories: list[str] | None = None,
    ) -> bool:
        """新增或覆盖整套预设。"""
        return self.get_manager().add_preset(
            name,
            description,
            top=top,
            bottom=bottom,
            outerwear=outerwear,
            shoes=shoes,
            accessories=accessories,
        )

    def delete_preset(self, name: str) -> bool:
        """删除整套预设。"""
        return self.get_manager().delete_preset(name)

    def set_daily_pool(self, preset_names: list[str]) -> bool:
        """更新每日预设池。"""
        return self.get_manager().set_daily_pool(preset_names)

    def get_presets_summary(self) -> str:
        """获取预设摘要字符串。"""
        return self.get_manager().get_presets_summary()

    def get_slot_options_summary(self) -> str:
        """获取槽位选项摘要字符串。"""
        return self.get_manager().get_slot_options_summary()
