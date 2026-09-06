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

## License

MIT
