# ADR: Bound agent-assisted changes with evidence and human gates

- **Status:** Accepted
- **Date:** 2026-09-06
- **Scope:** Engineering workflow for PixelGym-OSWorld

## Context

PixelGym-OSWorld makes claims about observation boundaries, reward correctness,
determinism, model spend, and experimental evidence. A fast delivery process is useful only when it
preserves those claims and leaves a record that another engineer can inspect. The repository
therefore needs a workflow that separates four things that are easy to conflate: agent-written
changes, automated review, deterministic checks, and owner decisions. [`CLAUDE.md`](../CLAUDE.md)
delegates repository-wide rules to `AGENTS.md` and repeats the instruction to stop at human gates;
it does not create a second policy source.

The bounded history snapshot for this decision is generated, not estimated. Its inclusive Git
window starts at `fa70acc07593a80379a473244b8797ab499fe3b1` (authored and committed
`2026-08-08T08:22:54+08:00`) and ends at the then-current `main` head
`01572d5e59a2c9e786547e95c8810677a9d8e841` (authored
`2026-09-06T16:48:13-07:00`, committed `2026-09-07T07:48:13+08:00`). Within that exact
revision/time window, 377 commits are reachable from the end revision and 94 pull requests were
created. The definitions, source commands, full branch-prefix distribution, and cutoff-state
counts are in the [generated history evidence](../artifacts/agent-assisted-workflow-history.json).
The generator counts commits from `git log` and pull requests from `gh pr list`; rerun
`python scripts/generate_workflow_history_evidence.py` only when intentionally recording a new
cutoff.

The history demonstrates agent-associated branch names without pretending that every branch used
one convention: 52 pull requests used `agent/`, six used `codex/`, and one used `claude/`. The same
[evidence file](../artifacts/agent-assisted-workflow-history.json) records every other observed
prefix rather than discarding it. It also records 65 multi-parent merge commits and 29
PR-numbered, single-parent squash-style commits. The latter pattern appears at PR #51 and then
PRs #129–#156; it is a later workflow, not a claim that all historical PRs were squashed.

Review did catch material defects. For example:

- The repository's Claude reviewer found that the platform lock verifier checked package names
  but not exact declared versions in [PR #21](https://github.com/elanthus/OSWorldTasks/pull/21#issuecomment-5301476422);
  a later [review of the fix](https://github.com/elanthus/OSWorldTasks/pull/21#issuecomment-5301543161)
  verified the new mismatch coverage. This reviewer is the non-blocking workflow configured in
  [`.github/workflows/claude-code-review.yml`](../.github/workflows/claude-code-review.yml).
- CodeRabbit found that the recovery control could bypass the required recovery state in
  [PR #87](https://github.com/elanthus/OSWorldTasks/pull/87#discussion_r3849730562). The
  [thread response](https://github.com/elanthus/OSWorldTasks/pull/87#discussion_r3849840791)
  identifies the fix and regression coverage. CodeRabbit is identified from the GitHub App author
  on that thread; there is no repository-owned CodeRabbit configuration in this snapshot.
- An orchestrator review explicitly backed by an independent read-only review found that CLI
  fault classification erased policy violations in
  [PR #155](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557419773). The
  [follow-up](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557506551) records
  the fixing commit and rerun checks. This is separate from both named review bots.

Those successes did not make the review system complete. The following defects escaped earlier
automation or deterministic checks and were later found through backlog analysis or independent
review:

| Escaped defect | Later record | Fixing pull request |
|---|---|---|
| Reward-hacking evidence contained unconditional audit truths, while navigation remained expressible through visible browser chrome. | [Issue #95](https://github.com/elanthus/OSWorldTasks/issues/95) | [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156) |
| A non-ASCII CSRF token could raise `hmac.compare_digest` and return HTTP 500 instead of rejection. | [Issue #96](https://github.com/elanthus/OSWorldTasks/issues/96) | [PR #129](https://github.com/elanthus/OSWorldTasks/pull/129) |
| Privileged environment diagnostics were passed into policy state. | [Issue #98](https://github.com/elanthus/OSWorldTasks/issues/98) | No fixing PR existed at this ADR's cutoff; the issue remained open. |
| Repeated rollback could reactivate the build that had just been abandoned. | [Issue #99](https://github.com/elanthus/OSWorldTasks/issues/99) | [PR #131](https://github.com/elanthus/OSWorldTasks/pull/131) |
| Fake widget geometry and keyboard behavior drifted from Chromium. | [Issue #94](https://github.com/elanthus/OSWorldTasks/issues/94) | [PR #151](https://github.com/elanthus/OSWorldTasks/pull/151) |
| Browser evidence and the OSWorld guest used different renderer arguments. | [Issue #101](https://github.com/elanthus/OSWorldTasks/issues/101) | [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156) |
| In-flight panel requests had no retained worst-case spend hold. | [Issue #103](https://github.com/elanthus/OSWorldTasks/issues/103) | [PR #130](https://github.com/elanthus/OSWorldTasks/pull/130) |
| Cross-phase summaries omitted unknown spend reservations and mixed enforcement conventions. | [Issue #104](https://github.com/elanthus/OSWorldTasks/issues/104) | [PR #133](https://github.com/elanthus/OSWorldTasks/pull/133) |
| A guest frame was captured before page initialization even though every navigation check passed. | The late [PR #156 review finding](https://github.com/elanthus/OSWorldTasks/pull/156#issuecomment-5558082637) and [round follow-up](https://github.com/elanthus/OSWorldTasks/pull/156#issuecomment-5558229333) record the defect and corrected interpretation. | [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156) (found and fixed in flight) |
| CLI-fault handling erased independently detected policy violations. | The independent [PR #155 finding](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557419773) and [fix record](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557506551) preserve the distinction. | [PR #155](https://github.com/elanthus/OSWorldTasks/pull/155) (found and fixed in flight) |

The final two defects were found and fixed inside their in-flight pull requests rather than filed
as separate backlog issues.

The open diagnostic-leak item is deliberately not paired with an invented fixing PR. It is a
known workflow gap at the recorded revision and prevents this ADR from implying that the backlog
was fully remediated.

## Decision

Use a branch-and-PR workflow in which agents may implement, test, document, and prepare evidence,
but cannot convert automated output into scientific, security, spending, or publication approval.

1. Start a scoped branch. Use an agent-associated prefix when it identifies the executor, while
   treating the generated prefix counts as history rather than an enforced taxonomy. Keep one
   writing agent per overlapping path, preserve unrelated work, and keep optional OSWorld work out
   of the fast path as required by [`AGENTS.md`](../AGENTS.md#5-working-agreements).
2. Open pull requests review-ready by default. Drafts are reserved for a known blocker or an owner
   question. The Claude workflow listens for `opened` and `ready_for_review`, explicitly excludes
   drafts, runs read-only on the repository, and is informational because its step uses
   `continue-on-error`:
   [review workflow](../.github/workflows/claude-code-review.yml). The history evidence reports all
   94 in-window PRs as non-draft at query time, but also states why that snapshot cannot prove how
   each PR was originally opened.
3. Run deterministic checks independently of model review. Pull-request CI installs the documented
   Python 3.12 development environment, then runs Ruff, mypy, and the fast unit suite in separate
   jobs: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). Local preflight repeats the
   configured lint and fast-suite commands: [`.agentic-preflight.toml`](../.agentic-preflight.toml).
4. Treat review output as claims to verify. Reply to actionable threads with a fixing commit and
   regression result, then rerun review on the changed head. The CodeRabbit finding and response in
   [PR #87](https://github.com/elanthus/OSWorldTasks/pull/87#discussion_r3849730562) and the
   independent finding and response in
   [PR #155](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557419773) show this
   loop. A reviewer name must come from the record: Claude, CodeRabbit, and orchestrator/independent
   review are not interchangeable labels.
5. Prefer squash-style integration for current review-ready PRs so the base branch receives one
   attributable PR-labeled change. Preserve the historical transition: the
   [generated evidence](../artifacts/agent-assisted-workflow-history.json) reports both the older
   multi-parent merges and the newer single-parent pattern instead of rewriting the past.
6. Generate quantitative process evidence at a frozen main revision. The checked-in
   [generator](../scripts/generate_workflow_history_evidence.py) resolves the end ref before either
   query, applies an inclusive timestamp window, fails if the GitHub query reaches its record
   limit, and records the query strings. Its parser and aggregation run against an offline fixture
   in [the unit test](../tests/unit/test_generate_workflow_history_evidence.py).

## Boundary between agent automation and human gates

| Layer | May do | Must not imply |
|---|---|---|
| Agent-written change | Implement the scoped issue, add failure-mode tests, generate a new versioned artifact, and prepare a review-ready PR under the [working agreements](../AGENTS.md#5-working-agreements). | That authored code is correct because an agent produced it, or that a broader scope change is authorized. |
| Automated review | Inspect a PR and report actionable findings. The configured Claude reviewer is read-only on the repository and non-blocking: [workflow](../.github/workflows/claude-code-review.yml). | That no comment means no defect. The workflow explicitly tolerates reviewer failure, and CodeRabbit can be rate-limited, as its [PR #129 record](https://github.com/elanthus/OSWorldTasks/pull/129#issuecomment-5532055304) shows. |
| Deterministic checks | Enforce formatting/static rules and exercise the offline fast suite through [CI](../.github/workflows/ci.yml) and [preflight](../.agentic-preflight.toml). | Scientific validity, security completeness, visual correctness outside the exercised conditions, or a milestone verdict. |
| Owner gate | Decide scope changes, sprint gates, provider/cloud spend, paid model calls, and public claims as listed in [`AGENTS.md`](../AGENTS.md#4-human-gates--stop-and-ask). | Delegation by silence. Agents report raw results and stop at these boundaries. |

The content boundary is equally explicit. [`AGENTS.md` observation and action invariants](../AGENTS.md#3-non-negotiable-invariants)
keep screenshots as the only observation and restrict the action vocabulary. Its
[reward invariants](../AGENTS.md#3-non-negotiable-invariants) reserve success for the privileged evaluator and require
one-shot reward timing. Its [determinism invariants](../AGENTS.md#3-non-negotiable-invariants) bind seeds, canonical
tasks, reset behavior, and stable rendering. Its [boundary invariants](../AGENTS.md#3-non-negotiable-invariants)
exclude answers, bounding boxes, and target-informed marks from the evaluation path. Finally, its
[human gates](../AGENTS.md#4-human-gates--stop-and-ask) reserve spend and public claims for the
owner, while its [evidence rules](../AGENTS.md#7-evidence-and-reporting-standards) require reports
to derive from stored structured observations and preserve negative results.

## Alternatives considered

### Let agents commit directly to `main`

This removes the durable review surface and makes it harder to associate a finding, fix, and test
with one change. It also bypasses the pull-request CI and reviewer triggers in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) and
[`.github/workflows/claude-code-review.yml`](../.github/workflows/claude-code-review.yml).

### Rely on deterministic checks alone

Ruff, mypy, and unit tests are reproducible, but only for encoded assertions. The initialization
race in [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156#issuecomment-5558082637)
passed every existing navigation check, and the CSRF Unicode case was absent until
[issue #96](https://github.com/elanthus/OSWorldTasks/issues/96) required a rejection matrix.

### Treat automated review as the approval gate

Named reviewers found real defects, but they also returned clean reviews for changes later covered
by the escaped-defect backlog: `claude[bot]` reported no high-confidence issues on the rollback
rewrite in [PR #22](https://github.com/elanthus/OSWorldTasks/pull/22#issuecomment-5301594146),
which was later covered by issue #99. The Claude job is deliberately non-blocking and warns when
it does not complete: [review workflow](../.github/workflows/claude-code-review.yml). Automated
review is a source of falsifiable findings, not a security or scientific sign-off.

### Reserve all implementation and review for humans

This provides human judgment but gives up useful automation for bounded implementation, regression
tests, evidence assembly, and repetitive checking. The chosen workflow keeps those mechanical
steps while preserving the decisions that require scientific context, security threat modeling,
or authority to spend and publish in [`AGENTS.md`](../AGENTS.md#4-human-gates--stop-and-ask).

## Consequences

The workflow leaves a stronger audit trail: a reader can connect a scoped branch, PR, named review
finding, fixing commit, deterministic checks, and owner decision. Generated history counts avoid
timeless productivity claims. Review-ready PRs expose work early, while squash-style integration
keeps the current base history attributable to a PR.

The costs are substantial. Review can churn through several heads, as the correction sequence in
[PR #31](https://github.com/elanthus/OSWorldTasks/pull/31#issuecomment-5303651791) and the extended
[PR #87 conversation](https://github.com/elanthus/OSWorldTasks/pull/87) demonstrate. Large changes
increase reviewer load and the chance that one fix creates another. The grounding package also
accumulated experiment-specific runners and scripts; [issue #111](https://github.com/elanthus/OSWorldTasks/issues/111)
documents the duplicated calibration surface and proposes a versioned, manifest-driven
consolidation without rewriting frozen evidence.

Evidence overclaim remains a first-class failure mode. Hard-coded audit truths in
[issue #95](https://github.com/elanthus/OSWorldTasks/issues/95), the misinterpreted pre-initialization
frame in [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156#issuecomment-5558082637), and
the accounting gaps in [issue #103](https://github.com/elanthus/OSWorldTasks/issues/103) and
[issue #104](https://github.com/elanthus/OSWorldTasks/issues/104)
show that a green check can validate the wrong proxy or omit a liability. Human scientific and
security judgment remains necessary to challenge the measurement, threat model, and interpretation;
the owner-only gates are therefore a feature, not an exception path.

### What we would change next time

- Define one manifest-driven calibration runner and shared CLI lifecycle before adding
  experiment-specific variants, while keeping provider-specific parsing and security contracts
  explicit. This is the direction recorded in [issue #111](https://github.com/elanthus/OSWorldTasks/issues/111).
- Define the typed policy-visible/host-only result boundary before implementing resume and
  diagnostics. Keep [issue #98](https://github.com/elanthus/OSWorldTasks/issues/98) open until a
  tested fix exists; do not treat this ADR as remediation.
- Make every evidence claim consume a stored result with provenance. Do not let a report generator
  execute the check it reports, and never represent a literal boolean as evidence. This follows
  [`AGENTS.md`](../AGENTS.md#7-evidence-and-reporting-standards) and the later review finding in
  [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156#discussion_r3943508362).
- Test the actual readiness and mutation boundary, not a proxy such as navigation chrome, stable
  intermediate pixels, or eventual zero reward. Add adversarial matrices early for Unicode,
  duplicate fields, rollback lineage, concurrent reservations, interruption, and resume.
- Split broad changes before review and budget an independent pass for evidence interpretation.
  Automated review remains useful, but a clean response never closes a scientific or security
  question.
- Generate the workflow-history artifact from the first milestone onward, at named revisions, so
  process counts are snapshots rather than retrospective claims.

Public-facing wording derived from this ADR remains subject to the owner's public-claim approval
gate in [`AGENTS.md`](../AGENTS.md#4-human-gates--stop-and-ask).
