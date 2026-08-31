---
name: smart-summarize
description: 智能内容提取工具：提取 YouTube/B站视频字幕、网页正文、本地文件（PDF/Word/EPUB/文本）与音视频语音转录。只提取，不调用 LLM；提取结果由当前 agent 阅读并总结。
compatibility: Windows / macOS / Linux + Python 3.9+；音视频转录另需 ffmpeg、whisper.cpp 及 ggml 模型
---

# 智能内容提取工具 (smart-summarize)

**设计原则**：只负责内容提取，不调用 LLM。提取结果由当前 agent 阅读、总结或进一步处理。

## 支持的内容源

| 类型 | 支持格式 | 说明 |
|------|---------|------|
| **YouTube** | 视频 URL | 通过 yt-dlp 提取手动/自动字幕和元数据 |
| **B站** | 视频 URL | 提取 CC 字幕和视频信息（免登录 API） |
| **网页** | HTTP/HTTPS 链接 | 通过 Jina Reader 提取正文 |
| **文本文件** | `.txt`, `.md`, `.markdown`, `.rst`, `.csv` | 直接读取 |
| **PDF** | `.pdf` | pdfplumber 或 PyMuPDF |
| **Word** | `.docx`, `.doc` | python-docx；`.doc` 另需 pandoc |
| **EPUB** | `.epub` | ebooklib |
| **音频** | `.mp3`, `.wav`, `.aac`, `.m4a`, `.flac`, `.ogg`, `.wma` | ffmpeg 转 PCM 后用 whisper.cpp 转录 |
| **视频** | `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv`, `.flv`, `.webm` | 先提取内置字幕，无字幕则提取音频转录 |

## 安装依赖

入口优先使用 `SMART_SUMMARIZE_PYTHON`，未设置时使用 PATH 中的 `python`。技能不会假设某台机器上的 Python、venv 或磁盘路径。

```bash
PYTHON="${SMART_SUMMARIZE_PYTHON:-python}"
"$PYTHON" -m pip install --upgrade requests pdfplumber pymupdf python-docx ebooklib yt-dlp
```

推荐使用独立环境（Unix/macOS/Linux）：

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade requests pdfplumber pymupdf python-docx ebooklib yt-dlp
export SMART_SUMMARIZE_PYTHON="$PWD/.venv/bin/python"
```

Windows PowerShell：

```powershell
$Python = if ($env:SMART_SUMMARIZE_PYTHON) { $env:SMART_SUMMARIZE_PYTHON } else { "python" }
& $Python -m pip install --upgrade requests pdfplumber pymupdf python-docx ebooklib yt-dlp
```

音视频功能还需要单独安装 `ffmpeg`；`.doc` 文件还需要 `pandoc`。两者应安装到 PATH，或分别通过 `SMART_SUMMARIZE_FFMPEG`、系统包管理器配置。

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

- `--output json`（默认）：完整 JSON（含 title/author/transcript/success）
- `--output text`：标题+正文纯文本
- `--output srt`：SRT 字幕（仅音视频转录）

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

**刎始安装不下载任何组件；实际使用时运行时检测。** 所有自动下载的组件都只装在用户目录（`~/.smart-summarize`），不写系统目录。

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
- whisper-cli：按优先级安装：①macOS/Linux 有 Homebrew 时 `brew install whisper-cpp`；②Windows 下载 GitHub 官方预编译 zip（CPU 构建）；③源码构建兑底（需 git/cmake/编译器）。源码构建时自动检测 GPU 工具链：有 NVIDIA GPU + CUDA Toolkit 则启用 CUDA，有 Vulkan SDK 则启用 Vulkan，macOS 默认启用 Metal，都没有则 CPU（并明确告知）。也可用 `SMART_SUMMARIZE_WHISPERCPP_CMAKE_FLAGS` 追加自定义 CMake 参数；需要换后端时删除 `~/.smart-summarize/whisper.cpp` 构建目录及受管 bin 中的二进制后重试。
- ggml 模型：从 HuggingFace `ggerganov/whisper.cpp` 下载（large-v3-turbo 约 1.6GB，q5_0 约 560MB），存到上述模型目录首个可用位置。

### GPU 加速边界

脚本不会根据 OS 自动开启 GPU。GPU 是否启用取决于 whisper.cpp 二进制编译时包含的后端及其运行环境：

- Windows + Vulkan/CUDA 构建：可使用对应 GPU；
- macOS + Metal 构建：可使用 Metal；
- Linux/WSL + Vulkan/CUDA 构建：满足驱动和设备透传条件时可使用；
- CPU 构建，或 GPU 后端/驱动不可用：回退为 CPU（不会自动安装驱动或重新编译）。

可直接运行 `whisper-cli -h` 或执行一次转录查看其启动日志，确认实际加载的 backend。`large-v3-turbo-q5_0` 仅是量化模型，不等于 GPU 加速。

## Cookies 隐私规则

技能包**不携带任何 cookies 文件**，也不再要求用户预先配置路径。

- cookies 文件的约定位置自动确定：`~/.smart-summarize/cookies/youtube-cookies.txt`（或用 `SMART_SUMMARIZE_YOUTUBE_COOKIES` 指定任意位置）；
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

| 症状 | 处理 |
|------|------|
| 找不到 `python` | 设置 `SMART_SUMMARIZE_PYTHON` 为目标解释器的完整路径 |
| YouTube yt-dlp 报 JS runtime 错误 | 安装 Node.js 并确保 `node` 在 PATH；脚本使用 `--js-runtimes node` |
| 音视频提示 whisper.cpp 不可用 | 运行时检查会列出缺失组件与大小；同意后确认或由 agent 加 `--download-deps` 重跑 |
| PDF 提取为空 | 扫描件没有文字层，属正常；本工具不做 OCR |
| B站无字幕 | 该视频没有 CC 字幕，API 返回 `success:false`，属正常 |

## 更新日志

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
