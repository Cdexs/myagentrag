# 更新日志（smart-summarize）

完整版本历史。技能运行所需的功能说明见 SKILL.md。

### v0.8.0（FTS coverage 重排 + 结构感知入库：标题锚点/源位置账本/出口内部化）

- **FTS 打分修复（端侧反馈）**：SQLite FTS5 bm25 在 chunk 级小语料上对常见词 IDF 钳制（≈1e-6），score 全显 -0.0、排序退化——自然多词查询改为 **OR 召回 + coverage/tf/bm25 三键重排**；`score` 语义变为 0..1 覆盖率，bm25 原值全精度入 `fts_detail.bm25_raw`；显式 `AND/OR/NOT/NEAR` 语义保留；
- **结构感知入库（§8D）**：提取层 `Extraction{text, srcmap}`——PDF 逐页偏移记账 + outline 书签、docx Heading 样式与 EPUB h1-h6 归一化为 markdown 标题、EPUB spine 章节账本；入库层新增 `headings/headings_fts/source_map`（老库连接时幂等迁移），标题锚点解析（markdown 不限行长 + 启发式正则 + 页眉自适应过滤），`--reindex` 幂等补建老条目锚点；
- **检索增强**：标题锚点成为第三路参与 RRF；同 (entry, 章节命中自动聚合（best_window 保留最高分窗口 + `same_section_hits`），`score_source` 并列标注（如 `fused+heading`）；
- **出处锚定源文件、full.md 内部化**：命中 `source_loc` 锚定源文件结构（PDF 页码/EPUB 章节/时间戳/文本行号），full.md 路径与偏移不再出现在 agent 可见输出；新增 `--section <ref>` 按不透明引用精读整节；
- **修复**：组件确认 y/N 提示改走 stderr（不再污染 agent JSON 管道）；`--verify` 扩锚点校验；删除条目级联清理锚点。

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
