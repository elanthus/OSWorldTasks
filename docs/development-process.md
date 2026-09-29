# Development process

- **Status:** Accepted, 2026-09-06
- **Scope:** How changes to PixelGym-OSWorld are proposed, checked, reviewed, and approved

## Context

The repository makes claims about observation boundaries, reward correctness, determinism, model
spend, and experimental evidence. A fast delivery process helps only if it keeps those claims
intact and leaves a record that another engineer can audit. Four activities are easy to blur
together and are therefore kept apart: changes written by coding agents, automated review,
deterministic checks, and human decisions. [`CLAUDE.md`](../CLAUDE.md) defers to
[`AGENTS.md`](../AGENTS.md) for repository rules and does not define a second policy.

A generated history snapshot backs the quantitative statements in this record. It covers the
inclusive window from commit `fa70acc07593a80379a473244b8797ab499fe3b1` (2026-08-08) to the `main`
head `01572d5e59a2c9e786547e95c8810677a9d8e841` (2026-09-06): 377 commits reachable from the end
revision and 94 pull requests created in the window. The
[history evidence](../artifacts/agent-assisted-workflow-history.json) holds the definitions, source
queries, and counts, including 65 multi-parent merge commits and 29 PR-numbered single-parent
squash-style commits. The squash pattern first appears at PR #51 and then from PR #129 to PR #156;
it describes a later workflow and does not mean every historical PR was squashed. The
[generator](../scripts/generate_workflow_history_evidence.py) should be rerun only to record a new
cutoff on purpose.

### Defects caught in review

Review found real defects. Each reviewer is named from the record, not inferred:

- **Claude code review.** The repository's Claude reviewer found that the platform lock verifier
  compared package names but not declared versions in
  [PR #21](https://github.com/elanthus/OSWorldTasks/pull/21#issuecomment-5301476422). A
  [later review](https://github.com/elanthus/OSWorldTasks/pull/21#issuecomment-5301543161) confirmed
  the new version-mismatch coverage. This reviewer is the non-blocking job in
  [`claude-code-review.yml`](../.github/workflows/claude-code-review.yml).
- **CodeRabbit.** CodeRabbit found that the recovery control could skip the required recovery state
  in [PR #87](https://github.com/elanthus/OSWorldTasks/pull/87#discussion_r3849730562); the
  [thread reply](https://github.com/elanthus/OSWorldTasks/pull/87#discussion_r3849840791) names the
  fix and its regression test. CodeRabbit is identified by the GitHub App author on that thread. The
  repository holds no CodeRabbit configuration of its own.
- **Orchestrator review with an independent read-only pass.** In
  [PR #155](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557419773), this review
  showed that policy violations were lost when a CLI fault was classified. The
  [follow-up](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557506551) records the
  fixing commit and the rerun checks. It is distinct from both review bots.

### Defects that escaped

Review did not catch everything. These defects passed earlier automation or deterministic checks
and were found later through backlog analysis or independent review:

| Escaped defect | Later record | Fixing pull request |
|---|---|---|
| Reward-hacking evidence contained unconditional audit truths, while navigation remained expressible through visible browser chrome. | [Issue #95](https://github.com/elanthus/OSWorldTasks/issues/95) | [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156) |
| A non-ASCII CSRF token could raise `hmac.compare_digest` and return HTTP 500 instead of rejection. | [Issue #96](https://github.com/elanthus/OSWorldTasks/issues/96) | [PR #129](https://github.com/elanthus/OSWorldTasks/pull/129) |
| Privileged environment diagnostics were passed into policy state. | [Issue #98](https://github.com/elanthus/OSWorldTasks/issues/98) | No fixing PR existed at this record's cutoff; the issue remained open. |
| Repeated rollback could reactivate the build that had just been abandoned. | [Issue #99](https://github.com/elanthus/OSWorldTasks/issues/99) | [PR #131](https://github.com/elanthus/OSWorldTasks/pull/131) |
| Fake widget geometry and keyboard behavior drifted from Chromium. | [Issue #94](https://github.com/elanthus/OSWorldTasks/issues/94) | [PR #151](https://github.com/elanthus/OSWorldTasks/pull/151) |
| Browser evidence and the OSWorld guest used different renderer arguments. | [Issue #101](https://github.com/elanthus/OSWorldTasks/issues/101) | [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156) |
| In-flight panel requests had no retained worst-case spend hold. | [Issue #103](https://github.com/elanthus/OSWorldTasks/issues/103) | [PR #130](https://github.com/elanthus/OSWorldTasks/pull/130) |
| Cross-phase summaries omitted unknown spend reservations and mixed enforcement conventions. | [Issue #104](https://github.com/elanthus/OSWorldTasks/issues/104) | [PR #133](https://github.com/elanthus/OSWorldTasks/pull/133) |
| A guest frame was captured before page initialization even though every navigation check passed. | The late [PR #156 review finding](https://github.com/elanthus/OSWorldTasks/pull/156#issuecomment-5558082637) and [round follow-up](https://github.com/elanthus/OSWorldTasks/pull/156#issuecomment-5558229333) record the defect and corrected interpretation. | [PR #156](https://github.com/elanthus/OSWorldTasks/pull/156) (found and fixed in flight) |
| CLI-fault handling erased independently detected policy violations. | The independent [PR #155 finding](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557419773) and [fix record](https://github.com/elanthus/OSWorldTasks/pull/155#issuecomment-5557506551) preserve the distinction. | [PR #155](https://github.com/elanthus/OSWorldTasks/pull/155) (found and fixed in flight) |

The last two rows were found and fixed inside the pull requests that introduced them, so no
separate backlog issue exists. Issue #98 has no fixing PR in this record on purpose: at the cutoff
it was an open gap, and pairing it with an invented fix would overstate the remediation.

## Decision

Changes go through branches and pull requests. Coding agents may implement, test, document, and
prepare evidence. Automated output never becomes scientific, security, spending, or publication
approval.

1. **Scoped branches.** Each change starts on its own branch. One agent writes to any given set of
   overlapping paths at a time, unrelated work is preserved, and optional OSWorld work stays out of
   the fast path, as the [working agreements](../AGENTS.md#5-working-agreements) require.
2. **Review-ready pull requests.** Pull requests open ready for review unless a known blocker or an
   open question for the maintainer applies. The Claude review job runs on `opened` and
   `ready_for_review`, skips drafts, has read-only repository access, and cannot block a merge
   because its step sets `continue-on-error`.
3. **Deterministic checks run separately from model review.** Pull-request
   [CI](../.github/workflows/ci.yml) installs the documented Python 3.12 environment and runs Ruff,
   mypy, and the fast unit suite as separate jobs. Local preflight repeats the configured lint and
   fast-suite commands ([`.agentic-preflight.toml`](../.agentic-preflight.toml)).
4. **Review findings are claims to check.** Each actionable thread gets a reply with the fixing
   commit and regression result, and review is rerun on the new head. The PR #87 and PR #155 threads
   above show this loop. Reviewer labels come from the record; Claude, CodeRabbit, and orchestrator
   or independent review are not interchangeable.
5. **Squash-style integration for current pull requests.** The base branch receives one change per
   PR. The history evidence keeps both the older multi-parent merges and the newer single-parent
   pattern rather than rewriting the past.
6. **Process numbers are generated at a frozen revision.** The generator resolves the end ref
   before either query, applies an inclusive time window, fails if the GitHub query hits its record
   limit, and stores the query strings. Its parser and aggregation are tested offline in
   [`test_generate_workflow_history_evidence.py`](../tests/unit/test_generate_workflow_history_evidence.py).

## Where automation stops

| Layer | Permitted | Not implied |
|---|---|---|
| Agent-written change | Implement the scoped issue, add failure-mode tests, generate a new versioned artifact, and open a review-ready PR under the [working agreements](../AGENTS.md#5-working-agreements). | That the code is correct because an agent wrote it, or that a wider scope was authorized. |
| Automated review | Inspect a PR and report actionable findings. The Claude reviewer is read-only and non-blocking. | That silence means no defect. The job tolerates reviewer failure, and CodeRabbit can be rate-limited, as in [PR #129](https://github.com/elanthus/OSWorldTasks/pull/129#issuecomment-5532055304). |
| Deterministic checks | Enforce formatting and static rules and run the offline fast suite through CI and preflight. | Scientific validity, security completeness, visual correctness outside the tested conditions, or a milestone verdict. |
| Human gate | Decide scope changes, milestone gates, provider and cloud spend, paid model calls, and public claims, as listed in [`AGENTS.md`](../AGENTS.md#4-human-gates--stop-and-ask). | Approval by silence. Agents report raw results and stop at these points. |

The content boundary is the [environment contract](environment-contract.md): screenshots are the
only observation, the action vocabulary is fixed, success comes only from the privileged
evaluator, seeds and resets are deterministic, and answers, boxes, and target-informed marks stay
out of the evaluation path. The [evidence rules](../AGENTS.md#7-evidence-and-reporting-standards)
require reports to derive from stored observations and to keep negative results.

## Alternatives considered

**Agents commit directly to `main`.** Rejected. There would be no durable review surface linking a
finding, fix, and test to one change, and the pull-request CI and review triggers would never run.

**Deterministic checks alone.** Rejected. Ruff, mypy, and unit tests only check what has been
encoded. The PR #156 initialization race passed every existing navigation check, and the CSRF
Unicode case had no test until issue #96 required a rejection matrix.

**Automated review as the approval gate.** Rejected. The named reviewers found real defects but
also passed changes later covered by the escaped-defect backlog: `claude[bot]` reported no
high-confidence issues on the rollback rewrite in
[PR #22](https://github.com/elanthus/OSWorldTasks/pull/22#issuecomment-5301594146), which issue #99
later covered. Automated review produces falsifiable findings, not a security or scientific
sign-off.

**All implementation and review by people.** Rejected. It keeps human judgment but discards useful
automation for bounded implementation, regression tests, evidence assembly, and repetitive checks.
The chosen process keeps those steps automated and reserves decisions that need scientific
context, threat modeling, or authority to spend and publish.

## Consequences

A reader can trace a scoped branch, its PR, a named review finding, the fixing commit, the
deterministic checks, and the human decision. Generated history counts replace undated
productivity claims.

The costs are real. Review can churn through several heads, as in
[PR #31](https://github.com/elanthus/OSWorldTasks/pull/31#issuecomment-5303651791) and the long
[PR #87 conversation](https://github.com/elanthus/OSWorldTasks/pull/87). Large changes raise
reviewer load and the chance that one fix introduces another. The grounding package accumulated
experiment-specific runners and scripts;
[issue #111](https://github.com/elanthus/OSWorldTasks/issues/111) records the duplicated calibration
surface and proposes a versioned, manifest-driven consolidation that leaves frozen evidence alone.

Overclaiming evidence remains a primary failure mode. The hard-coded audit truths in issue #95, the
misread pre-initialization frame in PR #156, and the accounting gaps in issues #103 and #104 show
that a green check can validate the wrong proxy or omit a liability. Human scientific and security
judgment is still needed to challenge the measurement, the threat model, and the interpretation.

### Lessons for the next project

- A single manifest-driven calibration runner and a shared CLI lifecycle come first; experiment
  variants are added on top of them, with provider-specific parsing and security contracts still
  explicit (issue #111).
- The typed boundary between policy-visible and host-only results is settled before resume and
  diagnostics are built. Issue #98 remains open until a tested fix lands.
- Evidence claims read a stored result that carries provenance. Report generators never execute
  the check they report, and a hard-coded boolean does not count as evidence
  ([PR #156 review](https://github.com/elanthus/OSWorldTasks/pull/156#discussion_r3943508362)).
- Tests target the actual readiness and mutation boundary, not a stand-in such as navigation
  chrome, stable intermediate pixels, or reward that eventually stays at zero. Adversarial cases
  for Unicode, duplicate fields, rollback lineage, concurrent reservations, interruption, and
  resume are written at the start of a feature rather than after a defect.
- Large changes are divided before review, and evidence interpretation gets its own independent
  review pass.
- The workflow-history artifact is generated at named revisions from the first milestone onward.

Public wording derived from this record is subject to the public-claim gate in
[`AGENTS.md`](../AGENTS.md#4-human-gates--stop-and-ask).
