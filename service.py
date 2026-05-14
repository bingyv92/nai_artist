"""nai_artist Service：调用多种 NAI 中转接口生成图片。

当前支持三条显式 provider 生图链路：
- ikun 风格：OpenAI Chat Completions 兼容接口，返回 markdown 图片 URL
- idlecloud 风格：支持官方兼容端点或自有提交任务后轮询接口
- 7877 风格：/v1/chat/completions 接口，返回 markdown 图片 URL
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import os
import random
import re
import time
import zipfile
from typing import TYPE_CHECKING, Literal, cast

import httpx

from src.app.plugin_system.base import BaseService
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from .config import NaiArtistConfig

logger = get_logger("nai_artist")

ConfiguredApiProvider = Literal["auto", "ikun", "idlecloud", "7877"]
ApiProvider = Literal["ikun", "idlecloud", "7877"]

_MARKDOWN_IMAGE_URL_PATTERN = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
_SHORTHAND_IMAGE_URL_PATTERN = re.compile(r"!\((https?://[^)\s]+)\)")
_PLAIN_IMAGE_URL_PATTERN = re.compile(r"(https?://\S+?\.(?:png|jpg|jpeg|webp|gif)(?:\?\S*)?)", re.IGNORECASE)
_HTML_DOCUMENT_PATTERN = re.compile(r"^\s*(?:<!doctype html|<html\b)", re.IGNORECASE)


def _resolve_api_provider(config: "NaiArtistConfig") -> ApiProvider | None:
    """解析当前 provider；legacy auto 配置会被拒绝执行。"""
    provider = cast(ConfiguredApiProvider, config.api.provider)
    if provider == "auto":
        logger.warning(
            "NAI 生图 provider=auto 已停用；请在 config/plugins/nai_artist/config.toml 中手动指定 ikun、idlecloud 或 7877"
        )
        return None
    return cast(ApiProvider, provider)


def _build_chat_completions_url(base_url: str) -> str:
    """规范化 OpenAI-compatible chat/completions 地址。"""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1/chat/completions") or normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _build_idlecloud_api_root(base_url: str) -> str:
    """规范化 IdleCloud API 根路径。"""
    normalized = base_url.rstrip("/")
    for suffix in ("/generate_image", "/ai/generate-image"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    if not normalized.endswith("/api"):
        normalized = f"{normalized}/api"
    return normalized


def _is_idlecloud_compat_endpoint(base_url: str) -> bool:
    """判断是否显式配置了 IdleCloud 官方兼容生图端点。"""
    return base_url.rstrip("/").endswith("/api/ai/generate-image")


def _build_idlecloud_compat_payload(
    *,
    model: str,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
) -> dict[str, object]:
    """构造 IdleCloud 官方兼容端点所需的 NovelAI 原生请求体。"""
    is_v4_model = "diffusion-4" in model

    parameters: dict[str, object] = {
        "width": width,
        "height": height,
        "scale": 5.0,
        "steps": steps,
        "sampler": "k_euler",
        "seed": random.randint(0, 9_999_999_999),
        "n_samples": 1,
        "ucPreset": 1,
        "qualityToggle": False,
        "sm": False,
        "sm_dyn": False,
        "noise_schedule": "karras" if is_v4_model else "native",
    }

    if is_v4_model:
        parameters.update(
            {
                "params_version": 3,
                "cfg_rescale": 0,
                "autoSmea": False,
                "legacy": False,
                "legacy_v3_extend": False,
                "legacy_uc": False,
                "add_original_image": True,
                "controlnet_strength": 1,
                "dynamic_thresholding": False,
                "prefer_brownian": True,
                "normalize_reference_strength_multiple": True,
                "use_coords": False,
                "inpaintImg2ImgStrength": 1,
                "deliberate_euler_ancestral_bug": False,
                "skip_cfg_above_sigma": None,
                "characterPrompts": [],
                "stream": "msgpack",
                "v4_prompt": {
                    "caption": {
                        "base_caption": prompt,
                        "char_captions": [],
                    },
                    "use_coords": False,
                    "use_order": True,
                },
                "v4_negative_prompt": {
                    "caption": {
                        "base_caption": negative_prompt,
                        "char_captions": [],
                    },
                    "legacy_uc": False,
                },
                "negative_prompt": negative_prompt,
                "reference_image_multiple": [],
                "reference_information_extracted_multiple": [],
                "reference_strength_multiple": [],
            }
        )
    else:
        parameters["negative_prompt"] = negative_prompt

    payload: dict[str, object] = {
        "input": prompt,
        "model": model,
        "action": "generate",
        "parameters": parameters,
    }
    if is_v4_model:
        payload["use_new_shared_trial"] = True
    return payload


def _build_ikun_payload(
    *,
    model: str,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
) -> dict[str, object]:
    """构造 ikun 文档约定的 chat/completions 生图请求体。"""
    user_content = json.dumps(
        {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "size": [width, height],
        },
        ensure_ascii=False,
    )
    return {
        "model": model,
        "stream": False,
        "scale": 5.0,
        "cfg_rescale": 0.7,
        "width": width,
        "height": height,
        "sampler": "k_euler_ancestral",
        "noise_schedule": "karras" if "diffusion-4" in model else "native",
        "messages": [
            {"role": "user", "content": user_content},
        ],
    }


def _guess_image_format(image_bytes: bytes) -> str:
    """根据文件头猜测图片格式。"""
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "webp"
    return "png"


def _decode_image_base64(image_b64: str) -> tuple[bytes, str] | None:
    """解码裸 base64 图片数据，并推断格式。"""
    try:
        image_bytes = base64.b64decode(image_b64)
    except (binascii.Error, ValueError):
        return None
    return image_bytes, _guess_image_format(image_bytes)


def _extract_image_from_archive(raw_data: bytes) -> tuple[bytes, str] | None:
    """从二进制响应中提取图片；支持 ZIP 压缩包和裸图片。"""
    if not raw_data:
        return None

    if raw_data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw_data)) as zip_file:
                for info in zip_file.infolist():
                    if info.is_dir():
                        continue
                    image_bytes = zip_file.read(info)
                    if not image_bytes:
                        continue
                    return image_bytes, _guess_image_format(image_bytes)
        except (zipfile.BadZipFile, KeyError, OSError) as e:
            logger.warning(f"IdleCloud 官方兼容端点 ZIP 解压失败: {e}")
            return None

    return raw_data, _guess_image_format(raw_data)


def _extract_remote_image_url(content: str, provider_name: str) -> str | None:
    """从响应文本里提取首个远程图片 URL。

    兼容三种上游返回：
    - 标准 markdown: ![alt](url)
    - 畸形 shorthand: !(url)
    - 裸图片 URL
    """
    for pattern in (
        _MARKDOWN_IMAGE_URL_PATTERN,
        _SHORTHAND_IMAGE_URL_PATTERN,
        _PLAIN_IMAGE_URL_PATTERN,
    ):
        match = pattern.search(content)
        if match is not None:
            if pattern is not _MARKDOWN_IMAGE_URL_PATTERN:
                logger.warning(
                    f"{provider_name} 返回了非标准图片标记，已按兼容模式解析，content前200: {content[:200]}"
                )
            return match.group(1)
    return None


def _looks_like_html_document(raw_text: str, content_type: str) -> bool:
    """判断响应体是否看起来像前端 HTML 页面。"""
    normalized_type = content_type.lower()
    return "text/html" in normalized_type or _HTML_DOCUMENT_PATTERN.match(raw_text) is not None


def _build_html_response_hint(request_url: str) -> str:
    """根据请求 URL 生成更具体的 HTML 误配提示。"""
    if "/pricing/" in request_url or request_url.rstrip("/").endswith("/pricing"):
        return "检测到当前 URL 含 /pricing；这通常是前端页面路由，优先尝试移除 /pricing 后改用同域名根 /v1。"
    return "请检查 api.base_url 是否填成了网页地址、错误端口或错误的反向代理路径。"


def _extract_image_gateway_response_content(
    response: httpx.Response,
    request_url: str,
    provider_name: str,
) -> str | None:
    """提取图片网关响应中的文本内容。

    优先读取 OpenAI-compatible JSON；若上游直接返回纯文本 URL / markdown，
    则回退使用原始响应体继续解析。
    """
    raw_text = response.text.strip()
    content_type = ""
    headers = getattr(response, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        content_type = str(headers.get("content-type", "")).strip()

    try:
        body = response.json()
    except ValueError:
        if not raw_text:
            logger.warning(
                f"{provider_name} 返回了空响应体，content-type={content_type or '<unknown>'}"
            )
            return None

        if _looks_like_html_document(raw_text, content_type):
            logger.warning(
                f"{provider_name} 返回了 HTML 页面而不是 API 响应，"
                f"当前请求 URL={request_url}，content-type={content_type or '<unknown>'}；"
                f"{_build_html_response_hint(request_url)}"
                f"body前200: {raw_text[:200]}"
            )
            return None

        logger.warning(
            f"{provider_name} 返回了非 JSON 响应，已按纯文本继续解析，content-type={content_type or '<unknown>'}，body前200: {raw_text[:200]}"
        )
        return raw_text

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        if not raw_text:
            logger.warning(
                f"{provider_name} JSON 响应缺少标准 content 字段，content-type={content_type or '<unknown>'}"
            )
            return None

        logger.warning(
            f"{provider_name} JSON 响应缺少标准 content 字段，已回退解析原始响应，content-type={content_type or '<unknown>'}，body前200: {raw_text[:200]}"
        )
        return raw_text

    return content.strip() if isinstance(content, str) else raw_text or None


def _normalize_tag_list(raw_tags: str) -> list[str]:
    """将逗号分隔的 tag 字符串清洗为有序去重列表。

    支持全角逗号，保留原有顺序，并按不区分大小写去重。
    """
    normalized = raw_tags.replace("，", ",").replace("、", ",")
    seen: set[str] = set()
    result: list[str] = []
    for part in normalized.split(","):
        tag = part.strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return result


def _merge_prompt_tags(*tag_groups: str) -> str:
    """按顺序合并多组 tags，保留前者优先级并去重。"""


    merged: list[str] = []
    seen: set[str] = set()
    for group in tag_groups:


        for tag in _normalize_tag_list(group):
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(tag)
    return ", ".join(merged)


def build_final_prompt(
    prompt_tags: str,
    style_type: Literal["photo", "drawing"],
    config: "NaiArtistConfig",
) -> str:
    """构建最终发送给 NAI 的完整 prompt。

    - photo: style tags → fixed tags → 翻译结果
    - drawing: style tags → 翻译结果

    photo 模式下的角色人设已在翻译阶段作为同等级输入参与理解，
    这里不再机械拼接 base tags，避免在局部出镜、多人合影、第一人称构图时
    强行注入不相关的人设词。若需要稳定追加某些固定词条，则使用
    character.fixed_tags 在 photo 模式下固定拼接。衣柜系统的当前穿搭现改为只在
    photo 翻译阶段作为上下文参与理解，不再固定拼接到最终 prompt。drawing 模式
    仍只使用风格串和翻译结果。
    """
    preset = config.photo if style_type == "photo" else config.drawing
    fixed_tags = config.character.fixed_tags if style_type == "photo" else ""
    return _merge_prompt_tags(
        preset.style_tags,
        fixed_tags,
        prompt_tags,
    )


class NaiArtistService(BaseService):
    """NAI 生图核心 Service。

    负责组合提示词、发起 HTTP 请求、解析结果、管理本地缓存。
    """

    service_name: str = "nai_artist"
    service_description: str = "通过显式 provider 生成 NAI 图片"
    version: str = "1.0.0"

    async def generate_image(
        self,
        prompt_tags: str,
        style_type: Literal["photo", "drawing"],
        config: "NaiArtistConfig",
    ) -> str | None:
        """生成一张图片并返回 base64 字符串。

        Args:
            prompt_tags: 翻译好的 NAI tags（自然语言翻译结果）
            style_type: 风格类型，"photo" 或 "drawing"
            config: 插件配置实例
        Returns:
            base64 编码的图片字符串；失败时返回 None
        """
        preset = config.photo if style_type == "photo" else config.drawing

        # 角色人设在翻译阶段处理；photo 可额外固定拼接 character.fixed_tags。
        full_prompt = build_final_prompt(
            prompt_tags=prompt_tags,
            style_type=style_type,
            config=config,
        )

        provider = _resolve_api_provider(config)
        if provider is None:
            return None

        if provider == "idlecloud":
            if _is_idlecloud_compat_endpoint(config.api.base_url):
                result = await self._generate_image_via_idlecloud_compat(
                    prompt=full_prompt,
                    negative_prompt=config.character.negative_tags,
                    width=preset.width,
                    height=preset.height,
                    steps=preset.steps,
                    config=config,
                )
            else:
                result = await self._generate_image_via_idlecloud(
                    prompt=full_prompt,
                    negative_prompt=config.character.negative_tags,
                    width=preset.width,
                    height=preset.height,
                    steps=preset.steps,
                    config=config,
                )
        elif provider == "ikun":
            result = await self._generate_image_via_ikun(
                prompt=full_prompt,
                negative_prompt=config.character.negative_tags,
                width=preset.width,
                height=preset.height,
                config=config,
            )
        else:
            result = await self._generate_image_via_7877(
                prompt=full_prompt,
                negative_prompt=config.character.negative_tags,
                width=preset.width,
                height=preset.height,
                config=config,
            )

        if result is None:
            return None

        image_bytes, fmt = result
        b64_data = base64.b64encode(image_bytes).decode()
        self._save_cache(image_bytes, fmt, config)
        logger.debug(f"NAI 生图成功，provider={provider}，格式={fmt}，大小={len(image_bytes)}字节")
        return b64_data

    async def _generate_image_via_chat_completions_gateway(
        self,
        *,
        provider_name: str,
        payload: dict[str, object],
        config: "NaiArtistConfig",
    ) -> tuple[bytes, str] | None:
        """通过返回远程图片 URL 的 chat/completions 网关发起生图。"""

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api.api_key}",
        }
        url = _build_chat_completions_url(config.api.base_url)

        try:
            async with httpx.AsyncClient(timeout=config.api.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                content = _extract_image_gateway_response_content(resp, url, provider_name)
                if not content:
                    return None

                image_url = _extract_remote_image_url(content, provider_name)
                if image_url is None:
                    logger.warning(f"{provider_name} 响应中未找到图片 URL，content前200: {content[:200]}")
                    return None

                image_resp = await client.get(image_url, follow_redirects=True)
                image_resp.raise_for_status()
                image_bytes = image_resp.content
                if not image_bytes:
                    logger.warning(f"{provider_name} 图片下载结果为空: {image_url}")
                    return None

                return image_bytes, _guess_image_format(image_bytes)
        except httpx.HTTPStatusError as e:
            logger.warning(f"{provider_name} 生图 HTTP 错误: {e.response.status_code} — {e.response.text[:200]}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"{provider_name} 生图请求失败: {e}")
            return None
        except (ValueError, TypeError) as e:
            logger.warning(f"{provider_name} 生图响应解析失败: {e} — body前200: {resp.text[:200]}")
            return None

    async def _generate_image_via_ikun(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        config: "NaiArtistConfig",
    ) -> tuple[bytes, str] | None:
        """通过 ikun 文档约定的 chat/completions 接口发起生图。"""
        payload = _build_ikun_payload(
            model=config.api.model,
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
        )
        return await self._generate_image_via_chat_completions_gateway(
            provider_name="ikun",
            payload=payload,
            config=config,
        )

    async def _generate_image_via_7877(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        config: "NaiArtistConfig",
    ) -> tuple[bytes, str] | None:
        """通过 7877 风格接口发起生图，并下载返回的图片 URL。"""
        payload = {
            "model": config.api.model,
            "width": width,
            "height": height,
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "system", "content": f"Negative prompt: {negative_prompt}"},
            ],
        }
        return await self._generate_image_via_chat_completions_gateway(
            provider_name="7877",
            payload=payload,
            config=config,
        )

    async def _generate_image_via_idlecloud(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        config: "NaiArtistConfig",
    ) -> tuple[bytes, str] | None:
        """通过 IdleCloud 自有文生图接口提交任务并轮询结果。"""
        payload = {
            "model": config.api.model,
            "positivePrompt": prompt,
            "negativePrompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api.api_key}",
        }
        api_root = _build_idlecloud_api_root(config.api.base_url)
        submit_url = f"{api_root}/generate_image"

        try:
            async with httpx.AsyncClient(timeout=config.api.timeout) as client:
                submit_resp = await client.post(submit_url, json=payload, headers=headers)
                submit_resp.raise_for_status()
                submit_body = submit_resp.json()
                job_id = str(submit_body["job_id"]).strip()
                if not job_id:
                    logger.warning(f"IdleCloud 返回了空 job_id，body前200: {submit_resp.text[:200]}")
                    return None
                return await self._poll_idlecloud_result(
                    client=client,
                    api_root=api_root,
                    headers=headers,
                    job_id=job_id,
                    config=config,
                )
        except httpx.HTTPStatusError as e:
            logger.warning(f"IdleCloud 生图 HTTP 错误: {e.response.status_code} — {e.response.text[:200]}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"IdleCloud 生图请求失败: {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"IdleCloud 提交响应解析失败: {e}")
            return None

    async def _generate_image_via_idlecloud_compat(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        config: "NaiArtistConfig",
    ) -> tuple[bytes, str] | None:
        """通过 IdleCloud 官方兼容端点发起同步生图。"""
        payload = _build_idlecloud_compat_payload(
            model=config.api.model,
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api.api_key}",
        }
        url = config.api.base_url.rstrip("/")

        try:
            async with httpx.AsyncClient(timeout=config.api.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning(f"IdleCloud 官方兼容端点 HTTP 错误: {e.response.status_code} — {e.response.text[:200]}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"IdleCloud 官方兼容端点请求失败: {e}")
            return None

        decoded = _extract_image_from_archive(resp.content)
        if decoded is None:
            logger.warning(
                f"IdleCloud 官方兼容端点未返回可解析图片，body前32字节: {resp.content[:32].hex()}"
            )
            return None
        return decoded

    async def _poll_idlecloud_result(
        self,
        client: httpx.AsyncClient,
        api_root: str,
        headers: dict[str, str],
        job_id: str,
        config: "NaiArtistConfig",
    ) -> tuple[bytes, str] | None:
        """轮询 IdleCloud 任务结果，直到成功、失败或超时。"""
        result_url = f"{api_root}/get_result/{job_id}"
        deadline = time.monotonic() + max(config.api.timeout, config.api.poll_interval)

        while True:
            try:
                result_resp = await client.get(result_url, headers=headers)
                result_resp.raise_for_status()
                result_body = result_resp.json()
            except httpx.HTTPStatusError as e:
                logger.warning(f"IdleCloud 结果查询 HTTP 错误: {e.response.status_code} — {e.response.text[:200]}")
                return None
            except httpx.RequestError as e:
                logger.warning(f"IdleCloud 结果查询失败: {e}")
                return None
            except ValueError as e:
                logger.warning(f"IdleCloud 结果解析失败: {e} — body前200: {result_resp.text[:200]}")
                return None

            status = str(result_body.get("status", "")).lower()
            if status == "completed":
                image_b64 = result_body.get("image_base64")
                if isinstance(image_b64, str) and image_b64.strip():
                    decoded = _decode_image_base64(image_b64.strip())
                    if decoded is None:
                        logger.warning("IdleCloud 返回的 image_base64 不是有效图片数据")
                        return None
                    return decoded

                logger.warning(f"IdleCloud 任务已完成但未返回 image_base64，body前200: {result_resp.text[:200]}")
                return None

            if status == "failed":
                logger.warning(f"IdleCloud 生图任务失败: {result_body.get('error', 'unknown error')}")
                return None

            if time.monotonic() >= deadline:
                logger.warning(f"IdleCloud 生图轮询超时: job_id={job_id}")
                return None

            await asyncio.sleep(config.api.poll_interval)

    def _save_cache(self, image_bytes: bytes, fmt: str, config: "NaiArtistConfig") -> None:
        """将图片保存到本地缓存，并按 max_cache 限制清理最旧的文件。

        Args:
            image_bytes: 图片二进制数据
            fmt: 图片格式（如 "png"）
            config: 插件配置实例
        """
        cache_dir = config.storage.cache_dir
        os.makedirs(cache_dir, exist_ok=True)

        filename = f"{int(time.time() * 1000)}.{fmt}"
        filepath = os.path.join(cache_dir, filename)
        try:
            with open(filepath, "wb") as f:
                f.write(image_bytes)
        except OSError as e:
            logger.warning(f"NAI 缓存写入失败: {e}")
            return

        # 超出 max_cache 时删除最旧的文件
        max_cache = config.storage.max_cache
        if max_cache <= 0:
            return

        try:
            all_files = sorted(
                (
                    os.path.join(cache_dir, fn)
                    for fn in os.listdir(cache_dir)
                    if os.path.isfile(os.path.join(cache_dir, fn))
                ),
                key=os.path.getmtime,
            )
            while len(all_files) > max_cache:
                oldest = all_files.pop(0)
                try:
                    os.remove(oldest)
                    logger.debug(f"NAI 缓存清理: {oldest}")
                except OSError:
                    pass
        except OSError as e:
            logger.warning(f"NAI 缓存清理失败: {e}")
