"""生图适配器抽象基类。

分层与注册机制（借鉴 OpenMontage：流水线=能力接口，适配器=注册实例）：
- 流水线 = 能力接口：g-pic/i-pic/p-pic 各自对应一条生图流水线前缀
  （GEN_IMAGE / GEN_INFOGRAPH / EDIT_IMAGE）。流水线只调用能力方法
  （generate / generate_edit / validate_size），不感知后端细节。
- 适配器 = 注册实例：每个适配器类用「能力声明字段」（capability / capabilities / supports）
  声明自己的能力，由 adapters/__init__.py 的注册表（{provider: 类}）实例化后挂到流水线下。
  新增 provider 只改注册表 + 适配器类，不改任何流水线代码。

命名实例机制（借鉴 OpenMontage 多实例注册扩展）：
- 每条流水线除主实例（无后缀默认变量）外可声明多个命名实例
  （如 GEN_IMAGE_INSTANCES=a,b）。命名实例未配置的字段自动回退主实例——
  _cfg 优先级：{prefix}_{instance}_{suffix} → {prefix}_{suffix} → 默认值。

统一返回结构契约（借鉴 OpenMontage ToolResult 的 success/data/model 结构）：
- 执行层（generate / generate_edit / recognize / 批量派发）统一返回 str：
  「✅ 前缀成功 / ❌ 前缀失败（带原因）」，MCP 客户端可按前缀快速判定（见 §2.2）。
- 结构化内部返回（如有）统一契约：
    {"success": bool, "data": ..., "model": str}
  其中 model=实际命中模型名；工具层负责把 data 格式化为带 ✅/❌ 前缀的 str。
  当前实现执行层已直接返回格式化 str（含成功标记与原因），保持契约收敛在「str + 前缀」。
"""
from abc import ABC, abstractmethod
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ImageAdapter(ABC):
    # —— 能力声明（仿 OpenMontage BaseTool 类属性，base_tool.py:251-262）——
    capability: str = "image_generation"   # 能力族：image_generation / vision
    capabilities: List[str] = []           # 细分能力：text_to_image / image_to_image / ...
    supports: Dict = {}                    # 能力细节 dict（如 {"n_multi": True, "edit": True}）
    adapter_provider: str = ""             # 供应商标识（= 注册表 key，如 sensenova / openai_compat）

    prefix: str = "GEN_IMAGE"   # 流水线前缀（子类 __init__ 中赋值）
    instance: Optional[str] = None   # None=主实例；非空=命名实例名（如 "a"，见 3.7）

    def __init__(self, prefix: str = "GEN_IMAGE", instance: Optional[str] = None):
        self.prefix = prefix
        self.instance = (instance or "").strip().lower() or None
        self.instance_suffix = f"_{self.instance.upper()}" if self.instance else ""

    def _cfg(self, suffix: str, default: str = "") -> str:
        """读配置：命名实例优先读 {prefix}_{INSTANCE}_{suffix}，空则回退 {prefix}_{suffix}。
        注意：instance 已小写归一，env 变量名用 instance_suffix（大写）拼接，与文档
        （如 GEN_IMAGE_A_MODEL）一致。"""
        if self.instance:
            val = os.getenv(f"{self.prefix}{self.instance_suffix}_{suffix}")
            if val:
                return val
        return os.getenv(f"{self.prefix}_{suffix}", default)

    def _get_secret_values(self) -> List[str]:
        """收集当前实例的密钥值（零硬编码，只从配置段读取），供脱敏使用。"""
        vals = []
        for k in (self._cfg("API_KEYS", "") or "").split(","):
            k = k.strip()
            if k:
                vals.append(k)
        return vals

    def _safe_error(self, msg: str) -> str:
        """错误脱敏（仿 OpenMontage custom_image._safe_error）：将错误文本中出现的
        API Key 片段替换为 [redacted]，防止 Key 泄漏进日志 / MCP 返回。"""
        if not msg:
            return msg
        safe = msg
        for s in self._get_secret_values():
            if s and len(s) >= 6:      # 仅脱敏有长度的密钥，避免误伤短串
                safe = safe.replace(s, "[redacted]")
        return safe

    def get_status(self) -> str:
        """配置完整性自检（仿 custom_*：URL+KEY 齐全才 AVAILABLE，custom_image.py:119-126）。
        生图线判定：API_KEYS 非空 → AVAILABLE。"""
        if self.supports_multi_key():
            return "AVAILABLE" if self.has_keys() else "UNAVAILABLE"
        return "AVAILABLE" if getattr(self, "_api_key", "") else "UNAVAILABLE"

    def get_display_name(self) -> str:
        """展示名：适配器名（主实例）或 适配器名[实例 X]（命名实例），用于工具输出。"""
        return f"{self.get_adapter_name()} [实例 {self.instance}]" if self.instance else self.get_adapter_name()

    @abstractmethod
    def get_endpoint(self) -> str:
        """文生图 API URL"""
        ...

    @abstractmethod
    def build_headers(self) -> dict:
        """鉴权请求头"""
        ...

    @abstractmethod
    def build_payload(self, prompt: str, size: str, n: int) -> dict:
        """文生图请求体"""
        ...

    @abstractmethod
    def extract_urls(self, resp_json: dict) -> List[str]:
        """从响应提取图片来源（URL 或 base64）"""
        ...

    def validate_size(self, size: str) -> Tuple[bool, str]:
        return True, ""

    def supports_edit(self) -> bool:
        return False

    def get_edit_endpoint(self) -> Optional[str]:
        return None

    def build_edit_payload(self, prompt: str, size: str, image_data_uri: str) -> dict:
        """图生图 JSON 请求体（不支持的适配器抛 NotImplementedError）"""
        raise NotImplementedError(f"{self.get_adapter_name()} 不支持图生图")

    def supports_multi_key(self) -> bool:
        return False

    def has_keys(self) -> bool:
        return False

    def is_base64_result(self) -> bool:
        return False

    def get_adapter_name(self) -> str:
        # P4-CA-1 修正：返回 adapter_provider（sensenova/openai_compat/openai），而非类名派生值（sensenovaimage）
        return self.adapter_provider

    def get_pipeline_name(self) -> str:
        return (self.prefix + self.instance_suffix).lower()

    def get_key_env(self) -> str:
        """密钥环境变量名：主实例读 {prefix}_API_KEYS；命名实例读 {prefix}_{X}_API_KEYS（大写）。"""
        prefix = self.prefix if not self.instance else f"{self.prefix}{self.instance_suffix}"
        return f"{prefix}_API_KEYS"
