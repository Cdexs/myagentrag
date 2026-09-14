Ingest & workspace operations — content sources, ingestion rules, batch contract, usage/output/exit codes, management commands, layout & migration.
READ WHEN: ingesting files or URLs, batch runs, metadata, dedup, workspace management/migration.
NOT READING COSTS: nothing contract-wise — precision and convenience only.

## Supported Content Sources (what can be ingested)

| Type           | Formats                                                 | Notes                                                                                                                                                                                                                                                                                                |
| -------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **YouTube**    | Video URL                                               | Manual/auto captions and metadata via yt-dlp                                                                                                                                                                                                                                                         |
| **Bilibili**   | Video URL                                               | CC subtitles and video info via the login-free API                                                                                                                                                                                                                                                   |
| **Web page**   | HTTP/HTTPS link                                         | Body text via Jina Reader (`r.jina.ai`) — **the URL is sent to a third-party service** (avoid privacy-sensitive links); intranet/loopback addresses are refused; if Jina is unavailable or rate-limited the `--url` feature is entirely unavailable (structured error including HTTP status and URL) |
| **Text files** | `.txt`, `.md`, `.markdown`, `.rst`, `.csv`              | Read directly                                                                                                                                                                                                                                                                                        |
| **PDF**        | `.pdf`                                                  | pdfplumber or PyMuPDF                                                                                                                                                                                                                                                                                |
| **Word**       | `.docx`, `.doc`                                         | python-docx; `.doc` additionally needs pandoc                                                                                                                                                                                                                                                        |
| **EPUB**       | `.epub`                                                 | ebooklib                                                                                                                                                                                                                                                                                             |
| **Excel**      | `.xlsx`, `.xlsm`                                        | openpyxl (one segment per worksheet, rows joined with " \| ")                                                                                                                                                                                                                                        |
| **PowerPoint** | `.pptx`                                                 | python-pptx (one segment per slide, structured: `## Slide N` + `#` title + `###` subtitle/body/table/speaker notes)                                                                                                                                                                                  |
| **Audio**      | `.mp3`, `.wav`, `.aac`, `.m4a`, `.flac`, `.ogg`, `.wma` | Transcode to PCM with ffmpeg, then transcribe with whisper.cpp                                                                                                                                                                                                                                       |
| **Video**      | `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv`, `.flv`, `.webm` | Embedded subtitles first; if none, extract audio and transcribe                                                                                                                                                                                                                                      |

**① "Store this book/document into the knowledge base" (extract and ingest)**

```bash
"$PYTHON" "$EXTRACTOR" --file "book.epub" --workspace my-books
```

The ingest result contains entry_id/chunk_count/vectors (number of vector windows). The first knowledge-base use walks you through installing the embedding chain (llama.cpp engine ~34MB + Qwen3 model ~610MB + sqlite-vec ~0.3MB): interactive terminals prompt y/N directly; an agent first shows the list to the user for consent, then re-runs with `--download-deps`. A missing workspace is created implicitly; re-ingesting identical content is idempotent and only updates. **Re-ingesting the same source** (same source_ref) with changed content creates a new entry; the response lists the old entry id under `workspace.supersedes` with a stderr warning — after confirming, clean it up with `--remove <old-id>`, or pass `--replace` at ingest time to replace automatically.

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

### Full management command set

| Operation             | Command                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Explicit creation     | `--workspace <name> --create`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| List workspaces       | `--workspace-list`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Delete workspace      | `--workspace <name> --delete-workspace` (needs `--yes`; misusing `--delete-workspace <name>` is a usage error — it must accompany `--workspace`)                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Rename                | `--workspace <old> --rename <new>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Stats                 | `--workspace <name> --stats` (entries/characters/chunks/source distribution/db size)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| List entries          | `--workspace <name> --list`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Delete entry          | `--workspace <name> --remove <entry-id>` (needs `--yes`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Integrity check       | `--workspace <name> --verify` (chunk count / per-chunk consistency / coverage / FTS index comparison; **includes orphaned entry directories and leftover full.md.tmp** — `orphan_entry_dir`/`tmp_residual`, so failed-embedding rollback leftovers are detectable)                                                                                                                                                                                                                                                                                                                  |
| Rebuild index         | `--workspace <name> --reindex` (rebuilds FTS + heading anchors + sub-sections; **no vectors**; `consistent` includes per-chunk chunk↔full.md verification; refreshes ANALYZE statistics afterwards)                                                                                                                                                                                                                                                                                                                                                                                 |
| Backfill vectors      | `--workspace <name> --embed` (build vectors for entries with none; needs the embedding chain)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Space reclamation     | `--workspace <name> --vacuum`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Seeked playback       | `--workspace <name> --play <entry-id> --at mm:ss [--duration seconds]` (built-in ffplay by default; override with `--player vlc\|potplayer\|mpv\|system`)                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Protocol registration | `--register-protocol` / `--unregister-protocol` (the myagentrag:// locator-link handler: `goto` is the **unified entry** — seeked playback for media entries, open-original-file for documents; the registration binds only the **self-locating launcher inside the managed directory** `<home>/protocol/play.py` and never hardcodes the source/skill directory; the skill directory is resolved at runtime via the `MYAGENTRAG_SKILL_DIR` environment variable → `skill.json` next to the launcher (recorded at registration); registered automatically when ffmpeg is installed) |
| Protocol entry        | `--goto-uri "myagentrag://goto?ws=<workspace>&entry=<id>[&at=<seconds>]"` (invoked by the OS when a link is clicked; **unified entry**: seeked playback for media (`at` defaults to the beginning), open-original-file for documents; `--play-uri` is the **compatibility alias** for v0.1.1 links (`myagentrag://play`, `at` required). Parameters are whitelist-validated per action against injection — `entry` must be 16 hex digits, unknown parameters are rejected, and **the open path is resolved only from the workspace DB; the URI accepts no path parameter**)         |
| Component repair      | `--repair-deps` (idempotently restores the managed ffmpeg components `ffmpeg`/`ffplay`, fetching only what is missing and downloading nothing when complete; use it when an upgraded older install lacks ffplay or `--play` reports no player)                                                                                                                                                                                                                                                                                                                                      |

Destructive operations by default only print `confirm_required: true` and the paths to be deleted — the agent must confirm with the user and re-run with `--yes`. Machine-to-machine migration = copy the workspace directory (self-contained); no command needed.

