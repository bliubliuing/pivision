"""视觉识别基类（r-pic / r-vid 共用）。

前缀机制：r-pic 绑定前缀 "VISION"，r-vid 绑定前缀 "VISION_VIDEO"。
"VISION_VIDEO_" 变量未配置（空串）时自动回退读 "VISION_*"，一套配置两种用法。

能力声明（借鉴 3 → OpenMontage BaseTool 类属性）：
capability="vision"（能力族），capabilities=["image_recognition", "video"]（细分能力；
recognize() 的 video_url 分支即视频识别，故全部视觉适配器默认声明 video），supports 见子类。
get_status() 配置完整性自检：API_KEY 非空 → AVAILABLE（仿 custom_* get_status）。

K2（方案 A）：__init__ 接收 prefix 参数并赋值 self.prefix 后再读配置，
保证 §4.8 视觉工厂 _VISION_ADAPTERS[provider](prefix=base) 关键字调用不抛 TypeError。
"""
from abc import ABC, abstractmethod
import logging
import os
from typing import Dict, List
from pathlib import Path

import httpx

from utils import encode_image_to_data_uri

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 20 * 1024 * 1024     # 单张图片 ≤20MB
TIMEOUT_SECONDS = 180.0               # 视频解析 TTFT 较长，建议按需调大
FALLBACK_PREFIX = "VISION"


class VisionAdapter(ABC):
    capability: str = "vision"
    capabilities: List[str] = ["image_recognition", "video"]
    supports: Dict = {"video": True}
    adapter_provider: str = ""

    prefix: str = "VISION"

    def __init__(self, prefix: str = "VISION"):
        self.prefix = prefix
        self._api_key = self._cfg("API_KEY", "")
        self._base_url = (self._cfg("BASE_URL", "https://api.openai.com/v1")).rstrip("/")

    def _cfg(self, suffix: str, default: str = "") -> str:
        """读 {prefix}_{suffix}；未配置时回退读 VISION_{suffix}。"""
        val = os.getenv(f"{self.prefix}_{suffix}")
        if not val and self.prefix != FALLBACK_PREFIX:
            val = os.getenv(f"{FALLBACK_PREFIX}_{suffix}")
        return val or default

    def _safe_error(self, msg: str) -> str:
        """错误脱敏（借鉴 5）：错误文本中出现的 API Key 片段替换为 [redacted]。"""
        if not msg or not self._api_key or len(self._api_key) < 6:
            return msg
        return msg.replace(self._api_key, "[redacted]")

    def get_status(self) -> str:
        """配置完整性自检：视觉线 API_KEY 非空 → AVAILABLE。"""
        return "AVAILABLE" if self._api_key else "UNAVAILABLE"

    @abstractmethod
    def get_endpoint(self) -> str: ...

    @abstractmethod
    def build_headers(self) -> dict: ...

    @abstractmethod
    def build_payload(self, question: str, image_data_uri: str,
                      model: str, max_tokens: int, video_url: str = None) -> dict: ...

    @abstractmethod
    def parse_response(self, resp_json: dict) -> str: ...

    @abstractmethod
    def get_key_env(self) -> str: ...

    def get_adapter_name(self) -> str:
        # P4-CA-1b 修正：返回 adapter_provider（openai/dots），而非类名派生值（openaivision）
        return self.adapter_provider

    def _video_payload_extra(self) -> dict:
        """视频请求体附加字段（有差异的适配器覆写）。"""
        return {}

    def _build_video_payload(self, question: str, video_url: str,
                             model: str, max_tokens: int) -> dict:
        """视频请求体：content 为 video_url + text 内容块数组（video_url 在前）。"""
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video_url}},
                    {"type": "text", "text": question},
                ],
            }],
            "max_tokens": max_tokens,
        }
        payload.update(self._video_payload_extra())
        return payload

    async def recognize(self, image_path: str, question: str, model: str,
                        max_tokens: int, video_url: str = "") -> str:
        """识别图片/视频（基类具体方法）。
        video_url 非空走视频直链分支（不读本地文件、不做图片格式校验）。
        """
        if not self._api_key:
            return f"❌ 未配置 {self.get_key_env()}。"
        if max_tokens < 1:
            return "❌ max_tokens 必须 ≥ 1。"

        try:
            if video_url:
                headers = self.build_headers()
                payload = self._build_video_payload(question, video_url, model, max_tokens)
            else:
                p = Path(image_path)
                if not p.exists():
                    return f"❌ 图片不存在：{image_path}"
                if not p.is_file():
                    return f"❌ 路径不是文件：{image_path}"
                img_bytes = p.read_bytes()
                if len(img_bytes) > MAX_IMAGE_SIZE:
                    size_mb = len(img_bytes) / (1024 * 1024)
                    return f"❌ 图片过大（{size_mb:.1f}MB），请压缩至 20MB 以内。"
                image_data_uri = encode_image_to_data_uri(img_bytes)
                if image_data_uri is None:
                    return "❌ 不支持的图片格式，仅支持 PNG/JPG/WEBP/GIF。"
                headers = self.build_headers()
                payload = self.build_payload(question, image_data_uri, model, max_tokens)
        except OSError as e:
            logger.exception("读取图片失败")
            return f"❌ 读取图片失败：{self._safe_error(str(e))}"
        except MemoryError:
            return "❌ 内存不足，图片过大。"

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
                resp = await client.post(self.get_endpoint(), headers=headers, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                status = resp.status_code
                detail = self._safe_error(resp.text[:500])
                if status == 401:
                    return f"❌ 401 鉴权失败：{detail}"
                if status == 429:
                    return f"❌ 429 限流：{detail}"
                return f"❌ HTTP {status}：{detail}"
            try:
                resp_json = resp.json()
            except Exception:
                return f"❌ 响应非 JSON 格式：{self._safe_error(resp.text[:300])}"
            return self.parse_response(resp_json)
        except httpx.RequestError as e:
            logger.exception("请求异常")
            return f"❌ 请求异常：{self._safe_error(str(e))}"
        except Exception as e:
            logger.exception("未知异常")
            return f"❌ 未知异常：{self._safe_error(str(e))}"
