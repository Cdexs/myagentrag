# smart-summarize

[中文](README.md) | English

A skill that can automatically read and summarize web pages, online videos, and local files (PDF/Word/EPUB/text), as well as audio and video — including text and subtitle transcription for audio/video.

An agent skill for intelligent content extraction — extract YouTube/Bilibili video subtitles, web page content, local files (PDF/Word/EPUB/text), and speech-to-text transcription of audio/video. **Extraction only — no LLM calls**; extracted results are handed to the host agent for summarization.

Cross-platform: Windows / macOS / Linux / WSL.

## Features

- **Extraction only**: no LLM calls; outputs JSON/text/SRT for the current agent to read and summarize
- **Zero hardcoded paths**: components are discovered in the order env vars → PATH → user directory (`~/.smart-summarize`)
- **Runtime on-demand install**: on first audio/video transcription, missing components are detected and listed (name / purpose / source / estimated size); they are downloaded only after user confirmation, then the original task continues — never silently
- **Privacy safe**: no cookies are shipped with or read from the skill directory; YouTube cookies are read from `~/.smart-summarize/cookies/youtube-cookies.txt` (exported manually by the user) and only requested when a login wall is hit
- **GPU neutral**: whether GPU is used depends on the user's whisper.cpp build (Vulkan/Metal/CUDA); during source builds the toolchain is auto-detected and honestly reported

## Install

**Install from npm (recommended):**

```bash
npm install @cdexs/smart-summarize
```

**One-liner for pi users (installs straight into the skill directory):**

```bash
pi install npm:@cdexs/smart-summarize
```

The skill lands in `node_modules/@cdexs/smart-summarize/`. Copy it (or point directly at it) into your agent's skill directory (e.g. `~/.pi/agent/skills/smart-summarize`), or run it in place:

```bash
python node_modules/@cdexs/smart-summarize/scripts/extract.py --file demo.pdf
```

**Install from source:**

Place this directory into your agent's skill directory (e.g. `~/.pi/agent/skills/smart-summarize`), or run it in place:

```bash
# Dependencies (optional, pure Python): on first use of each format the script
# detects missing libraries, lists them and installs on confirmation (out-of-box).
# To pre-install everything:
python -m pip install requests pdfplumber pymupdf python-docx ebooklib yt-dlp

python scripts/extract.py --url "https://www.bilibili.com/video/BVxxxx"   # Bilibili subtitles
python scripts/extract.py --file document.pdf                              # PDF
python scripts/extract.py --file lecture.mp3                               # Transcription (prompts first-run downloads)
python scripts/extract.py --file lecture.mp3 --output srt                  # SRT subtitles
```

For detailed usage, environment variables, and troubleshooting, see [SKILL.md](SKILL.md).

## Runtime Environment & Dependencies

The installed package works right away, but each feature has its own software requirements — all detected on first use:

| Feature | Dependency | Notes |
| --- | --- | --- |
| All features | **Python 3.9+** | Always required; uses `python` from PATH by default, or set `SMART_SUMMARIZE_PYTHON` |
| Web pages / Bilibili subtitles | `requests` | Detected on first use; installed via `pip` after confirmation; also required by the component download chain |
| PDF | `pdfplumber` or `PyMuPDF` (either one) | Same on-demand detection + confirmed pip install |
| Word `.docx` | `python-docx` | Same as above |
| EPUB | `ebooklib` | Same as above |
| YouTube subtitles | `yt-dlp` | Same as above |
| Restricted YouTube content | **Node.js** (`node` on PATH) | Required as JS runtime by yt-dlp; see troubleshooting if missing |
| Word `.doc` (legacy) | **pandoc** (on PATH) | Only for this format; not auto-downloaded |
| Audio/video transcription | **ffmpeg + whisper-cli + ggml model** | On first use, each is listed with name/purpose/source/estimated size (~1.6–2.4 GB total); after confirmation they are downloaded into `~/.smart-summarize`, or point env vars at existing installs |

Note: Python libraries require **no pre-installation**; everything is detected on first use and installed on confirmation.

## Environment Variables

| Variable                                 | Purpose                                                                     |
| ---------------------------------------- | --------------------------------------------------------------------------- |
| `SMART_SUMMARIZE_PYTHON`                 | Python interpreter (defaults to `python` on PATH)                          |
| `SMART_SUMMARIZE_HOME`                   | Managed component directory (default `~/.smart-summarize`)                  |
| `SMART_SUMMARIZE_TMPDIR`                 | Temp directory (defaults to the system temp dir)                            |
| `SMART_SUMMARIZE_FFMPEG`                 | Path to the ffmpeg executable                                              |
| `SMART_SUMMARIZE_WHISPERCPP_CLI`         | Path to the whisper-cli executable                                         |
| `SMART_SUMMARIZE_WHISPERCPP_DIR`         | Search directory for whisper.cpp executables                               |
| `SMART_SUMMARIZE_WHISPERCPP_MODELS_DIR`  | GGML model directory                                                       |
| `SMART_SUMMARIZE_YOUTUBE_COOKIES`        | YouTube cookies file (defaults to `~/.smart-summarize/cookies/youtube-cookies.txt`) |
| `SMART_SUMMARIZE_WHISPERCPP_CMAKE_FLAGS` | Extra CMake flags when building whisper.cpp from source                     |

## Cookies (restricted YouTube content)

No cookies are needed for normal use. Only when the script says so (the `cookieHint` field in the returned JSON):

1. Install the browser extension **Get cookies.txt LOCALLY** (or similar);
2. Visit youtube.com, log in, and export cookies in Netscape format;
3. Save to: `~/.smart-summarize/cookies/youtube-cookies.txt`
   (on Windows: `C:\Users\<you>\.smart-summarize\cookies\youtube-cookies.txt`);
4. Re-run the same command.

To use a custom location: point `SMART_SUMMARIZE_YOUTUBE_COOKIES` to any path. Cookies are equivalent to an account session — never commit or share them.

## License

MIT