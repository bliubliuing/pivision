"""pivision 公共工具层（P1）。

职责通用的纯工具函数：文件命名 / 图片保存 / 历史记录 / 魔数检测 /
Data-URI 编码 / 尺寸适配 / 批量行解析等。

约束：
- 本模块不含任何 API Key、厂商名、供应商字面量，可被任意适配器复用。
- save_image / log_request / resize_image 为 async，与入口层 await 调用点对齐。
"""

import re
import json
import time
import hashlib
import base64
import logging
import asyncio
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# 锚定到项目根目录：art/（图片落盘目录）与 history.json（调用历史）
# 本文件位于项目根目录，故 __file__.parent 即项目根目录。
ART_DIR = Path(__file__).resolve().parent / "art"
HISTORY_PATH = ART_DIR.parent / "history.json"

# 图片下载超时（秒）
DOWNLOAD_TIMEOUT = 120.0

# log_request 的 asyncio.Lock：防止并发追加 history.json 时交错写坏 JSONL
_history_lock = asyncio.Lock()

# 英文文件名生成时过滤的停用词
STOP_WORDS = {
    "a", "an", "the", "of", "for", "in", "and", "with", "on",
    "to", "by", "at", "from", "as", "or", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had",
}


def generate_filename(prompt: str, ext: str = "png", *, asset_id: str = None) -> str:
    """从 prompt 生成语义化文件名。

    - 中文 prompt：取前 15 个中文字符作为文件名主体；
    - 英文 prompt：过滤停用词后取前 4 个单词，用 `_` 连接；
    - 均追加时间戳与 prompt 的 md5 前缀（防重名）。
    - asset_id 不为 None 时，用该标识符（取路径 basename）直接命名，忽略语义化分支。

    返回：`{主体}_{时间戳}_{md5}.{ext}`
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if asset_id:
        asset_id = Path(asset_id).name
        return f"{asset_id}_{timestamp}.{ext}"

    md5_suffix = hashlib.md5(prompt.encode()).hexdigest()[:6]

    if re.search(r"[\u4e00-\u9fff]", prompt):
        # 中文：取前 15 个中文字符
        chars = re.findall(r"[\u4e00-\u9fff]", prompt)
        words = "".join(chars[:15])
        if not words:
            words = "image"
    else:
        # 英文：过滤停用词 + 非字母数字字符，取前 4 词
        cleaned = []
        for w in prompt.split():
            w = re.sub(r"[^a-zA-Z0-9]", "", w)
            if w and w.lower() not in STOP_WORDS:
                cleaned.append(w)
        words = "_".join(cleaned[:4]) if cleaned else "image"

    return f"{words}_{timestamp}_{md5_suffix}.{ext}"


def detect_image_format(data: bytes) -> str:
    """通过魔数检测图片格式，返回小写扩展名（'png'/'jpg'/'webp'/'gif'）。

    无法识别时返回 'png' 作为兜底（注意：合法性判断请用 _is_valid_image）。
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "png"


def _is_valid_image(data: bytes) -> bool:
    """校验字节是否为 PNG/JPG/WEBP/GIF 之一（detect_image_format 的 png 兜底不可信）。"""
    if len(data) < 4:
        return False
    if data[:4] == b"\x89PNG":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return False


def encode_image_to_data_uri(image_bytes: bytes) -> Optional[str]:
    """将图片字节编码为 base64 Data-URI：`data:image/{mime};base64,...`。

    复用 _is_valid_image / detect_image_format 魔数检测。
    非法（不支持）格式返回 None，调用方应据此返回 ❌ 错误。
    """
    if not _is_valid_image(image_bytes):
        return None
    fmt = detect_image_format(image_bytes)
    mime = "jpeg" if fmt == "jpg" else fmt
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


async def save_image(source: str, filename: str, is_base64: bool = False) -> str:
    """统一保存图片到 art/ 目录。

    - is_base64=False：source 为 URL，使用 httpx 下载；
    - is_base64=True：source 为 base64 字符串（自动剥除 `data:image/...;base64,` 前缀）；
    - 保存前做魔数检测：若实际格式与文件扩展名不符，自动修正扩展名；
    - 失败返回以 `❌` 开头的错误字符串（与适配器 generate() 风格一致）。
    """
    try:
        ART_DIR.mkdir(parents=True, exist_ok=True)
        save_path = ART_DIR / filename

        if is_base64:
            if source.startswith("data:image"):
                source = source.split(",", 1)[1]
            image_data = base64.b64decode(source)
            if not image_data:
                return "❌ 保存失败：图片数据为空"
            real_ext = detect_image_format(image_data)
            if real_ext != filename.rsplit(".", 1)[-1].lower():
                save_path = save_path.with_suffix(f".{real_ext}")
        else:
            import httpx

            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
                resp = await client.get(source)
                resp.raise_for_status()
                image_data = resp.content
            if not image_data:
                return "❌ 保存失败：下载内容为空"

        save_path.write_bytes(image_data)
        return str(save_path)
    except Exception as e:  # noqa: BLE001 - 统一转错误字符串返回
        logger.exception("保存图片失败：%s", e)
        return f"❌ 保存失败：{e}"


async def log_request(
    prompt: str,
    size: str,
    n: int,
    urls: List[str],
    local_paths: List[str] = None,
) -> None:
    """记录一次生成调用到 history.json（JSONL 追加写）。

    asyncio.Lock 保证并发下逐行原子追加。
    n 取 max(n, len(urls))：base64 直返模式（urls 为空）不写 0。
    """
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prompt": prompt,
        "size": size,
        "n": max(n, len(urls)),
        "urls": urls,
        "local_paths": local_paths or [],
    }
    async with _history_lock:
        try:
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("写入 history.json 失败（图片已保存）：%s", e)


def parse_size(size: str) -> Tuple[int, int]:
    """解析 'WxH' 或 'W×H' 尺寸字符串 → (宽, 高)。格式非法抛 ValueError。"""
    size = size.replace("×", "x").strip().lower()
    parts = size.split("x")
    if len(parts) != 2:
        raise ValueError(f"无效的尺寸格式：{size}，预期格式如 '1024x1024'")
    return int(parts[0]), int(parts[1])


def find_nearest_size(target_size: str, supported_sizes: List[str]) -> str:
    """从支持的尺寸列表中找到宽高比最接近目标尺寸的选项，返回 "WxH" 常量串。

    纯比例匹配（只考虑宽高比，面积由调用方 resize_image 后处理解决），
    保证生成图片方向一致、不被拉伸变形。
    """
    tw, th = parse_size(target_size)
    target_ratio = tw / th if th > 0 else 999

    best_size = supported_sizes[0]
    best_diff = float("inf")

    for size in supported_sizes:
        sw, sh = parse_size(size)
        ratio = sw / sh if sh > 0 else 999
        diff = abs(target_ratio - ratio)
        if diff < best_diff:
            best_diff = diff
            best_size = size

    return best_size


async def resize_image(image_path: str, target_size: str) -> str:
    """将图片缩放到目标尺寸（LANCZOS 高质量重采样），覆盖原文件，返回路径。

    尺寸已一致时直接返回原路径，跳过重采样。
    """
    from PIL import Image

    tw, th = parse_size(target_size)
    img = Image.open(image_path)

    if img.size == (tw, th):
        return image_path

    resized = img.resize((tw, th), Image.LANCZOS)
    ext = Path(image_path).suffix.lower()
    save_kwargs = {"quality": 95} if ext in (".jpg", ".jpeg") else {}
    resized.save(image_path, **save_kwargs)
    return image_path


def read_text_safely(path) -> str:
    """读取文本文件，自动剥离 UTF-8 BOM。"""
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8")


def parse_batch_file(task: str, content: str) -> list:
    """按 task 解析批量输入，返回 list[str]。

    5 种 task 均统一「每行一条」：非空行即任务行，行首尾空白剥除。
    其中 p-pic 的 "prompt | 参考图路径" 分隔符在 dispatch 层（b_gen._dispatch）
    二次拆分，本函数不处理。
    """
    lines = [ln.strip() for ln in content.splitlines()]
    return [ln for ln in lines if ln]
