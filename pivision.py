"""
pivision — MCP 视觉与图像生成服务（v6.1）。

服务：FastMCP("pivision")
工具：r-pic / r-vid 视觉；g-pic / i-pic / p-pic 生图；b-gen 批量引擎
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 加载 .env（所有项目 import 之前注入环境变量）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    print("警告：未安装 python-dotenv，.env 不会自动加载", file=sys.stderr)

from mcp.server.fastmcp import FastMCP
from adapters import get_pipeline_adapter, get_vision_adapter
from utils import (
    find_nearest_size,
    generate_filename,
    log_request,
    parse_batch_file,
    read_text_safely,
    resize_image,
    save_image,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROGRESS_FILE = SCRIPT_DIR / ".bgen_progress.json"
RESULT_FILE = SCRIPT_DIR / "pivision_batch_results.txt"

mcp = FastMCP("pivision", log_level="ERROR")


# ---------- 流水线启用开关（v6.1.1 增补）----------
def _pipeline_enabled(prefix: str) -> bool:
    """流水线启用判定：{PREFIX}_ENABLED 显式为 false → 停用；不配置 / 留空 / true 视为启用。
    视频线（VISION_VIDEO）未配置 VISION_VIDEO_ENABLED 时回退 VISION_ENABLED（与 VISION_VIDEO_*
    其他变量回退 VISION_* 一致）；显式配置 false 表示视频流水线单独停用。"""
    if prefix == "VISION_VIDEO":
        return (os.getenv("VISION_VIDEO_ENABLED") or os.getenv("VISION_ENABLED") or "").strip().lower() != "false"
    return (os.getenv(f"{prefix}_ENABLED") or "").strip().lower() != "false"


def _disabled_msg(prefix: str) -> str:
    """停用提示（统一格式）：供各工具开头与 b-gen 派发使用。"""
    return f"❌ 该流水线已停用（{prefix}_ENABLED=false）"


# ---------- 工具 1：r-pic（图片识别）----------
@mcp.tool(name="r-pic")
async def r_pic(
    image_path: str,
    question: str = "请详细描述这张图片的内容。",
    provider: str = None,
    model: str = None,
    max_tokens: int = 1024,
) -> str:
    """识别本地图片（PNG/JPG/WEBP/GIF），发送给视觉模型，返回文本描述或问答结果。

    参数：
      image_path: 本地图片路径，必填
      question: 对图片的提问/指令（默认"请详细描述这张图片的内容。"）
      provider: 视觉后端（openai/dots，默认读 VISION_PROVIDER→dots）
      model: 视觉模型（默认读 VISION_MODEL→dots3-note-prev）
      max_tokens: 返回文本上限（默认 1024）
    """
    if not _pipeline_enabled("VISION"):
        return _disabled_msg("VISION")
    if not image_path:
        return "❌ image_path 不能为空。"
    if model is None:
        model = os.getenv("VISION_MODEL") or "dots3-note-prev"
    try:
        adapter = get_vision_adapter(provider, video=False)
    except ValueError as e:
        return str(e)

    result = await adapter.recognize(image_path, question, model, max_tokens)
    if result.startswith("❌"):
        return result
    return (
        f"✅ 识别完成（模型：{model}）\n"
        f"图片：{Path(image_path).name}\n"
        f"提问：{question}\n"
        f"识别结果：\n{result}"
    )


# ---------- 工具 2：r-vid（视频识别）----------
@mcp.tool(name="r-vid")
async def r_vid(
    video_url: str,
    question: str = "请详细描述这段视频的内容。",
    provider: str = None,
    model: str = None,
    max_tokens: int = 8192,
) -> str:
    """解析视频直链（http/https，视觉模型服务端可直接访问），返回文本描述或问答结果。

    参数：
      video_url: 视频直链，必填
      question: 对视频的提问/指令（默认"请详细描述这段视频的内容。"）
      provider: 视觉后端，空则读 VISION_VIDEO_PROVIDER→回退 VISION_PROVIDER
      model: 视觉模型，空则读 VISION_VIDEO_MODEL→回退 VISION_MODEL（默认 dots3-note-prev）
      max_tokens: 返回文本上限（默认 8192；视频解析建议 ≥8192，避免输出截断）
    """
    if not _pipeline_enabled("VISION_VIDEO"):   # K1 闭环点：§2 契约要求开头检查视频线开关
        return _disabled_msg("VISION_VIDEO")
    if not video_url:
        return "❌ video_url 不能为空。"
    if model is None:
        model = os.getenv("VISION_VIDEO_MODEL") or os.getenv("VISION_MODEL") or "dots3-note-prev"
    try:
        adapter = get_vision_adapter(provider, video=True)
    except ValueError as e:
        return str(e)

    result = await adapter.recognize("", question, model, max_tokens, video_url=video_url)
    if result.startswith("❌"):
        return result
    return (
        f"✅ 视频解析完成（模型：{model}）\n"
        f"视频：{video_url}\n"
        f"提问：{question}\n"
        f"解析结果：\n{result}"
    )


# ---------- 生图公共执行体（文生图）----------
async def _run_text_to_image(adapter, prompt: str, size: str, n: int, provider_name: str) -> str:
    """公共文生图执行体：尺寸校验/自动适配 + 单张或多张循环生成 + 落盘 + 历史记录。"""
    if not prompt:
        return "❌ prompt 不能为空。"
    if n < 1 or n > 20:
        return "❌ n 必须为 1~20。"

    # 尺寸校验 + 自动尺寸适配（API 不支持时取最近宽高比，生成后回缩）
    valid, err_msg = adapter.validate_size(size)
    supported = getattr(adapter, "VALID_SIZES", None)
    if not valid and supported:
        api_size = find_nearest_size(size, list(supported))
        auto_resized = True
    elif not valid:
        return err_msg
    else:
        api_size = size
        auto_resized = False

    # 多 Key 池 / 单 Key 检查
    if adapter.supports_multi_key():
        if not getattr(adapter, "has_keys", lambda: False)():
            return f"❌ 未配置 {adapter.get_key_env()}。"
    elif not getattr(adapter, "_api_key", None):
        return f"❌ 未配置 {adapter.get_key_env()}。"

    if n > 1:
        # 多张模式：官方生图接口每次仅支持 1 张，逐张循环
        results = []
        for i in range(n):
            raw = await adapter.generate(prompt, api_size)
            if raw.startswith("❌"):
                return f"❌ 生成第 {i+1} 张失败：{raw}"
            local_path = await save_image(raw, generate_filename(prompt), is_base64=adapter.is_base64_result())
            if isinstance(local_path, str) and local_path.startswith("❌"):
                return local_path
            if auto_resized:
                try:
                    await resize_image(local_path, size)
                except Exception as e:
                    logger.warning("缩放失败：%s", e)
            results.append((raw, Path(local_path).name, local_path))
            if i < n - 1:
                await asyncio.sleep(0.5)

        output = f"✅ {n} 张图片已生成（后端：{provider_name}）"
        if auto_resized:
            output += f"\n⚡ 自动缩放：API 使用 {api_size} → 目标 {size}（LANCZOS 高质量）"
        output += "：\n"
        for idx, (url, fname, local) in enumerate(results, 1):
            output += f"{idx}. 文件名：{fname}\n   本地：{local}\n   URL：{url}\n"
        await log_request(prompt, size, n, [r[0] for r in results], [r[2] for r in results])
        return output

    # 单张模式
    raw = await adapter.generate(prompt, api_size)
    if raw.startswith("❌"):
        return raw
    filename = generate_filename(prompt)
    local_path = await save_image(raw, filename, is_base64=adapter.is_base64_result())
    if isinstance(local_path, str) and local_path.startswith("❌"):
        return local_path
    if auto_resized:
        try:
            await resize_image(local_path, size)
        except Exception as e:
            logger.warning("缩放失败：%s", e)

    urls = [] if adapter.is_base64_result() else [raw]
    await log_request(prompt, size, 1, urls, [local_path])
    msg = f"✅ 图片已生成（后端：{provider_name}）"
    if auto_resized:
        msg += f"\n⚡ 自动缩放：API 使用 {api_size} → 目标 {size}（LANCZOS 高质量）"
    return (
        f"{msg}\n"
        f"文件名：{Path(local_path).name}\n"
        f"本地：{local_path}\n"
        f"URL：{raw if not adapter.is_base64_result() else '(base64)'}"
    )


# ---------- 工具 3：g-pic（通用文生图）----------
@mcp.tool(name="g-pic")
async def g_pic(
    prompt: str,
    size: str = "2752x1536",
    n: int = 1,
    provider: str = None,
    instance: str = None,
) -> str:
    """通用文生图（GEN_IMAGE 流水线）。支持多张 n>1，可指定 provider 后端与命名实例。

    参数：
      prompt: 图片描述，必填
      size: 尺寸（默认 2752x1536，API 不支持时自动适配最近宽高比）
      n: 生成数量（默认 1）
      provider: 后端（sensenova/openai_compat，默认读 GEN_IMAGE_ADAPTER→sensenova）
      instance: 命名实例名（如 "a"；默认 None 走主实例，见 3.7 命名实例机制）
    """
    if not _pipeline_enabled("GEN_IMAGE"):   # K1 闭环点：§2 契约要求开头检查生图线开关
        return _disabled_msg("GEN_IMAGE")
    try:
        adapter = get_pipeline_adapter("GEN_IMAGE", provider, instance=instance)
    except ValueError as e:
        return str(e)
    return await _run_text_to_image(adapter, prompt, size, n, adapter.get_display_name())


# ---------- 工具 4：i-pic（信息图文生图）----------
@mcp.tool(name="i-pic")
async def i_pic(
    prompt: str,
    size: str = "2752x1536",
    n: int = 1,
    provider: str = None,
    instance: str = None,
) -> str:
    """信息图文生图（GEN_INFOGRAPH 流水线，默认模型 sensenova-u1-fast）。支持多张 n>1。

    参数：
      prompt: 信息图描述，必填
      size: 尺寸（默认 2752x1536，u1-fast 提供 11 种 2K 常量）
      n: 生成数量（默认 1）
      provider: 后端（sensenova/openai_compat，默认读 GEN_INFOGRAPH_ADAPTER→sensenova）
      instance: 命名实例名（默认 None 走主实例，见 3.7）
    """
    if not _pipeline_enabled("GEN_INFOGRAPH"):   # K1 闭环点：§2 契约要求开头检查信息图线开关
        return _disabled_msg("GEN_INFOGRAPH")
    try:
        adapter = get_pipeline_adapter("GEN_INFOGRAPH", provider, instance=instance)
    except ValueError as e:
        return str(e)
    return await _run_text_to_image(adapter, prompt, size, n, adapter.get_display_name())


# ---------- 工具 5：p-pic（图生图）----------
@mcp.tool(name="p-pic")
async def p_pic(
    prompt: str,
    image_path: str,
    size: str = "2752x1536",
    provider: str = None,
    instance: str = None,
) -> str:
    """图生图（EDIT_IMAGE 流水线，默认模型 sensenova-u1.5-lite）。

    参数：
      prompt: 编辑指令，描述期望最终画面，必填
      image_path: 参考图路径（本地，PNG/JPG/WEBP/GIF），必填
      size: 尺寸（默认 2752x1536）
      provider: 后端（sensenova/openai_compat，默认读 EDIT_IMAGE_ADAPTER→sensenova）
      instance: 命名实例名（默认 None 走主实例，见 3.7）

    说明：Python 语法要求必填参数（image_path）置于带默认值参数（size）之前，
    工具契约即本签名：p-pic(prompt, image_path, size, provider=None, instance=None)。
    """
    if not _pipeline_enabled("EDIT_IMAGE"):   # K1 闭环点：§2 契约要求开头检查图生图线开关
        return _disabled_msg("EDIT_IMAGE")
    if not prompt:
        return "❌ prompt 不能为空。"
    try:
        adapter = get_pipeline_adapter("EDIT_IMAGE", provider, instance=instance)
    except ValueError as e:
        return str(e)

    if not adapter.supports_edit():
        return f"❌ {adapter.get_adapter_name()} 不支持图生图。"

    # 尺寸校验 + 自动适配
    valid, err_msg = adapter.validate_size(size)
    supported = getattr(adapter, "VALID_SIZES", None)
    if not valid and supported:
        api_size = find_nearest_size(size, list(supported))
        auto_resized = True
    elif not valid:
        return err_msg
    else:
        api_size = size
        auto_resized = False

    result = await adapter.generate_edit(prompt, api_size, image_path)
    if result.startswith("❌"):
        return result

    filename = generate_filename(prompt)
    local_path = await save_image(result, filename, is_base64=adapter.is_base64_result())
    if isinstance(local_path, str) and local_path.startswith("❌"):
        return local_path
    if auto_resized:
        try:
            await resize_image(local_path, size)
        except Exception as e:
            logger.warning("缩放失败：%s", e)

    urls = [] if adapter.is_base64_result() else [result]
    await log_request(prompt, size, 1, urls, [local_path])
    msg = f"✅ 图生图完成（后端：{adapter.get_display_name()}）"
    if auto_resized:
        msg += f"\n⚡ 自动缩放：API 使用 {api_size} → 目标 {size}（LANCZOS 高质量）"
    return (
        f"{msg}\n"
        f"文件名：{Path(local_path).name}\n"
        f"本地：{local_path}\n"
        f"URL：{result if not adapter.is_base64_result() else '(base64)'}"
    )


# ---------- 工具 6：b-gen（通用批量引擎）----------
# task → 流水线前缀映射（b-gen 停用派发判定，K5 落地点）
_TASK_PREFIX = {
    "g-pic": "GEN_IMAGE",
    "i-pic": "GEN_INFOGRAPH",
    "p-pic": "EDIT_IMAGE",
    "r-pic": "VISION",
    "r-vid": "VISION_VIDEO",
}


async def _dispatch(task: str, line: str, size: str, n: int) -> str:
    """按 task 将单行输入派发到对应单次工具。"""
    task = task.strip().lower()
    prefix = _TASK_PREFIX.get(task)
    if prefix and not _pipeline_enabled(prefix):
        return _disabled_msg(prefix)   # K5：目标流水线停用 → 跳过该任务，不发任何 API 请求
    if task in ("g-pic", "i-pic"):
        fn = g_pic if task == "g-pic" else i_pic
        return await fn(line, size=size, n=n)
    if task == "p-pic":
        prompt, _, ref = line.partition("|")
        prompt, ref = prompt.strip(), ref.strip()
        if not prompt or not ref:
            return "❌ p-pic 行格式应为：prompt | 参考图路径"
        return await p_pic(prompt, ref, size=size)
    if task == "r-pic":
        if not line.strip():
            return "❌ r-pic 行不能为空"
        return await r_pic(line.strip())
    if task == "r-vid":
        if not line.strip():
            return "❌ r-vid 行不能为空"
        return await r_vid(line.strip())
    return f"❌ 不支持的 task：{task}（可选：g-pic / i-pic / p-pic / r-pic / r-vid）"


@mcp.tool(name="b-gen")
async def b_gen(
    task: str,
    file_path: str,
    interval: float = 0,
    resume: bool = False,
    size: str = "2752x1536",
    n: int = 1,
) -> str:
    """通用批量引擎：从 .txt/.md 文件逐行读取任务并批量执行，支持间隔与断点续传。

    参数：
      task: 任务类型，必填（g-pic/i-pic 每行一个 prompt；r-pic 每行一个图片路径；
            r-vid 每行一个视频 URL；p-pic 每行 "prompt | 参考图路径"）
      file_path: 输入文件路径，必填
      interval: 相邻任务间隔秒数（默认 0）
      resume: 断点续传开关（进度记录于 .bgen_progress.json）
      size: 尺寸（仅生图任务生效，默认 2752x1536）
      n: 生成数量（仅 g-pic/i-pic 生效，默认 1）
    """
    path = Path(file_path)
    if not path.exists():
        return f"❌ 文件不存在：{file_path}"
    try:
        content = read_text_safely(path)
    except Exception as e:
        return f"❌ 读取文件失败：{str(e)}"

    entries = parse_batch_file(task, content)
    if not entries:
        return "❌ 文件中没有找到任何有效的任务行。"

    completed_indices = set()
    if resume and PROGRESS_FILE.exists():
        try:
            data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
            completed_indices = set(data.get("completed", []))
            if data.get("task") != task or data.get("file") != str(path):
                completed_indices = set()  # 任务或文件变更，进度作废
        except Exception as e:
            logger.warning("进度文件损坏，将从头执行：%s", e)

    total = len(entries)
    results = []
    for idx, line in enumerate(entries, 1):
        if idx in completed_indices:
            results.append(f"第 {idx} 个任务: ⏭️ 已跳过（之前已完成）")
            continue
        try:
            result = await _dispatch(task, line, size, n)
        except Exception as e:
            logger.exception("第 %d 个任务执行异常", idx)
            result = f"❌ 第 {idx} 个异常：{e}"
        results.append(f"第 {idx} 个任务:\n{result}")
        if resume:
            completed_indices.add(idx)
            try:
                PROGRESS_FILE.write_text(
                    json.dumps(
                        {"task": task, "file": str(path), "completed": sorted(completed_indices), "total": total},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except OSError as e:
                logger.warning("写入进度文件失败：%s", e)
        if idx < total and interval > 0:
            await asyncio.sleep(interval)

    if resume and PROGRESS_FILE.exists():
        try:
            PROGRESS_FILE.unlink()
        except OSError as e:
            logger.warning("删除进度文件失败：%s", e)

    summary = f"✅ 批量生成完成！共 {total} 个任务。\n\n" + "\n---\n".join(results)
    try:
        RESULT_FILE.write_text(summary, encoding="utf-8")
    except OSError as e:
        logger.warning("写入结果文件失败：%s", e)
    summary += f"\n\n📄 详细结果已保存至：{RESULT_FILE.absolute()}"
    return summary


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
