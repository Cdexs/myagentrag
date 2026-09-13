---
name: myagentrag
description: Local knowledge-base (RAG) build & retrieval tool: ingest YouTube/Bilibili subtitles, web articles, local files (PDF/Word/Excel/PowerPoint/EPUB/text) and audio/video transcripts into local knowledge-base workspaces; hybrid keyword + semantic + heading-anchor retrieval (FTS5 + Qwen3 embeddings); structure-aware deep reading and timestamped playback. Extraction and indexing run fully locally with no LLM calls; reading and answering from retrieval results is done by the calling agent. Also matches Chinese-language requests such as 知识库 / 入库 / 检索 / 精读 / 定位回放 / 字幕 / 知识库检索.
compatibility: Windows / macOS / Linux / WSL; bootstrap layer requires any Python ≥3.8 (standard library only); first use auto-installs the dedicated runtime (SQLite ≥3.34 included); audio/video transcription additionally needs ffmpeg, whisper.cpp and a GGML model
---

# MyAgentRAG — Local Knowledge-Base Build & Retrieval Tool

**Design principle**: the scripts only extract and index — they never call an LLM. Searching, deep-reading and answering from ingested content is done by the calling agent; **presenting retrieval/deep-read results must follow the unified presentation contract** (hit-list format + closing source-links list; see section ② "Result presentation contract" under "Retrieval").

## Supported Content Sources (what can be ingested)

| Type | Formats | Notes |
| -------------- | ------------------------------------------------------- | ---------------------------------------------------------------------- |
| **YouTube** | Video URL | Manual/auto captions and metadata via yt-dlp |
| **Bilibili** | Video URL | CC subtitles and video info via the login-free API |
| **Web page** | HTTP/HTTPS link | Body text via Jina Reader (`r.jina.ai`) — **the URL is sent to a third-party service** (avoid privacy-sensitive links); intranet/loopback addresses are refused; if Jina is unavailable or rate-limited the `--url` feature is entirely unavailable (structured error including HTTP status and URL) |
| **Text files** | `.txt`, `.md`, `.markdown`, `.rst`, `.csv` | Read directly |
| **PDF** | `.pdf` | pdfplumber or PyMuPDF |
| **Word** | `.docx`, `.doc` | python-docx; `.doc` additionally needs pandoc |
| **EPUB** | `.epub` | ebooklib |
| **Excel** | `.xlsx`, `.xlsm` | openpyxl (one segment per worksheet, rows joined with " \| ") |
| **PowerPoint** | `.pptx` | python-pptx (one segment per slide, structured: `## Slide N` + `#` title + `###` subtitle/body/table/speaker notes) |
| **Audio** | `.mp3`, `.wav`, `.aac`, `.m4a`, `.flac`, `.ogg`, `.wma` | Transcode to PCM with ffmpeg, then transcribe with whisper.cpp |
| **Video** | `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv`, `.flv`, `.webm` | Embedded subtitles first; if none, extract audio and transcribe |

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

## Usage

Single entry point:

```bash
PYTHON="${MYAGENTRAG_PYTHON:-python}"
EXTRACTOR="<skill-dir>/scripts/extract.py"
"$PYTHON" "$EXTRACTOR" --file "document.pdf" --workspace my-docs        # extract and ingest
"$PYTHON" "$EXTRACTOR" --workspace my-docs --search "query terms"       # hybrid retrieval
"$PYTHON" "$EXTRACTOR" --workspace my-docs --entry <entry-id>           # deep read one entry
"$PYTHON" "$EXTRACTOR" --workspace my-docs --play <entry-id> --at 12:33 # seeked audio/video playback
```

From Windows PowerShell:

```powershell
$Python = if ($env:MYAGENTRAG_PYTHON) { $env:MYAGENTRAG_PYTHON } else { "python" }
& $Python "<skill-dir>/scripts/extract.py" --file "document.pdf" --workspace my-docs
```

Output formats:

- `--output json` (default): full JSON (with title/author/transcript/content/success)
- `--output text`: title + plain-text body
- `--output srt`: SRT subtitles (audio/video transcription only)

Extra flags and fields:

- `--download-deps`: skip the interactive confirmation and download missing components directly (for an agent that re-runs after obtaining user consent);
- `--lang zh|en`: feedback language. Defaults to auto-detected system locale (the `MYAGENTRAG_LANG` environment variable also overrides it); built-in messages are provided in JSON as `error_i18n: {"zh": ..., "en": ...}` so the agent can pick the language of its UI (raw exception text is not translated);
- On failure the JSON may include `missing` (list of missing components) or `cookieHint` (YouTube requires a login check) — show these to the user verbatim.

**Exit-code and success contract**: `rc=0` task succeeded; `rc=1` task failed (extraction or ingestion failed — always with top-level `success:false`); `rc=2` argument misuse. **Failed ingestion** (embedding failure / missing components) reports top-level `success=false`, rc=1, details under the nested `workspace_error` (with bilingual `error_i18n`), and still returns `content` — **the agent must check top-level `success`, not merely whether content exists**; with `--workspace`, failed ingestion means the task failed — do not treat a successful extraction as a successful task.

> Set `HTTPS_PROXY` first when YouTube needs a proxy. Restricted YouTube content may need cookies; public captions normally do not.

## Typical Scenarios (agent playbook)

Pick the path by user intent; the same command shows a y/N prompt on an interactive terminal and emits a structured missing-items list to an agent (re-run with `--download-deps` after obtaining the user's consent).

**① "Store this book/document into the knowledge base" (extract and ingest)**

```bash
"$PYTHON" "$EXTRACTOR" --file "book.epub" --workspace my-books
```

The ingest result contains entry_id/chunk_count/vectors (number of vector windows). The first knowledge-base use walks you through installing the embedding chain (llama.cpp engine ~34MB + Qwen3 model ~610MB + sqlite-vec ~0.3MB): interactive terminals prompt y/N directly; an agent first shows the list to the user for consent, then re-runs with `--download-deps`. A missing workspace is created implicitly; re-ingesting identical content is idempotent and only updates. **Re-ingesting the same source** (same source_ref) with changed content creates a new entry; the response lists the old entry id under `workspace.supersedes` with a stderr warning — after confirming, clean it up with `--remove <old-id>`, or pass `--replace` at ingest time to replace automatically.

**② "What did that document I saved earlier say about X" (retrieval → deep reading loop; the recommended main path)**

Fixed three steps, **step 2 is mandatory**: ① `--search` to get candidates → ② `--section` deep-read the hits **you are going to present** → ③ output per the presentation contract. **Retrieval finds the location; deep reading gets the content** — both are required (answering a content-type question from `snippet` alone is a violation).

```bash
"$PYTHON" "$EXTRACTOR" --workspace "@my-books" --search "X keywords"   # user names the workspace with @: pass @ through as-is
"$PYTHON" "$EXTRACTOR" --workspace my-books --search "X keywords"      # equivalent form (agent already resolved the name)
"$PYTHON" "$EXTRACTOR" --workspace my-books --search "X" --mode fts    # keyword only (no vector chain)
```

Hit JSON fields (agent consumption guide): `title/entry_id/source_type/source_ref` (source file or URL), `score + score_source + score_kind` (multi-route overlaps such as `fused+heading`), `scores: {fts, vector, heading}` (per-route RRF contribution in fused mode), `column_filter` (metadata-filter marker) + `filtered_entries` (candidate entry count after filtering), `candidates_total` (**pre-truncation** fused candidate count) + `truncated_by_limit` (whether `--limit` cut the pool) + `hint`/`hint_i18n` (present only when truncated, with a remedy), `keyword_miss` (no FTS hits, semantic route only — wording may differ from the source; do not conclude "the library has nothing about it"), `snippet` (『』 highlight; **a preview window returned by retrieval, incomplete — deep-read with `--section`**), `chunk_no/chars` (read a chunk with `--chunk N`), `heading{text,level}` (containing section), `section_ref` (opaque deep-read reference) + `section_chars`, `same_section_hits` (other hits in the same section), `source_loc` (source position: `{kind:"pdf",page}` / `{kind:"epub",chapter,title}` / `{kind:"time",start_ms,end_ms}` / `{kind:"line",n}`), `vector_backend`, **`locator` (clickable locator; retrieval hits and the `--entry/--chunk/--section` read paths share one shape, unified protocol entry as of v0.1.2)**: always expose `{link:"myagentrag://goto?ws=…&entry=…[&at=…]", action}` as the clickable target — `action=open` (document hit, with `kind`/`open_scope:"file_only"`/transition field `open`) → render `[Open original file](link) (target_label)`; **when `open_scope=file_only`, never claim the link jumps to the target page/chapter** (readers do not support deep links; promise "opens the file" only). `action=play` (media hit, with `target_label:"mm:ss"`/`fallback_play_cmd`) → render `[▶ Play from mm:ss](link)`; the link is handled by the skill's own protocol handler (`goto`: seeked playback for media, open-original-file for documents) and **does not depend on the client's `file://` policy**; `open` (file:///) is a transition field (kept for old renderers, removed in the next major version). When the client does not render non-http(s) URIs, use `fallback_play_cmd` (a copy-pasteable command); **when `fallback_play_cmd` is `null`, do not give the user any playback command** (no ffplay on this machine — point to `--repair-deps` or `MYAGENTRAG_FFPLAY`; the link itself still works and the protocol entry returns a structured error). **The two shapes of an empty result must be distinguished**: unknown workspace name → rc=1 structured error `ws_not_found` (identical in every mode, including column filters; a cross-workspace search with no workspaces at all reports `ws_no_workspaces`); a valid workspace with no matches / zero candidates after filtering → success:true + `workspaces_searched` listing the searched workspaces + empty hits. On zero hits, retry with different wording or `--mode vector` (the semantic route recalls cross-language content).

Performance semantics (transparent to the agent): each route over-recalls 3×limit candidates before fusion and truncation; a cross-workspace search starts the embedding engine only once (independent of workspace count); query embeddings are persistently cached (`~/.myagentrag/cache/query-embeddings.db` — a repeated search with the same model and query costs no embedding at all; switching models invalidates it automatically, and the file can simply be deleted to rebuild).

**② Result presentation contract (hit-list format + source-links list; mandatory for every retrieval/deep-read presentation)**

Punctuation in rendered links follows the language of the reply. Independent of client and phrasing, the structure is fixed: **retrieval-type answer = retrieval summary → hit list → source-links list**; **deep-read-type answer (the user asks about one chapter/entry) = content or conclusion → source-links list** (no hit list). Question-type requests get the conclusion first, then the list and the links; inline citations in the conclusion use the same labels.

**All of this refers to a single reply: one question, one answer that delivers everything — the hit list and the clickable source-links list both appear in that one reply. Never deliver half the result and wait for the user to ask again for the links** (the agent may call retrieval → deep reading several times internally, but it assembles one complete answer; the user sees a single reply).

1. **Retrieval summary** (one line): `N hits in "workspace-name"` (N = number of entries actually expanded in the list; when the CLI returned more than shown, append "N more not shown" per the "count" rule below); cross-workspace: `M hits across N workspaces`, followed by per-workspace grouping. Do not mention retrieval modes or other implementation details.
2. **Hit list** (one uniform format per item; nothing else interleaved between items):

   ```markdown
   N. **entry title** — provenance label
      > hit snippet
   ```

   The provenance label is the natural-language form of `source_loc`: `page 12` / `chapter 3 · chapter title` / `line 88` / `12:33–12:47` (media uses `start_ms–end_ms`); the snippet comes from `snippet` (**a preview window returned by retrieval, possibly truncated with `…`, not the full content; the conclusion section must come from deep reading**) — **keep the 『』 highlights**, limit to 1–2 lines, truncate long text with `…`, and **never rewrite, splice or invent**; items follow the returned order (i.e. relevance order); multiple hits sharing one `entry_id` merge into its first occurrence (keep the highest-scoring item's label, at most 2 quoted snippets in the block) while the relative order of other items is unchanged; items without a provenance label (web pages etc.) get the source type or domain instead.
3. **Source-links list** (mandatory at the end of the same reply; rules below); a deep-read-type answer skips items 1–2 and goes straight to "content/conclusion → list".

Other hard rules:

- **Never show internal fields to the user**: `score/scores/rrf/chunk_no/section_ref/entry_id/offset/vector_backend` etc. must not appear in the answer text (parameters inside `myagentrag://` links are carried by the link itself — do not extract them for human reading).
- **Count**: expand at most **10** items by default; with more, append "N more not shown (narrowing the keywords reduces noise)"; when the user explicitly asks for "all / everything / list them all", list them all (with `--limit 50`).
- **Cross-workspace**: start with a group heading `**workspace-name** (N hits)` and keep relevance ordering inside each group; to state which workspace an item came from, the group heading is enough — do not repeat the workspace name per item.
- **Inventory (`--list`/`--stats`) follows the same rules**: numbered items, bold titles, no internal fields; there is no retrieval-hit concept, so no source-links list.
- **Zero hits**: produce neither list nor links — explain honestly per "the two shapes of an empty result" above and add one next-step suggestion (different wording / `--mode vector` / verify the workspace name).
- The provenance labels in the body citations, the hit list and the source-links list must correspond one-to-one (if one says "title — page N", the others say "page N" too; never page in one place and chapter in another).

**Retrieval depth: hit lists use `snippet`, the conclusion section must come from deep reading (never pass a preview off as content)**

The hit list and the conclusion section draw on different sources and **must not be mixed up**:

| Section | Data source | Notes |
| --- | --- | --- |
| Hit list | `snippet` | A **preview window** returned by retrieval (possibly truncated with `…`); it only tells the user "which item this is" |
| Conclusion / content section | **The original text returned by `--section` deep reading** | **Must** come from deep reading; `snippet` must **not** serve as the content source |

> `snippet` is an **index preview, not the content itself** — using `snippet` as the conclusion section is passing off a table of contents as the book; `…` means the content was truncated, and **whatever follows the cut must be obtained by deep reading**.

- **Content-type questions: deep-read first, then answer (hard requirement)**: when the user asks "what does it say / what did it mention / what is the specific content / tell me in detail" — ① `--section <section_ref>` deep-read **every** hit you are going to write into the conclusion; ② when hits span multiple sources (multiple `entry_id`s / chapters) **deep-read each one** — never read the first and extrapolate to the rest; ③ if deep reading still falls short (the hit lands in an oversized synthetic section, or the original text itself is a fragment), **say honestly "only a fragment is available here"** — never pass `snippet` off as the full text; ④ control deep-reading cost by chunked reads with `--max-chars` (oversized sections open a window around the hit), and **never skip deep reading on the excuse of "saving context"**.
- **Fix candidate truncation first**: `truncated_by_limit: true` (or a `hint`) means candidates were **never returned at all** — for content/enumeration questions, re-run with `--limit 50` as the `hint` suggests (mind the shared quota in cross-workspace mode) and then deep-read the new results; **never draw conclusions from a truncated candidate set**.
- **Deep-reading boundary (avoid unbounded deep reading)**: **every source actually cited in the conclusion must be deep-read**; high-scoring candidates you do not cite need not be — for cross-workspace/wide searches, first narrow to the hits you will present, then deep-read them one by one.
- **Qualitative conclusions must rest on deep reading**: any "this source does / does not contain X" judgement (e.g. "this source is only prophetic tradition with no concrete prophecy") must rest on deep-read text and point to a specific page/chapter/timestamp in the sentence; **never** derive a qualitative conclusion from a `snippet` preview.

**Example: the same hit — `snippet` (preview) vs deep reading (content)**

```markdown
snippet returned by --search (hit list only):
  > ……prophets will foretell『things soon to come』unto thee; thou shalt behold……

after --section deep reading of the same hit (the conclusion section may only use this):
  > The mathematicians prophesied great cities and nations about to rise; they foretold that this and that would go to war;
  > their great cities would become ruins, covered by falling nebula…… the mathematicians foretold the coming of kosmon,
  > the ruined cities would be discovered, their history read in the hand of the Great Jehovih……
  > And God said: in that time men will betray the Most High, slay his believers, and prefer idols of stone and metal……

> If the conclusion section can only give the truncated `…` fragment above, deep reading has not happened — **deep-read first, then answer**.
```

**Presentation requirements for the conclusion / content section (anti-compression)**

The three sections must not swap roles:

| Section | Role | Length |
| --- | --- | --- |
| Retrieval summary | Report counts and scope only | One line |
| Hit list | **An index, not content** | ≤10 items by default; ≤2 lines quoted per item |
| Conclusion / content section | **The only section carrying substantive content** | **No limit** — driven by the question and the volume of hits |

⚠️ Every "1–2 lines" and "at most N items" in this contract **constrains the hit list only and does not spill over into the conclusion section**.

- **Information completeness (hard requirement)**: the conclusion/content section must cover every substantive point in the **original text obtained by deep reading** (the data source is the deep-read text, not `snippet` previews), with the following five classes of information individually traceable — ① time (era/date/range); ② quantities (counts/ratios/ordinals); ③ proper nouns (people/places/organizations/events); ④ causes and conditions ("if…then…" / "because…therefore…"); ⑤ exceptions and qualifications ("not all…", "not necessarily every…"). **Never** replace countable facts with vague quantifiers ("etc.", "several", "a number of", "a series of", "some"); **never** merge two or more independent points into one sentence.
- **Quote first (applies to the whole answer)**: prefer direct quotation of the original (keep 『』 and the original wording); a paraphrase must retain the five classes above, must not merge distinct points and must not drop qualifications; **never** replace a specific term with a broader one ("Chernobyl" → "a nuclear accident", "Reglando River" → "a border river"). "Snippets must not be rewritten/spliced/invented" also constrains paraphrasing in the conclusion section, not just list snippets.
- **Detail levels** (choose by user intent; when unsure, err on the detailed side — detail can be trimmed, brevity cannot be filled in):

  | User intent | Typical phrasing | Conclusion / content section |
  | --- | --- | --- |
  | Locating | "is there anything about X" / "where is X mentioned" | Brief: one sentence per point + provenance |
  | **Content-type** | **"what does it say" / "what did it mention" / "what is the specific content"** | **Expand**: list item by item following the original structure, keeping details and quotations; **fetch candidates with an explicit `--limit 50` (never the default 20)** |
  | Enumerating | "list everything about…" / "find all…" | Expand without omission (with `--limit 50`) |
  | Comparing | "what A and B say about X" | Expand per source, point by point |

- **Pre-output self-check** (rewrite if any item fails): ① does every substantive point of the deep-read text appear in the conclusion? ② are eras/numbers/proper nouns preserved verbatim (not replaced by "several"/"a number of")? ③ is any place merging multiple points into one sentence? split it. ④ do paraphrases keep the original conditions and exceptions? ⑤ are the hit list and the conclusion clearly different in detail level? (if both are equally brief, the conclusion was compressed)
- **Retrieval-depth self-check** (on par with the previous item; rewrite if any fails): ⑥ can every substantive statement in the conclusion be located in the **text returned by deep reading**? (if it can only be located in a `snippet`, deep reading did not happen) ⑦ has **every** source cited in the conclusion been deep-read via `--section`? ⑧ is any `snippet` `…` truncation being cited as if it were complete content?

**Example contrast (same hits, two phrasings)**:

```markdown
Q: "Is there anything in the library about move semantics?" (locating → brief + list + links)

3 hits in "my-books":
1. **Rust Ownership Model** — chapter 3 · Move Semantics
   > ……when a variable leaves its scope a 『move』 occurs and ownership is transferred……
(the remaining hits and the source-links list omitted — same as the example above)

Q: "What does the library say about move semantics?" (content-type → expand)

Three sources in the library discuss move semantics:

**1. Rust Ownership Model (chapter 3 · Move Semantics)**
- 『move』 is an ownership transfer, not a memory copy: ownership moves when the variable leaves its scope;
- after the move the original variable can no longer be used; the compiler errors at compile time;
- versus borrow: borrowing does not transfer ownership and returns it when the scope ends.

**2. Lecture recording: Memory Management (12:33–12:47)**
- the speaker explicitly distinguishes it: move semantics here are not the same thing as C++ rvalue references……

(remaining sources expand at the same granularity; the source-links list is still mandatory at the end)
```

**Source-links list (mandatory at the end of the answer; never omit)**

**Whenever retrieval or deep-read results are presented to the user, the answer must end with a "source links" list** itemising the sources cited (deep-read ones always; otherwise the high-scoring hits; one line per `entry_id`, grouped per workspace in cross-workspace searches):

- Document hit (`locator.action=open`): `- [Open original file](myagentrag://goto?…) (page N / chapter N · title / line N)` — use `locator.link` for the link and `locator.target_label` inside the parentheses (omit the parentheses when the field is absent; never invent a location); with `open_scope=file_only` never claim the link jumps to the target page/chapter.
- Media hit (`locator.action=play`): `- [▶ Play from mm:ss](myagentrag://goto?…)` — use `locator.link` (mm:ss is `target_label`); when the client does not render non-http(s) URIs, add the copy-pasteable `fallback_play_cmd`; when `fallback_play_cmd` is `null`, give no command but one fix hint (`--repair-deps` / `MYAGENTRAG_FFPLAY`).
- Hits without a `locator` (web sources, source file no longer reachable, ingested with `--no-keep-source`): list `title + source_ref` as plain text — **never fabricate a clickable link**.
- The list must correspond one-to-one with the body citations (every source mentioned in the body has an entry); never substitute internal references such as `section_ref`/`entry_id` for a user-readable link.
- Deep-read outputs (`--entry/--chunk/--section`) also carry `locator`, with the same rules as retrieval hits; on zero hits no list is produced.

Full presentation example (user asks: "where does the library discuss move semantics?"):

```markdown
3 hits in "my-books":

1. **Rust Ownership Model** — chapter 3 · Move Semantics
   > ……when a variable leaves its scope a 『move』 occurs and ownership is transferred……
2. **Lecture recording: Memory Management** — 12:33–12:47
   > ……the 『move』 semantics here are not the same thing as C++ rvalue references……
3. **Language design notes** — line 88
   > ……a 『move』 happens implicitly when passing arguments……

**Source links**
- [Open original file](myagentrag://goto?ws=%E6%88%91%E7%9A%84%E4%B9%A6%E6%9E%B6&entry=826ab4aa12ebf20b) (chapter 3 · Move Semantics) — Rust Ownership Model
- [▶ Play from 12:33](myagentrag://goto?ws=%E6%88%91%E7%9A%84%E4%B9%A6%E6%9E%B6&entry=0123456789abcdef&at=753) — Lecture recording: Memory Management
- [Open original file](myagentrag://goto?ws=%E6%88%91%E7%9A%84%E4%B9%A6%E6%9E%B6&entry=0c9280141973a80c) (line 88) — Language design notes
```

**Phrasebook: how user wording maps to agent actions** (Chinese phrasings are kept verbatim as literal triggers)

| User says | Intent | Agent action |
| ----------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| "@my-books look up XXX" / "**in** the 'my-books' **library**, find XXX" / "**search** with my my-books library" / "**based on** my document library, answer …" / "**use** library XXX to retrieve" (Chinese: "@我的书架 查一下 XXX" / "在'我的书架'库里查" / "用我的书架库搜" / "根据我的资料库回答" / "使用 XXX 库检索") | Named workspace (@ / in / with / based on / using + name) | Parse the workspace name from the prompt → `--workspace <name> --search ...`; **the @ prefix may be passed through as-is** (the CLI strips it); when unsure of the name, resolve it first with `--workspace-list` (fuzzy matching is the agent's job) |
| "what does the library say about X" / "what did it mention" / "what is the specific content" (Chinese: "库中关于 X **讲了什么 / 说了哪些 / 具体内容是什么**") | **Content-type** | ① `--search "X" --limit 50` for candidates (**never the default 20**; raise it further when `truncated_by_limit: true`); ② `--section` deep-read **every** hit to be presented (**never answer from `snippet` alone**); ③ output per "detail levels · content-type" — **expanded** — plus the closing source-links list |
| "**find** XXX in the 'my-books' knowledge base" (Chinese: "在知识库'我的书架'里**查找** XXX") | Named-workspace retrieval | `--workspace my-books --search "XXX"` (fused by default); **output per the section ② presentation contract** (uniform hit list + closing source-links list); deep-read with `--section` for follow-up questions |
| "**look up** XXX in the knowledge base" (no workspace given; Chinese: "在知识库里**查一下** XXX") | Cross-workspace retrieval | `--search "XXX" --all-workspaces` — results carry a `workspace` field; when hits spread across workspaces, group them per workspace; **limit is the shared quota across all workspaces** — use an explicit `--limit 50` for wide cross-workspace searches |
| "**research** whether XXX … in knowledge base XX" / "does it cover XXX" / "does it support XXX" (Chinese: "在知识库 XX 中**研究一下**是否 XXX / 有没有讲 XXX / 是否支持 XXX") | Verification question | ① `--search "XXX"` (fused); ② zero hits → retry with synonyms/split terms, or `--mode vector` (the semantic route recalls cross-language content — English sources can be recalled from a Chinese question); ③ deep-read the top 1–3 hits with `--section`; ④ **the answer must carry provenance** (entry title + `source_loc` page/chapter/timestamp) and **follow the section ② presentation contract** (hit list + closing source-links list); when the library genuinely has nothing, say so plainly ("the knowledge base contains nothing on this") — never substitute model memory for retrieval findings |
| "**compare** what A and B say about XXX" | Multi-source comparison | `--search` for each (or search one library and group by `entry_id`) → deep-read the best section of each with `--section` → compare per source, citing each `source_loc`; `--limit 50` is recommended so every source has candidates |
| "**list / find all** content about XXX / every entry touching XXX" | Enumeration | `--search "XXX" --limit 50` (the default 20 is a coverage trade-off; raise it explicitly for enumeration/inventory/multi-entity cases; pair with `--list` for an entry-level overview) |
| "what does the knowledge base **contain** / what materials are in it" | Inventory | `--workspace <name> --list` (entry list) or `--stats` (entries/characters/source distribution/db size) |
| "**collect** these files into the knowledge base" | Batch ingest | Multiple `--file a.pdf --file b.docx` or one `--dir <directory>` batch ingest (**all windows merged into a single embedding call**); idempotent, no duplicates; a failing file is skipped without blocking the rest; contract in the "Ingestion" section's batch contract |
| "**search for** entries whose title contains XX" | Metadata filter | `--search 'title:XX'` (column prefixes `title:/author:/publisher:/publish_date:` combinable with body terms as `title:XX AND keywords`; **column values need ≥3 characters** (trigram limitation); range filters `publish_date>=2024` (`created_at` likewise, supporting >=/<=/>/<); the filter narrows the candidate entry set and **all three retrieval routes participate**; results carry `column_filter: true` + `filtered_entries`; a **filter with no topic terms** cannot be retrieved in ranked order (structured error — use `--list` to browse)) |

Worked example for a verification question ("find what the library says about move semantics"):

```bash
"$PYTHON" "$EXTRACTOR" --workspace my-books --search "move semantics"                  # ① three-route fused retrieval
"$PYTHON" "$EXTRACTOR" --workspace my-books --search "move semantics" --mode vector    # ② semantic retry on zero hits
"$PYTHON" "$EXTRACTOR" --workspace my-books --section "<section_ref of the best hit>"   # ③ deep-read the whole section, then answer
```

When `section_chars` is very large, switch to `--entry <id> --chunk N` and read chunk by chunk; media hits carry `start_ms/end_ms` and can be verified directly with `--play --at`.

**③ "What exactly does clause 14 say" (structure-anchor deep read)**

Use `--search "clause 14"` (the heading route hits section titles directly) and pass the returned `section_ref` through unchanged:

```bash
"$PYTHON" "$EXTRACTOR" --workspace my-books --section "<section_ref>"     # deep-read the whole section
```

Structure anchors come automatically from docx heading styles / EPUB h1-h6 / PDF bookmarks / heuristics for "Chapter N, Clause N, Chapter N, numbered headings"; `--reindex` rebuilds them for older entries. Older entries may have `source_loc: null` (no source-position ledger) — re-ingest to get complete anchors.

**④ "Where in that video is the part about Y" (retrieval + seeked playback)**

```bash
"$PYTHON" "$EXTRACTOR" --workspace my-books --search "Y topic"             # media hits carry start_ms/end_ms
"$PYTHON" "$EXTRACTOR" --workspace my-books --play <entry-id> --at 12:33 [--duration 60]
```

When no player is available the tool returns structured JSON (candidates/hint) and the agent explains it to the user or installs a player on their behalf — nothing pops up on screen.

**⑤ Multiple workspaces and cross-workspace search**

```bash
"$PYTHON" "$EXTRACTOR" --search "keywords" --all-workspaces                 # search every workspace
"$PYTHON" "$EXTRACTOR" --workspace-list / --workspace <name> --stats / --list  # management and inventory
```

**⑥ Agent invocation conventions (general)**: parse the JSON output; argument misuse (at the argparse layer) also returns JSON (rc=2); when merging `2>&1`, stderr progress lines break the JSON — stdout is the only JSON channel, or pass `--quiet` to suppress progress; act on `error` rather than retrying blindly; the `missing` list requires the user's consent before re-running with `--download-deps`; when a destructive operation (`--remove`/`--delete-workspace`) returns `confirm_required`, confirm with the user and re-run with `--yes`; pick bilingual `error_i18n` text by UI language.

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

| User environment | First auto-install | GPU acceleration? |
| ----------------------- | ---------------------------------- | ----------------------------------------------- |
| Windows + NVIDIA | Official prebuilt cublas (bundles the CUDA runtime) | ✅ yes |
| Windows + AMD/Intel GPU | Official prebuilt CPU + upgrade guide (install the Vulkan SDK and re-run) | ❌ no, explicit upgrade required |
| Linux (any GPU) | Official ubuntu CPU prebuild / brew | ❌ no (source builds only produce a GPU build when a Toolkit/ROCm/Vulkan SDK is detected) |
| WSL | Same as Linux | ❌ no (a GPU build additionally needs WSL driver passthrough) |
| macOS | brew install whisper-cpp | ✅ yes (Metal on by default) |

The official prebuilt assets only cover Windows NVIDIA cublas, macOS xcframework and CPU builds per platform; Vulkan for AMD/Intel and Linux CUDA exist only as source/Docker forms — hence the skill never compiles silently and only gives explicit upgrade guidance.

### GPU acceleration support matrix

After transcription, stderr reports the backend actually used (`🎮 GPU acceleration: Vulkan: AMD Radeon 780M...` / `🖥 CPU`). Pass `--no-gpu` to force CPU.

| GPU | Recommended backend | How to get it on Windows | How to get it on Linux/macOS |
| ----------------- | ----------------- | ----------------------------------- | ------------------------------------------- |
| **NVIDIA** | CUDA | Official prebuilt cublas zip (selected automatically; bundles the CUDA runtime) | Source build (needs the CUDA Toolkit) or the official main-cuda Docker image |
| **AMD discrete** | ROCm/HIP or Vulkan | Source build (Vulkan SDK or ROCm) | Source build (HIP when ROCm is present, otherwise the Vulkan SDK) |
| **AMD/Intel iGPU** | Vulkan | Source build (needs the Vulkan SDK; no official GPU prebuild for AMD) | Same as at left |
| **Apple Silicon** | Metal | — | Enabled by default, no configuration needed |

Whether the GPU is used depends on the backends compiled into the whisper.cpp binary; CPU builds — or an unavailable GPU backend/driver — fall back to CPU. The backend actually loaded is visible in the transcription stderr log. `large-v3-turbo-q5_0` is merely a quantised model and does not imply GPU acceleration.

## Knowledge-Base Workspace (SQLite FTS5 full-text retrieval + timestamped seeked playback)

Extracted content can be ingested into a local knowledge base (workspace) for later retrieval and deep reading. **Extraction and indexing only — no LLM calls**; the retrieval engine is FTS5 built into the Python standard-library sqlite3 (trigram tokenizer) with zero external dependencies, supporting BM25 relevance ranking, snippet previews, and phrase/boolean/prefix/NEAR queries.

### Ingestion (ingest while extracting)

```bash
# passing --workspace ingests; a missing workspace is created implicitly (explicit creation: --workspace <name> --create)
"$PYTHON" "$EXTRACTOR" --file "document.pdf" --workspace my-docs
"$PYTHON" "$EXTRACTOR" --url "https://www.bilibili.com/video/BVxxxx" --workspace my-docs
# batch ingest: multiple --file or a --dir (scans supported files in that directory, no subdirectories) —
# all windows merge into ONE embedding call (one llama-server start, N files N×1.2s → 1×1.2s); output is always JSON
"$PYTHON" "$EXTRACTOR" --file a.pdf --file b.docx --file c.epub --workspace my-docs
"$PYTHON" "$EXTRACTOR" --dir "docs-folder" --workspace my-docs
# optional metadata (title/author/publisher/publish-date); defaults come from the content or the file name
"$PYTHON" "$EXTRACTOR" --file "lecture.mp3" --workspace my-docs --title "Lecture title" --author "Author"
```

Ingestion rules:

- **Idempotent**: an entry id is the first 16 hex digits of the content sha256; re-ingesting identical content only updates metadata and never duplicates an entry;
- **Source copy**: local files and web snapshots are copied into `source/<id>/` by default (audio/video are always copied — playback needs them); `--no-keep-source` disables it;
- **Audio/video timestamp index**: ingested audio/video always goes through whisper SRT transcription, segment-level timestamps are stored in `transcript.json`, and every chunk carries `start_ms`/`end_ms` (ordinary extraction is unaffected: video still tries embedded subtitles first);
- **Granularity semantics for media entries** (settled in KB-AUD-06): a media entry's **chunk granularity is decided by the transcribed text length** (spoken density ≈260 characters/minute, so the 40K-character cap only produces a second chunk after ≈2.5 hours of audio — long recordings are usually one chunk, which is normal). **Seek/playback granularity does not depend on chunks**: retrieval hits are precise to the segment timestamp (`timestamp_precision: segment`), the semantic route works on 800-character embedding windows (independent of chunks), and reads are protected by `--max-chars` plus hit-centred windowing — `--play --at` seeks by paragraph without needing time-based slicing;
- **Batch contract** (with multiple `--file` or `--dir`): output `{"batch": true, "results": [per-file entry summary (entry_id/title/chunk_count/vectors/updated/**supersedes/replaced**) or {success:false,error}], "failed": [failed files], "embedded_windows": N}`, rc = 0 when all succeed / 1 when any fails; a failing single file is skipped without blocking the rest; **a failed merged embedding → nothing is ingested** (structured error); `--url` does not participate in batch mode (combining them is an error); **`--dir` is always batch-shaped** (even when it scans a single supported file, so callers only parse one contract); a single `--file` keeps its original output contract.

### Retrieval

```bash
"$PYTHON" "$EXTRACTOR" --workspace my-docs --search "full-text query" --limit 10
"$PYTHON" "$EXTRACTOR" --workspace my-docs --search 'publisher:publisher-name AND keywords'
"$PYTHON" "$EXTRACTOR" --search "keywords" --all-workspaces     # every workspace; results are labelled with their workspace
"$PYTHON" "$EXTRACTOR" --workspace my-docs --search "what is machine learning" --mode vector  # pure semantic retrieval (cross-language)
"$PYTHON" "$EXTRACTOR" --file doc.md --workspace my-docs --no-embed               # FTS-only ingest
```

**Hybrid retrieval (fused by default)**: three routes in parallel — the semantic vector route (Qwen3-Embedding, Chinese/English/cross-language), the FTS5 keyword route and the heading-anchor route (section titles indexed separately) — fused and ranked by RRF. Hits carry `score_source` (fused/fts/vector/heading; a section hit on several routes is labelled e.g. `fused+heading`); the meaning of `score` follows `score_kind`: `coverage` = 0..1 coverage ratio (query terms hit / total terms; `--mode fts` and the heading route), `similarity` = vector cosine, `rrf` = the fused ranking score (1/(60+rank) — **not comparable across queries and not a semantic relevance measure; ordering within one result set only**; judge confidence together with `score_kind` and `fts_detail.coverage_terms`); the raw BM25 value is in `fts_detail.bm25_raw`; multiple fragment hits in one section are aggregated into a single hit (`same_section_hits` counts them) whose representative carries the containing `heading` and `section_ref`. Ingestion embeds automatically by default (`--no-embed` disables it); the first knowledge-base use walks through installing the embedding engine and vector model once (y/N confirmation).

**Result count (`--limit`, default 20, 1..100)**: the **candidate → presented** cap for one search. The default 20 balances coverage against context budget (measured ≈25–27 KB of JSON, roughly 5K tokens for Chinese; an agent usually deep-reads only the top 1–3 hits). **Truncation must be perceptible**: the response carries `candidates_total` (**pre-truncation** fused candidate count) and `truncated_by_limit`, plus `hint`/`hint_i18n` with a remedy when truncation occurred. **Note that `len(hits) < limit` does not mean "not truncated"** (same-section aggregation merges hits) — the only judge is `truncated_by_limit`. And `truncated_by_limit: false` only means **no truncation within the current recall depth**: the per-route recall depth itself grows with limit (≈3×limit, capped at 100 per route) — measured on the same query, `--limit 20` yielded 19 candidates while `--limit 50` yielded 28, so **a larger limit recovers deeper sources**; content/enumeration questions therefore use an explicit `--limit 50` as stated above.

**Cases that must raise `--limit` explicitly** (especially with `truncated_by_limit: true`): ① **content-type** ("what does it say / what did it mention") — **never the default 20**, prefer `--limit 50` (the content-type contract demands covering every substantive point, and a cut candidate pool means invisible sources); ② **enumeration/inventory** ("list everything about X"); ③ **multi-entity comparison** ("compare what A/B/C say about X"); ④ **wide cross-workspace search** — with `--all-workspaces`, limit is the **shared quota across all workspaces** (with 7 workspaces that is under 3 hits per workspace on average, so it cannot prove any single workspace lacks relevant content). Fewer hits than limit is normal (same-section fragment aggregation) and does not indicate failure.

Query syntax: terms of ≥3 characters enter the trigram index (input is escaped automatically); **natural multi-term queries default to OR recall + coverage re-ranking** (single-term hits are returned too, two-term hits rank first); `AND`/`OR`/`NOT`/`NEAR(a b, 5)`/`prefix*` pass through as-is; `title:`/`author:`/`publisher:`/`publish_date:` restrict columns; **metadata range filters**: `publish_date>=2024`, `created_at<2025-01-01` etc. (both columns support `>=`/`<=`/`>`/`<`; TEXT ISO values compare lexicographically, which equals chronological order; **the comparison is by prefix** — `<=2019` does not include `2019-05-01`, so for date semantics write `<=2019-12-31`), and combined with topic terms they restrict the candidate entry set for all three routes (`column_filter: true` + `filtered_entries`; a filter with no topic terms returns a structured error); **Chinese terms shorter than 3 characters** (a trigram limitation) automatically fall back to a LIKE scan over the chunks table and are labelled `like-low-precision` in the results (that route reports `score=None` as a low-precision match; for high precision use terms of ≥3 characters or `--mode vector`).

**Structure-aware ingestion**: docx heading styles / EPUB h1-h6 / embedded PDF bookmarks are normalised into heading anchors, and plain text is heuristically scanned for "Chapter N / Clause N / Chapter N / numbered headings" (older entries: rebuild with `--reindex`); provenance is anchored to the **source structure** — PDF page, EPUB chapter, audio/video timestamp, text line number (the `source_loc` field) — internal full.md coordinates are never exposed.

### Reading (the agent deep-reads full.md; paths stay internal)

```bash
"$PYTHON" "$EXTRACTOR" --workspace my-docs --entry <entry-id>            # full.md in full
"$PYTHON" "$EXTRACTOR" --workspace my-docs --entry <entry-id> --chunk 3  # a specific chunk
"$PYTHON" "$EXTRACTOR" --workspace my-docs --section <section_ref>       # deep-read the whole section containing the hit (recommended)
```

When a hit falls before the first heading (preface/table-of-contents area), `section_ref` is the sentinel reference `entry_id#front`, which `--section` deep-reads just the same. **The read paths (`--entry`/`--chunk`/`--section`) also return `locator`** (same shape as retrieval hits: `{link, action, target_label, …}` — documents get `action=open` with page/chapter/line labels, media get `action=play` with mm:ss; a whole-entry `--entry` read gives an open link without `target_label` and never invents a location) — the source-links list of both retrieval and deep-read turns can be rendered with the same rules. Retrieval hits carry `section_ref` (an opaque reference) and `section_chars` — the agent passes the ref unchanged to `--section` to deep-read the whole section. Sparse-heading or **heading-less** long documents (scanned books / plain text / EPUBs whose headings failed to parse) get **synthetic sub-section anchors** every 20K characters across very long ranges (in the front matter area `卷首·续N`, otherwise `父标题·续N`; they do not participate in heading retrieval), so hit placement and `--section` deep-reading fall back to a 20K granularity. **The default read cap is 30,000 characters** (`--max-chars` applies uniformly to the entry/chunk/section read paths; 0 = unlimited); longer content is truncated with `truncated/total_chars/remaining_chars`; oversized sections are windowed around the hit position (the hit offset is embedded in `section_ref`) so the returned text centres on the matching terms. Media hits carry `start_ms/end_ms` timestamps (precise to the paragraph containing the matched term; `timestamp_precision: segment/window/chunk` records the precision source; pair with `--play --at`).

### Full management command set

| Operation | Command |
| ----- | ------------------------------------------------ |
| Explicit creation | `--workspace <name> --create` |
| List workspaces | `--workspace-list` |
| Delete workspace | `--workspace <name> --delete-workspace` (needs `--yes`; misusing `--delete-workspace <name>` is a usage error — it must accompany `--workspace`) |
| Rename | `--workspace <old> --rename <new>` |
| Stats | `--workspace <name> --stats` (entries/characters/chunks/source distribution/db size) |
| List entries | `--workspace <name> --list` |
| Delete entry | `--workspace <name> --remove <entry-id>` (needs `--yes`) |
| Integrity check | `--workspace <name> --verify` (chunk count / per-chunk consistency / coverage / FTS index comparison; **includes orphaned entry directories and leftover full.md.tmp** — `orphan_entry_dir`/`tmp_residual`, so failed-embedding rollback leftovers are detectable) |
| Rebuild index | `--workspace <name> --reindex` (rebuilds FTS + heading anchors + sub-sections; **no vectors**; `consistent` includes per-chunk chunk↔full.md verification; refreshes ANALYZE statistics afterwards) |
| Backfill vectors | `--workspace <name> --embed` (build vectors for entries with none; needs the embedding chain) |
| Space reclamation | `--workspace <name> --vacuum` |
| Seeked playback | `--workspace <name> --play <entry-id> --at mm:ss [--duration seconds]` (built-in ffplay by default; override with `--player vlc\|potplayer\|mpv\|system`) |
| Protocol registration | `--register-protocol` / `--unregister-protocol` (the myagentrag:// locator-link handler: `goto` is the **unified entry** — seeked playback for media entries, open-original-file for documents; the registration binds only the **self-locating launcher inside the managed directory** `<home>/protocol/play.py` and never hardcodes the source/skill directory; the skill directory is resolved at runtime via the `MYAGENTRAG_SKILL_DIR` environment variable → `skill.json` next to the launcher (recorded at registration); registered automatically when ffmpeg is installed) |
| Protocol entry | `--goto-uri "myagentrag://goto?ws=<workspace>&entry=<id>[&at=<seconds>]"` (invoked by the OS when a link is clicked; **unified entry**: seeked playback for media (`at` defaults to the beginning), open-original-file for documents; `--play-uri` is the **compatibility alias** for v0.1.1 links (`myagentrag://play`, `at` required). Parameters are whitelist-validated per action against injection — `entry` must be 16 hex digits, unknown parameters are rejected, and **the open path is resolved only from the workspace DB; the URI accepts no path parameter**) |
| Component repair | `--repair-deps` (idempotently restores the managed ffmpeg components `ffmpeg`/`ffplay`, fetching only what is missing and downloading nothing when complete; use it when an upgraded older install lacks ffplay or `--play` reports no player) |

Destructive operations by default only print `confirm_required: true` and the paths to be deleted — the agent must confirm with the user and re-run with `--yes`. Machine-to-machine migration = copy the workspace directory (self-contained); no command needed.

### Seeked playback (audio/video)

```bash
"$PYTHON" "$EXTRACTOR" --workspace my-docs --play <entry-id> --at 12:33 [--duration 60]
"$PYTHON" "$EXTRACTOR" --goto-uri "myagentrag://goto?ws=<workspace>&entry=<id>&at=<seconds>"   # protocol entry (invoked on link click; documents may omit at)
"$PYTHON" "$EXTRACTOR" --play-uri "myagentrag://play?ws=<workspace>&entry=<id>&at=<seconds>"   # compatibility alias (v0.1.1 links)
```

- **Built-in ffplay seeked playback by default** (shipped with the ffmpeg bundle; `-ss` seeks precisely, `-autoexit` quits when done, `-t` limits the duration): `player: "ffplay"` with `degraded: false` — there is no "plays from the start" degraded state;
- `--player vlc|potplayer|mpv|system` overrides explicitly (`system` = the OS default association, which can only play from the start and marks the result `degraded: true`); without a bundled ffplay on macOS the chain is ffplay → VLC/IINA/mpv;
- The player process is started through a shell handoff (Windows `cmd /c start` / an independent session elsewhere) so it survives the caller's exit;
- **When no usable player is found, structured JSON is returned** (`candidates`/`hint`/`play_cmd: null`) for the agent to handle — the skill never pops up UI; `locator.fallback_play_cmd` follows the same rule and becomes `null` (no unexecutable command); fix with `--repair-deps`.
- `myagentrag://goto` unified entry (link clicks): media entries → seeked playback (`at` defaults to the beginning); document entries → the OS default association opens the original file (the path is resolved only from the workspace DB; the URI accepts no path parameter); `myagentrag://play` is the compatibility alias for v0.1.1 links. A URL source / deleted source file / entry ingested without a copy → structured error (never a silent success).

### Environment requirements (guaranteed by the dedicated runtime)

The workspace relies on SQLite ≥3.34 (FTS5 trigram). Since v1.4 the skill always runs inside the dedicated runtime (standalone CPython 3.12 with SQLite 3.5x), so trigram is always available regardless of the user's system Python; a missing runtime is supplied by the bootstrap install flow — **a clear error is preferred over any low-precision degraded retrieval**.

### Directory layout (a workspace is self-contained; copying the directory migrates it)

```
~/.myagentrag/workspaces/<workspace>/
├── workspace.db            # SQLite (WAL): entries / chunks / entries_fts
├── source/<entry-id>/      # original source copies (audio/video, web snapshots, raw subtitle files)
└── entries/<entry-id>/
    ├── meta.json           # metadata (title/source/author/publisher info/chunk count/copy file name)
    ├── full.md             # the full extracted text (hits are deep-read by offset)
    └── transcript.json     # per-segment timestamps for audio/video (including each segment's character range in full.md)
```

The workspace root can be overridden with `MYAGENTRAG_WORKSPACES_DIR` (default `~/.myagentrag/workspaces/`; under WSL avoid /mnt/c to prevent performance and file-locking problems).
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

## Working with Agents

```
user request → extract.py extract & ingest → agent retrieval/deep reading → reply to the user
```

- On extraction or retrieval failure, read the `error` field first — do not retry blindly;
- **Presenting retrieval/deep-read results must follow the section ② result presentation contract** (uniform hit list + closing source-links list) — a hard output contract that does not vary with client or phrasing;
- Watch the context budget when deep-reading long content: read in stages with `--section` / `--chunk` / `--max-chars`;
- Agents with native web tooling may prefer their own web-reading ability for web pages and YouTube; this script is especially suited to Bilibili subtitles, local documents and local audio/video.

## Troubleshooting

| Symptom | Fix |
| ------------------------------ | ---------------------------------------------------------------------------------------- |
| `python` not found | Set `MYAGENTRAG_PYTHON` to the full path of the target interpreter |
| YouTube yt-dlp reports a JS runtime error | Install Node.js and make sure `node` is on PATH; the script passes `--js-runtimes node` |
| Audio/video says whisper.cpp is unavailable | The runtime check lists the missing components and sizes; confirm after agreeing, or let the agent re-run with `--download-deps` |
| YouTube asks for cookies | Export Netscape-format cookies with a browser extension as prompted and save them to `~/.myagentrag/cookies/youtube-cookies.txt`, then retry |
| PDF extraction is empty | A scan without a text layer — expected; this tool does not do OCR |
| Bilibili has no subtitles | The video has no CC subtitles; the API returns `success:false` — expected |
| `--mode vector` always returns 0 hits | First check whether the workspace was ingested with `--no-embed` (a `vectors` value of 0 in `--list` means it was); backfill with `--embed` or re-ingest without disabling embeddings |
| `--search` reports "workspace not found" | The workspace name is misspelled; list all names with `--workspace-list` (an `@name` prefix is stripped automatically); strictly distinct from "no match inside the workspace" (success:true + empty result) |
| `--play` reports no player | Run `--repair-deps` to restore the bundled ffplay (idempotent; zero downloads when complete); or point `MYAGENTRAG_FFPLAY` at an existing ffplay; or degrade with `--player system` |
| Playback chain lacks ffplay after an upgrade (0.1.0→0.1.2) | An older install's managed `bin/` may contain `ffmpeg` but no `ffplay`: run `--repair-deps` once to restore it (existing components are never overwritten) |
| Link shape changed after upgrading from v0.1.1 | Document hits gained the unified protocol link `link` (`myagentrag://goto`) — an old launcher forwarding `--play-uri` is still accepted by the new version (both the `play` alias and the `open` transition field exist), so **it works without re-registering**; to refresh the registration description and launcher (description play→goto), re-run `--register-protocol` once (the bound path is unchanged; zero migration cost) |
| A myagentrag:// link does not open | The protocol handler needs registration: `--register-protocol` (registered automatically when ffmpeg is installed; it writes only the skill's own registry namespace and never touches the system default players or file associations); remove it with `--unregister-protocol` |
| Document links are blocked by the client | Since v0.1.2 document hits use `locator.link` (`myagentrag://goto`, opening the original file through the registered protocol handler) and no longer depend on the client's `file://` policy; only the transition field `open` still carries a file:/// form — prefer `link` when rendering |
| Links break after moving the skill directory | The registration binds only the managed-directory launcher (unaffected); the skill directory is resolved at runtime — set `MYAGENTRAG_SKILL_DIR` to the new skill root, or re-run `--register-protocol` to refresh the record (the launcher prints the same guidance on stderr when it cannot find the directory) |
