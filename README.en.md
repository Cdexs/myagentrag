# smart-summarize

[中文](README.md) | English

![Platform](https://img.shields.io/badge/platform-Windows_%7C_macOS_%7C_Linux_%7C_WSL-0078D4)
![GitHub tag](https://img.shields.io/github/v/tag/Cdexs/smart-summarize?label=version&color=green)
![License](https://img.shields.io/npm/l/@cdexs/smart-summarize?color=orange)

A skill that can automatically read and summarize web pages, online videos, and local files (PDF/Word/Excel/PowerPoint/EPUB/text), as well as audio and video. Local audio and video content can be transcribed with high accuracy into text and subtitle files using a local ASR model, and extracted content can be stored into a local knowledge base (hybrid retrieval: SQLite FTS5 + local embedding model for **cross-lingual Chinese/English semantic search** + timestamp-based media playback).

An agent skill for intelligent content extraction — extract YouTube/Bilibili video subtitles, web page content, local files (PDF/Word/EPUB/text), and speech-to-text transcription of audio/video. **Extraction only — no LLM calls**; extracted results are handed to the host agent for summarization.

Cross-platform: Windows / macOS / Linux / WSL.

## Features

- **Extraction only**: no LLM calls; outputs JSON/text/SRT for the current agent to read and summarize
- **Dedicated runtime isolation**: a standalone CPython 3.12 + version-pinned libraries installed under `~/.smart-summarize/runtime/`, fully decoupled from the user's system Python — no libraries installed into user environments, no system env vars, immune to user environment changes
- **Zero hardcoded paths**: components are discovered in the order env vars → PATH → user directory (`~/.smart-summarize`)
- **Runtime on-demand install**: on first audio/video transcription, missing components are detected and listed (name / purpose / source / estimated size); they are downloaded only after user confirmation, then the original task continues — never silently
- **Knowledge-base workspace**: hybrid retrieval — SQLite FTS5 keyword route + local Qwen3 semantic route (RRF fusion, **Chinese/English cross-lingual**), source-file snapshots, offset-based deep reading, 13 management operations, timestamp-based media playback
- **Bilingual feedback (zh/en)**: `--lang` flag or automatic locale detection; JSON errors carry an `error_i18n` pair
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
# No dependencies to pre-install: on first use the skill auto-installs its dedicated
# runtime (standalone CPython 3.12 + pinned libraries), fully isolated from the system
# Python; the bootstrap layer only needs Python >= 3.8 (standard library only)

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
| Bootstrap | **Python ≥3.8** (standard library only) | Launches the skill and manages the dedicated runtime; no third-party libraries required |
| All extraction features | **Dedicated runtime** (auto-installed) | On first use, lists name/source/size (~150 MB download) and installs a standalone CPython 3.12 + pinned libraries (requests/pdfplumber/PyMuPDF/python-docx/ebooklib/openpyxl/python-pptx/yt-dlp) into `~/.smart-summarize/runtime/` after confirmation — fully isolated from the system Python |
| Restricted YouTube content | **Node.js** (`node` on PATH) | Required as JS runtime by yt-dlp; see troubleshooting if missing |
| Word `.doc` (legacy) | **pandoc** (on PATH) | Only for this format; not auto-downloaded |
| Audio/video transcription | **ffmpeg + whisper-cli + ggml model** | On first use, each is listed with name/purpose/source/estimated size (~1.6–2.4 GB total); after confirmation they are downloaded into `~/.smart-summarize`, or point env vars at existing installs |

Note: Python libraries and components require **no pre-installation** — everything is detected on first use and installed after confirmation; all extension libraries ship with the dedicated runtime and never touch the user's system Python.

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
| `SMART_SUMMARIZE_WORKSPACES_DIR`         | Knowledge-base workspace root (default `~/.smart-summarize/workspaces/`)    |
| `SMART_SUMMARIZE_LANG`                   | Feedback language zh/en (`--lang` flag wins; auto-detected by default)      |
| `SMART_SUMMARIZE_PIP_INDEX_URL`          | pip mirror for the dedicated runtime library install (e.g. Tsinghua mirror) |
| `SMART_SUMMARIZE_PYTHON_MIRROR`          | Mirror for the runtime CPython download (default: GitHub Releases)          |

## Large-document summarization optimization (slice protocol)

When extracted content of a single document exceeds **256K chars** (~85K Chinese characters), the script does not dump the full text into stdout for the agent to swallow in one go — that would blow the agent context window, trigger context compaction/truncation, and visibly degrade summary quality.

Instead:

1. The full text is automatically **chunked to disk** under a managed temp directory (`ss_slice_<hash>/chunk-001.md ...`): ≤40K chars per chunk, aligned to paragraph boundaries, 300-char overlap between neighboring chunks, SHA256 checksum per chunk;
2. stdout only emits a **<1KB manifest JSON** (title, total chars, chunk count, chunk directory, per-chunk filename + checksum) — the extractor physically caps how much can blow up the context;
3. The agent then reads chunks in order and produces **per-chunk directed summaries** (the user's custom instructions are carried verbatim into every chunk summary), merges them into the final summary, and verifies the chunk count.

This keeps very large books, long transcriptions (with GPU acceleration), and big spreadsheets fully summarizable instead of losing content to context compaction. Documents ≤256K chars behave exactly as before (stdout, zero overhead). `--slice N` outputs chunk N directly.

## Knowledge-base workspace (optional)

Extracted content can be stored into a local knowledge base for search and deep reading: SQLite FTS5+trigram full-text search (built into the Python standard library — zero extra dependencies; BM25 ranking, snippet highlights, boolean/NEAR queries), source-file snapshots, timestamp indexing with seeked playback for audio/video (VLC/PotPlayer/mpv/system default, probed per platform), and 13 management operations (create/list/delete/rename/stats/verify/reindex/vacuum…).

```bash
python scripts/extract.py --file document.pdf --workspace my-docs          # extract + ingest
python scripts/extract.py --workspace my-docs --search "keyword"           # search (with offset/chunk locators)
python scripts/extract.py --workspace my-docs --play <entry-id> --at 12:33 # seeked playback
```

A workspace directory is self-contained (copy it to migrate); destructive operations require a `--yes` confirmation. The knowledge base requires SQLite ≥3.34 (satisfied by official CPython builds; if the current interpreter lacks it, the skill auto-switches to a capable one instead of degrading search quality). See the "Knowledge-base workspace" section in [SKILL.md](SKILL.md).

## Cookies (restricted YouTube / Bilibili content)

No cookies are needed for normal use. Only when the script says so (the `cookieHint` field in the returned JSON):

1. Install the browser extension **Get cookies.txt LOCALLY** (or similar);
2. Visit youtube.com, log in, and export cookies in Netscape format;
3. Save to: `~/.smart-summarize/cookies/youtube-cookies.txt`
   (on Windows: `C:\Users\<you>\.smart-summarize\cookies\youtube-cookies.txt`);
4. Re-run the same command.

To use a custom location: point `SMART_SUMMARIZE_YOUTUBE_COOKIES` to any path. Cookies are equivalent to an account session — never commit or share them.

**Bilibili**: AI auto-generated subtitles and login-walled videos require a logged-in session:

1. Export bilibili.com cookies in Netscape format with the same browser extension;
2. Save to: `~/.smart-summarize/cookies/bilibili-cookies.txt`;
3. Re-run the same command. (`SMART_SUMMARIZE_BILIBILI_COOKIES` points to any path; raw Cookie-header-style files are also accepted.)

## License

MIT