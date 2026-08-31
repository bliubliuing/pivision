"""通用 OpenAI 兼容生图适配器（新模型只配 env 即挂）。

定位：**只做协议，不做业务定制**（借鉴 4 → OpenMontage custom_* 二分法，Q5）。只实现 OpenAI 兼容
标准形态；非标准接口（异步提交/轮询、特殊鉴权头）一律走专属适配器（如 sensenova.py）。两者都以
同一套 `ImageAdapter` 接口注册进注册表，流水线无感。

- 文生图：POST {base}/images/generations（JSON）
- 图生图：POST {base}/images/edits（multipart form-data，files.image 传参考图字节）
- 响应默认 response_format="url"；鉴权 Bearer；单 Key 语义。
- 命名实例：instance="a" 时读 {PREFIX}_A_*，未配置字段回退主实例（见 3.7）。
- 报错脱敏：HTTP 错误 / 异常统一经 _safe_error（借鉴 5）。
"""
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

from .base import ImageAdapter
from utils import detect_image_format, _is_valid_image

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 300.0


class OpenAICompatImageAdapter(ImageAdapter):
    adapter_provider = "openai_compat"
    capability = "image_generation"
    capabilities = ["text_to_image", "image_to_image"]
    supports = {"n_multi": True, "edit": True, "multi_key": False, "video": False, "b64_json": True}

    def __init__(self, prefix: str = "GEN_IMAGE", instance: Optional[str] = None):
        super().__init__(prefix, instance)
        self._api_key = self._cfg("API_KEYS", "")   # 命名实例回退主实例 Key
        self._edit_url = self._cfg("EDIT_URL", "")

    # —— ImageAdapter 接口 ——
    def get_endpoint(self) -> str:
        base = (self._cfg("BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        return f"{base}/images/generations"

    def get_edit_endpoint(self) -> Optional[str]:
        if self._edit_url:
            return self._edit_url
        base = (self._cfg("BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        return f"{base}/images/edits"

    # 密钥环境变量名由基类 get_key_env 统一给出，此处不覆写

    def build_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}"}

    def build_payload(self, prompt: str, size: str, n: int) -> dict:
        return {
            "model": self._cfg("MODEL", "gpt-image-1"),
            "prompt": prompt,
            "size": size,
            "n": 1,
            "response_format": self._cfg("RESPONSE_FORMAT", "url"),
        }

    def extract_urls(self, resp_json: dict) -> List[str]:
        try:
            data = resp_json["data"][0]
            # url 优先（url 模式下 b64_json 不存在）
            return [data["url"] if "url" in data else data["b64_json"]]
        except (KeyError, IndexError, TypeError) as e:
            return [f"❌ 响应结构异常（缺少 data/0/url 或 b64_json）：{e}；原始：{str(resp_json)[:300]}"]

    def supports_edit(self) -> bool:
        return True

    def supports_multi_key(self) -> bool:
        return False

    def is_base64_result(self) -> bool:
        return self._cfg("RESPONSE_FORMAT", "url") == "b64_json"

    # —— 图生图（multipart）——
    def build_edit_request(self, prompt: str, size: str, image_bytes: bytes, image_filename: str):
        """构建 multipart 请求。返回 (data_dict, files_dict)；Content-Type 由 httpx 自动生成（含 boundary）。"""
        data = {
            "model": self._cfg("MODEL", "gpt-image-1"),
            "prompt": prompt,
            "size": size,
            "response_format": self._cfg("RESPONSE_FORMAT", "url"),
        }
        if not _is_valid_image(image_bytes):
            raise ValueError("参考图格式不支持，仅支持 PNG/JPG/WEBP/GIF")
        ext = detect_image_format(image_bytes)
        mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
        files = {"image": (image_filename or f"reference.{ext}", image_bytes, mime)}
        return data, files

    # —— 文生图 ——
    async def generate(self, prompt: str, size: str) -> str:
        if not self._api_key:
            return f"❌ 未配置 {self.get_key_env()}。"
        payload = self.build_payload(prompt, size, 1)
        headers = self.build_headers()
        headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.post(self.get_endpoint(), json=payload, headers=headers)
                resp.raise_for_status()
            urls = self.extract_urls(resp.json())
            return urls[0]
        except httpx.HTTPStatusError as e:
            return self._http_error(e)
        except Exception as e:
            logger.exception("请求异常: %s", self._safe_error(str(e)))
            return f"❌ 请求异常：{self._safe_error(str(e))}"

    # —— 图生图 ——
    async def generate_edit(self, prompt: str, size: str, image_path: str) -> str:
        if not self._api_key:
            return f"❌ 未配置 {self.get_key_env()}。"
        img_path = Path(image_path)
        if not img_path.exists():
            return f"❌ 参考图不存在：{self._safe_error(image_path)}"
        try:
            image_bytes = img_path.read_bytes()
        except OSError as e:
            return f"❌ 读取参考图失败：{self._safe_error(str(e))}"
        try:
            data, files = self.build_edit_request(prompt, size, image_bytes, img_path.name)
        except ValueError as e:
            return f"❌ {self._safe_error(str(e))}"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    self.get_edit_endpoint(), data=data, files=files,
                    headers={"Authorization": f"Bearer {self._api_key}"},  # 勿设 Content-Type
                )
                resp.raise_for_status()
            urls = self.extract_urls(resp.json())
            return urls[0]
        except httpx.HTTPStatusError as e:
            return self._http_error(e)
        except Exception as e:
            logger.exception("请求异常: %s", self._safe_error(str(e)))
            return f"❌ 请求异常：{self._safe_error(str(e))}"

    def _http_error(self, e: httpx.HTTPStatusError) -> str:
        status = e.response.status_code
        try:
            detail = str(e.response.json())[:300]
        except Exception:
            detail = e.response.text[:300]
        # 错误体可能回显 Key（鉴权失败详情），统一脱敏后再返回
        return f"❌ HTTP {status}：{self._safe_error(detail)}"
