# pivision — MCP Vision & Image Generation Server

> One service, two ways to use it: an **MCP server** for AI assistants (CodeBuddy / Claude Desktop / any MCP client), and a **FastAPI HTTP API** (port 7002) for your own programs, scripts, and browser.

pivision is a plug-and-play "see images + draw images" toolbox. It recognizes what's inside a local image or video link, and generates images from a text description — with no coding required to use it.

> **💡 Free to use** — both the vision and image-generation APIs backed into pivision are **currently free**, with generous quotas that are enough for personal use and large projects. You only need to register and get your own API keys (see [Getting API Keys](#getting-api-keys)).
>
> Think of `r-pic` / `r-vid` as **giving your LLM "eyes"** — your AI assistant can finally see the images and videos you hand it, describe them, and answer questions about them.

- **Vision (see)**: describe a local picture, parse a video URL — the model's "eyes"
- **Generation (draw)**: text-to-image, infographic-to-image, image-to-image (reference + instruction)
- **Batch**: run hundreds of tasks from a simple text file with resume support

---

## ✨ Features

| | |
|---|---|
| **6 MCP tools** | `r-pic` · `r-vid` · `g-pic` · `i-pic` · `p-pic` · `b-gen` — one tool per pipeline, fully decoupled config |
| **Dual interface** | MCP (stdio) for AI clients **+** FastAPI HTTP server (8 endpoints, port 7002) for any program |
| **Adapter architecture** | Two-layer adapters (vendor-specific + OpenAI-compatible generic). Add a new model = fill in `.env`, **zero code changes** |
| **5 isolated pipelines** | `VISION_` / `VISION_VIDEO_` / `GEN_IMAGE_` / `GEN_INFOGRAPH_` / `EDIT_IMAGE_` env prefixes, each with an `ENABLED` switch |
| **Multi-key pool** | Generation keys support comma-separated pools; auto-rotate on `401/403/429` |
| **Named instances** | Multiple config instances per pipeline, selectable at call time via `instance=` |
| **Smart fallback** | Video config auto-falls-back to image config; named instances fall back to main instance field-by-field |
| **Batch engine** | `b-gen` runs tasks line-by-line with **resume** (skip completed rows after interruption) |
| **Secure by design** | Zero hardcoded keys, `_safe_error()` redaction, config preflight that never prints real keys |

---

## 🏗 Architecture

```
                 pivision.py (entry, FastMCP "pivision")
    ┌───────┬───────┬──────────┬──────────┬────────┐
  r-pic   r-vid   g-pic/i-pic   p-pic      b-gen
   │        │        │           │          │
   ▼        ▼        ▼           ▼          ▼
get_vision_adapter      get_pipeline_adapter(pipeline, provider, instance)
(prefix VISION_ /      (prefix GEN_IMAGE_ / GEN_INFOGRAPH_ / EDIT_IMAGE_,
 VISION_VIDEO_ fallback)   main instance + named instances)
   │        │        │           │
   ▼        ▼        ▼           ▼
 /v1/chat/completions   /v1/images/generations (url) · /v1/images/edits (JSON+base64)
 (image / video_url)   · openai_compat multipart (image-to-image)

                 api_server.py (FastAPI, :7002 — same business layer)
   /health · /tools · /r-pic · /r-vid · /g-pic · /i-pic · /p-pic · /b-gen

   utils.py (save/naming/size/base64/batch/history)
   adapters/__init__.py (adapter registry + factory + get_config_summary())
   On disk: art/*.png · pivision_batch_results.txt · .bgen_progress.json · history.json
```

**Core design**:

- **Pipeline prefix isolation** — the three generation pipelines read independent env vars (`GEN_IMAGE_*` / `GEN_INFOGRAPH_*` / `EDIT_IMAGE_*`); switch backend per pipeline without touching code.
- **Vision dual-branch fallback** — `r-vid` reuses `VISION_*` when `VISION_VIDEO_*` is not configured: one key set, two use cases.
- **Enable switches** — each pipeline has `{PREFIX}_ENABLED` (default `true`; set to `false` to fully disable it, no API calls are made).
- **Named instances** — declare multiple config instances per generation pipeline (`{PREFIX}_INSTANCES`), select at runtime with the `instance` tool parameter.
- **Multi-key pool** — comma-separated keys in `*_API_KEYS`; automatic key rotation on `401/403/429`.

---

## 🚀 Quick Start

**Prerequisite**: Python ≥ 3.11.

### 1. Install

```bash
cd pivision            # wherever you cloned/unpacked the project
python -m venv .venv
.venv/bin/pip install -e .
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

Fill in your keys: vision → `VISION_API_KEY` (video falls back to it automatically when empty); generation → each section's `*_API_KEYS` (comma-separated for multiple keys). Pipelines without keys return `❌ 未配置 {xxx_API_KEYS}` instead of making wrong requests.

### 2.5 Get API Keys (free)

Both backed APIs are **currently free** with generous quotas — you only register once and grab your keys:

| Pipeline | Provider | Where to register | Key to fill in `.env` |
|---|---|---|---|
| Vision (`r-pic` / `r-vid`) | dots (Xiaohongshu Dots Studio) | https://dots.ai/platform — sign in with phone number, then create a key on the **API Keys** page | `VISION_API_KEY` |
| Generation (`g-pic` / `i-pic` / `p-pic`) | SenseNova (SenseTime) | https://platform.sensenova.cn — register + phone verify, then **Console → API Keys** → create an `sk-` key | `GEN_IMAGE_API_KEYS` / `GEN_INFOGRAPH_API_KEYS` / `EDIT_IMAGE_API_KEYS` |

> Both providers are OpenAI-compatible. If you already have another OpenAI-compatible key (OpenAI / DeepSeek / Moonshot / local gateway…), point `VISION_BASE_URL` / `*_BASE_URL` to your own endpoint and reuse your existing key — no extra registration needed.

### 3a. Use as MCP (for AI assistants)

Register the server in your MCP client (e.g. CodeBuddy → Settings → MCP Servers). The `pic-config.json` / `mcp-register.json` in the project are ready-to-merge templates:

```json
{
  "mcpServers": {
    "pivision": {
      "command": "<your-project-path>/.venv/bin/python",
      "args": ["<your-project-path>/pivision.py"]
    }
  }
}
```

> Replace `<your-project-path>` with the actual path on the target machine. The Python code itself uses relative paths — only the registration JSON needs per-machine paths.

Start / verify:

```bash
cd pivision
.venv/bin/python pivision.py
```

The service waits silently for the AI client to connect. Once registered, just chat with your assistant — it will call the tools for you.

### 3b. Use as HTTP API (for programs & scripts)

```bash
cd pivision
.venv/bin/python api_server.py
```

Expected output:

```text
INFO:     Uvicorn running on http://127.0.0.1:7002
INFO:     pivision API 服务器就绪（HTTP 端口 7002）
```

The server listens on **127.0.0.1:7002** (local machine only). Run it in the background if you want it to survive closing the terminal:

```bash
nohup python api_server.py > pivision_server.log 2>&1 &
# check:  curl http://127.0.0.1:7002/health
# stop:   pkill -f api_server.py
```

---

## 🛠 MCP Tools

Every tool returns a string: success starts with `✅`, failure with `❌` (with reason) — MCP clients can judge by prefix.

### `r-pic` — Image recognition

```text
r-pic(image_path, question="请详细描述这张图片的内容。", provider=None, model=None, max_tokens=1024)
```

- `image_path`: local image (PNG/JPG/WEBP/GIF), required
- `question`: what you want to know about the image
- `max_tokens`: response length cap, default 1024

### `r-vid` — Video recognition

```text
r-vid(video_url, question="请详细描述这段视频的内容。", provider=None, model=None, max_tokens=8192)
```

- `video_url`: must be a **publicly accessible http/https link** (local paths / LAN addresses won't work)
- `max_tokens`: default 8192 — video parsing output is long, keep it high

### `g-pic` / `i-pic` — Text-to-image

```text
g-pic(prompt, size="2752x1536", n=1, provider=None, instance=None)
i-pic(prompt, size="2752x1536", n=1, provider=None, instance=None)
```

- `g-pic`: general text-to-image (GEN_IMAGE pipeline)
- `i-pic`: infographics / posters / charts (GEN_INFOGRAPH pipeline, default `sensenova-u1-fast`)
- `n`: 1–20; `n>1` is generated one-by-one (the official API supports one per call)
- `instance`: named instance, e.g. `"a"`; unknown instance → explicit error (no silent fallback)

### `p-pic` — Image-to-image (reference + instruction)

```text
p-pic(prompt, image_path, size="2752x1536", provider=None, instance=None)
```

- `image_path`: reference image path (local), required — sent as `data:image/*;base64,` Data-URI
- Only `n=1` is allowed

### `b-gen` — Batch engine

```text
b-gen(task, file_path, interval=0, resume=False, size="2752x1536", n=1)
```

- `task`: `g-pic` / `i-pic` / `p-pic` / `r-pic` / `r-vid`
- Reads tasks line-by-line from a `.txt` / `.md` file (see format below)
- `resume=True`: progress saved to `.bgen_progress.json`; re-run skips completed rows. **Progress auto-invalidates if the task type or input file changes.**
- `interval`: seconds between tasks (set 1–2 to avoid rate limits)
- Results are written to `pivision_batch_results.txt`

**Task file format (one task per line):**

| `task` | Line format | Example line |
|---|---|---|
| `g-pic` / `i-pic` | 1 prompt | `一只猫咪咖啡厅插图` |
| `r-pic` | 1 image path | `/data/photo.png` |
| `r-vid` | 1 video URL | `https://example.com/clip.mp4` |
| `p-pic` | `prompt \| reference path` | `把背景改成雪山 \| /data/sketch.png` |

---

## 🌐 HTTP API (FastAPI, port 7002)

Same business layer as MCP — no duplicated logic. All endpoints return a **unified response structure**:

```json
{"code": 0, "msg": "ok", "data": "..."}
```

| `code` | Meaning | How to judge |
|---|---|---|
| `0` | success | `msg` is `"ok"`, real result in `data` |
| `1` | business failure (missing key, file not found) | read `msg` |
| `2` | internal exception (bug) | read `msg` |

**8 endpoints:**

| Method | Endpoint | Purpose | Required fields |
|---|---|---|---|
| GET | `/health` | health check | — |
| GET | `/tools` | list available tools | — |
| POST | `/r-pic` | image recognition | `image_path`; optional `question`, `max_tokens` |
| POST | `/r-vid` | video recognition | `video_url`; optional `question`, `max_tokens` |
| POST | `/g-pic` | text-to-image | `prompt`; optional `size`, `n`, `instance` |
| POST | `/i-pic` | infographic generation | `prompt`; optional `size`, `n`, `instance` |
| POST | `/p-pic` | image-to-image | `prompt`, `image_path`; optional `size`, `instance` |
| POST | `/b-gen` | batch engine | `task`, `file_path`; optional `interval`, `resume`, `size`, `n` |

**Quick test:**

```bash
curl http://127.0.0.1:7002/health
curl -X POST http://127.0.0.1:7002/g-pic -H "Content-Type: application/json" \
  -d '{"prompt": "a cat by a coffee shop window, illustration style"}'
```

---

## ⚙️ Environment Variables Reference

5 sections — all new v6 variables (code defaults shown in parentheses; env vars override).

### `VISION_` — r-pic image recognition

| Variable | Meaning | Default |
|---|---|---|
| `VISION_ENABLED` | enable switch: `true`/`false` (empty = on) | `true` |
| `VISION_PROVIDER` | vision backend: `openai` / `dots` | `dots` |
| `VISION_MODEL` | vision model | `dots3-note-prev` |
| `VISION_BASE_URL` | OpenAI-compatible base URL | `https://note3-prev-api.askdiandian.com/v1` |
| `VISION_API_KEY` | vision API key | empty |

### `VISION_VIDEO_` — r-vid video recognition (all empty → falls back to `VISION_*`)

| Variable | Meaning | Default |
|---|---|---|
| `VISION_VIDEO_ENABLED` | video switch; empty falls back to `VISION_ENABLED`; explicit `false` = video line disabled | `true` |
| `VISION_VIDEO_PROVIDER` / `_MODEL` / `_BASE_URL` / `_API_KEY` | video backend; empty → fall back to `VISION_*` | same as vision |

### `GEN_IMAGE_` — g-pic text-to-image

| Variable | Meaning | Default |
|---|---|---|
| `GEN_IMAGE_ENABLED` | enable switch | `true` |
| `GEN_IMAGE_ADAPTER` | `sensenova` / `openai_compat` | `sensenova` |
| `GEN_IMAGE_MODEL` | text-to-image model (main instance) | `sensenova-u1.5-lite` |
| `GEN_IMAGE_BASE_URL` | OpenAI-compatible base URL (main instance) | `https://token.sensenova.cn/v1` |
| `GEN_IMAGE_API_KEYS` | comma-separated key pool (main instance) | empty |
| `GEN_IMAGE_INSTANCES` | named instance list, e.g. `a,b`; empty = main only | empty |

### `GEN_INFOGRAPH_` — i-pic infographics

Same shape as `GEN_IMAGE_*`; default model `sensenova-u1-fast`.

### `EDIT_IMAGE_` — p-pic image-to-image

Same shape as `GEN_IMAGE_*`; default model `sensenova-u1.5-lite` (decoupled from text-to-image).

> `openai_compat` optional extras: `{PREFIX}_RESPONSE_FORMAT` (`url`/`b64_json`, default `url`), `{PREFIX}_EDIT_URL` (image-edit endpoint override, default `${BASE_URL}/images/edits`).

### Fallback chain (priority order)

```text
named-instance vars ({PREFIX}_{X}_FIELD) → main-instance vars ({PREFIX}_FIELD) → code defaults
```

Only two fallback types exist, no legacy-variable fallback:
1. **Video → image**: `VISION_VIDEO_*` all empty → `VISION_*` (switch follows the same chain).
2. **Named instance → main instance**: instance field empty → main instance field (no `_ADAPTER` per instance; `{PREFIX}_ADAPTER` is pipeline-level).

---

## 🔌 Adapter Mechanism

**Two-layer**: common differences are absorbed by generic adapters, special differences by vendor-specific adapters. The tools only see capability interfaces.

| Adapter | Type | Purpose | Special differences handled |
|---|---|---|---|
| `openai_compat` | generation · generic | OpenAI-compatible protocol (`/images/generations`, multipart `edits`) | none — differences handled by config (change MODEL/BASE_URL = switch vendor) |
| `sensenova` | generation · vendor | SenseNova image generation | multi-key pool, `watermark`/`prompt_extend`, image-edit JSON + base64 Data-URI |
| `openai` | vision · generic | OpenAI-compatible vision | none (Bearer auth, `detail=auto`) |
| `dots` | vision · vendor | dots vision | `api-key` header auth, `detail=medium`, `enable_thinking=false`, video `stream=false` |

**Add a new OpenAI-compatible model — zero code:**

```bash
GEN_IMAGE_ADAPTER=openai_compat
GEN_IMAGE_MODEL=foo-image-x1
GEN_IMAGE_BASE_URL=https://foo.example.com/v1
GEN_IMAGE_API_KEYS=sk-foo-xxxx
```

Tools work immediately. Only write a custom adapter when the API is not OpenAI-compatible (private protocols, two-stage polling, special auth headers, JSON+base64 edits, multi-key pools, non-configurable differences) — register it in `adapters/__init__.py` in one line, **pipeline code untouched**.

**Multi-key pool constraints**: keys in one `*_API_KEYS` must be same vendor + same `BASE_URL` + same `MODEL` (they rotate for one endpoint). Don't mix vendors/URLs/models in one pool (→ `400/404`, no key rotation, cascading errors). Use different config sections or named instances for multiple endpoints.

---

## 🔍 Config Preflight

`adapters.get_config_summary()` returns the readiness of all 5 pipelines (+ named instances) — `N of M available` — so agents/clients can check before calling. **It only reports "configured / not configured", never prints actual keys.**

```python
import json
from adapters import get_config_summary

for r in get_config_summary():
    print(f"{r['tool']:6s} {r['prefix']:16s} instance={str(r['instance'] or '(main)'):6s} "
          f"adapter={r['adapter']:12s} model={r['model']:20s} keys={r['api_keys']:3s} → {r['status']}")
```

Example output (fully configured case):

```text
g-pic  GEN_IMAGE        实例=(主)   adapter=sensenova    model=sensenova-u1.5-lite keys=已配置 → AVAILABLE
i-pic  GEN_INFOGRAPH    实例=(主)   adapter=sensenova    model=sensenova-u1-fast   keys=已配置 → AVAILABLE
p-pic  EDIT_IMAGE       实例=(主)   adapter=sensenova    model=sensenova-u1.5-lite keys=已配置 → AVAILABLE
r-pic  VISION           实例=(主)   adapter=dots         model=dots3-note-prev     keys=已配置 → AVAILABLE
r-vid  VISION_VIDEO     实例=(主)   adapter=dots         model=dots3-note-prev     keys=已配置 → AVAILABLE

汇总：5 of 5 available
```

---

## 🔒 Security

- Keys live only in `.env` (excluded by `.gitignore`, never committed) — **zero hardcoded keys in code**.
- All adapters redact key fragments in errors and logs via `_safe_error()` → `[redacted]`.
- `get_config_summary()` prints only "configured / not configured" — never the keys themselves.
- Never put full keys in frontend code, logs, or public repos.
- HTTP server binds to **127.0.0.1** by default — local machine only, not exposed to LAN/Internet unless you change `host`.

---

## 📝 Notes & FAQ

**Q1: Generated image URLs expire?**
u1.5-lite URLs expire in 24h, u1-fast in 1h (per official docs). pivision **downloads every generated image to local `art/` automatically** — always use the local file path, ignore the temp URL.

**Q2: Video recognition fails / can't parse?**
The video URL must be a **publicly reachable http/https link** — LAN addresses, `localhost`, and local file paths won't work. Parsing can be slow, that's normal; if it times out, use a shorter/smaller video. Default timeout is 180s (`TIMEOUT_SECONDS` in `adapters/vision_base.py`).

**Q3: "Not configured {xxx}_API_KEYS"?**
That pipeline has no key. Fill `*_API_KEYS` in `.env`, then restart the service.

**Q4: "Pipeline disabled ({PREFIX}_ENABLED=false)"?**
The switch is off. Set `{PREFIX}_ENABLED` to `true` (or delete the line — empty counts as on), then restart.

**Q5: Key rotation still hits 400/404?**
Almost certainly a mixed pool — keys pointing to different models/endpoints in one pool. Split by endpoint using different config sections or named instances.

**Q6: Env vars changed but no effect?**
`.env` is loaded once at startup. Restart the service. Note named-instance vars are case-sensitive (`GEN_IMAGE_A_MODEL` — instance suffix uppercase).

**Q7: Size errors or wrong orientation?**
Generation auto-matches the nearest aspect ratio + LANCZOS downscale for unsupported sizes (output notes `⚡ 自动缩放`). If still failing, make sure `size` is `WxH` format (e.g. `1024x1024`).

**Limits**: single image ≤ 20MB for `r-pic`; video timeout 180s (adjustable constant); `b-gen` per-line failure doesn't stop the batch.

---

## 📦 Project Layout

```
pivision/
├── pivision.py           # MCP entry (FastMCP "pivision")
├── api_server.py         # FastAPI HTTP server (:7002)
├── utils.py              # save/naming/size/base64/batch/history
├── adapters/             # adapter registry + factory
│   ├── __init__.py       #   registry + get_config_summary()
│   ├── vision_base.py    #   vision base (timeout, max image size)
│   ├── vision_openai.py  #   vision · generic OpenAI-compatible
│   ├── vision_dots.py    #   vision · dots vendor
│   ├── openai_compat.py  #   generation · generic OpenAI-compatible
│   └── sensenova.py      #   generation · SenseNova vendor
├── pyproject.toml
├── .env.example          # config template (fill your keys → .env)
├── pic-config.json       # MCP registration template (generic)
├── mcp-register.json     # MCP registration template (CodeBuddy tagged)
└── README-zh.md          # 中文版说明
```

---

## 📄 License

To be determined by the project owner — see repository listing. (Internal / local deployment tool; not published to npm/PyPI.)