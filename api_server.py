"""
pivision — REST API 服务器（MCP stdio + FastAPI 双接口模式）。

对外部系统（curl / 浏览器 / 脚本）通过 HTTP 提供 pivision 的 6 个工具能力：
    POST /r-pic  图片识别（复用 pivision.r_pic）
    POST /r-vid  视频识别（复用 pivision.r_vid）
    POST /g-pic  通用文生图（复用 pivision.g_pic）
    POST /i-pic  信息图文生图（复用 pivision.i_pic）
    POST /p-pic  图生图（复用 pivision.p_pic）
    POST /b-gen  通用批量引擎（复用 pivision.b_gen）
    GET  /health 健康检查
    GET  /tools  工具能力清单

与 MCP 共享同一套业务层：直接 import pivision.py 中的 async 工具函数，
不复制任何业务逻辑；import pivision 时其顶层 load_dotenv 会自动加载 .env
（含各后端 Key / 模型配置），因此本模块无需任何配置即可调用。

启动方式：
    cd /home/my/work/0proj/mcp-bulid/zijian/pivision
    source .venv/bin/activate
    python api_server.py

默认监听 http://127.0.0.1:7002（仅本机可访问，与 pic5.0/sucai 7001、haimianmusic 7000 并排）。

统一响应结构（供应用消费，业务失败仍返回 HTTP 200，错误信息放 msg）：
    {code: 0, msg: "ok",   data: <工具成功返回字符串>}
    {code: 1, msg: <工具返回的 ❌ 业务失败信息>}
    {code: 2, msg: <工具调用抛出的异常信息>}
"""
import logging
import os
import sys

# 确保项目根目录在 sys.path 中（以便 import pivision）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from pydantic import BaseModel

# 共享业务层：直接复用 pivision 的 async 工具函数（import 时自动加载 .env）
from pivision import b_gen, g_pic, i_pic, p_pic, r_pic, r_vid

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# 6 个工具名清单（供 GET /tools 暴露能力）
TOOLS = ["r-pic", "r-vid", "g-pic", "i-pic", "p-pic", "b-gen"]

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(
    title="pivision MCP API",
    description="pivision 视觉识别与图像生成 REST API（r-pic/r-vid/g-pic/i-pic/p-pic/b-gen），与 MCP stdio 共享业务层",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup() -> None:
    """业务层为函数直调（无长连接客户端实例），启动无需额外初始化，仅记录日志。"""
    logger.info("pivision API 服务器就绪（HTTP 端口 7002）")


# ---------------------------------------------------------------------------
# 请求体模型
# ---------------------------------------------------------------------------

class RPicRequest(BaseModel):
    image_path: str
    question: str | None = None
    provider: str | None = None
    model: str | None = None
    max_tokens: int | None = None


class RVidRequest(BaseModel):
    video_url: str
    question: str | None = None
    provider: str | None = None
    model: str | None = None
    max_tokens: int | None = None


class GPicRequest(BaseModel):
    prompt: str
    size: str | None = None
    n: int | None = None
    provider: str | None = None
    instance: str | None = None


class IPicRequest(BaseModel):
    prompt: str
    size: str | None = None
    n: int | None = None
    provider: str | None = None
    instance: str | None = None


class PPicRequest(BaseModel):
    prompt: str
    image_path: str
    size: str | None = None
    provider: str | None = None
    instance: str | None = None


class BGenRequest(BaseModel):
    task: str
    file_path: str
    interval: float | None = None
    resume: bool | None = None
    size: str | None = None
    n: int | None = None


# ---------------------------------------------------------------------------
# 统一调用包装
# ---------------------------------------------------------------------------

async def _call_tool(fn, params: dict) -> dict:
    """调用业务工具函数并统一包装响应。

    - 工具返回以 ❌ 开头 → 业务失败，HTTP 200 + {code:1, msg:结果}（错误信息自带，放 msg 便于应用判断）
    - 否则成功 → {code:0, msg:"ok", data:结果}
    - 工具调用抛异常 → {code:2, msg:str(e)}
    """
    try:
        result = await fn(**params)
    except Exception as e:  # noqa: BLE001 - 兜底异常，错误信息返回给调用方
        logger.exception("工具调用异常：%s", fn.__name__)
        return {"code": 2, "msg": str(e)}
    if result.startswith("❌"):
        return {"code": 1, "msg": result}
    return {"code": 0, "msg": "ok", "data": result}


def _drop_none(params: dict) -> dict:
    """过滤值为 None 的入参，避免覆盖工具函数自身的默认值（如 question/max_tokens/size）。"""
    return {k: v for k, v in params.items() if v is not None}


# ---------------------------------------------------------------------------
# API 接口
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    """健康检查接口。"""
    return {"status": "ok", "service": "pivision"}


@app.get("/tools")
async def tools() -> dict:
    """返回 6 个工具名清单，方便应用发现能力。"""
    return {"tools": TOOLS}


@app.post("/r-pic")
async def r_pic_endpoint(req: RPicRequest) -> dict:
    """识别本地图片（PNG/JPG/WEBP/GIF），返回文本描述或问答结果。"""
    return await _call_tool(r_pic, _drop_none(req.model_dump()))


@app.post("/r-vid")
async def r_vid_endpoint(req: RVidRequest) -> dict:
    """解析视频直链（http/https），返回文本描述或问答结果。"""
    return await _call_tool(r_vid, _drop_none(req.model_dump()))


@app.post("/g-pic")
async def g_pic_endpoint(req: GPicRequest) -> dict:
    """通用文生图（GEN_IMAGE 流水线）。"""
    return await _call_tool(g_pic, _drop_none(req.model_dump()))


@app.post("/i-pic")
async def i_pic_endpoint(req: IPicRequest) -> dict:
    """信息图文生图（GEN_INFOGRAPH 流水线）。"""
    return await _call_tool(i_pic, _drop_none(req.model_dump()))


@app.post("/p-pic")
async def p_pic_endpoint(req: PPicRequest) -> dict:
    """图生图（EDIT_IMAGE 流水线）。"""
    return await _call_tool(p_pic, _drop_none(req.model_dump()))


@app.post("/b-gen")
async def b_gen_endpoint(req: BGenRequest) -> dict:
    """通用批量引擎：从 .txt/.md 文件逐行读取任务批量执行。"""
    return await _call_tool(b_gen, _drop_none(req.model_dump()))


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=7002, log_level="info")
