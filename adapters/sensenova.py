"""商汤（日日新）生图适配器。
- 文生图：POST {base}/v1/images/generations
- 图生图：POST {base}/v1/images/edits（JSON 请求体，参考图以 data:image/*;base64, Data-URI 传入）
- 多 Key 池：按「前缀#实例名」隔离的类级池 + 轮询 + asyncio.Lock，401/403/429 自动换 Key 重试
- 能力声明：capability="image_generation"，capabilities=["text_to_image", "image_to_image"]（借鉴 3）
- 报错脱敏：所有 HTTP 错误 / 异常 / 校验失败信息统一经 _safe_error（借鉴 5）
API 参数见官方文档存档 04/05/06（sensenova-官方文档-cb）。
"""
import asyncio
import itertools
import logging
import os
from pathlib import Path
from typing import List, Optional

import httpx

from .base import ImageAdapter
from utils import encode_image_to_data_uri

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 180.0

# u1.5-lite（04/05 文生图/图生图）与 u1-fast（06 信息图）共用的 2K 尺寸常量
# u1-fast 官方 11 种宽高比；u1.5-lite 支持 32 倍数规则，2752x1536 等 2K 常量均合法
SENSENOVA_VALID_SIZES = {
    "1664x2496", "2496x1664", "1760x2368", "2368x1760",
    "1824x2272", "2272x1824", "2048x2048", "2752x1536",
    "1536x2752", "1696x2368", "2368x1696",
}


class _KeyPool:
    """单条流水线 + 实例的密钥池：按「前缀#实例」隔离 + 线程安全轮询。"""

    def __init__(self, keys: List[str]):
        self.all_keys = keys
        self._cycle = itertools.cycle(keys)
        self._lock = asyncio.Lock()

    async def next_key(self) -> str:
        async with self._lock:
            return next(self._cycle)


class SensenovaImageAdapter(ImageAdapter):
    """商汤生图适配器：supports_edit()=True，response_format="url" 走 extract_urls()。
    命名实例（instance="a"）时 Key 池 key = "GEN_IMAGE#a"，与主实例 "GEN_IMAGE" 完全隔离。"""

    adapter_provider = "sensenova"                       # 注册表 key
    capability = "image_generation"                      # 能力族（借鉴 3）
    capabilities = ["text_to_image", "image_to_image"]   # 细分能力（借鉴 3）
    supports = {"n_multi": True, "edit": True, "multi_key": True, "video": False}  # 能力细节（借鉴 3）

    # 类级多 Key 池：{(prefix, instance): _KeyPool}，跨调用共享
    _pools: dict = {}
    VALID_SIZES = SENSENOVA_VALID_SIZES
    # 流水线级代码默认值（§3.9 回退链末层「代码默认值」；§6.4/§6.6 权威断言依赖）：
    # 信息图（GEN_INFOGRAPH）默认 u1-fast（官方 06 信息图），其余生图线默认 u1.5-lite
    _DEFAULT_MODELS = {
        "GEN_IMAGE": "sensenova-u1.5-lite",
        "GEN_INFOGRAPH": "sensenova-u1-fast",
        "EDIT_IMAGE": "sensenova-u1.5-lite",
    }
    _DEFAULT_BASE_URL = "https://token.sensenova.cn/v1"

    def __init__(self, prefix: str = "GEN_IMAGE", instance: Optional[str] = None):
        super().__init__(prefix, instance)
        self._ensure_pool()

    # —— 配置取值覆写：实例 → 主实例 → 代码默认值（§3.9 回退链）——
    # 基类 _cfg 只读环境变量（default 由调用点传入），不承载「代码默认值」层；
    # §6.4 断言①（main._cfg("MODEL")/inst_a._cfg("BASE_URL")）与 §6.6 预期输出
    # （i-pic 默认 u1-fast）依赖此层，故在此补齐（方案 §4.2/§4.3 缺实现，见缺口登记）。
    def _cfg(self, suffix: str, default: str = "") -> str:
        val = super()._cfg(suffix, "")          # 实例 → 主实例（不带 default，避免吞掉代码默认值）
        if val:
            return val
        if suffix == "MODEL":
            return self._DEFAULT_MODELS.get(self.prefix, "sensenova-u1.5-lite")
        if suffix == "BASE_URL":
            return self._DEFAULT_BASE_URL
        return default

    # —— 多 Key 池（按「前缀#实例」隔离）——
    def _pool_key(self) -> str:
        return f"{self.prefix}#{self.instance}" if self.instance else self.prefix   # GEN_IMAGE / GEN_IMAGE#a

    def _ensure_pool(self) -> None:
        key = self._pool_key()
        if key in self._pools:
            return
        keys_str = self._cfg("API_KEYS", "")            # 命名实例自动回退主实例变量
        keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        self._pools[key] = _KeyPool(keys)

    @property
    def _pool(self) -> Optional[_KeyPool]:
        return self._pools.get(self._pool_key())

    def has_keys(self) -> bool:
        return bool(self._pool and self._pool.all_keys)

    # —— ImageAdapter 接口 ——
    def get_endpoint(self) -> str:
        base = (self._cfg("BASE_URL", "https://token.sensenova.cn/v1")).rstrip("/")
        return f"{base}/images/generations"

    def get_edit_endpoint(self) -> Optional[str]:
        base = (self._cfg("BASE_URL", "https://token.sensenova.cn/v1")).rstrip("/")
        return f"{base}/images/edits"

    # 密钥环境变量名（主实例/命名实例）由基类 get_key_env 统一给出，此处不覆写

    def build_headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def build_payload(self, prompt: str, size: str, n: int) -> dict:
        return {
            "model": self._cfg("MODEL", "sensenova-u1.5-lite"),
            "prompt": prompt,
            "size": size,
            "n": 1,                    # 官方文档：n 仅支持 1
            "watermark": False,        # 官方文档：去水印免费公测；显式传参避免默认值变更影响业务
            "response_format": "url",  # 走 extract_urls()
            "prompt_extend": True,     # 提示词自动润色
        }

    def build_edit_payload(self, prompt: str, size: str, image_data_uri: str) -> dict:
        return {
            "model": self._cfg("MODEL", "sensenova-u1.5-lite"),  # 图生图模型由 EDIT_IMAGE_MODEL 独立控制
            "images": [{"image_url": image_data_uri}],           # 官方：仅支持 data:image/*;base64 前缀 Data-URI
            "prompt": prompt,
            "n": 1,
            "size": size,
            "watermark": False,
            "prompt_extend": True,
            "response_format": "url",
        }

    def extract_urls(self, resp_json: dict) -> List[str]:
        try:
            return [resp_json["data"][0]["url"]]
        except (KeyError, IndexError, TypeError) as e:
            return [f"❌ 响应结构异常（缺少 data/0/url）：{e}；原始：{str(resp_json)[:300]}"]

    def validate_size(self, size: str):
        if size not in self.VALID_SIZES:
            return False, f"❌ 不支持的尺寸：{size}。可选：{', '.join(sorted(self.VALID_SIZES))}"
        return True, ""

    def supports_edit(self) -> bool:
        return True

    def supports_multi_key(self) -> bool:
        return True

    def is_base64_result(self) -> bool:
        return False

    # —— 文生图 ——
    async def generate(self, prompt: str, size: str) -> str:
        """单张文生图。返回图片 URL 或 ❌ 错误字符串。"""
        if not self.has_keys():
            return f"❌ 未配置 {self.get_key_env()}。"
        valid, err = self.validate_size(size)
        if not valid:
            return err
        payload = self.build_payload(prompt, size, 1)
        return await self._post_with_retry(payload)

    # —— 图生图 ——
    async def generate_edit(self, prompt: str, size: str, image_path: str) -> str:
        """图生图（JSON + base64 Data-URI）。返回图片 URL 或 ❌ 错误字符串。"""
        if not self.has_keys():
            return f"❌ 未配置 {self.get_key_env()}。"
        valid, err = self.validate_size(size)
        if not valid:
            return err

        img_path = Path(image_path)
        if not img_path.exists():
            return f"❌ 参考图不存在：{self._safe_error(image_path)}"
        try:
            image_bytes = img_path.read_bytes()
        except OSError as e:
            return f"❌ 读取参考图失败：{self._safe_error(str(e))}"
        data_uri = encode_image_to_data_uri(image_bytes)
        if data_uri is None:
            return "❌ 不支持的图片格式，仅支持 PNG/JPG/WEBP/GIF。"

        payload = self.build_edit_payload(prompt, size, data_uri)
        return await self._post_with_retry(payload, edit=True)

    # —— 多 Key 池核心：401/403/429 自动换 Key 重试 ——
    async def _post_with_retry(self, payload: dict, edit: bool = False) -> str:
        pool = self._pool
        if not pool:
            return f"❌ 未配置 {self.get_key_env()}。"
        tried: set = set()
        max_attempts = len(pool.all_keys)
        endpoint = self.get_edit_endpoint() if edit else self.get_endpoint()

        for _ in range(max_attempts):
            current_key = await pool.next_key()
            if current_key in tried:
                continue
            tried.add(current_key)

            headers = self.build_headers()
            headers["Authorization"] = f"Bearer {current_key}"
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    resp = await client.post(endpoint, json=payload, headers=headers)
                    resp.raise_for_status()
                urls = self.extract_urls(resp.json())
                if urls[0].startswith("❌"):
                    return urls[0]
                return urls[0]
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                # 仅 401/403/429（鉴权/限流类）换 Key 重试；其余请求级错误换 Key 必然失败
                if status in (401, 403, 429):
                    logger.warning("%s 调用 HTTP %d，切换 Key 重试", self.get_key_env(), status)
                    continue
                if 400 <= status < 500:
                    # 请求级错误体可能回显 Key（如鉴权失败详情含 Authorization），统一脱敏
                    return f"❌ HTTP {status}：{self._safe_error(e.response.text[:300])}"
                return f"❌ 服务器错误：{self._safe_error(e.response.text[:300])}"
            except httpx.RequestError as e:
                logger.exception("请求异常: %s", self._safe_error(str(e)))
                return f"❌ 请求异常：{self._safe_error(str(e))}"
            except Exception as e:
                logger.exception("请求异常: %s", self._safe_error(str(e)))
                return f"❌ 请求异常：{self._safe_error(str(e))}"

        return f"❌ 所有 Key 均调用失败（401/403/429）。"
