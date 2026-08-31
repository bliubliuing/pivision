"""dots 视觉适配器（小红书 Dots Studio，模型示例 dots3-note-prev）。
差异点：api-key 头鉴权（非 Bearer）、关闭深度思考、图片 detail=medium、视频显式 stream=false。
禁止写入日志/仓库的是完整 Key；基址默认 https://note3-prev-api.askdiandian.com/v1。
"""
import os
import logging

from .vision_openai import OpenAIVisionAdapter

logger = logging.getLogger(__name__)


class DotsVisionAdapter(OpenAIVisionAdapter):
    adapter_provider = "dots"
    capability = "vision"
    capabilities = ["image_recognition", "video"]
    supports = {"video": True, "enable_thinking": False, "multi_key": False, "edit": False}

    def __init__(self, prefix: str = "VISION"):
        self.prefix = prefix
        self._api_key = self._cfg("API_KEY", "")
        self._base_url = (
            self._cfg("BASE_URL", "https://note3-prev-api.askdiandian.com/v1")
        ).rstrip("/")

    # —— 配置取值覆写：VISION_VIDEO_* → VISION_* → 代码默认值（§3.9 回退链）——
    # 基座 vision_base._cfg 只回退前缀不承载「代码默认值」层；§6.4 断言④
    # （v._cfg("BASE_URL") == note3-prev 默认）依赖此层，故在此补齐（方案缺实现，见缺口登记）。
    def _cfg(self, suffix: str, default: str = "") -> str:
        val = super()._cfg(suffix, default)
        if val:
            return val
        if suffix == "BASE_URL":
            return "https://note3-prev-api.askdiandian.com/v1"
        return default

    def get_adapter_name(self) -> str:
        return "dots"

    def build_headers(self) -> dict:
        return {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

    def build_payload(self, question, image_data_uri, model, max_tokens, video_url=None) -> dict:
        return {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {
                        "url": image_data_uri, "detail": "medium",
                    }},
                ],
            }],
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }

    def _video_payload_extra(self) -> dict:
        return {"stream": False, "chat_template_kwargs": {"enable_thinking": False}}
