"""nai_artist WebUI 应用。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .webui_logic import (
    DEFAULT_PLUGIN_CONFIG_PATH,
    apply_config_overrides,
    check_deletion_impact,
    config_to_editor_payload,
    delete_preset,
    delete_slot_option,
    generate_preview,
    get_wardrobe_data,
    initialize_webui_runtime,
    load_nai_artist_config,
    preview_translation,
    preview_with_preset,
    save_preset,
    save_nai_artist_config,
    save_slot_option,
    update_daily_pool,
    update_slot_option,
    update_wardrobe_state,
)
from .wardrobe import SlotName, WardrobeManager


NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


class ConfigOverrides(BaseModel):
    base_tags: str | None = None
    fixed_tags: str | None = None
    negative_tags: str | None = None
    photo_style_tags: str | None = None
    photo_style_tags_append: str | None = None
    photo_width: int | None = None
    photo_height: int | None = None
    photo_steps: int | None = None
    drawing_style_tags: str | None = None
    drawing_style_tags_append: str | None = None
    drawing_width: int | None = None
    drawing_height: int | None = None
    drawing_steps: int | None = None
    wardrobe_enabled: bool | None = None
    wardrobe_auto_daily: bool | None = None
    wardrobe_data_file: str | None = None


class PreviewRequest(BaseModel):
    mode: Literal["photo", "drawing"]
    description: str = Field(min_length=1)
    overrides: ConfigOverrides | None = None


class SaveRequest(BaseModel):
    overrides: ConfigOverrides


class SlotOptionRequest(BaseModel):
    slot: Literal["top", "bottom", "outerwear", "shoes", "accessories"]
    name: str = Field(min_length=1)
    description: str = ""
    tags: str = ""


class SlotOptionUpdateRequest(BaseModel):
    description: str | None = None
    tags: str | None = None


class PresetRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    top: str | None = None
    bottom: str | None = None
    outerwear: str | None = None
    shoes: str | None = None
    accessories: list[str] = Field(default_factory=list)


class DailyPoolRequest(BaseModel):
    preset_names: list[str] = Field(default_factory=list)


class WardrobeStateRequest(BaseModel):
    preset_name: str | None = None
    slots: dict[str, str | None] | None = None
    accessories: list[str] | None = None
    persist: bool = False


class WardrobePreviewRequest(BaseModel):
    description: str = ""
    mode: Literal["photo", "drawing"] = "photo"
    preset_name: str | None = None
    overrides: ConfigOverrides | None = None


def _ensure_preview_temp_dir(app: FastAPI) -> Path:
    """返回 WebUI 测试态专用临时目录。"""
    temp_dir = getattr(app.state, "webui_preview_temp_dir", None)
    if temp_dir is None:
        temp_dir = TemporaryDirectory(prefix="nai_artist_webui_")
        app.state.webui_preview_temp_dir = temp_dir
    return Path(temp_dir.name)


def _get_preview_wardrobe_path(app: FastAPI) -> Path:
    """返回 WebUI 测试态使用的临时衣柜文件路径。"""
    return _ensure_preview_temp_dir(app) / "wardrobe_preview.json"


def _sync_preview_wardrobe(app: FastAPI, *, keep_preview_state: bool = True) -> Path:
    """将实时衣柜库同步到 WebUI 测试态文件，并按需保留测试中的当前穿搭。"""
    live_data = get_wardrobe_data(app.state.nai_artist_config_path)
    preview_path = _get_preview_wardrobe_path(app)

    preview_state = None
    if keep_preview_state and preview_path.exists():
        preview_state = WardrobeManager(preview_path).get_data().get("state")

    preview_data = deepcopy(live_data)
    if preview_state is not None:
        preview_data["state"] = preview_state

    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(
        json.dumps(preview_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return preview_path


def _build_preview_overrides(app: FastAPI, overrides: ConfigOverrides | None) -> dict[str, object]:
    """构造指向 WebUI 测试态衣柜文件的配置覆盖。"""
    merged: dict[str, object] = overrides.model_dump(exclude_none=True) if overrides else {}
    merged["wardrobe_data_file"] = str(_sync_preview_wardrobe(app))
    return merged


def create_app(
    *,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
    initialize_runtime: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if initialize_runtime:
            initialize_webui_runtime()
        try:
            yield
        finally:
            temp_dir = getattr(app.state, "webui_preview_temp_dir", None)
            if temp_dir is not None:
                temp_dir.cleanup()
                app.state.webui_preview_temp_dir = None

    app = FastAPI(
        title="NAI Artist WebUI",
        description="挂载到主程序 HTTP 服务的 nai_artist 提示词测试与出图工作台",
        lifespan=lifespan,
    )
    app.state.nai_artist_config_path = Path(config_path)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(
            Path(__file__).with_name("webui") / "index.html",
            headers=NO_CACHE_HEADERS,
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "nai_artist_webui"}

    @app.get("/api/config")
    async def get_config() -> dict:
        config = load_nai_artist_config(app.state.nai_artist_config_path)
        return config_to_editor_payload(config, app.state.nai_artist_config_path)

    @app.post("/api/translate")
    async def api_translate(request: PreviewRequest) -> dict:
        return await preview_translation(
            description=request.description,
            mode=request.mode,
            overrides=_build_preview_overrides(app, request.overrides),
            config_path=app.state.nai_artist_config_path,
        )

    @app.post("/api/generate")
    async def api_generate(request: PreviewRequest) -> dict:
        result = await generate_preview(
            description=request.description,
            mode=request.mode,
            overrides=_build_preview_overrides(app, request.overrides),
            config_path=app.state.nai_artist_config_path,
        )
        if result["imageDataUrl"] is None:
            raise HTTPException(status_code=502, detail="图片生成失败")
        return result

    @app.post("/api/config/save")
    async def api_save_config(request: SaveRequest) -> dict:
        config = save_nai_artist_config(
            request.overrides.model_dump(exclude_none=True),
            app.state.nai_artist_config_path,
        )
        return config_to_editor_payload(config, app.state.nai_artist_config_path)

    @app.post("/api/config/preview")
    async def api_preview_config(request: SaveRequest) -> dict:
        config = load_nai_artist_config(app.state.nai_artist_config_path)
        apply_config_overrides(config, request.overrides.model_dump(exclude_none=True))
        return config_to_editor_payload(config, app.state.nai_artist_config_path)

    @app.get("/api/wardrobe")
    async def api_get_wardrobe() -> dict:
        preview_path = _sync_preview_wardrobe(app)
        return get_wardrobe_data(
            app.state.nai_artist_config_path,
            wardrobe_data_file=preview_path,
        )

    @app.post("/api/wardrobe/slot_option")
    async def api_save_slot_option(request: SlotOptionRequest) -> dict:
        return save_slot_option(
            slot=cast(SlotName, request.slot),
            name=request.name,
            description=request.description,
            tags=request.tags,
            config_path=app.state.nai_artist_config_path,
        )

    @app.put("/api/wardrobe/slot_option/{slot}/{name}")
    async def api_update_slot_option(
        slot: Literal["top", "bottom", "outerwear", "shoes", "accessories"],
        name: str,
        request: SlotOptionUpdateRequest,
    ) -> dict:
        try:
            return update_slot_option(
                slot=cast(SlotName, slot),
                name=name,
                description=request.description,
                tags=request.tags,
                config_path=app.state.nai_artist_config_path,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"槽位选项不存在: {exc.args[0]}") from exc

    @app.get("/api/wardrobe/slot_option/{slot}/{name}/impact")
    async def api_slot_option_impact(
        slot: Literal["top", "bottom", "outerwear", "shoes", "accessories"],
        name: str,
    ) -> dict:
        try:
            return check_deletion_impact(
                slot=cast(SlotName, slot),
                name=name,
                config_path=app.state.nai_artist_config_path,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"槽位选项不存在: {exc.args[0]}") from exc

    @app.delete("/api/wardrobe/slot_option/{slot}/{name}")
    async def api_delete_slot_option(
        slot: Literal["top", "bottom", "outerwear", "shoes", "accessories"],
        name: str,
    ) -> dict:
        try:
            return delete_slot_option(
                slot=cast(SlotName, slot),
                name=name,
                config_path=app.state.nai_artist_config_path,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"槽位选项不存在: {exc.args[0]}") from exc

    @app.post("/api/wardrobe/preset")
    async def api_save_preset(request: PresetRequest) -> dict:
        return save_preset(
            name=request.name,
            description=request.description,
            top=request.top,
            bottom=request.bottom,
            outerwear=request.outerwear,
            shoes=request.shoes,
            accessories=request.accessories,
            config_path=app.state.nai_artist_config_path,
        )

    @app.delete("/api/wardrobe/preset/{name}")
    async def api_delete_preset(name: str) -> dict:
        try:
            return delete_preset(name, app.state.nai_artist_config_path)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"预设不存在: {exc.args[0]}") from exc

    @app.post("/api/wardrobe/daily-pool")
    async def api_update_daily_pool(request: DailyPoolRequest) -> dict:
        return update_daily_pool(request.preset_names, app.state.nai_artist_config_path)

    @app.post("/api/wardrobe/state")
    async def api_update_wardrobe_state(request: WardrobeStateRequest) -> dict:
        try:
            wardrobe_data_file = None
            if request.persist:
                _sync_preview_wardrobe(app, keep_preview_state=False)
            else:
                wardrobe_data_file = _sync_preview_wardrobe(app)

            return update_wardrobe_state(
                preset_name=request.preset_name,
                preset_name_provided="preset_name" in request.model_fields_set,
                slots=request.slots,
                accessories=request.accessories,
                config_path=app.state.nai_artist_config_path,
                wardrobe_data_file=wardrobe_data_file,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"预设不存在: {exc.args[0]}") from exc

    @app.post("/api/wardrobe/preview")
    async def api_wardrobe_preview(request: WardrobePreviewRequest) -> dict:
        try:
            result = await preview_with_preset(
                description=request.description,
                mode=request.mode,
                preset_name=request.preset_name,
                overrides=_build_preview_overrides(app, request.overrides),
                config_path=app.state.nai_artist_config_path,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"预设不存在: {exc.args[0]}") from exc

        if result["imageDataUrl"] is None:
            raise HTTPException(status_code=502, detail="图片生成失败")
        return result

    return app
