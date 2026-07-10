---
type: consilium
date: 2026-07-10
project: Claude_Booster
topic: cross-session regression defense — institutional memory for an amnesiac agent
preserve: true
supersedes_emphasis_of: reports/consilium_2026-07-10_regression_loop_archaeology.md
---

# Consilium 2026-07-10 (#2) — Cross-Session Regression Memory

## Task context

Dmitry, after consilium #1: *"The goal is not only to prevent this inside `/go`, but session to
session — so we don't circle day after day around fixing one thing and breaking another. How is this
solved in large projects and multi-agent setups? Give me a consilium of ideas, with an understanding
of how it could work in ANY project."*

The structural difficulty: a human team defends against "fix A, break B" with **institutional
culture** — war stories, the senior who says "don't touch that, it's there because of the 2019
outage." An LLM agent has none of it. It begins every session with total amnesia. The industry's
primary mechanism is unavailable to it by construction. And the obvious substitute — write the
lesson down, read it next session — turns out to be the *weakest* available form, and can be
actively harmful.

## Method

Three research agents (WebSearch/WebFetch, citations demanded, folklore flagged) → a Verified Facts
Brief → a panel of 5 bio-specific agents + 3 external reviewers. Per the institutional lesson from
consilium #1, one external reviewer was a dedicated **brief-verifier** with live read access and an
explicit mandate to attack the Lead's facts rather than the Lead's conclusions.

| # | Perspective | Provider / model |
|---|---|---|
| R1 | Research: monorepo regression defense | anthropic / sonnet-4.6 |
| R2 | Research: encoding the "why" | anthropic / sonnet-4.6 |
| R3 | Research: agent memory across sessions | anthropic / sonnet-4.6 |
| B1 | Platform engineer, Google-scale monorepo | codex-cli / gpt-5.5 |
| B2 | Kernel maintainer / provenance engineer | codex-cli / gpt-5.5 |
| B3 | Agent-memory researcher | codex-cli / gpt-5.5 |
| B4 | Test-strength engineer (mutation, characterization) | codex-cli / gpt-5.5 |
| B5 | Anti-ceremony skeptic / cost realist | codex-cli / gpt-5.5 |
| X1 | External reviewer | grok-cli / grok-composer-2.5-fast |
| X2 | **Brief-verifier**, live read access | anthropic / opus-4.8 |
| X3 | External reviewer | zai-cli / glm-5.2 — **DEGRADED** (three attempts, `API Error 529 [1305] overloaded`; slot not substituted) |

Provider diversity: Anthropic ×3, OpenAI ×5, xAI ×1. Z.ai unavailable.

---

## [CRITICAL] Corrections to the Lead's brief — and to consilium #1

The brief-verifier found three errors. The Lead re-verified all three independently. All confirmed.
**One of them invalidates the headline conclusion of consilium #1.**

### C1 — "`weak_verification` = 35 of 58 runs" was a units error. It is 20 of 58 (34%).

`kpi_rework.jsonl` records `defect_categories` as `{category, count}` pairs. `35` is the **sum of
`count`** (instances). The number of **runs** touched is 20. Consilium #1 — and this brief — placed
`35` (instances) next to `11` (runs) in adjacent sentences and reasoned from the comparison.
Counted consistently in **runs**:

```
weak_verification     instances=35   runs=20   34%
integration_mismatch  instances=14   runs=11   19%
missed_failure_mode   instances=13   runs=11   19%
capability            instances= 6   runs= 3    5%
contract_ambiguity    instances= 2   runs= 2    3%

Worker-side defects  (missed_failure_mode + integration_mismatch) = 22 runs
Verification-side    (weak_verification)                          = 20 runs
```

**Worker-side blindness (22 runs) exceeds verification weakness (20 runs).** Consilium #1 concluded
"the real target is `weak_verification`, not `integration_mismatch`" and rejected design-time
mechanisms on that basis. That conclusion rested on a units error. The honest statement is:
**verification weakness and Worker failure-mode blindness are co-equal drivers.** A design that
hardens only the oracle addresses at most half the loop.

(Both numbers remain LLM self-labels typed by the Lead at `go.md:895` — see consilium #1 C2. The
correction changes the *emphasis*; it does not upgrade the evidence.)

### C2 — "Booster has zero edges between memory and code" was FALSE.

The `agent_memory` schema **already has a `related_files` column**. Verified live:

```
has related_files column: True
rows with related_files populated: 3     (one is junk: "/tmp")
  22223 error_lesson -> reports/incident_2026-05-06_paper_ped_destruction.md, feedback_never_git_clean_...
  22226 error_lesson -> reports/incident_2026-05-06_paper_ped_destruction.md, feedback_never_touch_paper_...
error_lesson/incident rows containing a hex SHA-like token: 11
```

So the failure is **not** "no schema for edges." It is "**the edge field is optional and
unenforced.**" This changes the implementation entirely: building a *new* ledger file at a new path
would create a second artifact to desynchronize — reproducing the exact ADR decay it aims to
prevent (R2-F3). The schema already carries `related_files`, `status`
(`active|under_review|superseded`), `superseded_by_id`, and `resolve_by_date`. **Every column the
design needs already exists.**

### C3 — A PreToolUse hook has no git diff, and fires BEFORE the edit is applied.

Verified against `go_gate.py:12,200-203`. PreToolUse stdin is
`{tool_name, tool_input, cwd, agent_id, agent_type, session_id}`. For `Edit`, `tool_input` is
`{file_path, old_string, new_string}` — a *region* diff, not a unified diff. `git diff` at hook time
does **not** show the pending edit, because the edit has not happened yet.

**This kills the enforcement mechanism proposed by five of the six panelists.** B1, B2, B3, B4 and
X1 all wrote hook pseudocode beginning with `changed = git_changed_files()` or
`git_diff_of_target(tool_input)`. None of it can run at PreToolUse. Enforcement must move to a
**Stop-hook** (where the edits exist and tests can run) or to CI.

### C4 — The research citations are real. (Verifier's mandate was to break them; it could not.)

- `arXiv:2605.29463` "Honest Lying" — **CONFIRMED**, ICML 2026 workshop, RRR metric, 0→86%.
- `arXiv:2604.01518` (STING), 77% surviving mutants, 4.2–9.0 pp drop — **CONFIRMED, exact match.**
- Huang et al. ICLR 2024, GPT-4 GSM8K 95.5% → 91.5% — **CONFIRMED, exact match.**

Also confirmed independently: `access_count = 0` on all audit/consilium/feedback/incident rows means
**uninstrumented, not unread**. `memory_session_start.py:340` calls `build_context()`, which never
calls `recall()`; `_fetch_start_context()` uses a **read-only** connection (`rolling_memory.py:1769`)
and *physically cannot* write. No log anywhere records whether an injected memory was used.

---

## What the research established

**R1-F8 — the finding that reframes the whole question.** The Lead assumed, and was going to argue
from, the widely-repeated claim that Google's SRE book and Amazon's COE require every incident to
produce an automated check. The research agent went to the primary sources. **Neither does.** The
SRE "Postmortem Culture" chapter requires "follow-up actions" of unspecified type. COE requires
action items with owner, priority, due date. The strong claim is a secondary-blog gloss.

> **We are not catching up to industry. Industry does not do this.** That is either a twenty-year
> oversight by the two most process-mature engineering organizations on earth, or it is a cost signal.

**R2-F7 — the gap.** Every known mechanism falls into exactly one of two pits:

| | Tool-enforced (loud) | Culture-enforced (decays) |
|---|---|---|
| **Semantically shallow** | tests, assertions, `Fixes:` trailer, Pact contracts | — |
| **Semantically rich ("why")** | — | ADRs, comments, Chesterton's Fence, CODEOWNERS |

> *"No mechanism found combines tool-enforced loudness with rich causal 'why' content and incident
> traceability simultaneously."*

Closest analogue is the Linux `Fixes:` trailer — machine-parseable, consumed by backport tooling —
but it links **fix→bug-commit**, never **code→incident**.

**R2-F3 — nobody claims ADRs prevent regressions.** Every source describes them as context for a
*human* to re-litigate a decision. Nothing stops an engineer violating an ADR without reading it.
Documented decay: the `Superseded by`/`Supersedes` link is bidirectional and **teams update one side
and forget the other.**

**R2-F1/F2 — Chesterton, corrected.** The famous short form ("never take a fence down until you know
why it was put up") is **a paraphrase, sometimes misattributed to JFK — not Chesterton's words.**
The original (*The Thing*, 1929) puts the burden on **understanding** and explicitly permits
destruction afterward: *"Go away and think. Then, when you can come back and tell me that you do see
the use of it, I may allow you to destroy it."* Econlib's critique: the parable is an **argument
from ignorance** — it assumes the objector is ignorant, whereas real reformers usually know the
reason and *disagree about its value*. It functions as a **rhetorical ratchet**.
(Also: the term "Chesterton's Graveyard" **does not exist** in the literature. Verified.)

**R2-F5 — assertions are the one loud "why" primitive.** An assertion cannot be silently deleted:
its removal is a visible line in the diff. TigerBeetle's Tiger Style (primary source): *"Assertions
downgrade catastrophic correctness bugs into liveness bugs"*; **minimum two assertions per
function**; *"for every property you want to enforce, find at least two different code paths where
an assertion can be added"* — deliberate redundancy so deleting one line does not remove the only
guard. And their own honest caveat: *"Assertions are a safety net, not a substitute for human
understanding."*

**R3-F1 — prose memory self-poisons.** "Honest Lying" (arXiv:2605.29463): Reflexion-style agents
write a **confident but wrong self-diagnosis** into memory, then **act on that false belief across
trials** even though the environment resets correctly. The authors' fix: replace open-ended LLM
self-diagnosis with **programmatic, deterministic extraction of failure signals.** Correct object
mention 0% → 86%; Reflection Repetition Rate 0.64 → 0.10.

**R3-F3/F4 — "just remember more" is harmful.** *Lost in the Middle* (TACL 2024): a fact in the
middle of a long context is significantly less likely to be used. Chroma's "Context Rot" (18 models):
reliability degrades with input length **long before the window fills**, framed as an architectural
property of attention. And **a wrong retrieved memory is worse than none**: models ignore correctly
retrieved context 28.4%–42.3% of the time ("Evidence Override"), and under misleading context they
**trust the retrieved-but-wrong content over their own correct prior knowledge.**

**R3-F5 — self-correction fails without an external signal.** Huang et al., ICLR 2024: intrinsic
self-correction **degrades** accuracy (GPT-4 on GSM8K: 95.5% → 91.5%). Only *extrinsic* correction
(compiler, test suite, oracle) reliably helps. Follow-up: the failure is in **locating** the error,
not fixing it once located. And: *"correlated error between generator and evaluator can render
self-evaluation non-identifying"* — the mathematical basis for the cross-provider rule.

**R3-F6 — the oracle is the bottleneck, everywhere.** 77% of SWE-bench Verified instances have at
least one surviving mutant. Strengthening the suites drops top agents' resolved rates 4.2–9.0 pp.
An ICSE 2026 audit found ~29.6% of "plausible" patches behaviorally diverge from the reference fix.

**R3-F7 — every vendor with two-tier memory tells you not to trust the auto tier.** Devin's and
Windsurf's own docs recommend promoting anything durable out of auto-generated "memories" into
version-controlled Rules / `AGENTS.md`. No controlled study of `CLAUDE.md`/`.cursorrules` efficacy
exists; the "errors dropped 40%→3%" claims are unbenchmarked blog posts. One source, quoted:
*"self-reports are vibes, not evidence."*

---

## Panel positions

| Agent | On the Ledger | Minimum edge | Unique contribution |
|---|---|---|---|
| **B1** Platform | Right problem, wrong artifact if hand-written | `incident → test`; code edge **derived** from coverage | *"The ledger should be an index over tests, not a separate source of truth."* Beyoncé Rule as scalable law: unguarded behavior is **legitimately free to break**. |
| **B2** Kernel | Yes to ledger, **no to YAML as source of truth** | `incident → failing test → fix commit` | Put provenance **in git trailers** (`Regression-Test:`, `Regression-Id:`, `Fixes:`); the ledger file is a *generated index rebuilt from commits and tests*. Git gives authorship, chronology, revertability for free. |
| **B3** Agent memory | Right shape, **demote anchors** | `incident → executable oracle` | Admission control is the entire design. *"The predicate may be LLM-drafted, but never LLM-authoritative."* Proposed the falsification experiment. |
| **B4** Test strength | Build it, but prove the oracle | `incident → invariant → test`, anchors are routing hints | **The canonical mutant.** Also: label the oracle `characterization \| normative \| contract \| metamorphic` — a characterization entry is *quarantine*, because Feathers warns it freezes bugs. |
| **B5** Skeptic | Yes to shape, **no to ceremony level** | `incident → predicate → test`, **no anchors in v1** | *"Adding a second memory system before instrumenting the first is madness"* (A3). Cheapest 80%: instrument memory telemetry first. |
| **X1** Grok | Small ledger, admission by exit 0 | `predicate + test_cmd` | **Cardinality bound: at most ONE executable invariant per incident.** Answers "who maintains 500 entries." |
| **X2** Opus (verifier) | **Do not build a new file at all** | `incident → test`, anchor is the *test* | C1–C3. The schema already has every column. Enforcement belongs at **Stop-hook**, not PreToolUse. |
| **X3** GLM | — | — | DEGRADED, no position recorded. |

### Unanimity (7/7 responding, across 3 providers)

1. **The code-line anchor must NOT be the source of truth.** It is the rot-prone part (file moves,
   refactors, squash merges). **The test is the durable anchor.**
2. **Admission requires a fail-before/pass-after (F2P) proof.** No red-then-green test → the entry
   is `advisory` and guards nothing. This is the direct, mechanical defense against R3-F1: *a
   confabulated lesson cannot produce a failing-then-passing test.*
3. **The agent must never read the ledger as prose.** Memory reaches it as a **red test**, not a
   remembered paragraph. Forced by R3-F3 (context rot) + R3-F4 (wrong memory displaces correct
   knowledge) + R3-F5 (only extrinsic signals correct).
4. **The Beyoncé Rule is the scalability law.** Unguarded behavior is legitimately free to break.
   A ledger that tries to protect untested folklore becomes the wall.
5. **Expiry is mandatory, not optional.** It is the only thing separating a guard from a ratchet.
6. **A surviving mutant files a debt; it does not block.** Equivalent mutants are 4–39% and
   undecidable — never spend the Lead's time adjudicating them.

---

## Decision

### D1 — Do NOT build a new ledger file. Enforce the columns that already exist. (C2)

`agent_memory` already has `related_files`, `status`, `superseded_by_id`, `resolve_by_date`. A new
YAML at a new path is a second artifact to desync — the ADR decay mode (R2-F3) reproduced.
The "Regression Ledger" is a **view over `agent_memory`**, defined by an admission predicate:

```
An error_lesson / incident row is `active` (i.e. it GUARDS something) iff:
    related_files            contains a path matching tests/**      # the guard test = the anchor
    metadata_json.f2p_proof  = {pre_sha, post_sha, test_id}         # the machine-checkable edge
    resolve_by_date          IS NOT NULL                            # forced expiry (anti-ratchet)
otherwise:
    status := 'advisory'   — prose a human may read; it guards nothing, blocks nothing
```

### D2 — Admission is a fail-to-pass proof, executed, never an LLM's assertion.

This is the load-bearing rule and the panel's unanimous answer to R3-F1.

```bash
# deterministic admission oracle — no model in the loop
git checkout "$pre_sha"  -- $code_paths
run_test "$test_id" && exit 1     # MUST fail pre-fix, else the test proves nothing
git checkout "$post_sha" -- $code_paths
run_test "$test_id" || exit 1     # MUST pass post-fix
# only now: status='active', metadata_json.f2p_proof={pre_sha,post_sha,test_id}
```

The agent may *draft* the prose invariant. The prose is a **caption over evidence**, never the
evidence. A confidently-wrong lesson — the exact "Honest Lying" failure — **cannot pass this gate**,
because a confabulated invariant has no test that goes red then green. This inverts the usual
direction: the executable artifact is primary, the `error_lesson` prose row is its projection.

### D3 — Enforcement lives at the Stop-hook, not PreToolUse. (C3)

Five of six panelists proposed hooks that read a git diff. **They cannot run** — PreToolUse fires
before the edit and receives no diff (C3). The correct venue is the **Stop hook**, where the edits
exist on disk and tests can actually execute. This is also the **Pact inversion** (R1-F6): the party
who changed the code pays the cost, in their own build, at the moment they try to finish.

```python
# Stop-hook. The agent tries to end the turn.
touched  = files_edited_this_session()
guards   = ledger_rows(status='active')
at_risk  = [g for g in guards if covers(g.test_id, touched)]   # precomputed test→files map
red      = [g for g in at_risk if run_test(g.test_id) != 0]
if red:
    block(f"You touched code guarded by {[g.id for g in red]}; their regression tests are RED.")
    sys.exit(2)
sys.exit(0)
```

Note what this does **not** do: it does not block the edit, and it does not lecture the agent about
history. It lets the work proceed and makes the guard **go red**. Unguarded behavior is free to
break (R1-F1) — honest, and the only rule that scales.

### D4 — The agent never reads the ledger as prose. Memory arrives as a red exit code. (R3-F3/F4/F5)

Wholesale injection is the context-rot failure. Nothing from the ledger enters the context window
until a guard test fails, and then **only the failure string does**:

```
REGRESSION GUARD FAILED: REG-2026-0710-go-verifier-exit-code
Invariant: /go verdict must equal the verifier's exit code, never model judgment.
Command:   tests/test_go_verdict_exit_code.sh
Incident:  id=47327   (pull on demand: `ledger explain REG-...`)
```

**Push nothing. Pull on demand.** What is lost: the causal war story. The panel accepted the loss
unanimously. B2 put it best: *"The running test is a better teacher than a paragraph in the middle
of a 200k-token prompt."* The mitigation that does not reintroduce context rot is a **breadcrumb** —
an incident id in the assertion message, not a narrative.

### D5 — Prove the oracle with a canonical mutant. Survivors file debt; they never block. (B4)

`weak_verification` is a label the Lead types. Mutation testing makes it a number. But full-suite
mutation is a compute and triage disaster (R1-F7: equivalent mutants 4–39%, undecidable).

**B4's canonical mutant** is the tractable core: for each entry, name the ONE mutant that represents
the incident's trigger — the specific corruption the guard exists to catch.

```yaml
oracle:
  type: normative           # characterization | normative | contract | metamorphic
  freezes_current_behavior: false
canonical_mutant: remove-verifier-exit-code-check
policy:
  canonical mutant killed    -> entry may become `active`
  canonical mutant survives  -> the guard is fake protection; entry stays `advisory`
  other mutant survives      -> /debt add (test_strength_debt); never blocks /go
```

And B4's guard against the ugliest case, which no other panelist raised: **what if the ledger's test
is a characterization test that pinned a bug, and the ledger is now defending the bug?** Feathers
warned of exactly this (R1-F5). Answer: label the oracle type. A `characterization` entry is admitted
only as **quarantine** — useful today, suspect tomorrow — never as `active_normative`.

### D6 — The anti-ratchet, made mechanical. (R2-F1, R2-F2, R2-F6)

Chesterton permits destruction *after* understanding; Econlib warns the fence becomes a rhetorical
ratchet. Feature-flag hygiene supplies the mechanism: **mandatory expiry forces re-justification.**
`resolve_by_date` already exists in the schema.

Deterministic retirement evidence (never "the agent says it's obsolete"):
- the guarded code path is deleted and no reachable caller remains (grep/AST, not LLM);
- a **stronger entry supersedes it** (`superseded_by_id` set, *and its test is green*);
- the incident's trigger is unreachable, proven by a test;
- a replacement test kills every mutant the old test killed.

**The load-bearing anti-reward-hack rule (X2):**

> The agent may **never** retire an entry whose test is currently **RED**, in order to make the build
> green. A red guard is either fixed or the change is reverted. Retirement of a red guard is refused.

Destruction is permitted *after* evidence; never *to escape* evidence. That single sentence is the
difference between Chesterton's actual principle and the ratchet Econlib criticizes.

### D7 — Cardinality bound: at most ONE executable invariant per incident. (X1)

This is the answer to B5's strongest objection ("who maintains 500 entries; what happens when the
suite takes 40 minutes"). The ledger's size is bounded by incident frequency, not by agent
enthusiasm. Everything else an incident produces stays advisory / process / monitoring.

### D8 — Ship the instrumentation before the mechanism. (B5, and A3)

B5's argument is unanswerable: **memory has no read-telemetry** (C4 confirms it — `access_count` is
uninstrumented; `build_context()` never calls `recall()`; `_fetch_start_context()` is on a read-only
connection and physically cannot write). We do not know whether the memory we *already have* is ever
used. Building a second memory system before instrumenting the first is, in his words, madness.

And consilium #1 established the sibling fact: the **production, cross-session loop is unmeasured**.
`kpi_rework` measures retries within one `/go` run. Two independent diagnoses, one disease: we are
about to optimize things we cannot see.

---

## The portable core (Dmitry's actual ask: "how it could be in ANY project")

Strip Booster away. What remains is four things, in decreasing universality:

1. **An invariant encoded as a test.** Not prose. Not an ADR. Not a comment. A test.
2. **An F2P admission proof** — red at the pre-fix commit, green at the post-fix commit. This is the
   only thing that distinguishes knowledge from confabulation, and it requires no model.
3. **A hook or CI job that runs the guard tests when guarded files are touched** — at Stop or at
   merge, never before the edit exists.
4. **An expiry + supersession protocol**, so guards can die.

Requirements: a git repo and a test runner. Nothing else. Not Python, not Booster, not an LLM.

**Degraded mode — no test suite at all.** The ledger is not useless; it becomes the **seed of one**.
Entries start `advisory`/`seed`; the first engineering task is converting the highest-value seed into
one executable characterization test. Until then, the Beyoncé Rule applies honestly: the behavior is
not protected, and no one should pretend it is.

**Degraded mode — the agent cannot run tests** (no environment, no fixtures). Enforcement moves from
the local hook to **CI**. The agent may edit and commit; the merge is blocked until CI runs the
guard. The verdict remains an exit code produced by something other than the model. The axiom holds.

---

## Rejected alternatives

| Alternative | Proposed by | Why rejected |
|---|---|---|
| A new version-controlled `regression-ledger.yaml` | **Lead's hypothesis**, B1–B5, X1 (six of seven!) | **C2:** the `agent_memory` schema already has `related_files`, `status`, `superseded_by_id`, `resolve_by_date`. A new file is a second artifact to desync — the ADR decay mode (R2-F3) rebuilt in YAML. |
| Hand-written `file:line` code anchors | **Lead's hypothesis** | Unanimous 7/7 rejection. Line anchors rot under moves, refactors, squash merges, reformatting (R2 blame decay). **Anchor to the test.** Derive the code mapping from coverage if needed. |
| PreToolUse hook inspecting the diff | B1, B2, B3, B4, X1 (five of seven) | **C3:** PreToolUse receives no git diff and fires *before* the edit is applied. The proposal is physically unimplementable. Enforcement moves to the Stop hook. |
| `weak_verification` as the primary target | **Consilium #1's headline conclusion** | **C1:** rests on a units error (35 instances vs 20 runs). Worker-side defects total 22 runs. Verification weakness is a co-equal driver, not *the* root. |
| Injecting the ledger into the prompt | (nobody, once the research landed) | R3-F3 (context rot), R3-F4 (wrong memory displaces correct parametric knowledge, 28–42% Evidence Override). Push nothing; pull on demand. |
| Blocking `/go` on any surviving mutant | (rejected by B1, B2, B3, B4, X2) | Equivalent mutants are 4–39% and undecidable (R1-F7). A blocking rule creates denial-of-service against ourselves and burns the Lead's attention on an unanswerable question. Survivors file debt. |
| Mandating a test per incident (the strong form) | (the folk version of Google SRE / Amazon COE) | **R1-F8:** primary sources do **not** mandate this. Steelmanned by B5, B2, B4, X2 as a genuine cost signal, not an oversight. Narrowed to: *"no regression memory becomes active enforcement unless it has an executable guard."* |
| Letting the agent write the lesson and trusting it | (the status quo) | R3-F1: confabulated self-diagnosis is reinforced across trials. R3-F5: no self-correction without an external signal. R3-F7: even Devin and Windsurf tell users not to trust auto-memory. |

---

## Risks

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| R1 | **The ledger becomes an unbounded brittle wall** that blocks legitimate refactors — the precise cost that made Google and Amazon *not* build this (R1-F8) | **HIGH** | Mandatory `resolve_by_date` (D6). Cardinality bound: ≤1 invariant per incident (D7). Beyoncé Rule: unguarded behavior is free to break (D3). |
| R2 | **A characterization test pins a bug**, and the guard now defends the defect (Feathers' own warning, R1-F5) | **HIGH** | Oracle typing (D5): `characterization` entries are admitted as **quarantine only**, never `active_normative`. |
| R3 | **A poisoned entry gains machine authority** and is harder to remove than the bug it guards (R3-F4) | **HIGH** | The F2P admission gate (D2). A confabulated invariant cannot produce a red→green test. |
| R4 | The agent **retires an inconvenient guard** to make its build green | **HIGH** | D6's rule: retiring a RED guard is refused. Retirement needs a green superseding test or a deterministic unreachability proof. |
| R5 | We build all this against a defect we **cannot measure** (the cross-session loop) and on **self-labeled** telemetry (C1) | **HIGH** | D8: ship `reopen_rate` (consilium #1) and memory read-telemetry **first**. Do not build on the defendant's testimony. |
| R6 | Flaky guard tests teach the agent (and the human) to bypass the system. Google runs at 0.15% flake and *still* fights thousands daily | MED | Guards are few by construction (D7). A flaky guard is demoted to `advisory` on second flake, not tolerated. |
| R7 | **Hardening the oracle addresses at most half the loop** (C1: Worker-side 22 runs vs verification 20) | MED — **accepted, documented** | Recorded honestly. The Flow-Designer/Challenge design-time gates from consilium #1 address the Worker half; this design addresses the oracle half. Neither alone is sufficient. |

---

## Implementation order (each independently shippable; measurement precedes mechanism)

1. **Instrument memory reads.** Add read-telemetry to the `/start` injection path so we can answer
   "was this memory ever used?" Today the answer is unknowable (C4). *Cheapest, highest-information,
   zero risk.*
2. **Ship `reopen_rate`** (consilium #1, D5): deterministic, git-derived, no LLM label. The proxy for
   the production loop Dmitry actually described.
3. **Enforce `related_files` on `error_lesson`/`incident` rows** — the column exists and is populated
   in 3 of 44 rows. Make it required, pointing at `tests/**`. No new artifact.
4. **Implement the F2P admission gate** (D2) — the single most important mechanism in this document.
   Prose becomes a projection of an executed proof.
5. **Stop-hook guard runner** (D3), on the tiny set of `active` rows.
6. **Canonical mutants** (D5) for the first few entries; survivors file debt.
7. **Expiry / retirement protocol** (D6) once there are enough entries for the ratchet to bite.

Do not start at 3. Start at 1.

---

## Meta — the consilium mechanism, again

For the second time in one day, the highest-value output was not a design. It was the **brief-verifier
breaking the Lead's facts.** This session it found that "zero edges" was false (the schema already
had the column), that a PreToolUse hook cannot see a diff (invalidating five of six enforcement
proposals), and that **consilium #1's headline conclusion rested on a units error** — comparing
defect *instances* against defect *runs* in adjacent sentences.

Six of seven panelists then designed a new YAML file that a `related_files` column made unnecessary,
and five of seven wrote hook pseudocode that cannot execute. They reasoned faithfully — and therefore
wrongly — from the Lead's brief. **The brief is the highest-leverage artifact in a consilium, and it
is the least reviewed.**

The institutional rule from consilium #1 is hereby strengthened:

> A consilium MUST include a brief-verifier with live read access whose mandate is to attack the
> brief's **facts**, not its conclusions. The Lead's RECON is itself a report, and reports decay.
> Additionally: **when a brief cites a number, it must state the unit.** "35" and "20 of 58" are not
> the same claim, and a panel cannot catch the difference from prose.

A third lesson, on research: the Lead was about to argue from "Google and Amazon mandate a test per
incident." A research agent sent to primary sources found the claim is **folklore**. Had that not
been checked, this entire design would have been justified by a practice that does not exist.
