"""pivision 适配器注册表与工厂。

分层（借鉴 OpenMontage：流水线=能力接口，适配器=注册实例）：
- 注册表（仿 ToolRegistry）：`_IMAGE_ADAPTERS` / `_VISION_ADAPTERS` 为 {provider: 类} 映射。
  **新增 provider 只需：写适配器类 → 在此注册 → 配置段 `*_ADAPTER` 指过去；流水线代码零改动。**
- 工厂：get_pipeline_adapter(pipeline, provider, instance) 生图工厂；get_vision_adapter(provider, video) 视觉工厂。
- 配置汇总：get_config_summary() 仿 OpenMontage provider_menu_summary，供 Agent/客户端调用前预检（见 6.6）。
"""
import os
from typing import Dict, List, Optional

from .base import ImageAdapter
from .sensenova import SensenovaImageAdapter
from .openai_compat import OpenAICompatImageAdapter

# —— 生图适配器注册表（{provider: 类}，仿 OpenMontage ToolRegistry）——
_IMAGE_ADAPTERS = {
    "sensenova": SensenovaImageAdapter,
    "openai_compat": OpenAICompatImageAdapter,
}

# 流水线 → 默认 adapter 的读取变量名
_PIPELINES = {
    "GEN_IMAGE": "GEN_IMAGE_ADAPTER",
    "GEN_INFOGRAPH": "GEN_INFOGRAPH_ADAPTER",
    "EDIT_IMAGE": "EDIT_IMAGE_ADAPTER",
}


def get_pipeline_adapter(pipeline: str, provider: str = None, instance: str = None) -> ImageAdapter:
    """生图工厂。
    优先级：参数 provider → {PREFIX}_ADAPTER 环境变量 → "sensenova"。
    pipeline 取值：GEN_IMAGE / GEN_INFOGRAPH / EDIT_IMAGE。
    instance：主实例=None；命名实例="a"（须已在 {PREFIX}_INSTANCES 中声明，见 3.7）。
    """
    pipeline = pipeline.upper().strip()
    if pipeline not in _PIPELINES:
        raise ValueError(f"❌ 不支持的流水线：{pipeline}，可选：{list(_PIPELINES.keys())}")
    if provider is None:
        provider = (os.getenv(_PIPELINES[pipeline]) or "sensenova").strip()
    provider = provider.strip().lower()
    if provider not in _IMAGE_ADAPTERS:
        raise ValueError(f"❌ 不支持的 provider：{provider}，可选：{list(_IMAGE_ADAPTERS.keys())}")

    # 命名实例合法性校验：须在 {PREFIX}_INSTANCES 声明（大小写不敏感）
    if instance and instance.strip():
        declared = [x.strip().lower() for x in (os.getenv(f"{pipeline}_INSTANCES", "") or "").split(",") if x.strip()]
        if instance.strip().lower() not in declared:
            raise ValueError(
                f"❌ 不支持的命名实例：{instance}。{pipeline}_INSTANCES 已声明：{declared or '无'}（可选使用主实例）"
            )
        instance = instance.strip().lower()

    return _IMAGE_ADAPTERS[provider](prefix=pipeline, instance=instance)


# —— 视觉注册表 ——
from .vision_base import VisionAdapter
from .vision_openai import OpenAIVisionAdapter
from .vision_dots import DotsVisionAdapter

_VISION_ADAPTERS = {
    "openai": OpenAIVisionAdapter,
    "dots": DotsVisionAdapter,
}


def get_vision_adapter(provider: str = None, video: bool = False) -> VisionAdapter:
    """视觉工厂。video=True 时优先读 VISION_VIDEO_PROVIDER，未配置回退 VISION_PROVIDER。"""
    base = "VISION_VIDEO" if video else "VISION"
    if provider is None:
        provider = (os.getenv(f"{base}_PROVIDER") or os.getenv("VISION_PROVIDER") or "dots").strip()
    provider = provider.strip().lower()
    if provider not in _VISION_ADAPTERS:
        raise ValueError(f"❌ 不支持的 vision provider：{provider}，可选：{list(_VISION_ADAPTERS.keys())}")
    return _VISION_ADAPTERS[provider](prefix=base)


# —— 运行时配置汇总（借鉴 6 → OpenMontage provider_menu_summary，tool_registry.py:316-471）——

# 5 条流水线：工具名 → 配置前缀 → 是否生图（生图线走 get_pipeline_adapter，视觉线走 get_vision_adapter）
_PIPELINE_SPECS = [
    ("g-pic", "GEN_IMAGE", True),
    ("i-pic", "GEN_INFOGRAPH", True),
    ("p-pic", "EDIT_IMAGE", True),
    ("r-pic", "VISION", False),
    ("r-vid", "VISION_VIDEO", False),
]


def _append_pipeline_row(rows: List[dict], tool: str, prefix: str, is_image: bool,
                         instance: Optional[str]) -> None:
    """汇总单条（工具, 前缀, 实例）的配置状态。注意：绝不输出实际 Key / URL 中的鉴权信息。"""
    try:
        if is_image:
            adapter = get_pipeline_adapter(prefix, instance=instance)
            row = {
                "tool": tool,
                "prefix": f"{prefix}{'_' + instance.upper() if instance else ''}",
                "instance": instance,                    # None=主实例
                "adapter": adapter.get_adapter_name(),
                "capability": getattr(adapter, "capability", ""),
                "model": adapter._cfg("MODEL", ""),
                "base_url": adapter._cfg("BASE_URL", ""),
                "api_keys": "已配置" if adapter.get_status() == "AVAILABLE" else "未配置",
                "status": adapter.get_status(),          # AVAILABLE / UNAVAILABLE
            }
        else:
            adapter = get_vision_adapter(video=(prefix == "VISION_VIDEO"))
            row = {
                "tool": tool,
                "prefix": prefix,
                "instance": None,
                "adapter": adapter.get_adapter_name(),
                "capability": getattr(adapter, "capability", ""),
                "model": adapter._cfg("MODEL", ""),   # VISION_VIDEO_* 空则回退 VISION_MODEL
                "base_url": getattr(adapter, "_base_url", ""),
                "api_keys": "已配置" if getattr(adapter, "_api_key", "") else "未配置",
                "status": adapter.get_status(),
            }
    except ValueError as e:
        row = {"tool": tool, "prefix": prefix, "instance": instance,
               "adapter": "?", "model": "", "base_url": "",
               "api_keys": "-", "status": f"❌ {e}"}
    rows.append(row)


def get_config_summary() -> List[dict]:
    """运行时配置汇总：返回 5 条流水线 + 各命名实例的配置状态清单（N of M 已配置）。
    供 Agent / MCP 客户端在调用任意工具前预检（示例输出见 6.6）。"""
    rows: List[dict] = []
    for tool, prefix, is_image in _PIPELINE_SPECS:
        _append_pipeline_row(rows, tool, prefix, is_image, instance=None)
        if is_image:   # 生图流水线：追加声明过的命名实例
            declared = [x.strip().lower() for x in (os.getenv(f"{prefix}_INSTANCES", "") or "").split(",") if x.strip()]
            for inst in declared:
                _append_pipeline_row(rows, tool, prefix, is_image, instance=inst)
    return rows
