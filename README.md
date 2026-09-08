# smart-summarize

[English](README.en.md) | 中文

![Platform](https://img.shields.io/badge/platform-Windows_%7C_macOS_%7C_Linux_%7C_WSL-0078D4)
![GitHub tag](https://img.shields.io/github/v/tag/Cdexs/smart-summarize?label=version&color=green)
![License](https://img.shields.io/npm/l/@cdexs/smart-summarize?color=orange)

可自动进行网页、网络视频、本地文件（PDF/Word/Excel/PowerPoint/EPUB/文本）、音视频进行读取、总结，支持通过本地 ASR 模型将本地音频、视频内容高准确率地转写到文本和字幕文件，并可将提取内容存入本地知识库（SQLite FTS5 + 本地向量模型**混合检索**，支持中英文跨语言语义检索 + 音视频时间戳定位回放）。

智能内容提取技能包（agent skill）——提取 YouTube/B站视频字幕、网页正文、本地文件（PDF/Word/EPUB/文本）与音视频语音转录。**只提取，不调用 LLM**；提取结果交给宿主 agent 总结。

跨平台：Windows / macOS / Linux / WSL。

## 特性

- **纯提取**：不调用任何 LLM，输出 JSON/文本/SRT，由当前 agent 阅读总结
- **专用运行时隔离**：独立 CPython 3.12 + 锁定版本扩展库装在 `~/.smart-summarize/runtime/`，与用户系统 Python 彻底解耦——不装库到用户环境、不设系统环境变量、不被用户环境变化影响
- **零硬编码路径**：所有组件按 环境变量 → PATH → 用户目录（`~/.smart-summarize`）的顺序发现
- **运行时按需安装**：首次使用音视频转录时检测缺失组件，列出名称/用途/来源/预计大小，经用户确认后下载安装，随后自动继续；不会静默下载
- **知识库 workspace**：混合检索——SQLite FTS5 关键词路 + 本地 Qwen3 向量语义路（RRF 融合，**中英文跨语言**）、来源副本、偏移精读、13 项管理操作、音视频时间戳定位回放
- **中英双语反馈**：`--lang` 或 locale 自动探测，JSON 错误带 `error_i18n` 双份
- **隐私安全**：不携带、不读取技能目录内的任何 cookies；YouTube cookies 默认从 `~/.smart-summarize/cookies/youtube-cookies.txt` 读取（由用户手动导出放置），仅遇登录墙时才提示需要
- **GPU 中立**：是否启用 GPU 取决于用户安装的 whisper.cpp 构建（Vulkan/Metal/CUDA）；自动构建时会检测工具链并如实告知

## 安装

**从 npm 安装（推荐）：**

```bash
npm install @cdexs/smart-summarize
```

**pi 用户一键安装（自动装到技能目录）：**

```bash
pi install npm:@cdexs/smart-summarize
```

安装后技能包位于 `node_modules/@cdexs/smart-summarize/`，把该目录（或整个目录）放到 agent 的技能目录（如 `~/.pi/agent/skills/smart-summarize`），或直接指定路径运行：

```bash
python node_modules/@cdexs/smart-summarize/scripts/extract.py --file demo.pdf
```

**从源码安装：**

把本目录放到 agent 的技能目录（如 `~/.pi/agent/skills/smart-summarize`），或直接在本目录运行：

```bash
# 依赖无需预装：首次使用时自动安装技能专用运行时（独立 CPython 3.12 + 锁定扩展库），
# 与系统 Python 完全隔离；引导层仅需 Python ≥3.8（仅标准库）

python scripts/extract.py --url "https://www.bilibili.com/video/BVxxxx"   # B站字幕
python scripts/extract.py --file document.pdf                              # PDF
python scripts/extract.py --file lecture.mp3                               # 转录（首次会提示下载组件）
python scripts/extract.py --file lecture.mp3 --output srt                  # SRT 字幕
```

详细用法、环境变量、故障排除见 [SKILL.md](SKILL.md)。

## 运行环境与依赖组件

安装技能包本身即可用，但**各功能所需的软件环境不同**，首次使用对应功能时才会触发检测/安装：

| 功能 | 依赖 | 说明 |
| --- | --- | --- |
| 引导启动 | **Python ≥3.8**（仅标准库） | 启动技能并管理专用运行时；不要求安装任何第三方库 |
| 全部提取功能 | **专用运行时**（自动安装） | 首次使用列出名称/来源/大小（约 150 MB 下载），经确认后自动安装独立 CPython 3.12 + 锁定扩展库（requests/pdfplumber/PyMuPDF/python-docx/ebooklib/openpyxl/python-pptx/yt-dlp）到 `~/.smart-summarize/runtime/`，与系统 Python 彻底隔离 |
| YouTube 受限内容 | **Node.js**（`node` 在 PATH） | yt-dlp 需 JS runtime；未安装时见故障排除 |
| Word `.doc`（老格式） | **pandoc**（PATH） | 仅需此格式时装；不自动下载 |
| 音视频转录 | **ffmpeg + whisper-cli + ggml 模型** | 首次使用时列出名称/用途/来源/预计大小（合计约 1.6–2.4 GB），经确认后自动下载到 `~/.smart-summarize`，也可用环境变量指向已有安装 |

提示：所有 Python 库与组件都**无需预先安装**——首次用到时脚本会自动检测，并经确认后代为安装；扩展库全部随专用运行时预装，与用户系统 Python 零接触。

## 环境变量一览

| 变量                                       | 作用                                                                      |
| ---------------------------------------- | ----------------------------------------------------------------------- |
| `SMART_SUMMARIZE_PYTHON`                 | 指定 Python 解释器（默认 PATH 中的 `python`）                                      |
| `SMART_SUMMARIZE_HOME`                   | 受管组件目录（默认 `~/.smart-summarize`）                                         |
| `SMART_SUMMARIZE_TMPDIR`                 | 临时目录（默认系统临时目录）                                                          |
| `SMART_SUMMARIZE_FFMPEG`                 | 指定 ffmpeg 可执行文件                                                         |
| `SMART_SUMMARIZE_WHISPERCPP_CLI`         | 指定 whisper-cli 可执行文件                                                    |
| `SMART_SUMMARIZE_WHISPERCPP_DIR`         | whisper.cpp 可执行文件搜索目录                                                   |
| `SMART_SUMMARIZE_WHISPERCPP_MODELS_DIR`  | ggml 模型目录                                                               |
| `SMART_SUMMARIZE_YOUTUBE_COOKIES`        | YouTube cookies 文件（默认 `~/.smart-summarize/cookies/youtube-cookies.txt`） |
| `SMART_SUMMARIZE_WHISPERCPP_CMAKE_FLAGS` | 源码构建 whisper.cpp 时追加的 CMake 参数                                          |
| `SMART_SUMMARIZE_WORKSPACES_DIR`         | 知识库 workspace 根目录（默认 `~/.smart-summarize/workspaces/`）                  |
| `SMART_SUMMARIZE_LANG`                   | 反馈语言 zh/en（`--lang` 参数优先，默认按系统语言自动探测）                       |
| `SMART_SUMMARIZE_PIP_INDEX_URL`          | 专用运行时装库的 pip 镜像（国内建议清华源）                                       |
| `SMART_SUMMARIZE_PYTHON_MIRROR`          | 专用运行时 Python 本体下载源镜像（默认 GitHub Release）                            |

## 超大文档总结优化（slice protocol）

提取单个文档的内容超过 **256K 字符**（约 8.5 万汉字）时，脚本不会把全文塞进 stdout 让 agent 一次性读入——那样会**撑爆 agent 上下文窗口，触发上下文压缩/截断，导致总结丢失细节、质量明显下降**。

取而代之的处理方式：

1. 全文自动**分片落盘**到受管临时目录（`ss_slice_<hash>/chunk-001.md ...`）：每片 ≤40K 字符、按段落边界对齐、相邻片重叠 300 字符保住跨片上下文、每片带 SHA256 校验和；
2. stdout 只输出一份 **<1KB 的清单 JSON**（标题、总字符数、片数、分片目录、每片文件名与校验和）——塞爆上下文的物理上限被提取器锁死；
3. agent 按清单**逐片读取、逐片定向摘要**（用户的总结指令中的关注维度必须原样注入每片摘要），全部片完成后合并为最终总结，并校验已读片数 == 总片数。

这让超大书籍、长转录（配合 GPU 加速）、大表格也能被**完整而高质量地总结**，而不是在上下文压缩中损失内容。≤256K 字符的文档行为不变（stdout 直出，零额外开销）。也可以用 `--slice N` 直接输出第 N 片。

## 知识库 workspace（可选）

提取的内容可存入本地知识库做检索与精读：SQLite FTS5+trigram 全文检索（Python 标准库内置，零外部依赖，BM25 排序/snippet 摘要/布尔与 NEAR 查询）、来源文件副本、音视频时间戳索引与定位回放（VLC/PotPlayer/mpv/系统关联按平台探测）、13 项管理操作（创建/列举/删除/改名/统计/校验/重建/压缩等）。

```bash
python scripts/extract.py --file document.pdf --workspace 我的资料        # 提取并入库
python scripts/extract.py --workspace 我的资料 --search "全文检索"         # 检索（含偏移/分片定位）
python scripts/extract.py --workspace 我的资料 --play <entry-id> --at 12:33  # 音视频定位回放
```

workspace 目录自包含（拷走即迁移）；删除类操作需 `--yes` 二次确认。知识库需要 SQLite ≥3.34（CPython 官方构建默认满足，不满足时自动切换可用解释器，不做降级检索）。详见 [SKILL.md](SKILL.md)「知识库 workspace」章节。

## Cookies（YouTube / B站 受限内容）

平时完全不需要 cookies。只有当脚本提示需要时（返回 JSON 中的 `cookieHint` 字段）：

**YouTube**：

1. 浏览器安装扩展 **Get cookies.txt LOCALLY**（或同类）；
2. 访问 youtube.com 并登录，导出 Netscape 格式 cookies；
3. 保存到：`~/.smart-summarize/cookies/youtube-cookies.txt`
   （Windows 即 `C:\Users\<你>\.smart-summarize\cookies\youtube-cookies.txt`）；
4. 重新运行同一命令即可。

**B站**：AI 自动字幕、登录墙视频需要登录态：

1. 同样用浏览器扩展导出 bilibili.com 的 Netscape cookies；
2. 保存到：`~/.smart-summarize/cookies/bilibili-cookies.txt`；
3. 重新运行即可（`SMART_SUMMARIZE_BILIBILI_COOKIES` 可指定任意路径，也支持原生 Cookie 头格式文件）。

如需自定义位置：`SMART_SUMMARIZE_YOUTUBE_COOKIES` 指向任意路径。

## 更新日志

### v0.8.0（FTS coverage 重排 + 结构感知入库：标题锚点/源位置账本/出口内部化）

- **FTS 打分修复（端侧反馈）**：SQLite FTS5 bm25 在 chunk 级小语料上对常见词 IDF 钳制（≈1e-6），score 全显 -0.0、排序退化——自然多词查询改为 **OR 召回 + coverage/tf/bm25 三键重排**；`score` 语义变为 0..1 覆盖率，bm25 原值全精度入 `fts_detail.bm25_raw`；显式 `AND/OR/NOT/NEAR` 语义保留；
- **结构感知入库（§8D）**：提取层 `Extraction{text, srcmap}`——PDF 逐页偏移记账 + outline 书签、docx Heading 样式与 EPUB h1-h6 归一化为 markdown 标题、EPUB spine 章节账本；入库层新增 `headings/headings_fts/source_map`（老库连接时幂等迁移），标题锚点解析（markdown 不限行长 + 启发式正则 + 页眉自适应过滤），`--reindex` 幂等补建老条目锚点；
- **检索增强**：标题锚点成为第三路参与 RRF；同 (entry, 章节命中自动聚合（best_window 保留最高分窗口 + `same_section_hits`），`score_source` 并列标注（如 `fused+heading`）；
- **出处锚定源文件、full.md 内部化**：命中 `source_loc` 锚定源文件结构（PDF 页码/EPUB 章节/时间戳/文本行号），full.md 路径与偏移不再出现在 agent 可见输出；新增 `--section <ref>` 按不透明引用精读整节；
- **修复**：组件确认 y/N 提示改走 stderr（不再污染 agent JSON 管道）；`--verify` 扩锚点校验；删除条目级联清理锚点。
- **端侧反馈修复（WorkBuddy 两轮实测）**：列限定查询自动降级 FTS-only（`column_filter`，向量路不再绕过）；`--search ""` 空串契约修复（失败路径全部 JSON）；`--list`/`--stats` 暴露向量行数；无锚点区域 `#front` 哨兵引用可精读；`--embed` 为零向量条目补建向量；同源重入库返回 `supersedes` 并支持 `--replace` 显式清理；默认标题剥离扩展名、同源元数据沿用；fused 命中 `keyword_miss`/`scores` 分路贡献标注；检索 `vectors_rows`/`vector_zero_hint` 诊断字段；PDF 页脚噪声提取层过滤；全部失败路径 JSON 契约固化；
- **检索定位精化**：FTS/LIKE 命中按命中词真实位置归位章节/页码（`hit_offset`）；媒体 `start_ms/end_ms` 精确到命中词所在段落（`timestamp_precision` 分级）；读路径统一 `--max-chars`（默认 30000，0=不限）+ `remaining_chars`；超大节按命中位置开窗；同 key 多窗口 scores 累加修复（`score==Σscores` 不变式）；
- **@库名 操作约定**：用户提示词 `@库名` / “在X库” / “用X库” / “根据X库” / “使用X库” → 指定库检索；`--workspace "@库名"` 原样传入即可（CLI 自动剥离前缀）。

 


### v0.7.1（专用运行时 + 知识库 workspace + 混合检索 + sqlite-vec 向量后端 + 模块化拆分 + 双语反馈）

- **专用 Python 运行时（v1.4 方案）**：独立 CPython 3.12（python-build-standalone，SHA256SUMS 校验）+ 锁定版本扩展库安装到 `~/.smart-summarize/runtime/`，与用户系统 Python 彻底解耦——不向用户环境装库、不设系统环境变量、首次使用经确认自动安装；引导层仅需任意 Python ≥3.8（标准库）；根治用户环境依赖版本不可控类缺陷；
- **混合检索（中英文）**：本地 **Qwen3-Embedding-0.6B**（官方 GGUF Q8_0，llama.cpp 引擎 GPU 优先/Vulkan，CPU 回退）+ FTS5 双路 RRF 融合；`--search --mode fused|fts|vector`、`--no-embed`；**中文查询可召回英文文档**（跨语言语义），窗口级 full.md 偏移精读；首次使用知识库时一次性引导安装全链依赖（y/N 确认，镜像可配）；
- **模块化**：单文件 extract.py（1375 行）拆分为 slicing / extractors / transcribe / deps / messages / workspace / runtime / embeddings 八个模块，CLI 只保留入口、调度与闸门（纯重构，按 7.6 规则并入本版本不发单独版）；
- **新增知识库 workspace**：SQLite FTS5+trigram 全文检索（标准库零依赖、BM25 排序、snippet 摘要、布尔/前缀/NEAR/列限定查询、<3 字自动 LIKE 回退）、来源文件副本、full.md 偏移精读、13 项管理操作、完整性校验（含 FTS 索引逐行比对）、索引重建/VACUUM、跨库检索；
- **音视频时间戳索引与定位回放**：入库音视频统一 whisper SRT 转录 → 分片带 start_ms/end_ms → 播放器探测链按 OS 参数化定位播放；缺播放器输出结构化 JSON 交由 agent 处理；
- **双语反馈**：messages.py 集中管理 zh/en 文案，`--lang` 显式覆盖 / locale 自动探测；自有错误文案带 `error_i18n` 双份；
- **sqlite-vec 向量后端**：向量 KNN 下沉到 SQLite C 扩展（库内 SIMD 扫描），自动探测 OS/arch 安装（PyPI 优先/GitHub 兜底，约 0.3MB），确实加载失败才回退 numpy；检索输出标注 `vector_backend`；
- **依赖升级策略**：技能升级默认只更新文档与脚本，依赖包（运行时/嵌入引擎/模型/whisper/ffmpeg）不动；仅当新版本显式声明依赖升级/替换时经 y/N 确认处理（依赖锁 manifest）；
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


## License

MIT
