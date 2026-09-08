---
name: smart-summarize
description: 智能内容提取与知识库工具：提取 YouTube/B站视频字幕、网页正文、本地文件（PDF/Word/Excel/PowerPoint/EPUB/文本）与音视频语音转录；可选入库到本地知识库 workspace，支持关键词+语义混合检索（FTS5+Qwen3 向量+标题锚点三路融合）、按源文件结构（页码/章节/时间戳）定位精读与回放。只提取与索引，不调用 LLM；提取结果由当前 agent 阅读并总结。
compatibility: Windows / macOS / Linux / WSL；引导层任意 Python ≥3.8（仅标准库），首次使用自动安装专用运行时（含 SQLite ≥3.34）；音视频转录另需 ffmpeg、whisper.cpp 及 ggml 模型
---

# 智能内容提取工具 (smart-summarize)

**设计原则**：只负责内容提取，不调用 LLM。提取结果由当前 agent 阅读、总结或进一步处理。

## 支持的内容源

| 类型             | 支持格式                                                    | 说明                                                                     |
| -------------- | ------------------------------------------------------- | ---------------------------------------------------------------------- |
| **YouTube**    | 视频 URL                                                  | 通过 yt-dlp 提取手动/自动字幕和元数据                                                |
| **B站**         | 视频 URL                                                  | 提取 CC 字幕和视频信息（免登录 API）                                                 |
| **网页**         | HTTP/HTTPS 链接                                           | 通过 Jina Reader 提取正文                                                    |
| **文本文件**       | `.txt`, `.md`, `.markdown`, `.rst`, `.csv`              | 直接读取                                                                   |
| **PDF**        | `.pdf`                                                  | pdfplumber 或 PyMuPDF                                                   |
| **Word**       | `.docx`, `.doc`                                         | python-docx；`.doc` 另需 pandoc                                           |
| **EPUB**       | `.epub`                                                 | ebooklib                                                               |
| **Excel**      | `.xlsx`, `.xlsm`                                        | openpyxl（每个工作表一段，行以 " \| " 连接）                                         |
| **PowerPoint** | `.pptx`                                                 | python-pptx（每张幻灯片一段，结构化输出：`## 幻灯片 N` + `#` 标题 + `###` 副标题/正文/表格/演讲者备注） |
| **音频**         | `.mp3`, `.wav`, `.aac`, `.m4a`, `.flac`, `.ogg`, `.wma` | ffmpeg 转 PCM 后用 whisper.cpp 转录                                         |
| **视频**         | `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv`, `.flv`, `.webm` | 先提取内置字幕，无字幕则提取音频转录                                                     |

## 安装依赖（专用运行时，与系统 Python 彻底解耦）

技能使用**专用 Python 运行时**（独立 CPython 3.12 + 锁定版本的扩展库），安装到 `~/.smart-summarize/runtime/`，与用户系统的 Python 环境完全隔离——不向用户环境安装任何库，也不依赖其安装了什么版本。

- **首次使用自动引导安装**：检测到专用运行时缺失时，列出名称/来源/预计大小（约 150 MB 下载），经用户确认后自动下载安装（agent 征得同意后可加 `--download-deps` 非交互执行），完成后自动继续原任务；
- **引导层要求极低**：任意 Python ≥3.8（仅标准库）即可启动技能；扩展库（requests / yt-dlp / pdfplumber / PyMuPDF / python-docx / ebooklib / openpyxl / python-pptx）全部随专用运行时预装并锁定版本——技能测试通过的版本矩阵即用户实际运行的矩阵；
- Python 本体来自 python-build-standalone 独立构建（SHA256SUMS 校验）；下载源可用 `SMART_SUMMARIZE_PYTHON_MIRROR` 覆盖，pip 镜像可用 `SMART_SUMMARIZE_PIP_INDEX_URL`（国内网络建议配置）；
- 重置/升级：删除 `~/.smart-summarize/runtime/` 目录后重跑即可（用户知识库数据在 `workspaces/`，组件在 `bin/`、`models/`，均不受影响）；
- 音视频功能还需要 `ffmpeg`（首次使用按同一确认机制自动安装）；`.doc` 老格式需要 `pandoc`（不自动下载）。

入口优先使用 `SMART_SUMMARIZE_PYTHON` 作为引导解释器，未设置时使用 PATH 中的 `python`——它只负责启动技能并切换到专用运行时，不需要安装任何第三方库。

## 运行机制

模块结构（v0.6 起模块化，`extract.py` 只保留 CLI 入口与调度）：

```
scripts/extract.py      CLI 入口与调度
scripts/slicing.py      大文档分片协议（slice protocol）
scripts/extractors.py   内容提取器（YouTube/B站/网页/文档格式）
scripts/transcribe.py   whisper.cpp 转录（GPU 后端识别上报）
scripts/deps.py         组件定位、缺失检测与确认安装（专用运行时/ffmpeg/whisper/模型）
scripts/runtime.py      专用 Python 运行时（独立 CPython + 锁定扩展库，与系统 Python 解耦）
scripts/messages.py     zh/en 双语反馈（--lang 覆盖 / locale 自动探测）
scripts/workspace.py    知识库 workspace（FTS5 检索 / 时间戳索引 / 定位回放）
tests/                  自动化测试（test_<模块>.py）
```

提取流程：

```
extract.py 被调用（--url 或 --file）
  ├─ ① 类型检测：youtube / bilibili / web / 本地文件（按后缀）
  ├─ ② 分发
  │    ├─ youtube  → yt-dlp 拉字幕（失败且疑似登录墙 → 输出 cookieHint）
  │    ├─ bilibili → 免登录 API 拉 CC 字幕
  │    ├─ web      → Jina Reader
  │    └─ 本地文件 → 按 MIME 分派（文本直读/PDF/Word/EPUB 解析）
  ├─ ③ 音视频：先查 ffmpeg/whisper-cli/ggml 模型
  │     ├─ 全部就绪 → ffmpeg 转 WAV → whisper-cli 转录 → SRT/文本
  │     └─ 有缺失  → 列出清单（名称/用途/来源/大小）→ 用户确认
  │                    ├─ 同意 → 下载安装（仅装到 ~/.smart-summarize）→ 自动重跑原任务
  │                    └─ 拒绝/非交互 → 返回 JSON 缺失清单，不下载
  └─ ④ 输出 JSON（成功：title/author/transcript/content；失败：error/missing/cookieHint）
```

关键规则：

- 提取与总结分离：脚本永不调用 LLM；
- 组件只在缺失时、经确认后才下载，且只装进 `~/.smart-summarize`，不动系统目录；
- 每次运行的中间文件用 `ss_*` 临时目录，正常退出即清理；
- 无网络/组件缺失时返回结构化 JSON 错误，agent 可据此决定重试或向用户说明。

## 使用方法

统一入口：

```bash
PYTHON="${SMART_SUMMARIZE_PYTHON:-python}"
EXTRACTOR="<技能目录>/scripts/extract.py"
"$PYTHON" "$EXTRACTOR" --url "https://www.bilibili.com/video/BVxxxx"
"$PYTHON" "$EXTRACTOR" --file "document.pdf"
"$PYTHON" "$EXTRACTOR" --file "lecture.mp3" --output srt
"$PYTHON" "$EXTRACTOR" --file "lecture.mp3" --model large-v3-turbo-q5_0
```

Windows PowerShell 调用：

```powershell
$Python = if ($env:SMART_SUMMARIZE_PYTHON) { $env:SMART_SUMMARIZE_PYTHON } else { "python" }
& $Python "<技能目录>/scripts/extract.py" --file "lecture.mp3"
```

输出格式：

- `--output json`（默认）：完整 JSON（含 title/author/transcript/content/success）
- `--output text`：标题+正文纯文本
- `--output srt`：SRT 字幕（仅音视频转录）

附加参数与字段：

- `--download-deps`：缺组件时跳过交互确认直接下载安装（用于 agent 在征得用户同意后代为确认后重跑）；
- `--lang zh|en`：反馈语言。默认按系统语言自动探测（环境变量 `SMART_SUMMARIZE_LANG` 亦可覆盖）；自有错误文案在 JSON 中同时提供 `error_i18n: {"zh": ..., "en": ...}` 双份，agent 可按界面语言选用（原始异常文本不翻译）；
- 失败时 JSON 可能包含 `missing`（缺失组件清单）或 `cookieHint`（YouTube 需要登录验证的提示），agent 应原样展示给用户。

> YouTube 需要代理时，先设置 `HTTPS_PROXY`。YouTube 受限内容可能需要 cookies；公开字幕通常不需要。

## 典型场景（agent 操作手册）

按用户意图选择路径；同一命令对交互终端弹 y/N 确认、对 agent 输出结构化缺失清单（征得用户同意后加 `--download-deps` 重跑）。

**① 直接总结一份本地文档 / 一个网页 / 一条视频（不入库）**

```bash
"$PYTHON" "$EXTRACTOR" --file "报告.pdf"          # → JSON{title, content}
"$PYTHON" "$EXTRACTOR" --url "https://..."        # 网页正文
```

agent 直接阅读 content 总结回答，不涉及知识库与任何依赖安装（文档链零依赖）。

**② "把这本书/这份资料存进知识库"（入库 + 即时摘要）**

```bash
"$PYTHON" "$EXTRACTOR" --file "book.epub" --workspace 我的书架
```

入库结果含 entry_id/chunk_count/vectors（向量窗口数）；随后 agent 阅读提取文本给摘要。首次使用知识库会一次性引导安装嵌入链（llama.cpp 引擎 ~34MB + Qwen3 模型 ~610MB + sqlite-vec ~0.3MB）：交互终端直接 y/N；agent 先向用户展示清单征得同意，再以 `--download-deps` 重跑。库不存在隐式创建；同名内容幂等只更新。**同源重入库**（source_ref 相同）内容变更时会产生新条目，响应 `workspace.supersedes` 列出旧条目 id 并在 stderr 警告——确认后 `--remove <旧id>` 清理，或入库时加 `--replace` 自动替换。

**③ "我之前存过的那份资料里关于 X 讲了什么"（检索→精读闭环，推荐主路径）**

```bash
"$PYTHON" "$EXTRACTOR" --workspace "@我的书架" --search "X 关键词"       # 用户以 @库名 指定：@ 原样传入即可
"$PYTHON" "$EXTRACTOR" --workspace 我的书架 --search "X 关键词"            # 等价写法（agent 已解析库名时）
"$PYTHON" "$EXTRACTOR" --workspace 我的书架 --search "X" --mode fts       # 纯关键词（不加载向量链）
```

命中 JSON 字段（agent 消费指南）：`title/entry_id/source_type/source_ref`（来源文件或 URL）、`score + score_source + score_kind`（多路并列如 `fused+heading`）、`scores: {fts, vector, heading}`（fused 各路 RRF 贡献）、`column_filter`（列限定降级标注）、`keyword_miss`（FTS 零命中而仅语义召回，提示术语可能与原文不一致，勿据此断言“库中没有相关内容”）、`snippet`（『』高亮）、`chunk_no/chars`（可 `--chunk N` 读分片）、`heading{text,level}`（所在章节）、`section_ref`（不透明精读引用）+ `section_chars`、`same_section_hits`（同节其他命中数）、`source_loc`（源文件出处：`{kind:"pdf",page}` / `{kind:"epub",chapter,title}` / `{kind:"time",start_ms,end_ms}` / `{kind:"line",n}`）、`vector_backend`。检索零命中时换词或 `--mode vector` 重试（语义路可跨语言召回）。

**知识库检索话术对照（常见说法 → agent 动作）**

| 用户说法                                            | 判定    | agent 动作                                                                                                                                                                                               |
| ----------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| "@我的书架 查一下 XXX" / "**在**'我的书架'**库**里查" / "**用**我的书架库搜" / "**根据**我的资料库回答" / "**使用**XXX库检索" | 指定库（@/在/用/根据/使用 + 库名） | 从提示词解析库名 → `--workspace <名> --search ...`；**@ 前缀可原样传入**（CLI 自动剥离）；库名不确定时先 `--workspace-list` 解析（模糊匹配是 agent 的活） |
| "在知识库'我的书架'里**查找** XXX"                         | 指定库检索 | `--workspace 我的书架 --search "XXX"`（默认 fused）；给出命中清单（标题/章节/snippet/出处），深问再 `--section` 精读                                                                                                                |
| "在知识库里**查一下** XXX"（未指定库）                        | 跨库检索  | `--search "XXX" --all-workspaces`——结果带 `workspace` 字段标注来源库；命中分散在多库时按库归组陈述                                                                                                                              |
| "在知识库 XX 中**研究一下**是否 XXX / 有没有讲 XXX / 是否支持 XXX" | 核实型问题 | ① `--search "XXX"`（fused）；② 零命中 → 换近义词/拆词重试，或 `--mode vector`（语义路可跨语言召回，中文问句可召回英文资料）；③ 命中后对最高分 1-3 条 `--section` 精读；④ **回答必须带出处**（条目标题 + `source_loc` 页码/章节/时间戳）；库内确无相关内容时明说"知识库中未见相关内容"，不要用模型记忆替代检索结论 |
| "**对比**一下 A、B 两份资料对 XXX 的说法"                    | 多源对比  | 分别 `--search`（或同库检索后按 `entry_id` 分组）→ 各取最优节 `--section` 精读 → 分来源对比陈述，引用各自 `source_loc`                                                                                                                 |
| "知识库里**都有什么**/都有哪些资料"                           | 盘点    | `--workspace <名> --list`（条目清单）或 `--stats`（条目/字符/来源分布/db 体积）                                                                                                                                            |
| "把这几份文件都**收进**知识库"                              | 批量入库  | 逐个 `--file ... --workspace <名>`；幂等无重复；大文件自动走分片协议                                                                                                                                                       |
| "**搜一下**标题里有 XX 的条目" | 元数据过滤 | `--search 'title:XX'`（列限定 `title:/author:/publisher:/publish_date:`，可与正文词组合作 `title:XX AND 关键词`；**列值需 ≥3 字**（trigram 物理限制）；列限定查询自动走 fts 路，结果标注 `column_filter: true`） |

核实型问题的工作示例（"查一下知识库里讲 move 语义的内容"）：

```bash
"$PYTHON" "$EXTRACTOR" --workspace 我的书架 --search "move 语义"                  # ① 三路融合检索
"$PYTHON" "$EXTRACTOR" --workspace 我的书架 --search "move 语义" --mode vector    # ② 零命中时语义路重试
"$PYTHON" "$EXTRACTOR" --workspace 我的书架 --section "<最优命中的 section_ref>"   # ③ 精读整节后作答
```

`section_chars` 超大时改用 `--entry <id> --chunk N` 按分片读；媒体条目命中带 `start_ms/end_ms`，可直接 `--play --at` 定位佐证。

**④ "第 14 条具体讲了什么"（结构锚点定位精读）**

用 `--search "条款14"`（标题路直接命中章节标题），把返回的 `section_ref` 原样传入：

```bash
"$PYTHON" "$EXTRACTOR" --workspace 我的书架 --section "<section_ref>"     # 精读整节
```

结构锚点自动来自 docx 标题样式 / EPUB h1-h6 / PDF 书签 / "第N章、条款N、Chapter N、编号标题"启发式；`--reindex` 可为老条目补建。老条目 `source_loc` 可能为 null（无源位置账本），重新入库即得完整锚点。

**⑤ "上次那个视频里讲 Y 的片段在哪"（检索 + 定位回放）**

```bash
"$PYTHON" "$EXTRACTOR" --workspace 我的书架 --search "Y 主题"              # 媒体命中带 start_ms/end_ms
"$PYTHON" "$EXTRACTOR" --workspace 我的书架 --play <entry-id> --at 12:33 [--duration 60]
```

检测不到播放器时返回结构化 JSON（candidates/hint），agent 向用户说明或代装播放器，不弹界面。

**⑥ 长文档（>256K 字符）总结**

stdout 只返回分片清单（<1KB）。agent 按清单逐片读取，**每片读完立即产出要点摘要**，用户指定的关注维度（感情线/时间线等）必须原样注入逐片摘要（防合并阶段丢线索）；读完核对 total_chunks。小文档直出，无需此流程。

**⑦ 多库与跨库**

```bash
"$PYTHON" "$EXTRACTOR" --search "关键词" --all-workspaces                  # 跨全部库检索
"$PYTHON" "$EXTRACTOR" --workspace-list / --workspace <名> --stats / --list  # 管理盘点
```

**⑧ agent 调用约定（普适）**：解析 JSON 输出；`error` 优先于盲目重试；`missing` 清单须先征得用户同意再加 `--download-deps` 重跑；删除类操作（`--remove`/`--delete-workspace`）返回 `confirm_required` 时必须向用户确认后加 `--yes`；双语 `error_i18n` 按界面语言选用。

## 临时目录：何时使用、保存什么

脚本只在需要中间文件的流程调用临时目录：

- YouTube：保存 yt-dlp 下载的字幕文件；
- 音频转录：保存 ffmpeg 生成的 16 kHz 单声道 WAV 及 whisper.cpp 生成的 SRT；
- 视频处理：保存内置字幕或抽取出来的音频 WAV。

B站字幕、网页正文、文本/PDF/Word/EPUB 通常不使用本工具的临时目录。每次运行使用 `ss_*` 子目录，正常结束会删除；异常遗留目录超过 72 小时会在后续运行时清理。

临时根目录解析顺序：

1. `SMART_SUMMARIZE_TMPDIR`（显式指定，支持 `~`）；
2. 其余一律使用 Python `tempfile.gettempdir()`：Windows 通常为 `%LOCALAPPDATA%\Temp`，macOS 为 `/var/folders/.../T`，Linux/WSL 为 `/tmp`。

例如：

```bash
export SMART_SUMMARIZE_TMPDIR="$HOME/.cache/smart-summarize-tmp"
```

不要把 cookies、模型或重要原始文件放入临时目录。

## ffmpeg 与 whisper.cpp

**初始安装不下载任何组件；实际使用时运行时检测。** 所有自动下载的组件都只装在用户目录（`~/.smart-summarize`），不写系统目录。

### 检测顺序（每次转录前自动执行）

- ffmpeg：`SMART_SUMMARIZE_FFMPEG` → PATH → 已下载到受管目录的副本。
- whisper-cli：`SMART_SUMMARIZE_WHISPERCPP_CLI` → PATH → `SMART_SUMMARIZE_WHISPERCPP_DIR` → 受管目录。
- ggml 模型：`SMART_SUMMARIZE_WHISPERCPP_MODELS_DIR` → 受管模型目录。模型文件名为 `ggml-large-v3-turbo.bin` 或 `ggml-large-v3-turbo-q5_0.bin`。
- 受管目录：`SMART_SUMMARIZE_HOME`（默认 `~/.smart-summarize`，下设 `bin/` 与 `models/`）。已有自定义安装的用户可用上述环境变量指向任意位置。

### 缺失时：提示并经确认后下载

音频/视频任务检测到缺失组件时，脚本会列出每项的**名称、用途、来源与预计大小**，等用户确认后才下载安装，完成后自动继续原任务：

- 交互终端：直接 `y/N` 确认；
- agent/脚本调用：先向用户展示清单并征得同意，再重新运行并加 `--download-deps`；用户未同意时脚本只报告缺失清单，不下载。

下载来源与安装位置：

- ffmpeg：Windows 用 gyan.dev zip、macOS 用 evermeet.cx、Linux x86_64/arm64 用 johnvansickle 静态包；安装到 `SMART_SUMMARIZE_HOME`（默认 `~/.smart-summarize/bin`）。
- whisper-cli：按硬件自动选版本安装：
  ①macOS/Linux 有 Homebrew 时 `brew install whisper-cpp`（macOS Metal 默认启用）；
  ②Windows：检测到 **NVIDIA GPU** 时优先下载官方 **cublas 预编译版**（自带 CUDA 运行库，无需 CUDA Toolkit，约 270MB）；否则下载官方 CPU 预编译 zip，并按 GPU 厂商给出升级指引（AMD/Intel：装 Vulkan SDK 后删受管二进制重跑即可源码构建 Vulkan 版）；
  ③源码构建兜底（需 git/cmake/编译器），构建时自动按硬件选后端：NVIDIA + CUDA Toolkit → CUDA；AMD + ROCm → HIP（自动检测 gfx 架构）；有 Vulkan SDK → Vulkan（A 卡核显如 Radeon 780M、Intel 核显的唯一官方 GPU 路径）；都没有则 CPU（并明确告知）。也可用 `SMART_SUMMARIZE_WHISPERCPP_CMAKE_FLAGS` 追加自定义 CMake 参数；需要换后端时删除 `~/.smart-summarize/whisper.cpp` 构建目录及受管 bin 中的二进制后重试。
- ggml 模型：从 HuggingFace `ggerganov/whisper.cpp` 下载（large-v3-turbo 约 1.6GB，q5_0 约 560MB），存到上述模型目录首个可用位置。

### 默认下载版本矩阵（whisper-cli）

按"预编译优先、GPU 编译作为显式升级路径"原则，各环境首次自动安装的版本：

| 用户环境                    | 首次自动安装的版本                          | GPU 加速？                                         |
| ----------------------- | ---------------------------------- | ----------------------------------------------- |
| Windows + NVIDIA        | 官方 cublas 预编译（自带 CUDA 运行库）         | ✅ 是                                             |
| Windows + AMD/Intel GPU | 官方 CPU 预编译 + 升级指引（装 Vulkan SDK 重跑） | ❌ 否，需显式升级                                       |
| Linux（任意 GPU）           | 官方 ubuntu CPU 预编译 / brew           | ❌ 否（源码构建时检测到 Toolkit/ROCm/Vulkan SDK 才编出 GPU 版） |
| WSL                     | 同 Linux                            | ❌ 否（GPU 版还需 WSL 驱动透传）                           |
| macOS                   | brew install whisper-cpp           | ✅ 是（Metal 默认开启）                                 |

官方预编译资产只有 Windows N 卡 cublas、macOS xcframework 和各平台 CPU 版；A 卡/Intel 的 Vulkan 与 Linux CUDA 只有源码/Docker 形式，因此技能不会静默编译，只给出明确升级指引。

### GPU 加速支持矩阵

转录完成后，stderr 会标注实际使用的计算后端（`🎮 GPU 加速: Vulkan: AMD Radeon 780M...` / `🖥 CPU`）。加 `--no-gpu` 可强制 CPU。

| GPU               | 推荐后端              | Windows 获取方式                        | Linux/macOS 获取方式                            |
| ----------------- | ----------------- | ----------------------------------- | ------------------------------------------- |
| **NVIDIA**        | CUDA              | 官方 cublas 预编译 zip（自动选用，自带 CUDA 运行库） | 源码构建（需 CUDA Toolkit）或官方 main-cuda Docker 镜像 |
| **AMD 独显**        | ROCm/HIP 或 Vulkan | 源码构建（Vulkan SDK 或 ROCm）             | 源码构建（有 ROCm 用 HIP，否则 Vulkan SDK）            |
| **AMD/Intel 核显**  | Vulkan            | 源码构建（需 Vulkan SDK，官方无 A 卡 GPU 预编译）  | 同左                                          |
| **Apple Silicon** | Metal             | —                                   | 默认启用，无需任何配置                                 |

GPU 是否启用取决于 whisper.cpp 二进制编译时包含的后端；CPU 构建或 GPU 后端/驱动不可用时回退为 CPU。可从转录 stderr 日志确认实际加载的 backend。`large-v3-turbo-q5_0` 仅是量化模型，不等于 GPU 加速。

## 大文档处理协议（slice protocol）

单个文档提取内容超过 256K 字符时，脚本**不会**把全文塞进 stdout（防截断与上下文溢出），而是：

1. 在受管临时目录落盘分片：`ss_slice_<hash>/chunk-001.md ...`（Markdown 结构化文本），每片 ≤40K 字符、段落边界对齐、相邻片重叠 300 字符；
2. stdout 只输出**清单 JSON**（<1KB）：title / total_chars / total_chunks / chunk_dir / 每片文件名与校验和。

agent 收到清单后的标准流程（写入 SKILL.md 供所有 agent 遵循）：

1. 读清单，确认 `total_chunks`；
2. 按序读取分片文件，**每片读完立即产出一段要点摘要**（不要攒到最后）；
3. **用户的总结指令必须原样注入逐片摘要**：若用户指定了关注维度（如"按时间线梳理""男主对女主的感情线变化"），逐片摘要必须以这些维度做定向提取（例："本段感情线相关情节：…"），不得只做泛化摘要——关系型/全局型线索在 map 阶段丢失，合并阶段无法找回；
4. 全部片读完后合并摘要做最终总结；
5. 校验已读片数 == total_chunks，缺片时用 `--slice N` 或直接补读缺失文件；
6. 上下文紧张时可隔片抽取要点，但结尾片必须读（结论通常在末尾）。

`--slice N` 可让提取器直接输出第 N 片内容（JSON），适合不支持读文件工具的环境。小文档（≤256K 字符）行为不变，stdout 直出。

## 知识库 workspace（SQLite FTS5 全文检索 + 时间戳定位回放）

提取的内容可入库到本地知识库（workspace）供后续检索与精读。**只提取与索引，不调用 LLM**；检索引擎为 Python 标准库 sqlite3 内置的 FTS5（trigram 分词器），零外部依赖，支持 BM25 相关性排序、snippet 摘要、短语/布尔/前缀/NEAR 查询。

### 摄入（提取时入库）

```bash
# --workspace 带上即入库；库名不存在时隐式创建（也支持显式 --workspace <名> --create）
"$PYTHON" "$EXTRACTOR" --file "document.pdf" --workspace 我的资料
"$PYTHON" "$EXTRACTOR" --url "https://www.bilibili.com/video/BVxxxx" --workspace 我的资料
# 元数据可选指定（title/author/publisher/publish-date），缺省自动取自内容或文件名
"$PYTHON" "$EXTRACTOR" --file "lecture.mp3" --workspace 我的资料 --title "讲座标题" --author "作者"
```

入库规则：

- **幂等**：条目 id = 内容 sha256 前 16 位；同内容重灌只更新元数据，不产生重复条目；
- **来源副本**：本地文件与网页快照默认复制到 `source/<id>/`（音视频默认复制——回放必需），`--no-keep-source` 可关；
- **音视频时间戳索引**：入库的音视频统一走 whisper SRT 转录，段级时间戳存 `transcript.json`，每个分片带 `start_ms`/`end_ms`（普通提取不受影响：视频仍先试内置字幕）。

### 检索

```bash
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --search "全文检索" --limit 10
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --search 'publisher:出版社名 AND 关键词'
"$PYTHON" "$EXTRACTOR" --search "关键词" --all-workspaces     # 跨全部库，结果标注来源库名
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --search "什么是机器学习" --mode vector  # 纯语义检索（跨语言）
"$PYTHON" "$EXTRACTOR" --file doc.md --workspace 我的资料 --no-embed               # 仅 FTS 入库
```

**混合检索（默认 fused）**：三路并行——语义向量路（Qwen3-Embedding，中英文/跨语言）、FTS5 关键词路、标题锚点路（结构标题独立索引），RRF 融合排序。命中结果带 `score_source`（fused/fts/vector/heading，多路同节命中并列标注如 `fused+heading`）；`score` 的语义看 `score_kind` 判别字段：`coverage`=0..1 覆盖率（命中查询词数/总词数，`--mode fts` 与 heading 路）、`similarity`=向量余弦、`rrf`=fused 融合排序分（1/(60+rank)，**跨查询不可比、不表达语义相关度，仅组内排序**；置信度判断应结合 `score_kind` 与 `fts_detail.coverage_terms`）；bm25 原值在 `fts_detail.bm25_raw`；同章节多个碎片命中自动聚合为一条（`same_section_hits` 计数），代表命中附所在标题 `heading` 与 `section_ref`。入库默认自动嵌入（`--no-embed` 可关）；首次使用知识库时一次性引导安装嵌入引擎与向量模型（y/N 确认）。

查询语法：≥3 字词进 trigram 索引（输入自动转义）；**自然多词默认 OR 召回 + 覆盖率重排**（单词命中也返回，双词命中排前）；`AND`/`OR`/`NOT`/`NEAR(a b, 5)`/`前缀*` 原样透传；`title:`/`author:`/`publisher:`/`publish_date:` 可限定列；**<3 字中文词**（trigram 物理限制）自动回退 chunks 表 LIKE 并在结果中标注 `like-low-precision`。

**结构感知入库（v0.8.0）**：docx 标题样式 / EPUB h1-h6 / PDF 内嵌书签自动归一化为标题锚点，裸文本启发式识别"第N章/条款N/Chapter N/编号标题"（老条目 `--reindex` 补建）；出处锚定**源文件结构**——PDF 页码、EPUB 章节、音视频时间戳、文本行号（`source_loc` 字段），full.md 内部坐标不对外暴露。

### 读取（agent 精读对象是 full.md，路径内部化）

```bash
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --entry <entry-id>            # full.md 全文
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --entry <entry-id> --chunk 3  # 指定分片
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --section <section_ref>       # 精读命中所在整节（推荐）
```

命中落在首个标题之前（前言/目录区）时 `section_ref` 为 `entry_id#front` 哨兵引用，同样可 `--section` 精读。检索命中附 `section_ref`（不透明引用）与 `section_chars`——agent 将 ref 原样传给 `--section` 即可精读整节。标题稀疏的长文档（如扫描书）会自动在超长章节内生成 20K 步长的**合成子节锚点**（标题为 `父标题·续N`，不参与标题检索），命中归位与 `--section` 精读粒度回到 20K。**读取默认上限 30,000 字符**（`--max-chars` 对 entry/chunk/section 三读路径统一生效；0=不限），超出截断并标注 `truncated/total_chars/remaining_chars`；超大节按命中位置开窗返回（`section_ref` 内嵌命中偏移），保证内容围绕命中词。媒体命中带 `start_ms/end_ms` 时间戳（精确到命中词所在段落，`timestamp_precision: segment/window/chunk` 标注精度来源；配 `--play --at` 定位回放）。

### 管理操作全集

| 操作    | 命令                                               |
| ----- | ------------------------------------------------ |
| 显式创建  | `--workspace <名> --create`                       |
| 列举库   | `--workspace-list`                               |
| 删除库   | `--workspace <名> --delete-workspace`（需 `--yes`；误用 `--delete-workspace <名>` 走 usage 错误，须配 --workspace） |
| 重命名   | `--workspace <旧名> --rename <新名>`                 |
| 统计    | `--workspace <名> --stats`（条目/字符/分片/来源分布/db 体积）   |
| 条目列举  | `--workspace <名> --list`                         |
| 条目删除  | `--workspace <名> --remove <entry-id>`（需 `--yes`） |
| 完整性校验 | `--workspace <名> --verify`（片数/逐片一致性/覆盖/FTS 索引比对） |
| 索引重建  | `--workspace <名> --reindex`（重建 FTS+标题锚点；**不含向量**） |
| 向量补建  | `--workspace <名> --embed`（为零向量条目补建向量，需嵌入链） |
| 空间回收  | `--workspace <名> --vacuum`                       |

删除类操作默认只输出 `confirm_required: true` 与将删除的路径——agent 须向用户确认后加 `--yes` 重跑。跨机器迁移 = 直接拷贝 workspace 目录（自包含），无需命令。

### 定位回放（音视频）

```bash
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --play <entry-id> --at 12:33 [--duration 60]
```

- 播放器探测链按平台参数化：Windows `VLC → PotPlayer → mpv → 系统关联`；macOS `VLC → IINA → mpv → open`；Linux `VLC → mpv → xdg-open`；WSL `wslview → Windows 侧 VLC`；
- VLC/mpv/PotPlayer 支持从命中时间点起播（可加 `--duration` 限定时长）；系统默认方式只能从头播（结果标注 `degraded: true`）；
- **检测不到播放器时输出结构化 JSON**（`candidates`/`hint`/`play_cmd: null`）交由 agent 处理（向用户说明、经确认后代装 VLC 等），技能不弹界面。

### 环境要求（由专用运行时保证）

workspace 依赖 SQLite ≥3.34（FTS5 trigram）。v1.4 起技能固定运行在专用运行时内（独立 CPython 3.12，内置 SQLite 3.5x），trigram 恒可用、与用户系统 Python 无关；运行时缺失时由引导安装流程补齐——**宁可明确报错，不做低精度降级检索**。

### 目录结构（workspace 自包含，拷走目录即完成迁移）

```
~/.smart-summarize/workspaces/<库名>/
├── workspace.db            # SQLite（WAL 模式）：entries / chunks / entries_fts
├── source/<entry-id>/      # 原始来源副本（音视频/网页快照/字幕原始文件）
└── entries/<entry-id>/
    ├── meta.json           # 元数据（标题/来源/作者/出版信息/分片数/副本文件名）
    ├── full.md             # 全量提取文本（检索命中按 offset 精读）
    └── transcript.json     # 音视频段级时间戳（含每段在 full.md 中的字符区间）
```

workspace 根目录可用 `SMART_SUMMARIZE_WORKSPACES_DIR` 覆盖（默认 `~/.smart-summarize/workspaces/`；WSL 内注意勿放 /mnt/c 下，避免性能与文件锁问题）。

## Cookies 隐私规则

技能包**不携带任何 cookies 文件**，也不再要求用户预先配置路径。

- cookies 文件的约定位置自动确定：
  - YouTube：`~/.smart-summarize/cookies/youtube-cookies.txt`（或用 `SMART_SUMMARIZE_YOUTUBE_COOKIES` 指定任意位置）
  - B站：`~/.smart-summarize/cookies/bilibili-cookies.txt`（或用 `SMART_SUMMARIZE_BILIBILI_COOKIES` 指定任意位置；支持 Netscape 格式或原生 Cookie 头格式两种文件）
  - B站 cookies 用于登录墙内容（如 **AI 自动字幕**——无登录态时接口返回空列表，只能拿到 UP 主手动上传的 CC 字幕）；
- 平时（公开视频）不需要 cookies，脚本直接匿名访问；
- 只有当 yt-dlp 因登录验证/风控/年龄限制失败时，脚本才会在 `cookieHint` 字段和 stderr 中提示用户：用浏览器扩展（如 Get cookies.txt LOCALLY）导出 Netscape 格式 cookies，**自己手动**保存到上述路径后重试；
- 技能永不自动创建、收集或上传 cookies，文件只由用户手动放置。

cookies 具有账号会话权限，不能提交到技能仓库、复制到其他 agent 或放进共享目录；请注意文件权限。

## 与 agent 配合

```
用户请求 → extract.py 提取内容 → 当前 agent 阅读并总结 → 回复用户
```

- 提取失败时先看 `error` 字段，不要盲目重试；
- 长内容总结时注意上下文预算，必要时分段处理；
- 网页和 YouTube 在具备原生网页工具的 agent 中可优先使用其网页读取能力；本脚本尤其适合 B站字幕、本地文档和本地音视频转录。

## 故障排除

| 症状                             | 处理                                                                                       |
| ------------------------------ | ---------------------------------------------------------------------------------------- |
| 找不到 `python`                   | 设置 `SMART_SUMMARIZE_PYTHON` 为目标解释器的完整路径                                                  |
| YouTube yt-dlp 报 JS runtime 错误 | 安装 Node.js 并确保 `node` 在 PATH；脚本使用 `--js-runtimes node`                                   |
| 音视频提示 whisper.cpp 不可用          | 运行时检查会列出缺失组件与大小；同意后确认或由 agent 加 `--download-deps` 重跑                                     |
| YouTube 提示需要 cookies           | 按提示用浏览器扩展导出 Netscape 格式 cookies 保存到 `~/.smart-summarize/cookies/youtube-cookies.txt` 后重试 |
| PDF 提取为空                       | 扫描件没有文字层，属正常；本工具不做 OCR                                                                   |
| B站无字幕                          | 该视频没有 CC 字幕，API 返回 `success:false`，属正常                                                   |
| `--mode vector` 恒 0 命中 | 先查该库入库时是否用了 `--no-embed`（`--list` 的 `vectors` 字段为 0 即是）；补建：`--embed` 或重入库不加减嵌入 |

## 版本

当前版本见 package.json；完整版本历史见 [README.md](./README.md) 更新日志章节。
