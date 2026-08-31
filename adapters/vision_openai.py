"""通用 OpenAI 兼容视觉适配器（Bearer 鉴权）。
覆盖 gpt-4o / glm-4v / qwen-vl-plus / sensenova-6.8-flash-lite 等走 /v1/chat/completions 的模型。
r-pic 用前缀 VISION，r-vid 用前缀 VISION_VIDEO（未配置回退 VISION_*）。
"""
import os
import logging

from .vision_base import VisionAdapter

logger = logging.getLogger(__name__)


class OpenAIVisionAdapter(VisionAdapter):
    adapter_provider = "openai"                    # 注册表 key
    capability = "vision"
    capabilities = ["image_recognition", "video"]
    supports = {"video": True, "multi_key": False, "edit": False}

    def get_endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    def build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def build_payload(self, question, image_data_uri, model, max_tokens, video_url=None) -> dict:
        return {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": image_data_uri, "detail": "auto"}},
                ],
            }],
            "max_tokens": max_tokens,
        }

    def parse_response(self, resp_json: dict) -> str:
        try:
            content = resp_json["choices"][0]["message"]["content"]
            if not content:
                return "❌ 模型返回空内容。"
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict)]
                return "".join(parts) or "❌ 模型返回空内容。"
            return content
        except (KeyError, IndexError):
            logger.exception("响应结构异常: %s", str(resp_json)[:300])
            return f"❌ 响应结构异常：{str(resp_json)[:300]}"

    def get_key_env(self) -> str:
        return f"{self.prefix}_API_KEY"
