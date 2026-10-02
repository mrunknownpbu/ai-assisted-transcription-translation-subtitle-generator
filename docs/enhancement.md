# Subtitle AI: Documentation Enhancement (Round 3)

*Draft, 2026-09-30. Successor to `ENHANCEMENT_DRAFT.md` (Round 2, fully
implemented 2026-09-28). Scope is documentation and maintainability only --
no pipeline, translation, or ASR behaviour changes are proposed here.*

Every finding below was checked against the working tree on myphy-ai, not
inferred from reading prose. Where a count is quoted it was measured with
`wc`/`grep`/`git` at the time of writing. Where something is a proposal
rather than an observation, it says so.

## What was surveyed

`README.md` (539 lines, 4,082 words), `CLAUDE.md` (1,689 lines, 14,084
words, 102 KB, 44 `##` sections), all ten files in `docs/`, `.env.example`,
`scripts/` (19 files), `subtitle_ai/` (38 modules), and `git log`/`git
status` at commit `e37c506`.

**What is accurate and needs no work.** All 13 scripts cited across both
files exist. `asr.PIPELINE_VERSION` is `2.0.7`, matching the log's last
bump. `cast_enrichment.MIN_NAME_LINES = 5` and `MIN_NAME_EPISODES = 2`
match the evidence-bar prose. `refresh_days()` defaults to `0.5`, matching
both the README knobs table and the final CLAUDE.md entry. The README
mermaid diagram's node names match real module names.

## Finding 1: four documents answer "where are we"

This is the root cause of most of the individual defects below, and the
reason they recur.

| Document | Role it claims | Actual overlap |
|---|---|---|
| `CLAUDE.md` (1,689 ln) | "deploy/ops/precedent knowledge that isn't written down anywhere else" | 30 of its 44 sections are dated changelog entries |
| `docs/handover.md` (211 ln) | Handover Log: Purpose / How to log / Open issues / Change log | its change log duplicates CLAUDE.md entries; its open-issues list is unique |
| `docs/agents.md` (124 ln) | Agent Guide: picking up a session, working principles, validation reference | duplicates CLAUDE.md's Tests section and guardrails |
| `docs/architecture.md` (115 ln) | Overview, runtime components, invariants, toggles, operational model | duplicates README's Architecture section |

The four already contradict each other, and the contradictions are the
kind a reader cannot detect from inside any one file:

- `docs/handover.md` marks four items "Uncommitted". All four are
  committed: the glossary suffix matcher (`0eaac1e`), cast evidence
  refresh (`16a4f61`), the 12-hour refresh default (`860c4fc`), the
  authenticated `fetch()` SSE client (`344b56f`), and the analyzer
  scripts (`e37c506`). A session reading handover.md would re-do or
  re-verify work that is already on master.
- The same three fixes appear both as "OPEN ... RESOLVED" items in
  handover.md and as full sections in CLAUDE.md, with no cross-reference
  saying which is authoritative.
- `docs/cast-enrichment-staleness-handoff.md` and
  `docs/glossary-suffix-protection-handoff.md` both open with
  `## Resolved (2026-09-30)`. They are complete and superseded by the
  CLAUDE.md sections that describe the same fixes.

## Finding 2: four factual errors

| # | Location | Error | Evidence |
|---|---|---|---|
| E1 | `CLAUDE.md` Tests section | "Baseline as of 2026-09-28: 1065 passing" | Later entries in the same file report 1185 → 1201 → 1211 → 1215 → 1217 passed / 48 subtests. Tree has 61 test files, 1,252 `def test_`. |
| E2 | `README.md` knobs table | `SUBTITLE_AI_COMPUTE_TYPE` absent | Present at `.env.example:130` and twice in CLAUDE.md's host checklist. It is the one knob a new host must set correctly (`float16` vs `int8` by GPU generation). |
| E3 | `.env.example` | `SUBTITLE_AI_CODE_SWITCH_DETECTION` absent | Documented in README's table; README states all knobs are forwarded from `.env`. |
| E4 | `CLAUDE.md` Tests section | "89 frontend tests" | 12 frontend test files present; the case count was not re-measured and the figure predates at least two later frontend changes. |

E1 is notable because the line carrying it also instructs the reader:
*"Update this line rather than leaving it to drift the next time the count
moves."* It has drifted through roughly fifteen recorded test-count changes
since. An instruction to a human to maintain a number by hand has already
been shown, in this file, not to work.

## Finding 3: superseded statements are not marked in place

The `"If You Love" (2023)` entry (2026-09-30) states that
`worker._maybe_refresh_cast()` retries only once staleness clears, "which is
a flat 30-day interval (`refresh_days()`)", and closes with an
`**Open question, not yet acted on:**` asking whether it should re-check
when episode count grows.

Both were answered within the same file, two sections later ("Cast
enrichment retries when subtitle evidence grows", and the 12-hour default),
and `cast_enrichment.py:422` now reads `default 0.5 / 12 hours`. Nothing at
the point of first mention says so. Section order is also not strictly
chronological -- a 2026-09-28 Sonarr/Radarr section sits at line 530, ahead
of other 2026-09-28 entries -- so "later in the file" is not a reliable
proxy for "more recent".

## Finding 4: open items are not collectable

The genuinely open work is spread across entries; assembling it currently
means reading all 14,084 words. Consolidated here as the proposed content
for item D4:

- Gap coverage plateaued at 13.5% (102 of 756 human cues, Hammer Session!
  S01E01).
- Source-side `mid_sentence_end_rate = 0.873` unexplained; suspected to be
  an artifact of an English-oriented sentence-ending regex applied to
  Japanese punctuation, never checked.
- Thai (`th`) auto-glossary mining deferred -- character n-grams do not
  transfer; needs real word segmentation (e.g. `pythainlp`) or a new
  heuristic.
- Speaker-turn detection (`turns.py`) implemented, measured, shipped OFF;
  neither detector cleared the bar and ground truth (12 turn points/episode)
  cannot validate precision.
- ASR `initial_prompt` style experiment implemented, measured, shipped OFF;
  interjection recall was unchanged at 78.7%, which was the entire motive.
- Short Spanish clauses in "If You Love" S01E01 stay below `langid.py`'s
  3-clause corroboration threshold. Accepted and disclosed; `min_run=3` was
  re-measured and must not be lowered without new evidence.

Not on this list, contrary to `docs/handover.md`: browser API-key
provisioning. It was resolved and committed in `344b56f`
(`frontend/src/api/useEventStream.ts` uses authenticated `fetch()` with a
`getReader()` stream rather than headerless `EventSource`).

## Finding 5: README coverage gaps

- Six shipped modules have no node in the architecture diagram:
  `langid.py`, `name_correction.py`, `upload_cleanup.py`, `alerting.py`,
  `normalize.py`, `reference_aligner.py`. The first two are the
  implementation behind knobs the README's own table documents
  (`SUBTITLE_AI_CODE_SWITCH_DETECTION`, `SUBTITLE_AI_NAME_CORRECTION`), so
  a reader can find the switch but not the thing it switches.
- `docs/` holds ten files; README names two, and describes those two as
  "completed, frozen planning rounds". The other eight -- including
  `architecture.md`, `product-requirements.md` and
  `turkish-language-support.md` -- are undiscoverable from the README, and
  their live-vs-frozen status is unstated.
- The Status section quotes ~7 minutes per episode and then says to "run
  the tests rather than trusting a hardcoded number here". The tests do not
  produce that number; `benchmark-results/` and `scripts/bench_translate.py`
  do.

---

## Proposed work

| # | Item | Effort | Rationale |
|---|---|---|---|
| D1 | Correct E1-E4 | ~20 min | Factual errors; no judgment required |
| D2 | Add documentation-consistency tests | ~1-2 h | Stops D1 and D3 recurring; without it D5 decays |
| D3 | Clear stale `Uncommitted` markers, delete the two resolved handoff docs | ~15 min | Removes contradictions against master |
| D4 | Add "Current open items" to the top of CLAUDE.md | ~30 min | Content already assembled in Finding 4 |
| D5 | One owner per question: split CLAUDE.md, retire the duplication | ~2-3 h | Addresses Finding 1; do last and as its own commit |
| D6 | README diagram nodes, `docs/` index, throughput wording | ~30 min | Finding 5 |

Suggested order: **D1 → D2 → D3 → D4 → D6 → D5.** D2 goes early so the
split in D5 lands against a suite that already enforces consistency. D5 is
a large file move and should be reviewable on its own.

### D1 -- correct the four factual errors

`CLAUDE.md`: update the test baseline, or better, replace the hand-maintained
count with the command that produces it. README already applies exactly this
reasoning to its throughput figure ("it drifts with every change"); the same
argument applies to a test count, with fifteen drift events as evidence.

`README.md`: add a `SUBTITLE_AI_COMPUTE_TYPE` row to the knobs table
(default `float16`; `int8` for Pascal-era cards -- `asr.AsrConfig.compute_type`'s
docstring already holds the reasoning and can be cited rather than restated).

`.env.example`: add a commented `SUBTITLE_AI_CODE_SWITCH_DETECTION` entry
alongside the other feature toggles.

Re-measure the frontend test count, or replace it with the command.

### D2 -- documentation-consistency tests

The highest-leverage item, and the one that fits this repo's existing
discipline: 1,252 backend tests, and a standing rule that a change is not
done until it is measured. Documentation is currently the only part of the
system with no such gate, which is why it is the part that has drifted.

Three repeating failure shapes have been observed; two are mechanically
checkable:

```python
# tests/test_docs_consistency.py

def test_env_vars_documented_in_both_places(self):
    """Every SUBTITLE_AI_* in README's knobs table appears in .env.example,
    and vice versa. Would have caught E2 and E3 at commit time."""

def test_documented_defaults_match_code(self):
    """README's stated default equals the code's default for the knobs whose
    defaults are read at runtime: CAST_REFRESH_DAYS, VRAM_MARGIN_GB,
    NLLB_BATCH_SIZE, NLLB_NUM_BEAMS, MODEL_IDLE_SECONDS,
    FAILED_WORK_RETENTION_HOURS, SAMPLE_MODEL, NLLB_BACKEND."""
```

The third shape -- stale "Uncommitted" status markers -- is better solved by
removing the convention (D3/D5) than by testing it: a marker that records
working-tree state in a committed file is wrong the moment it is committed.

### D3 -- clear contradictions against master

Remove the four stale "Uncommitted" annotations in `docs/handover.md`,
replacing each with the commit that carried the work (`0eaac1e`, `16a4f61`,
`860c4fc`, `344b56f`, `e37c506`). Delete
`docs/cast-enrichment-staleness-handoff.md` and
`docs/glossary-suffix-protection-handoff.md`; both are marked Resolved and
superseded by CLAUDE.md sections covering the same fixes.

### D4 -- "Current open items" at the top of CLAUDE.md

Insert Finding 4's list, each entry one or two lines with a pointer to the
section holding the full reasoning. This is a navigation aid over existing
content, not new content: nothing is summarized away, and the measure-first
reasoning stays where it is.

While editing, mark the two superseded statements identified in Finding 3
in place (one line each, pointing forward to the section that resolved
them).

### D5 -- one owner per question

```
README.md              what it is, how to run it, what every knob does
docs/architecture.md   the deep architecture; README links to it and stops
                       duplicating it
CLAUDE.md              current-state guidance only (~400 lines):
                       tests, deploy, host + checklist, ops invariants,
                       policy precedents (QC advisory, the evidence bar,
                       measure-before-changing-translation)
docs/CHANGELOG.md      the ~30 dated entries, moved verbatim
docs/handover.md       open issues only; its change log retires into
                       docs/CHANGELOG.md
docs/agents.md         session-startup conventions only; its validation
                       reference points at CLAUDE.md rather than restating it
```

**The dated entries must move verbatim, not be summarized.** The rejected
approaches they record -- three disproved VAD fixes before the fourth
worked; `min_run=2` measured at 14 false positives against 2 true and
rejected; Thai mining investigated and deliberately not shipped; hotwords
measured and left off -- are the most valuable content in this repository
and exist nowhere else. The problem is their position between a future
session and the Host checklist, not their existence.

Mechanical check after the move: `CLAUDE.md` + `docs/CHANGELOG.md` should
contain every `##` heading the current `CLAUDE.md` has, and no prose should
be lost -- diff the concatenation against the original before committing.

Secondary benefit: `CLAUDE.md` is currently 102 KB. If it is loaded into
every session's context, that is roughly 25k tokens of largely historical
detail paid before any work begins.

### D6 -- README coverage

Add diagram nodes for `langid.py` (into the shared translation subgraph,
feeding `translate.py`), `name_correction.py` (post-ASR, into the video
subgraph), `upload_cleanup.py` and `alerting.py` (job service). Add a short
`docs/` index to the Development section, marking each file live or frozen.
Reword the Status throughput sentence to point at `benchmark-results/` and
`scripts/bench_translate.py` rather than at the test suite.

## Explicitly not proposed

- **No pipeline, ASR, translation or QC behaviour change.** Everything above
  is documentation. The open items in Finding 4 are listed so they can be
  found, not so they can be acted on in this round; each needs its own
  measurement pass first, per this project's standing rule.
- **No rewrite of the dated entries' prose.** See D5.
- **No change to the inline code-comment citations** (`# IMPROVEMENT_PLAN.md 4.2`
  and similar). Round 2 already decided to leave these as historical labels
  rather than rewrite dozens of files for a path move; that reasoning still
  holds.
