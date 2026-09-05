---
name: smart-summarize
description: 智能内容提取工具：提取 YouTube/B站视频字幕、网页正文、本地文件（PDF/Word/Excel/PowerPoint/EPUB/文本）与音视频语音转录；可选知识库 workspace（SQLite FTS5 全文检索 + 音视频时间戳定位回放）。只提取，不调用 LLM；提取结果由当前 agent 阅读并总结。
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
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --search "全文检索"
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --search 'publisher:出版社名 AND 关键词'
"$PYTHON" "$EXTRACTOR" --search "关键词" --all-workspaces     # 跨全部库，结果标注来源库名
```

查询语法：≥3 字词进 trigram 索引（输入自动转义）；`AND`/`OR`/`NOT`/`NEAR(a b, 5)`/`前缀*` 原样透传；`title:`/`author:`/`publisher:`/`publish_date:` 可限定列；**<3 字中文词**（trigram 物理限制）自动回退 chunks 表 LIKE 并在结果中标注 `like-low-precision`。

### 读取（agent 精读对象是 full.md）

```bash
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --entry <entry-id>            # full.md 全文
"$PYTHON" "$EXTRACTOR" --workspace 我的资料 --entry <entry-id> --chunk 3  # 指定分片
```

检索命中输出 `full_path + offset + chars`——agent 按偏移精读 full.md 上下文（与分片协议同一原则：读全量提取文本，不读原始二进制）。

### 管理操作全集

| 操作 | 命令 |
| --- | --- |
| 显式创建 | `--workspace <名> --create` |
| 列举库 | `--workspace-list` |
| 删除库 | `--workspace <名> --delete-workspace`（需 `--yes`） |
| 重命名 | `--workspace <旧名> --rename <新名>` |
| 统计 | `--workspace <名> --stats`（条目/字符/分片/来源分布/db 体积） |
| 条目列举 | `--workspace <名> --list` |
| 条目删除 | `--workspace <名> --remove <entry-id>`（需 `--yes`） |
| 完整性校验 | `--workspace <名> --verify`（片数/逐片一致性/覆盖/FTS 索引比对） |
| 索引重建 | `--workspace <名> --reindex` |
| 空间回收 | `--workspace <名> --vacuum` |

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

## 更新日志

### v0.6.0（模块化拆分 + 专用运行时 + 知识库 workspace + 双语反馈）

- **专用 Python 运行时（v1.4 方案）**：独立 CPython 3.12（python-build-standalone，SHA256SUMS 校验）+ 锁定版本扩展库安装到 `~/.smart-summarize/runtime/`，与用户系统 Python 彻底解耦——不向用户环境装库、不设系统环境变量、首次使用经确认自动安装；引导层仅需任意 Python ≥3.8（标准库）；根治用户环境依赖版本不可控类缺陷；
- **模块化**：单文件 extract.py（1375 行）拆分为 slicing / extractors / transcribe / deps / messages / workspace / runtime 七个模块，CLI 只保留入口、调度与运行时闸门（纯重构，按 7.6 规则并入本版本不发单独版）；
- **新增知识库 workspace**：SQLite FTS5+trigram 全文检索（标准库零依赖、BM25 排序、snippet 摘要、布尔/前缀/NEAR/列限定查询、<3 字自动 LIKE 回退）、来源文件副本、full.md 偏移精读、13 项管理操作、完整性校验（含 FTS 索引逐行比对）、索引重建/VACUUM、跨库检索；
- **音视频时间戳索引与定位回放**：入库音视频统一 whisper SRT 转录 → 分片带 start_ms/end_ms → 播放器探测链按 OS 参数化定位播放；缺播放器输出结构化 JSON 交由 agent 处理；
- **双语反馈**：messages.py 集中管理 zh/en 文案，`--lang` 显式覆盖 / locale 自动探测；自有错误文案带 `error_i18n` 双份；
- **FTS 环境自动检测**：SQLite <3.34 或缺 FTS5 时探测本机可用解释器自动切换重跑，全部失败给出按 OS 安装指引；不做降级检索；
- 修复拆分过程与历史遗留缺陷：extract_local_file 丢失、deps 缺 import、WHISPERCPP_CUBLAS_ASSETS 未定义（NVIDIA cublas 安装链路）、YouTube 元数据静默丢失（缺 import json）、YouTube 临时目录绕过受管链路、连续切片重叠窗口失效；
- 修复存量缺陷（端侧验证发现）：EPUB 依赖检测永远误报缺失（deps.py epub 组缺 `import: "ebooklib"` 声明，组名被当作导入名）；EPUB 提取在 ebooklib 0.20 下整体失败（`ITEM_DOCUMENT` 常量须取自顶层 ebooklib 模块，`ebooklib.epub` 命名空间自 0.20 起不再暴露）。

> 版本号说明：早期条目的 v3.x 为历史内部功能版本号，自 v0.4.0 起与 npm 包版本号对齐。

### v0.5.0（Excel/PowerPoint 支持 + 大文档分片协议）

- 新增 Excel (.xlsx/.xlsm) 与 PowerPoint (.pptx) 提取（运行时按需确认安装 openpyxl/python-pptx）；
- 大文档处理协议：>256K 字符自动分片落盘（段落边界 + 300 字符重叠窗口 + 每片校验和），stdout 只输出清单；`--slice N` 可直接取单片；
- SKILL.md 新增「大文档处理协议」章节，供所有 agent 遵循分页读取与增量总结流程。
- 协议硬规则：分片模式下逐片摘要必须原样保留用户总结指令中的关注维度（关系型/全局型任务防丢线索）。

### v0.4.1（B站直连修复）

- B站 API 请求默认绕过 Windows 系统代理直连（系统代理转发国内站常报 SSL EOF，导致 B站提取整体失败）；
- 用户显式设置 `SMART_SUMMARIZE_PROXY` 或终端 `HTTPS_PROXY` 时尊重该代理；

### v0.4.0（B站登录态 / AI 字幕支持）

- 新增 `SMART_SUMMARIZE_BILIBILI_COOKIES`（或受管目录 `bilibili-cookies.txt`）：有登录态时 B站 AI 自动字幕、登录墙视频可正常提取；
- 支持 Netscape 导出格式与原生 Cookie 头格式两种 cookies 文件；
- 未配置时行为完全不变（免登录提取 UP 主 CC 字幕）。

### v3.5.3（Python 库运行时预检与确认安装）

- 首次提取 PDF/Word(.docx)/EPUB/YouTube/网页时运行时检测对应 Python 库（pdfplumber/PyMuPDF/python-docx/ebooklib/yt-dlp/requests）；
- 缺失时与 ffmpeg/whisper 组件同一机制：列出名称/用途/安装命令，经用户确认后用当前解释器 pip 安装并自动继续；agent 可用 `--download-deps` 代为确认；
- 原先缺库仅 stderr 一句警告且 JSON 分不清“库缺失”与“文件损坏”，现在错误结构化为 `missing` 清单。

### v3.5.2（cookies 自动路径）

- cookies 文件默认位置自动确定：`~/.smart-summarize/cookies/youtube-cookies.txt`，无需预先配置；
- YouTube 遇登录墙/风控时输出 `cookieHint` 字段，提示用户手动导出 cookies 到该路径；
- yt-dlp 优先从当前 Python 环境查找；视频缺 ffmpeg 时走统一的缺失提示而非静默失败。

### v3.5.1（whisper-cli 三级安装与 GPU 检测）

- whisper-cli 安装优先级：brew 预编译包 → GitHub 官方预编译 zip → 源码构建兜底；
- 源码构建时自动检测 CUDA/Vulkan 工具链并如实告知；支持 `SMART_SUMMARIZE_WHISPERCPP_CMAKE_FLAGS`。

### v3.5（运行时确认下载）

- 转录前运行时检测 ffmpeg/whisper-cli/ggml 模型；
- 缺失时列出名称/用途/来源/预计大小，经用户确认后下载安装到 `~/.smart-summarize`，然后自动继续；
- agent 代为确认后可用 `--download-deps` 非交互执行。

### v3.4（跨平台与隐私补丁）

- 移除绝对 Python/venv 路径和 agent 专属表述；补充跨平台依赖安装命令；
- 临时目录改为 OS 相关默认值，支持 Windows/macOS/Linux/WSL；
- `whisper-cli` 按 OS 查找，ffmpeg 统一走 PATH/显式路径；
- 不再从技能目录读取 cookies，改为用户显式配置路径。

### v3.3

- 音频转录只走 whisper.cpp，移除 faster-whisper 回退与模型下载逻辑。
