FULL presentation contract — three-section structure, hit-list format, source-links list, retrieval depth (deep-read first), anti-compression rules, self-checks ①–⑪, worked examples.
READ WHEN: BEFORE rendering any answer whose situation is not the plain default (merged entries, >10 hits, hits without locator, cross-workspace grouping, edge labels), or when you need exact templates/rules.
NOT READING COSTS: edge-case misrendering — a contract violation. The skeleton in SKILL.md covers the default path only.

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

| Section                      | Data source                                                | Notes                                                                                                                 |
| ---------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Hit list                     | `snippet`                                                  | A **preview window** returned by retrieval (possibly truncated with `…`); it only tells the user "which item this is" |
| Conclusion / content section | **The original text returned by `--section` deep reading** | **Must** come from deep reading; `snippet` must **not** serve as the content source                                   |

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

| Section                      | Role                                              | Length                                                       |
| ---------------------------- | ------------------------------------------------- | ------------------------------------------------------------ |
| Retrieval summary            | Report counts and scope only                      | One line                                                     |
| Hit list                     | **An index, not content**                         | ≤10 items by default; ≤2 lines quoted per item               |
| Conclusion / content section | **The only section carrying substantive content** | **No limit** — driven by the question and the volume of hits |

⚠️ Every "1–2 lines" and "at most N items" in this contract **constrains the hit list only and does not spill over into the conclusion section**.

- **Information completeness (hard requirement)**: the conclusion/content section must cover every substantive point in the **original text obtained by deep reading** (the data source is the deep-read text, not `snippet` previews), with the following five classes of information individually traceable — ① time (era/date/range); ② quantities (counts/ratios/ordinals); ③ proper nouns (people/places/organizations/events); ④ causes and conditions ("if…then…" / "because…therefore…"); ⑤ exceptions and qualifications ("not all…", "not necessarily every…"). **Never** replace countable facts with vague quantifiers ("etc.", "several", "a number of", "a series of", "some"); **never** merge two or more independent points into one sentence.

- **Quote first (applies to the whole answer)**: prefer direct quotation of the original (keep 『』 and the original wording); a paraphrase must retain the five classes above, must not merge distinct points and must not drop qualifications; **never** replace a specific term with a broader one ("Chernobyl" → "a nuclear accident", "Reglando River" → "a border river"). "Snippets must not be rewritten/spliced/invented" also constrains paraphrasing in the conclusion section, not just list snippets.

- **Detail levels** (choose by user intent; when unsure, err on the detailed side — detail can be trimmed, brevity cannot be filled in):
  
  | User intent      | Typical phrasing                                                                | Conclusion / content section                                                                                                                                              |
  | ---------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | Locating         | "is there anything about X" / "where is X mentioned"                            | Brief: one sentence per point + provenance                                                                                                                                |
  | **Content-type** | **"what does it say" / "what did it mention" / "what is the specific content"** | **Expand**: list item by item following the original structure, keeping details and quotations; **fetch candidates with an explicit `--limit 50` (never the default 20)** |
  | Enumerating      | "list everything about…" / "find all…"                                          | Expand without omission (with `--limit 50`)                                                                                                                               |
  | Comparing        | "what A and B say about X"                                                      | Expand per source, point by point                                                                                                                                         |

- **Pre-output self-check** (rewrite if any item fails): ① does every substantive point of the deep-read text appear in the conclusion? ② are eras/numbers/proper nouns preserved verbatim (not replaced by "several"/"a number of")? ③ is any place merging multiple points into one sentence? split it. ④ do paraphrases keep the original conditions and exceptions? ⑤ are the hit list and the conclusion clearly different in detail level? (if both are equally brief, the conclusion was compressed)

- **Retrieval-depth self-check** (on par with the previous item; rewrite if any fails): ⑥ can every substantive statement in the conclusion be located in the **text returned by deep reading**? (if it can only be located in a `snippet`, deep reading did not happen) ⑦ has **every** source cited in the conclusion been deep-read via `--section`? ⑧ is any `snippet` `…` truncation being cited as if it were complete content?

- **Source-links self-check** (on par with the two items above; **a reply that fails it is incomplete — rewrite**): ⑨ does the reply **end with** the `**Source links**` list (not buried mid-answer, not omitted because the answer is long)? ⑩ does the list cover **every** source cited in the body and the hit list — **count check: number of link lines == number of distinct cited `entry_id`s** (no source missing, no extra)? ⑪ is every line rendered per the contract (documents → `[Open original file](link)` + `target_label`; media → `[▶ Play from mm:ss](link)`; hits without a `locator` → plain `title + source_ref`, never a fabricated link; `fallback_play_cmd: null` → no command, one fix hint)?

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

**Whenever retrieval or deep-read results are presented to the user, the answer must end with a "source links" list** (gated by self-check items ⑨–⑪ above: a reply whose links are missing, incomplete or mis-rendered is incomplete) itemising the sources cited (deep-read ones always; otherwise the high-scoring hits; one line per `entry_id`, grouped per workspace in cross-workspace searches):

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

