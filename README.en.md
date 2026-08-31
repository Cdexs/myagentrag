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

Place this directory into your agent's skill directory (e.g. `~/.pi/agent/skills/smart-summarize`), or run it in place:

```bash
# Dependencies (pure Python, no compilation)
python -m pip install requests pdfplumber pymupdf python-docx ebooklib yt-dlp

python scripts/extract.py --url "https://www.bilibili.com/video/BVxxxx"   # Bilibili subtitles
python scripts/extract.py --file document.pdf                              # PDF
python scripts/extract.py --file lecture.mp3                               # Transcription (prompts first-run downloads)
python scripts/extract.py --file lecture.mp3 --output srt                  # SRT subtitles
```

For detailed usage, environment variables, and troubleshooting, see [SKILL.md](SKILL.md).

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