Environment & dependencies — dedicated runtime install, how it works internally, temp dir, ffmpeg/whisper detection + download matrices, GPU matrix, cookie privacy.
READ WHEN: first use, missing components, transcription problems, environment questions.
NOT READING COSTS: you handle installs/troubleshooting less precisely; answering is unaffected.

## Installing Dependencies (dedicated runtime, fully decoupled from system Python)

The skill uses a **dedicated Python runtime** (standalone CPython 3.12 + pinned extension libraries) installed under `~/.myagentrag/runtime/`, completely isolated from the user's system Python — it installs nothing into the user environment and does not care which version that environment has.

- **First use auto-installs it**: when the dedicated runtime is missing, the skill lists name/source/estimated size (≈150 MB download) and installs it after the user confirms (an agent that already has consent may pass `--download-deps` to run non-interactively), then automatically continues the original task;
- **Very low bootstrap requirements**: any Python ≥3.8 (standard library only) can launch the skill; extension libraries (requests / yt-dlp / pdfplumber / PyMuPDF / python-docx / ebooklib / openpyxl / python-pptx) all ship pre-installed and pinned inside the dedicated runtime — the version matrix that passed the skill's tests is exactly what the user runs;
- The Python build comes from standalone python-build-standalone distributions (SHA256SUMS verified); the download source can be overridden with `MYAGENTRAG_PYTHON_MIRROR` and the pip mirror with `MYAGENTRAG_PIP_INDEX_URL` (recommended for networks in China);
- Reset/upgrade: delete `~/.myagentrag/runtime/` and re-run (knowledge-base data lives in `workspaces/`, components in `bin/`, `models/` — all unaffected);
- Audio/video features additionally need `ffmpeg` (auto-installed on first use through the same confirmation flow); the legacy `.doc` format needs `pandoc` (not auto-downloaded).

The entry point prefers `MYAGENTRAG_PYTHON` as the bootstrap interpreter and falls back to `python` on PATH — it only launches the skill and switches to the dedicated runtime; it needs no third-party libraries.

## How It Works

Module layout (`extract.py` keeps only the CLI entry point and dispatch):

```
scripts/extract.py      CLI entry point and dispatch
scripts/slicing.py      large-document slice protocol
scripts/extractors.py   content extractors (YouTube/Bilibili/web/document formats)
scripts/transcribe.py   whisper.cpp transcription (reports the GPU backend in use)
scripts/deps.py         component discovery, missing-dependency detection and confirmed install (runtime/ffmpeg/whisper/model)
scripts/runtime.py      dedicated Python runtime (standalone CPython + pinned libraries, decoupled from system Python)
scripts/messages.py     zh/en bilingual feedback (--lang override / locale auto-detect)
scripts/workspace.py    knowledge-base workspace (FTS5 retrieval / timestamp index / seeked playback)
tests/                  automated tests (test_<module>.py)
```

Extraction flow:

```
extract.py invoked (--url or --file)
  ├─ ① type detection: youtube / bilibili / web / local file (by extension)
  ├─ ② dispatch
  │    ├─ youtube  → yt-dlp fetches captions (on failure and suspected login wall → cookieHint)
  │    ├─ bilibili → login-free API fetches CC subtitles
  │    ├─ web      → Jina Reader
  │    └─ local file → dispatch by MIME (read text / parse PDF / Word / EPUB)
  ├─ ③ audio/video: check ffmpeg / whisper-cli / ggml model first
  │     ├─ all present → ffmpeg converts to WAV → whisper-cli transcribes → SRT/text
  │     └─ something missing → list items (name/purpose/source/size) → user confirmation
  │                    ├─ accepted → download & install (into ~/.myagentrag only) → automatically re-run the original task
  │                    └─ declined / non-interactive → return the missing-items JSON, download nothing
  └─ ④ emit JSON (success: title/author/transcript/content; failure: error/missing/cookieHint)
```

Key rules:

- The scripts never call an LLM: extraction and indexing are fully local;
- Components are downloaded only when missing and only after confirmation, and only into `~/.myagentrag` — never into system directories;
- Intermediate files of each run live in `myag_*` temp directories and are cleaned up on normal exit;
- With no network / missing components the tool returns a structured JSON error so the agent can decide whether to retry or explain to the user.

## Temporary Directory

Intermediate files from YouTube/audio/video extraction (yt-dlp subtitles, the 16 kHz mono WAV written by ffmpeg, whisper SRT) live in a temporary directory; each run uses a `myag_*` subdirectory that is deleted on normal exit, and leftovers older than 72 hours are swept by later runs.

Temp-root resolution order: `MYAGENTRAG_TMPDIR` (explicit, supports `~`) → the managed directory `~/.myagentrag/tmp` (default — a self-managed area next to bin/models/runtime, so nothing is scattered into the system temp directory) → the system temp directory (fallback only when the managed directory is not writable), e.g. `export MYAGENTRAG_TMPDIR="$HOME/.cache/myagentrag-tmp"`. Never place cookies, models or important original files into the temp directory.

## ffmpeg and whisper.cpp

**The initial install downloads no components; runtime detection happens on actual use.** Every auto-downloaded component goes only into the user directory (`~/.myagentrag`) — never into system directories.

### Detection order (runs automatically before each transcription)

- ffmpeg: `MYAGENTRAG_FFMPEG` → PATH → the copy downloaded into the managed directory.
- ffplay (the built-in player used by `--play` seeked playback): `MYAGENTRAG_FFPLAY` → PATH → the managed directory (shipped alongside the ffmpeg bundle; the macOS evermeet single-binary package contains no ffplay).
- whisper-cli: `MYAGENTRAG_WHISPERCPP_CLI` → PATH → `MYAGENTRAG_WHISPERCPP_DIR` → the managed directory.
- GGML model: `MYAGENTRAG_WHISPERCPP_MODELS_DIR` → the managed models directory. Model file names are `ggml-large-v3-turbo.bin` or `ggml-large-v3-turbo-q5_0.bin`.
- Managed directory: `MYAGENTRAG_HOME` (default `~/.myagentrag`, containing `bin/` and `models/`). Users with an existing custom install can point the environment variables above at any location.

### When missing: prompt, then download after confirmation

When an audio/video task detects missing components, the script lists each item's **name, purpose, source and estimated size**, waits for the user's confirmation, installs, and then automatically continues the original task:

- Interactive terminal: confirm directly with `y/N`;
- Agent/script invocation: show the list to the user for consent, then re-run with `--download-deps`; without consent the script only reports the missing list and downloads nothing.

Download sources and install locations:

- ffmpeg: gyan.dev zip on Windows, evermeet.cx on macOS, johnvansickle static builds on Linux x86_64/arm64; installed into `MYAGENTRAG_HOME` (default `~/.myagentrag/bin`).
- whisper-cli: the build is chosen automatically per hardware:
  ① `brew install whisper-cpp` on macOS/Linux when Homebrew is available (Metal enabled by default on macOS);
  ② Windows: with an **NVIDIA GPU** the official **prebuilt cublas** build is preferred (bundles the CUDA runtime — no CUDA Toolkit needed, ~270 MB); otherwise the official prebuilt CPU zip is downloaded together with upgrade guidance per GPU vendor (AMD/Intel: install the Vulkan SDK, delete the managed binaries and re-run to build a Vulkan version from source);
  ③ source build as the fallback (needs git/cmake/compiler); the backend is chosen automatically at build time: NVIDIA + CUDA Toolkit → CUDA; AMD + ROCm → HIP (gfx architecture auto-detected); Vulkan SDK present → Vulkan (the only official GPU path for AMD integrated GPUs such as the Radeon 780M, and for Intel iGPUs); otherwise CPU (stated explicitly). Custom CMake flags can be appended via `MYAGENTRAG_WHISPERCPP_CMAKE_FLAGS`; to switch backends, delete the `~/.myagentrag/whisper.cpp` build directory and the managed binaries, then retry.
- GGML model: downloaded from HuggingFace `ggerganov/whisper.cpp` (large-v3-turbo ≈1.6 GB, q5_0 ≈560 MB) into the first usable model directory above.

### Default download version matrix (whisper-cli)

Following the principle of "prebuilt first, GPU builds as an explicit upgrade path", the versions auto-installed per environment are:

| User environment        | First auto-install                                                        | GPU acceleration?                                                                        |
| ----------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Windows + NVIDIA        | Official prebuilt cublas (bundles the CUDA runtime)                       | ✅ yes                                                                                    |
| Windows + AMD/Intel GPU | Official prebuilt CPU + upgrade guide (install the Vulkan SDK and re-run) | ❌ no, explicit upgrade required                                                          |
| Linux (any GPU)         | Official ubuntu CPU prebuild / brew                                       | ❌ no (source builds only produce a GPU build when a Toolkit/ROCm/Vulkan SDK is detected) |
| WSL                     | Same as Linux                                                             | ❌ no (a GPU build additionally needs WSL driver passthrough)                             |
| macOS                   | brew install whisper-cpp                                                  | ✅ yes (Metal on by default)                                                              |

The official prebuilt assets only cover Windows NVIDIA cublas, macOS xcframework and CPU builds per platform; Vulkan for AMD/Intel and Linux CUDA exist only as source/Docker forms — hence the skill never compiles silently and only gives explicit upgrade guidance.

### GPU acceleration support matrix

After transcription, stderr reports the backend actually used (`🎮 GPU acceleration: Vulkan: AMD Radeon 780M...` / `🖥 CPU`). Pass `--no-gpu` to force CPU.

| GPU                | Recommended backend | How to get it on Windows                                                        | How to get it on Linux/macOS                                                 |
| ------------------ | ------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| **NVIDIA**         | CUDA                | Official prebuilt cublas zip (selected automatically; bundles the CUDA runtime) | Source build (needs the CUDA Toolkit) or the official main-cuda Docker image |
| **AMD discrete**   | ROCm/HIP or Vulkan  | Source build (Vulkan SDK or ROCm)                                               | Source build (HIP when ROCm is present, otherwise the Vulkan SDK)            |
| **AMD/Intel iGPU** | Vulkan              | Source build (needs the Vulkan SDK; no official GPU prebuild for AMD)           | Same as at left                                                              |
| **Apple Silicon**  | Metal               | —                                                                               | Enabled by default, no configuration needed                                  |

Whether the GPU is used depends on the backends compiled into the whisper.cpp binary; CPU builds — or an unavailable GPU backend/driver — fall back to CPU. The backend actually loaded is visible in the transcription stderr log. `large-v3-turbo-q5_0` is merely a quantised model and does not imply GPU acceleration.

## Cookie Privacy Rules

The skill package **ships no cookie files** and no longer asks users to configure paths up front.

- The conventional cookie locations are determined automatically:
  - YouTube: `~/.myagentrag/cookies/youtube-cookies.txt` (or any location via `MYAGENTRAG_YOUTUBE_COOKIES`)
  - Bilibili: `~/.myagentrag/cookies/bilibili-cookies.txt` (or any location via `MYAGENTRAG_BILIBILI_COOKIES`; both Netscape format and a raw Cookie header are accepted)
  - Bilibili cookies are for login-walled content (such as **AI-generated subtitles** — without a login session the API returns an empty list and only the uploader's manually uploaded CC subtitles are available);
- Public videos need no cookies; the scripts access them anonymously;
- Only when yt-dlp fails on a login check / bot protection / age restriction does the script hint, in the `cookieHint` field and on stderr, that the user should export Netscape-format cookies with a browser extension (e.g. Get cookies.txt LOCALLY) and **place them manually** at the paths above before retrying;
- The skill never creates, collects or uploads cookies; files are placed by the user alone.

Cookies carry account-session privileges and must not be committed to the skill repository, copied to other agents or put in shared directories; mind the file permissions.

