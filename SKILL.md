---
name: myagentrag
description: Local knowledge-base (RAG) build & retrieval tool: ingest YouTube/Bilibili subtitles, web articles, local files (PDF/Word/Excel/PowerPoint/EPUB/text) and audio/video transcripts into local knowledge-base workspaces; hybrid keyword + semantic + heading-anchor retrieval (FTS5 + Qwen3 embeddings); structure-aware deep reading and timestamped playback. Extraction and indexing run fully locally with no LLM calls; reading and answering from retrieval results is done by the calling agent. Also matches Chinese-language requests such as 知识库 / 入库 / 检索 / 精读 / 定位回放 / 字幕 / 知识库检索.
compatibility: Windows / macOS / Linux / WSL; bootstrap layer requires any Python ≥3.8 (standard library only); first use auto-installs the dedicated runtime (SQLite ≥3.34 included); audio/video transcription additionally needs ffmpeg, whisper.cpp and a GGML model — details in references/setup-and-deps.md
---

# MyAgentRAG — Local Knowledge-Base Build & Retrieval Tool

> ⚠️ **READ-FIRST — self-diagnosis.** This file is the contract. **The last line of this file is `<!-- END-OF-SKILL -->`. If your copy does not END with that line, you only got a fragment** — Read this whole file before any retrieval or answering; never infer the rules from a prefix. (If the doc was truncated, `--contract` prints the core block from the CLI.)
>
> **Core, in case you read nothing else**: ① `--search` finds locations → ② **`--section` deep-reads** the hits you will present → ③ answer, **ending with the `Source links` list**. Never answer from `snippet` previews; never show internal fields (`score/entry_id/section_ref/…`); content & enumeration questions use `--limit 50`.
>
> Detailed rules live in `references/*.md` next to this file; **this file alone is sufficient for the standard loop**. The trigger table below says which reference to load, and when.

<!-- CONTRACT-CORE -->
**NON-NEGOTIABLE CORE — applies to every reply. Full contract: `references/presentation-contract.md`.**

1. **Three-step loop (step 2 mandatory)**: ① `--search` finds locations → ② `--section` deep-reads the content of the hits you will present → ③ answer per the presentation contract. **Never** answer a content question from `snippet` previews — `snippet` is an index preview, not the content (`…` marks truncation; whatever follows the cut must be deep-read). Budget deep reading with `--max-chars`.
2. **Answer structure — one question, one reply carrying everything**: retrieval-type = one-line summary (`N hits in "workspace"`) → numbered hit list → **Source links** list; deep-read-type (a question about one chapter/entry) = content or conclusion → **Source links** list. Never deliver half a result and wait for the user to ask again.
3. **Hit list** (uniform format, relevance order): `N. **title** — provenance label` with an indented `> snippet` quote (keep 『』 highlights, 1–2 lines, never rewrite/splice/invent). Provenance labels in natural language: `page 12` / `chapter 3 · title` / `line 88` / `12:33–12:47`.
4. **Source links (mandatory, exact templates)** — render `locator.link` in both cases:
   - document hit (`action=open`): `- [Open original file](link) (target_label)`; with `open_scope=file_only` never claim the link jumps to a page/chapter;
   - media hit (`action=play`): `- [▶ Play from mm:ss](link)`; when the client does not render non-http(s) URIs, add the copy-pasteable `fallback_play_cmd`; when `fallback_play_cmd` is `null` give no command, only the fix hint (`--repair-deps` / `MYAGENTRAG_FFPLAY`);
   - hit without a `locator` (web source, deleted file, `--no-keep-source`): plain `title + source_ref` — **never fabricate a clickable link**;
   - one line per entry_id, grouped per workspace for cross-workspace searches; body citations, hit list and links list must correspond one-to-one.
5. **Never show internal fields to the user**: `score/scores/rrf/chunk_no/section_ref/entry_id/offset/vector_backend` (parameters inside `myagentrag://` links are carried by the link itself).
6. **Limits**: content-type and enumeration questions use an explicit `--limit 50` (**never the default 20**); `truncated_by_limit: true` (or a `hint`) means candidates were never returned — re-run larger before concluding; the hit list expands at most 10 items by default (then append "N more not shown").
7. **Conclusion completeness (anti-compression)**: the conclusion/content section is the only section carrying substance — no length limit, and the "1–2 lines / ≤10 items" limits constrain the hit list only. Cover every substantive point of the deep-read text with ① time (era/date/range) ② quantities ③ proper nouns ④ causes and conditions ⑤ exceptions and qualifications individually traceable; **never** replace countable facts with "several/a number of/etc.", never merge independent points into one sentence, and never substitute a broader term ("Chernobyl" → "a nuclear accident").
8. **Self-check before sending** (rewrite if any item fails): ① every substantive point of the deep-read text present? ② eras/numbers/proper nouns verbatim? ③ no merged points? ④ conditions/exceptions kept? ⑤ hit list and conclusion differ in detail level? ⑥ every statement locatable in the **deep-read text**? ⑦ every cited source deep-read via `--section`? ⑧ no `…` truncation cited as content? **⑨ does the reply end with the `Source links` list (not buried mid-answer, not omitted because the answer is long)? ⑩ count check: link lines == distinct cited entries? ⑪ is every link line rendered per template (4) above?**
9. **Errors and confirmations**: read the `error` field, never retry blindly; `confirm_required` → ask the user, then re-run with `--yes`; missing components → show the list, get consent, re-run with `--download-deps`; `rc=0` success / `rc=1` task failure / `rc=2` argument misuse; a `--workspace` ingest failure is a task failure (check top-level `success`, not merely whether `content` exists).
<!-- /CONTRACT-CORE -->

> **Invocation.** Every command runs this skill's entry point: `PYTHON="${MYAGENTRAG_PYTHON:-python}"; EXTRACTOR="<skill-dir>/scripts/extract.py"`. The scripts never call an LLM; extraction and indexing are fully local; components download only into `~/.myagentrag` and only after the user confirms.

## Which reference to load, and when

Each reference sits in `references/` next to this file and starts with a `READ WHEN` line. Loading one costs a single Read call; the filenames below are relative to the skill directory.

| Situation | Load |
| --- | --- |
| Rendering edge cases: merged entries, >10 hits, hits without locator, cross-workspace grouping, exact link templates, detail levels, worked examples | `references/presentation-contract.md` |
| Tuning retrieval: `--limit`, query syntax, metadata/range filters, score semantics, recall depth, read paths, seeked playback, unusual user wording (phrasebook) | `references/retrieval.md` |
| Ingesting files/URLs, batch runs, metadata, dedup/`--replace`, management commands, workspace layout & migration, output formats, exit codes | `references/ingest-and-workspace.md` |
| First use, missing components, transcription or environment problems, GPU questions, cookies | `references/setup-and-deps.md` |
| Any error message | `references/troubleshooting.md` |

**Rule of thumb**: the core above covers the plain default path. If the situation is *not* the plain default — or you are about to render something you have not rendered before — load the governing reference first; skipping it is what produces misrendered answers or an omitted links list.

## Minimal command set

```bash
PYTHON="${MYAGENTRAG_PYTHON:-python}"
EXTRACTOR="<skill-dir>/scripts/extract.py"

"$PYTHON" "$EXTRACTOR" --file "document.pdf" --workspace my-docs             # extract & ingest (batch: --file a --file b, or --dir <folder>)
"$PYTHON" "$EXTRACTOR" --workspace my-docs --search "terms" --limit 50       # retrieval (fused by default; content/enum questions: --limit 50)
"$PYTHON" "$EXTRACTOR" --workspace my-docs --section "<section_ref>"         # deep-read the hit's whole section (mandatory before answering)
"$PYTHON" "$EXTRACTOR" --workspace my-docs --entry <entry-id> [--chunk N]    # read a whole entry / one chunk
"$PYTHON" "$EXTRACTOR" --workspace my-docs --play <entry-id> --at 12:33      # seeked audio/video playback
"$PYTHON" "$EXTRACTOR" --goto-uri "myagentrag://goto?ws=<lib>&entry=<id>[&at=<s>]"   # protocol entry (link clicks; documents may omit at)
"$PYTHON" "$EXTRACTOR" --contract                                            # print the core block of this file (recovery if the doc was truncated)
```

Cross-workspace search adds `--all-workspaces` (limit is then the shared quota across workspaces). Media hits carry `start_ms/end_ms`; `section_ref` is opaque — pass it back unchanged.

<!-- END-OF-SKILL -->
