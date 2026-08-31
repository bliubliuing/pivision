# pivision — MCP 视觉与图像生成服务（v6.1）

一个 MCP 服务 `pivision`，覆盖「视觉识别 + 图像生成」两大能力域，对外暴露 **6 个工具**，一工具一流水线、配置互相解耦。生图适配器按「流水线前缀」隔离环境变量，视觉适配器支持图片 / 视频双分支与自动回退，生图流水线支持命名实例与多 Key 池。

---

## 1 简介与能力总览

**6 个工具**（两域三分组）：

| 工具 | 域 | 作用 |
|---|---|---|
| `r-pic` | 视觉·图片 | 识别本地图片（PNG/JPG/WEBP/GIF），返回文本描述或问答结果 |
| `r-vid` | 视觉·视频 | 解析视频直链，返回文本描述或问答结果 |
| `g-pic` | 生图·文生图 | 通用文生图（GEN_IMAGE 流水线） |
| `i-pic` | 生图·文生图 | 信息图文生图（GEN_INFOGRAPH 流水线，默认 `sensenova-u1-fast`） |
| `p-pic` | 生图·图生图 | 图生图 / 参考图编辑（EDIT_IMAGE 流水线） |
| `b-gen` | 批量引擎 | 从文件逐行批量派发 g-pic / i-pic / p-pic / r-pic / r-vid |

**架构：三层解耦**（借鉴 OpenMontage：流水线 = 能力接口，适配器 = 注册实例）：

```
                 pivision.py（入口，FastMCP("pivision")）
    ┌───────┬───────┬──────────┬──────────┬────────┐
  r-pic   r-vid   g-pic/i-pic   p-pic      b-gen
   │        │        │           │          │
   ▼        ▼        ▼           ▼          ▼
get_vision_adapter      get_pipeline_adapter(pipeline, provider, instance)
(前缀 VISION_ /        (前缀 GEN_IMAGE_ / GEN_INFOGRAPH_ / EDIT_IMAGE_，
 VISION_VIDEO_ 回退)    主实例 + 命名实例)
   │        │        │           │
   ▼        ▼        ▼           ▼
 /v1/chat/completions   /v1/images/generations（url）· /v1/images/edits（JSON+base64）
 (image / video_url)   · openai_compat multipart（图生图）

   utils.py（保存/命名/尺寸适配/base64/批量解析/历史）
   adapters/__init__.py（适配器注册表 + 工厂 + get_config_summary()）
   落地文件：art/*.png · pivision_batch_results.txt · .bgen_progress.json · history.json
```

**核心设计**：

- **流水线前缀隔离**：三条生图流水线各自独立读环境变量（`GEN_IMAGE_*` / `GEN_INFOGRAPH_*` / `EDIT_IMAGE_*`），互不干扰，按需挂不同后端。
- **视觉双分支回退**：`r-vid` 的 `VISION_VIDEO_*` 未配置时自动回退 `VISION_*`，一套配置两种用法。
- **启用开关（v6.1.1）**：每条流水线一个 `{PREFIX}_ENABLED`（默认 `true`，`false` 时该流水线整体停用、不发任何 API 请求）。
- **命名实例**：每条生图流水线可挂多个配置实例（`{PREFIX}_INSTANCES`），运行期用工具 `instance` 参数选择。
- **多 Key 池**：生图 `API_KEYS` 支持逗号分隔多把密钥，`401/403/429` 自动换 Key 重试。

---

## 2 快速开始

**前置要求**：Python ≥ 3.11。

**1. 安装依赖**（在项目根目录）：

```bash
python -m venv .venv
.venv/bin/pip install -e .
```

**2. 配置 `.env`**：

```bash
cp .env.example .env
```

按需填写各段密钥：视觉填 `VISION_API_KEY`（视频未单独配置时自动复用），生图填各段 `*_API_KEYS`（可逗号分隔多把）。未配 Key 的流水线调用时返回 `❌ 未配置 {xxx_API_KEYS}`，不会误发请求。

**3. 注册 MCP**：把 `pic-config.json`（或 `mcp-register.json`）的内容合并进客户端的 MCP 配置，`command` 指向项目 `.venv/bin/python`，`args[0]` 指向 `pivision.py`：

```json
{
  "mcpServers": {
    "pivision": {
      "command": "/home/my/work/0proj/mcp-bulid/zijian/pivision/.venv/bin/python",
      "args": ["/home/my/work/0proj/mcp-bulid/zijian/pivision/pivision.py"]
    }
  }
}
```

**4. 启动**（stdio，两种方式等价）：

```bash
# 已 pip install -e . 安装时
pivision
# 或直接用解释器运行入口
.venv/bin/python pivision.py
```

**5. 验证**：MCP 客户端连接后应能列出 6 个工具；也可先跑配置预检（见第 7 章）。

---

## 3 环境变量参考

5 段配置，全部 v6 化新变量。括号内为代码默认值（环境变量可整体覆盖）。

### 3.1 r-pic 视觉识别（视觉·图片）

| 变量 | 含义 | 代码默认值 |
|---|---|---|
| `VISION_ENABLED` | 启用开关：`true`/`false`，不配置或留空 = 开启 | `true` |
| `VISION_PROVIDER` | 视觉后端：`openai` / `dots` | `dots` |
| `VISION_MODEL` | 视觉模型 | `dots3-note-prev` |
| `VISION_BASE_URL` | OpenAI 兼容基址 | `https://note3-prev-api.askdiandian.com/v1` |
| `VISION_API_KEY` | 视觉 API 密钥 | 空 |

### 3.2 r-vid 视频识别（未配置全部回退 VISION_*）

| 变量 | 含义 | 代码默认值 |
|---|---|---|
| `VISION_VIDEO_ENABLED` | 视频线启用开关；未配置/空回退 `VISION_ENABLED`；**显式 `false` 视为有值、不回退，视频线单独停用** | `true` |
| `VISION_VIDEO_PROVIDER` | 视频后端；空回退 `VISION_PROVIDER` | `dots` |
| `VISION_VIDEO_MODEL` | 视频模型；空回退 `VISION_MODEL` | `dots3-note-prev` |
| `VISION_VIDEO_BASE_URL` | 视频基址；空回退 `VISION_BASE_URL` | 同 `VISION_BASE_URL` |
| `VISION_VIDEO_API_KEY` | 视频密钥；空回退 `VISION_API_KEY` | 同 `VISION_API_KEY` |

### 3.3 g-pic 通用文生图

| 变量 | 含义 | 代码默认值 |
|---|---|---|
| `GEN_IMAGE_ENABLED` | 启用开关 | `true` |
| `GEN_IMAGE_ADAPTER` | 生图适配器：`sensenova` / `openai_compat` | `sensenova` |
| `GEN_IMAGE_MODEL` | 通用文生图模型（主实例） | `sensenova-u1.5-lite` |
| `GEN_IMAGE_BASE_URL` | OpenAI 兼容基址（主实例） | `https://token.sensenova.cn/v1` |
| `GEN_IMAGE_API_KEYS` | 多把密钥，逗号分隔（主实例 Key 池） | 空 |
| `GEN_IMAGE_INSTANCES` | 命名实例名列表（逗号分隔，如 `a,b`）；空 = 仅主实例 | 空 |

### 3.4 i-pic 信息图文生图

| 变量 | 含义 | 代码默认值 |
|---|---|---|
| `GEN_INFOGRAPH_ENABLED` | 启用开关 | `true` |
| `GEN_INFOGRAPH_ADAPTER` | 生图适配器 | `sensenova` |
| `GEN_INFOGRAPH_MODEL` | 信息图模型（主实例） | `sensenova-u1-fast` |
| `GEN_INFOGRAPH_BASE_URL` | 基址（主实例） | `https://token.sensenova.cn/v1` |
| `GEN_INFOGRAPH_API_KEYS` | 多把密钥，逗号分隔（主实例 Key 池） | 空 |
| `GEN_INFOGRAPH_INSTANCES` | 命名实例名列表 | 空 |

### 3.5 p-pic 图生图

| 变量 | 含义 | 代码默认值 |
|---|---|---|
| `EDIT_IMAGE_ENABLED` | 启用开关 | `true` |
| `EDIT_IMAGE_ADAPTER` | 生图适配器 | `sensenova` |
| `EDIT_IMAGE_MODEL` | 图生图模型（主实例，与文生图解耦、独立可配） | `sensenova-u1.5-lite` |
| `EDIT_IMAGE_BASE_URL` | 基址（主实例） | `https://token.sensenova.cn/v1` |
| `EDIT_IMAGE_API_KEYS` | 多把密钥，逗号分隔（主实例 Key 池） | 空 |
| `EDIT_IMAGE_INSTANCES` | 命名实例名列表 | 空 |

> `openai_compat` 可选扩展变量：`{PREFIX}_RESPONSE_FORMAT`（`url`/`b64_json`，默认 `url`）、`{PREFIX}_EDIT_URL`（图生图端点覆盖，默认 `${BASE_URL}/images/edits`）。

### 3.6 默认值与回退规则（回退链）

**回退链 = 取值优先级**，按顺序取第一个有值的，全空落到代码默认值：

```text
优先：实例专属变量（{PREFIX}_{X}_FIELD）→ 其次：主实例变量（{PREFIX}_FIELD）→ 最后：代码默认值
```

本方案只保留**两类回退**，不存在第三类（旧变量回退）：

1. **视觉·视频 → 视觉·图片**：`VISION_VIDEO_MODEL/BASE_URL/API_KEY/PROVIDER` 全部未配/空 → 回退 `VISION_*`；开关同链（`VISION_VIDEO_ENABLED` 未配/空 → 回退 `VISION_ENABLED`，显式 `false` 则视频线单独停用）。
2. **命名实例 → 主实例**：命名实例某字段（`_MODEL/_BASE_URL/_API_KEYS`）空 → 回退主实例同字段；实例不设 `_ADAPTER`（`{PREFIX}_ADAPTER` 是流水线级）。

不读取任何 v5 旧变量（如 `SENSENOVA_PIC_*`），不存在「旧变量 → 新变量」回退。

### 3.7 命名实例变量（以 g-pic 为例）

```bash
GEN_IMAGE_INSTANCES=a,b          # 声明命名实例列表
GEN_IMAGE_A_MODEL=sensenova-u1-fast
GEN_IMAGE_A_BASE_URL=            # 留空 = 回退主实例 URL
GEN_IMAGE_A_API_KEYS=            # 留空 = 回退主实例 Key 池
# 依此类推 GEN_IMAGE_B_* / GEN_IMAGE_C_*
```

规则：主实例 = 无后缀默认变量；命名实例未配置的字段自动回退主实例；Key 池按「前缀#实例名」隔离（`GEN_IMAGE` / `GEN_IMAGE#a`），回退主实例 Key 时**不共享**主实例的轮询状态，若需独立计费/限流请单独配 `_API_KEYS`；工具传未声明的实例名会直接报错、不静默回退；视觉两条流水线不参与命名实例。

---

## 4 工具文档

统一返回契约：执行层统一返回 `str`，成功以 `✅` 开头、失败以 `❌` 开头（带原因），MCP 客户端可按前缀快速判定。

### 4.1 r-pic（图片识别）

```text
r-pic(image_path, question="请详细描述这张图片的内容。", provider=None, model=None, max_tokens=1024)
```

- `image_path`：本地图片路径（PNG/JPG/WEBP/GIF），必填；`question`：对图片的提问/指令；
- `provider`：视觉后端（`openai`/`dots`），空读 `VISION_PROVIDER`；`model`：空读 `VISION_MODEL`；
- `max_tokens`：返回文本上限，默认 1024。

```text
r-pic(image_path="/data/photo.png", question="图片里有什么动物？")
→ ✅ 识别完成（模型：dots3-note-prev）…
```

### 4.2 r-vid（视频识别）

```text
r-vid(video_url, question="请详细描述这段视频的内容。", provider=None, model=None, max_tokens=8192)
```

- `video_url`：视频直链（http/https，视觉模型服务端可直接访问），必填；
- `provider`/`model`：空则读 `VISION_VIDEO_*` → 回退 `VISION_*`；
- `max_tokens`：默认 8192（视频解析输出较长，避免截断）。

```text
r-vid(video_url="https://example.com/clip.mp4")
→ ✅ 视频解析完成（模型：dots3-note-prev）…
```

### 4.3 g-pic / i-pic（文生图）

```text
g-pic(prompt, size="2752x1536", n=1, provider=None, instance=None)
i-pic(prompt, size="2752x1536", n=1, provider=None, instance=None)
```

- `prompt`：图像描述，必填；`size`：默认 `2752x1536`（u1-fast 的 11 种 2K 常量之一，u1.5-lite 亦为 32 的倍数合法值）；
- `n`：生成数量，官方接口每次仅支持 1 张，`n>1` 由工具循环逐张请求实现，范围 `1..20`；
- `provider`：适配器名（`sensenova`/`openai_compat`），空读对应流水线 `*_ADAPTER`；
- `instance`：命名实例名（如 `"a"`），空走主实例；传不存在的实例名由工厂报错返回。

```text
g-pic("一只猫咪咖啡厅插图", size="2048x2048")
→ ✅ 图片已生成（后端：sensenova）
   文件名：一只猫咪咖啡厅插图_20260829_102030_a1b2c3.png
   本地：/…/pivision/art/一只猫咪咖啡厅插图_20260829_102030_a1b2c3.png
   URL：https://…

g-pic("一张秋季森林信息图", instance="a")          # 走命名实例 a（如 u1-fast）
g-pic("一张猫咪咖啡厅插图", instance="zz")         # ❌ 不支持的命名实例：zz（不静默回退）
```

### 4.4 p-pic（图生图）

```text
p-pic(prompt, image_path, size="2752x1536", provider=None, instance=None)
```

- `image_path`：参考图路径（本地），必填；读取后编码为 `data:image/*;base64,` Data-URI 传入 `/v1/images/edits`；
- 仅允许 `n=1`；图生图模型由 `EDIT_IMAGE_MODEL` 控制，与文生图解耦；`instance`：图生图命名实例（如 `EDIT_IMAGE_A_*`）。

```text
p-pic("把背景改成雪山", image_path="/data/sketch.png")
→ ✅ 图生图完成（后端：sensenova）…
```

### 4.5 b-gen（通用批量引擎）

```text
b-gen(task, file_path, interval=0, resume=False, size="2752x1536", n=1)
```

详见第 5 章。

---

## 5 b-gen 批量输入格式与断点续传

`b-gen` 从 `.txt`/`.md` 文件逐行读取任务并批量执行（UTF-8 自动 BOM 兼容）。

**`task` 五选一，决定行格式与派发目标**：

| task | 每行格式 | 示例行 |
|---|---|---|
| `g-pic` / `i-pic` | 1 个 prompt | `一只猫咪咖啡厅插图` |
| `r-pic` | 1 个图片路径 | `/data/photo.png` |
| `r-vid` | 1 个视频 URL | `https://example.com/clip.mp4` |
| `p-pic` | `prompt | 参考图路径`（分隔符 ` | ` 或 `|`） | `把背景改成雪山 | /data/sketch.png` |

**参数**：

- `interval`：相邻任务间隔秒数（默认 0），用于规避限流；
- `resume`：断点续传开关——进度写入 `.bgen_progress.json`（记录 `task/file/completed/total`），中断后带 `resume=True` 重跑会跳过已完成任务；**任务或输入文件变更时进度自动作废**，避免续传错乱；
- `size`/`n`：仅对生图任务（g-pic/i-pic/p-pic）生效；
- 结果汇总写入 `pivision_batch_results.txt`，返回同时附其绝对路径。

**边界**：b-gen 的 `task` 契约面向单条流水线主实例派发，**不新增 instance 维度**（避免批量输入格式复杂化）；`task` 指向 `{PREFIX}_ENABLED=false` 的流水线时，该任务被跳过并在结果中标注「已停用」（不发任何 API 请求）。

```text
b-gen(task="g-pic", file_path="/data/prompts.txt", interval=1, resume=True)
→ ✅ 批量生成完成！共 10 个任务。…
```

---

## 6 适配器机制

**两层二分**：标准差异由「通用适配器」消化，特殊差异由「专属适配器」承接，上层（工具）只面对能力接口。

| 适配器 | 类型 | 用途 | 承接的特殊差异 |
|---|---|---|---|
| `openai_compat` | 生图·通用 | 只做 OpenAI 兼容协议（`/images/generations` / multipart `edits`） | 无；差异全部由配置覆盖（改 MODEL/BASE_URL 即换厂商） |
| `sensenova` | 生图·专属 | 商汤生图 | 多 Key 池、`watermark`/`prompt_extend`、图生图 JSON + base64 Data-URI |
| `openai` | 视觉·通用 | OpenAI 兼容视觉 | 无（Bearer 鉴权、`detail=auto`） |
| `dots` | 视觉·专属 | dots 视觉 | `api-key` 头鉴权、`detail=medium`、`enable_thinking=false`、视频 `stream=false` |

**能力声明与状态自检**：每个适配器用类属性声明能力（`capability` / `capabilities` / `supports`，如 `sensenova.supports={"multi_key": True, "edit": True}`），并提供 `get_status()` 配置完整性自检——生图线 `API_KEYS` 非空 / 视觉线 `API_KEY` 非空 → `AVAILABLE`，否则 `UNAVAILABLE`。注册表在 `adapters/__init__.py`（`_IMAGE_ADAPTERS` / `_VISION_ADAPTERS`）。

**挂新模型（OpenAI 兼容，零代码）**：填 `{PREFIX}_ADAPTER=openai_compat` + `{PREFIX}_MODEL`（新模型名）+ `{PREFIX}_BASE_URL`（新服务基址）+ `{PREFIX}_API_KEYS` 即可，工具直接可用：

```bash
GEN_IMAGE_ADAPTER=openai_compat
GEN_IMAGE_MODEL=foo-image-x1
GEN_IMAGE_BASE_URL=https://foo.example.com/v1
GEN_IMAGE_API_KEYS=sk-foo-xxxx
```

**何时需要写专属适配器**：接口不兼容 OpenAI 标准形态时（私有协议/两段式轮询、特殊鉴权头如 `api-key`、图生图 JSON+base64、多 Key 池、不可用配置覆盖的参数差异），写适配器类并注册进注册表一行即可——**流水线代码零改动**。

**多实例**：同一条生图流水线可预配多个命名实例（见 3.7/3.8），用工具 `instance` 参数选择；跨协议调用某实例时显式传 `provider` 参数（`provider` 与 `instance` 正交）。

**多 Key 池「同接口」约束**：一个 `*_API_KEYS` 里的多把 Key 必须同厂商 + 同 `BASE_URL` + 同 `MODEL`，是给同一端点轮换的凭证。不同厂商/URL/模型不得混池（混池 → `400/404` 不换 Key、连锁全线报错）；需要多端点时用不同配置段或不同命名实例隔离。

---

## 7 配置预检

`adapters.get_config_summary()` 返回 5 条流水线（+ 各命名实例）的配置状态清单（`N of M available`），供 Agent / MCP 客户端在调用任意工具前预检，避免把 `UNAVAILABLE` 当可用。**汇总只输出「已配置/未配置」，绝不输出实际 Key**。

```python
import json
from adapters import get_config_summary

for r in get_config_summary():
    print(f"{r['tool']:6s} {r['prefix']:16s} 实例={str(r['instance'] or '(主)'):6s} "
          f"adapter={r['adapter']:12s} model={r['model']:20s} keys={r['api_keys']:3s} → {r['status']}")
```

示例输出（全已配置时，实际未配 Key 的流水线对应行显示 `keys=未配置 → UNAVAILABLE`）：

```text
g-pic  GEN_IMAGE        实例=(主)   adapter=sensenova    model=sensenova-u1.5-lite keys=已配置 → AVAILABLE
g-pic  GEN_IMAGE_A      实例=a      adapter=sensenova    model=sensenova-u1-fast   keys=已配置 → AVAILABLE
i-pic  GEN_INFOGRAPH    实例=(主)   adapter=sensenova    model=sensenova-u1-fast   keys=已配置 → AVAILABLE
p-pic  EDIT_IMAGE       实例=(主)   adapter=sensenova    model=sensenova-u1.5-lite keys=已配置 → AVAILABLE
r-pic  VISION           实例=(主)   adapter=dots         model=dots3-note-prev     keys=已配置 → AVAILABLE
r-vid  VISION_VIDEO     实例=(主)   adapter=dots         model=dots3-note-prev     keys=已配置 → AVAILABLE

汇总：6 of 6 available
```

---

## 8 注意事项

- **图片 URL 有效期短（24h / 1h）**：u1.5-lite 返回 URL 24 小时失效、u1-fast 仅 1 小时失效（官方文档原文）。生成后服务会立即下载落盘到 `art/`，返回的 URL 仅作临时代理——**跨天引用请用本地路径，不要依赖 URL**。
- **限流**：多 Key 池用 `asyncio.Lock` 串行出 Key；高并发下同一 Key 可能被相邻获得仍有 429 风险，429 后自动换下一把 Key，全部失败才返回错误（官方错误码 `quota_exceeded_error`）。批量场景可用 `b-gen(interval=…)` 控制节奏。
- **图片大小限制（20MB）**：r-pic 读取本地图片有单张 ≤20MB 限制（`MAX_IMAGE_SIZE`），超出返回 `❌ 图片过大，请压缩至 20MB 以内`。视频识别延迟较高，超时默认 180s（`TIMEOUT_SECONDS`，常量集中在 `adapters/vision_base.py` 顶部，长视频可显式上调）。
- **Key 安全与脱敏**：密钥只存于 `.env`（已被 `.gitignore` 排除，不纳入 git），**代码零硬编码 Key**；所有适配器统一 `_safe_error()` 把错误返回与日志中的 Key 片段替换为 `[redacted]`；`get_config_summary()` 只输出「已配置/未配置」，绝不输出实际 Key。请勿把完整 Key 写入前端、日志或公开仓库。
- **尺寸兼容**：u1-fast 官方仅支持 11 种 2K 常量（含 `2752x1536`）；u1.5-lite 支持 32 的倍数（512–4096）。工具对不支持的尺寸触发「最近宽高比匹配 + LANCZOS 回缩」，保证方向不错。
- **图生图输入**：参考图以 `data:image/*;base64,` 前缀 Data-URI 传入（官方不接受纯无前缀 Base64）。
- **水印**：实现固定显式传 `watermark: false`，免受官方默认值变更影响。

---

## 9 常见问题与排错

**Q1：调用生图工具返回 `❌ 未配置 GEN_IMAGE_API_KEYS`？**
对应流水线没配 Key（或命名实例 Key 回退后仍为空）。在 `.env` 填 `GEN_IMAGE_API_KEYS` 后重启服务；多把 Key 逗号分隔。

**Q2：返回 `❌ 不支持的命名实例：zz`？**
`instance` 传的名字不在 `{PREFIX}_INSTANCES` 声明里。这是有意为之（不静默回退，避免误用主实例 Key 计费）；检查 `GEN_IMAGE_INSTANCES` 声明，或省略 `instance` 走主实例。

**Q3：返回 `❌ 该流水线已停用（{PREFIX}_ENABLED=false）`？**
该流水线开关被显式设为 `false`。检查对应 `{PREFIX}_ENABLED`（不配置或留空即视为开启）；视频线单独停用请查 `VISION_VIDEO_ENABLED`。

**Q4：某任务换 Key 后仍然连环报 400/404？**
大概率是「多 Key 混池」——池里混入了指向其他模型/接口的 Key。按第 6 章「同接口」约束分池：不同端点用不同配置段或不同命名实例隔离。

**Q5：尺寸报错或图片方向不对？**
生图线对不支持的尺寸会自动「最近宽高比匹配 + LANCZOS 回缩」，输出会提示 `⚡ 自动缩放`。若仍失败，确认 `size` 是 `WxH` 格式（如 `1024x1024`）。

**Q6：视频识别超时 / 很慢？**
dots 视频输入 TTFT 通常较长。默认超时 180s（`adapters/vision_base.py` 顶部 `TIMEOUT_SECONDS`），长视频可上调该常量；`r-vid` 的 `max_tokens` 建议保持 ≥8192 避免输出截断。

**Q7：环境变量改了但没生效？**
服务启动时从项目根 `.env` 加载一次；修改后需重启服务。命名实例变量注意大小写（如 `GEN_IMAGE_A_MODEL` 实例后缀大写）。

**Q8：配置取值与预期不符？**
先对照第 3.6 章回退链：实例专属 → 主实例 → 代码默认，且只有两类回退、无旧变量回退。排错先看回退链再动手。
