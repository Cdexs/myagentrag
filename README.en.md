# MyAgentRAG

[中文](README.md) | English

![Platform](https://img.shields.io/badge/platform-Windows_%7C_macOS_%7C_Linux_%7C_WSL-0078D4)
![GitHub tag](https://img.shields.io/github/v/tag/Cdexs/myagentrag?label=version&color=green)
![License](https://img.shields.io/npm/l/@cdexs/myagentrag?color=orange)

MyAgentRAG is a local knowledge-base (RAG) builder and retrieval tool: content from YouTube/Bilibili videos, web articles, and local files (PDF/Word/Excel/PowerPoint/EPUB/text) is extracted and ingested, audio/video is transcribed with high accuracy by a local ASR model and ingested — forming a searchable knowledge-base workspace with **three-route hybrid retrieval** (SQLite FTS5 keywords + local embedding model semantics + heading anchors), cross-lingual Chinese/English semantic search, and structure-aware deep reading (page/section/timestamp) with seeked media playback.

**Extraction and indexing run entirely locally — no LLM calls**; reading the retrieved results and answering is done by the host agent.

Cross-platform: Windows / macOS / Linux / WSL.

## Features

- **Knowledge-base workspace**: hybrid retrieval — SQLite FTS5 keyword route + local Qwen3 semantic route + heading-anchor route (RRF fusion, **Chinese/English cross-lingual**), source-file snapshots, page/section/timestamp deep reading, 13 management operations, timestamp-based seeked playback
- **Pure local extraction & indexing**: no LLM calls; content is ingested directly as structured knowledge entries, and the current agent reads and answers from it
- **Dedicated runtime isolation**: a standalone CPython 3.12 + version-pinned libraries installed under `~/.myagentrag/runtime/`, fully decoupled from the user's system Python — no libraries installed into user environments, no system env vars, immune to user environment changes
- **Zero hardcoded paths**: components are discovered in the order env vars → PATH → user directory (`~/.myagentrag`)
- **Runtime on-demand install**: on first ingestion/transcription use, missing components are detected and listed (name / purpose / source / estimated size); they are downloaded only after user confirmation, then the original task continues — never silently
- **Bilingual feedback (zh/en)**: `--lang` flag or automatic locale detection; JSON errors carry an `error_i18n` pair
- **Privacy safe**: no cookies are shipped with or read from the skill directory; YouTube cookies are read from `~/.myagentrag/cookies/youtube-cookies.txt` (exported manually by the user) and only requested when a login wall is hit
- **GPU neutral**: whether GPU is used depends on the user's whisper.cpp build (Vulkan/Metal/CUDA); during source builds the toolchain is auto-detected and honestly reported

## Install

**Install from npm (recommended):**

```bash
npm install @cdexs/myagentrag
```

**One-liner for pi users (installs straight into the skill directory):**

```bash
pi install npm:@cdexs/myagentrag
```

The skill lands in `node_modules/@cdexs/myagentrag/`. Copy it (or point directly at it) into your agent's skill directory (e.g. `~/.pi/agent/skills/myagentrag`), or run it in place:

```bash
python node_modules/@cdexs/myagentrag/scripts/extract.py --file demo.pdf --workspace my-docs
```

**Install from source:**

Place this directory into your agent's skill directory (e.g. `~/.pi/agent/skills/myagentrag`), or run it in place:

```bash
# No dependencies to pre-install: on first use the skill auto-installs its dedicated
# runtime (standalone CPython 3.12 + pinned libraries), fully isolated from the system
# Python; the bootstrap layer only needs Python >= 3.8 (standard library only)

python scripts/extract.py --file document.pdf --workspace my-docs            # extract + ingest
python scripts/extract.py --workspace my-docs --search "keyword"             # three-route hybrid search
python scripts/extract.py --workspace my-docs --entry <id> --max-chars 8000  # located deep reading
python scripts/extract.py --workspace my-docs --play <id> --at 12:33         # seeked playback
```

For detailed usage, environment variables, and troubleshooting, see [SKILL.md](SKILL.md).

## Runtime Environment & Dependencies

The installed package works right away, but each feature has its own software requirements — all detected on first use:

| Feature | Dependency | Notes |
| --- | --- | --- |
| Bootstrap | **Python ≥3.8** (standard library only) | Launches the skill and manages the dedicated runtime; no third-party libraries required |
| Extraction & ingestion | **Dedicated runtime** (auto-installed) | On first use, lists name/source/size (~150 MB download) and installs a standalone CPython 3.12 + pinned libraries (requests/pdfplumber/PyMuPDF/python-docx/ebooklib/openpyxl/python-pptx/yt-dlp) into `~/.myagentrag/runtime/` after confirmation — fully isolated from the system Python |
| Restricted YouTube content | **Node.js** (`node` on PATH) | Required as JS runtime by yt-dlp; see troubleshooting if missing |
| Word `.doc` (legacy) | **pandoc** (on PATH) | Only for this format; not auto-downloaded |
| Audio/video ingest transcription | **ffmpeg + whisper-cli + ggml model** | On first use, each is listed with name/purpose/source/estimated size (~1.6–2.4 GB total); after confirmation they are downloaded into `~/.myagentrag`, or point env vars at existing installs |

Note: Python libraries and components require **no pre-installation** — everything is detected on first use and installed after confirmation; all extension libraries ship with the dedicated runtime and never touch the user's system Python.

## Environment Variables

| Variable                                 | Purpose                                                                     |
| ---------------------------------------- | --------------------------------------------------------------------------- |
| `MYAGENTRAG_PYTHON`                 | Python interpreter (defaults to `python` on PATH)                          |
| `MYAGENTRAG_HOME`                   | Managed component directory (default `~/.myagentrag`)                  |
| `MYAGENTRAG_TMPDIR`                 | Temp directory (defaults to `~/.myagentrag/tmp` — never touches the system temp dir; can point to another disk)                            |
| `MYAGENTRAG_FFMPEG`                 | Path to the ffmpeg executable                                              |
| `MYAGENTRAG_WHISPERCPP_CLI`         | Path to the whisper-cli executable                                         |
| `MYAGENTRAG_WHISPERCPP_DIR`         | Search directory for whisper.cpp executables                               |
| `MYAGENTRAG_WHISPERCPP_MODELS_DIR`  | GGML model directory                                                       |
| `MYAGENTRAG_YOUTUBE_COOKIES`        | YouTube cookies file (defaults to `~/.myagentrag/cookies/youtube-cookies.txt`) |
| `MYAGENTRAG_WHISPERCPP_CMAKE_FLAGS` | Extra CMake flags when building whisper.cpp from source                     |
| `MYAGENTRAG_WORKSPACES_DIR`         | Knowledge-base workspace root (default `~/.myagentrag/workspaces/`)    |
| `MYAGENTRAG_LANG`                   | Feedback language zh/en (`--lang` flag wins; auto-detected by default)      |
| `MYAGENTRAG_PIP_INDEX_URL`          | pip mirror for the dedicated runtime library install (e.g. Tsinghua mirror) |
| `MYAGENTRAG_PYTHON_MIRROR`          | Mirror for the runtime CPython download (default: GitHub Releases)          |

## Knowledge-base workspace

Extracted content is stored into a local knowledge base for search and deep reading: SQLite FTS5+trigram full-text search (built into the Python standard library — zero extra dependencies; BM25 ranking, snippet highlights, boolean/NEAR queries), heading anchors as a third route in fusion ranking, source-file snapshots, timestamp indexing with seeked playback for audio/video (VLC/PotPlayer/mpv/system default, probed per platform), and 13 management operations (create/list/delete/rename/stats/verify/reindex/vacuum…).

Granularity: content is stored as 40,000-char chunks (paragraph-aligned, 300-char overlap between neighboring chunks); embedding vectors use 800-char windows (100-char overlap, 700-char step, one vector per window); audio/video entries are additionally indexed by whisper segment-level timestamps — seek/playback precision is paragraph-level, independent of chunking.

```bash
python scripts/extract.py --file document.pdf --workspace my-docs            # extract + ingest
python scripts/extract.py --file a.pdf --file b.docx --workspace my-docs     # batch ingest (merged embeddings)
python scripts/extract.py --dir docs-folder --workspace my-docs              # whole-directory batch ingest
python scripts/extract.py --workspace my-docs --search "keyword"             # three-route hybrid search
python scripts/extract.py --workspace my-docs --entry <id> --max-chars 8000  # located deep reading
python scripts/extract.py --workspace my-docs --play <id> --at 12:33         # seeked playback
```

A workspace directory is self-contained (copy it to migrate); destructive operations require a `--yes` confirmation. The knowledge base requires SQLite ≥3.34 (satisfied by official CPython builds; if the current interpreter lacks it, the skill auto-switches to a capable one instead of degrading search quality). For search syntax, deep reading, playback, and the full set of management operations, see the "Knowledge-base workspace" section in [SKILL.md](SKILL.md).

## Cookies (restricted YouTube / Bilibili content ingestion)

No cookies are needed for normal use. Only when the script says so (the `cookieHint` field in the returned JSON):

1. Install the browser extension **Get cookies.txt LOCALLY** (or similar);
2. Visit youtube.com, log in, and export cookies in Netscape format;
3. Save to: `~/.myagentrag/cookies/youtube-cookies.txt`
   (on Windows: `C:\Users\<you>\.myagentrag\cookies\youtube-cookies.txt`);
4. Re-run the same command.

To use a custom location: point `MYAGENTRAG_YOUTUBE_COOKIES` to any path. Cookies are equivalent to an account session — never commit or share them.

**Bilibili**: AI auto-generated subtitles and login-walled videos require a logged-in session:

1. Export bilibili.com cookies in Netscape format with the same browser extension;
2. Save to: `~/.myagentrag/cookies/bilibili-cookies.txt`;
3. Re-run the same command. (`MYAGENTRAG_BILIBILI_COOKIES` points to any path; raw Cookie-header-style files are also accepted.)

## License

MIT
