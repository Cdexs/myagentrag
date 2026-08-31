# smart-summarize

[English](README.en.md) | 中文

可自动进行网页、网络视频、本地文件（PDF/Word/EPUB/文本）、音视频进行读取、总结的技能，可对音视频进行文本、字幕转写。

智能内容提取技能包（agent skill）——提取 YouTube/B站视频字幕、网页正文、本地文件（PDF/Word/EPUB/文本）与音视频语音转录。**只提取，不调用 LLM**；提取结果交给宿主 agent 总结。

跨平台：Windows / macOS / Linux / WSL。

## 特性

- **纯提取**：不调用任何 LLM，输出 JSON/文本/SRT，由当前 agent 阅读总结
- **零硬编码路径**：所有组件按 环境变量 → PATH → 用户目录（`~/.smart-summarize`）的顺序发现
- **运行时按需安装**：首次使用音视频转录时检测缺失组件，列出名称/用途/来源/预计大小，经用户确认后下载安装，随后自动继续；不会静默下载
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
# 依赖（纯 Python，无编译）
python -m pip install requests pdfplumber pymupdf python-docx ebooklib yt-dlp

python scripts/extract.py --url "https://www.bilibili.com/video/BVxxxx"   # B站字幕
python scripts/extract.py --file document.pdf                              # PDF
python scripts/extract.py --file lecture.mp3                               # 转录（首次会提示下载组件）
python scripts/extract.py --file lecture.mp3 --output srt                  # SRT 字幕
```

详细用法、环境变量、故障排除见 [SKILL.md](SKILL.md)。

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

## Cookies（YouTube 受限内容）

平时完全不需要 cookies。只有当脚本提示需要时（返回 JSON 中的 `cookieHint` 字段）：

1. 浏览器安装扩展 **Get cookies.txt LOCALLY**（或同类）；
2. 访问 youtube.com 并登录，导出 Netscape 格式 cookies；
3. 保存到：`~/.smart-summarize/cookies/youtube-cookies.txt`
   （Windows 即 `C:\Users\<你>\.smart-summarize\cookies\youtube-cookies.txt`）；
4. 重新运行同一命令即可。

如需自定义位置：`SMART_SUMMARIZE_YOUTUBE_COOKIES` 指向任意路径。cookies 等同账号会话，请勿提交到仓库或共享。

## License

MIT