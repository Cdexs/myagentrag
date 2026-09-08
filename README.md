# MyAgentRAG

[English](README.en.md) | 中文

![Platform](https://img.shields.io/badge/platform-Windows_%7C_macOS_%7C_Linux_%7C_WSL-0078D4)
![GitHub tag](https://img.shields.io/github/v/tag/Cdexs/myagentrag?label=version&color=green)
![License](https://img.shields.io/npm/l/@cdexs/myagentrag?color=orange)

MyAgentRAG 是一个本地知识库（RAG）构建与检索工具：把 YouTube/B站视频、网页、本地文件（PDF/Word/Excel/PowerPoint/EPUB/文本）提取入库，音视频经本地 ASR 模型高准确率转写后入库，形成可检索的知识库 workspace——SQLite FTS5 关键词 + 本地向量模型语义 + 标题锚点**三路混合检索**，支持中英文跨语言语义检索、按页码/章节/时间戳定位精读与回放。

**提取与索引全部本地完成，不调用 LLM**；检索结果的阅读与作答由宿主 agent 完成。

跨平台：Windows / macOS / Linux / WSL。

## 特性

- **知识库 workspace**：混合检索——SQLite FTS5 关键词路 + 本地 Qwen3 向量语义路 + 标题锚点路（RRF 融合，**中英文跨语言**）、来源文件副本、按页码/章节/时间戳定位精读、13 项管理操作、音视频时间戳定位回放
- **纯本地提取与索引**：不调用任何 LLM，内容直接入库为结构化知识条目，阅读与作答由当前 agent 完成
- **专用运行时隔离**：独立 CPython 3.12 + 锁定版本扩展库装在 `~/.myagentrag/runtime/`，与用户系统 Python 彻底解耦——不装库到用户环境、不设系统环境变量、不被用户环境变化影响
- **零硬编码路径**：所有组件按 环境变量 → PATH → 用户目录（`~/.myagentrag`）的顺序发现
- **运行时按需安装**：首次使用提取入库/音视频转录时检测缺失组件，列出名称/用途/来源/预计大小，经用户确认后下载安装，随后自动继续；不会静默下载
- **中英双语反馈**：`--lang` 或 locale 自动探测，JSON 错误带 `error_i18n` 双份
- **隐私安全**：不携带、不读取技能目录内的任何 cookies；YouTube cookies 默认从 `~/.myagentrag/cookies/youtube-cookies.txt` 读取（由用户手动导出放置），仅遇登录墙时才提示需要
- **GPU 中立**：是否启用 GPU 取决于用户安装的 whisper.cpp 构建（Vulkan/Metal/CUDA）；自动构建时会检测工具链并如实告知

## 安装

**从 npm 安装（推荐）：**

```bash
npm install @cdexs/myagentrag
```

**pi 用户一键安装（自动装到技能目录）：**

```bash
pi install npm:@cdexs/myagentrag
```

安装后技能包位于 `node_modules/@cdexs/myagentrag/`，把该目录（或整个目录）放到 agent 的技能目录（如 `~/.pi/agent/skills/myagentrag`），或直接指定路径运行：

```bash
python node_modules/@cdexs/myagentrag/scripts/extract.py --file demo.pdf --workspace 我的资料
```

**从源码安装：**

把本目录放到 agent 的技能目录（如 `~/.pi/agent/skills/myagentrag`），或直接在本目录运行：

```bash
# 依赖无需预装：首次使用时自动安装技能专用运行时（独立 CPython 3.12 + 锁定扩展库），
# 与系统 Python 完全隔离；引导层仅需 Python ≥3.8（仅标准库）

python scripts/extract.py --file document.pdf --workspace 我的资料            # 提取并入库
python scripts/extract.py --workspace 我的资料 --search "检索词"              # 三路混合检索
python scripts/extract.py --workspace 我的资料 --entry <id> --max-chars 8000  # 定位精读
python scripts/extract.py --workspace 我的资料 --play <id> --at 12:33         # 音视频定位回放
```

详细用法、环境变量、故障排除见 [SKILL.md](SKILL.md)。

## 运行环境与依赖组件

安装技能包本身即可用，但**各功能所需的软件环境不同**，首次使用对应功能时才会触发检测/安装：

| 功能 | 依赖 | 说明 |
| --- | --- | --- |
| 引导启动 | **Python ≥3.8**（仅标准库） | 启动技能并管理专用运行时；不要求安装任何第三方库 |
| 提取与入库 | **专用运行时**（自动安装） | 首次使用列出名称/来源/大小（约 150 MB 下载），经确认后自动安装独立 CPython 3.12 + 锁定扩展库（requests/pdfplumber/PyMuPDF/python-docx/ebooklib/openpyxl/python-pptx/yt-dlp）到 `~/.myagentrag/runtime/`，与系统 Python 彻底隔离 |
| YouTube 受限内容 | **Node.js**（`node` 在 PATH） | yt-dlp 需 JS runtime；未安装时见故障排除 |
| Word `.doc`（老格式） | **pandoc**（PATH） | 仅需此格式时装；不自动下载 |
| 音视频转录入库 | **ffmpeg + whisper-cli + ggml 模型** | 首次使用时列出名称/用途/来源/预计大小（合计约 1.6–2.4 GB），经确认后自动下载到 `~/.myagentrag`，也可用环境变量指向已有安装 |

提示：所有 Python 库与组件都**无需预先安装**——首次用到时脚本会自动检测，并经确认后代为安装；扩展库全部随专用运行时预装，与用户系统 Python 零接触。

## 环境变量一览

| 变量                                       | 作用                                                                      |
| ---------------------------------------- | ----------------------------------------------------------------------- |
| `MYAGENTRAG_PYTHON`                 | 指定 Python 解释器（默认 PATH 中的 `python`）                                      |
| `MYAGENTRAG_HOME`                   | 受管组件目录（默认 `~/.myagentrag`）                                         |
| `MYAGENTRAG_TMPDIR`                 | 临时目录（默认系统临时目录）                                                          |
| `MYAGENTRAG_FFMPEG`                 | 指定 ffmpeg 可执行文件                                                         |
| `MYAGENTRAG_WHISPERCPP_CLI`         | 指定 whisper-cli 可执行文件                                                    |
| `MYAGENTRAG_WHISPERCPP_DIR`         | whisper.cpp 可执行文件搜索目录                                                   |
| `MYAGENTRAG_WHISPERCPP_MODELS_DIR`  | ggml 模型目录                                                               |
| `MYAGENTRAG_YOUTUBE_COOKIES`        | YouTube cookies 文件（默认 `~/.myagentrag/cookies/youtube-cookies.txt`） |
| `MYAGENTRAG_WHISPERCPP_CMAKE_FLAGS` | 源码构建 whisper.cpp 时追加的 CMake 参数                                          |
| `MYAGENTRAG_WORKSPACES_DIR`         | 知识库 workspace 根目录（默认 `~/.myagentrag/workspaces/`）                  |
| `MYAGENTRAG_LANG`                   | 反馈语言 zh/en（`--lang` 参数优先，默认按系统语言自动探测）                       |
| `MYAGENTRAG_PIP_INDEX_URL`          | 专用运行时装库的 pip 镜像（国内建议清华源）                                       |
| `MYAGENTRAG_PYTHON_MIRROR`          | 专用运行时 Python 本体下载源镜像（默认 GitHub Release）                            |

## 知识库 workspace

提取的内容存入本地知识库做检索与精读：SQLite FTS5+trigram 全文检索（Python 标准库内置，零外部依赖，BM25 排序/snippet 片段预览/布尔与 NEAR 查询）、标题锚点作为第三路参与融合排序、来源文件副本、音视频时间戳索引与定位回放（VLC/PotPlayer/mpv/系统关联按平台探测）、13 项管理操作（创建/列举/删除/改名/统计/校验/重建/压缩等）。

```bash
python scripts/extract.py --file document.pdf --workspace 我的资料              # 提取并入库
python scripts/extract.py --workspace 我的资料 --search "全文检索"               # 检索（含偏移/分片定位）
python scripts/extract.py --workspace 我的资料 --entry <id> --section <ref>    # 按章节/结构精读
python scripts/extract.py --workspace 我的资料 --play <entry-id> --at 12:33    # 音视频定位回放
```

workspace 目录自包含（拷走即迁移）；删除类操作需 `--yes` 二次确认。知识库需要 SQLite ≥3.34（CPython 官方构建默认满足，不满足时自动切换可用解释器，不做降级检索）。检索语法、定位精读、回放与管理操作全集详见 [SKILL.md](SKILL.md)「知识库 workspace」章节。

## Cookies（YouTube / B站 受限内容入库）

平时完全不需要 cookies。只有当脚本提示需要时（返回 JSON 中的 `cookieHint` 字段）：

**YouTube**：

1. 浏览器安装扩展 **Get cookies.txt LOCALLY**（或同类）；
2. 访问 youtube.com 并登录，导出 Netscape 格式 cookies；
3. 保存到：`~/.myagentrag/cookies/youtube-cookies.txt`
   （Windows 即 `C:\Users\<你>\.myagentrag\cookies\youtube-cookies.txt`）；
4. 重新运行同一命令即可。

**B站**：AI 自动字幕、登录墙视频需要登录态：

1. 同样用浏览器扩展导出 bilibili.com 的 Netscape cookies；
2. 保存到：`~/.myagentrag/cookies/bilibili-cookies.txt`；
3. 重新运行即可（`MYAGENTRAG_BILIBILI_COOKIES` 可指定任意路径，也支持原生 Cookie 头格式文件）。

如需自定义位置：`MYAGENTRAG_YOUTUBE_COOKIES` 指向任意路径。

## License

MIT
