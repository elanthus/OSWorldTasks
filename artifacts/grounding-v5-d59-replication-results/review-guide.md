# D5.10 review guide: completed D5.9 replication

This package prepares the completed replication for the project owner's review. D5.10
and public model-quality wording remain undecided. No additional model calls were made
to audit, export, or analyze the evidence; none are authorized by this package.

Read the [generated analysis](confirmatory-analysis.md) first. Its JSON counterpart binds
the response-free projection, frozen plan, and unchanged historical analysis by hash.
It reuses the existing statistical functions and frozen seed, resample count, primary
pairing, cluster mapping, reliability schedule, and denominator rule.

| Evidence | Review purpose |
|---|---|
| [Full result projection](report.json) | Every assigned outcome, resources, conditional critical-decision measurements, reported tokens, source hashes, approvals' caps, journal digests, and verified retention receipts |
| [Pilot projection](pilot-report.json) | Separate operational pilot; task failures retained and never pooled into the full comparison |
| [Earlier pilot launch report](../grounding-v5-d59-replication/pilot-launch-report.json) | Earlier parser-incompatible pilot retained as a separate attempt, before the versioned parser fix and fresh pilot |
| [Frozen replication plan](../grounding-v5-d59-replication/execution-plan.json) and [pre-registration addenda](../grounding-v5-d59-freeze/analysis-plan.md) | Exact executed policies, source revision, approved stop-rule options, renewal protocol, caps, and two-result reporting rule; neither file changed during analysis |
| [Historical D5.9 analysis](../grounding-v5-d59-haiku-results/confirmatory-analysis.md) | Original result, post-result stop amendments, different executed policies, waived kernel sandbox, and lost raw journals; reported unchanged beside the replication |
| [Replication command index](../grounding-v5-d510/c398557ca05b8cc87d2f4371fe6b1335d427e5c5/REPORT.md) | Exact offline commands, exit statuses, counts, runtimes, full output, checklist mapping, redaction scan, and artifact hashes |
| [Earlier full D5.10 evidence index](../grounding-v5-d510/07824eeeafee6e61f95aaa3a414dcf021da68b15/REPORT.md) | Historical full-suite and integration observations at their recorded revision; not rerun or represented as observations of this analysis change |

## Findings requiring owner consideration

- The historical and replication primary differences disagree beyond the frozen threshold.
  Both estimates, their difference, and the replication's frozen test and interval are
  reported. This is not a statistical test of a difference between campaigns, and no
  after-the-fact selection of a preferred result is made.
- One post-send timeout remains unresolved in the ledger. The local process was confirmed
  stopped; its one bounded retry used the same request and both reservations counted.
  Provider-side completion and token use remain unknown. Recorded response-token totals
  exclude that unknown quantity rather than treating it as zero. Local process closure
  and subscription charge accounting do not establish provider settlement or billing.
- Named secondary diagnostics without frozen operational estimators are explicitly left
  unestimated. Retained traces exist; inventing estimators after observing outcomes would
  require separately labelled exploratory work. The conditional first-attempt diagnostic
  does not include unreached consumers and does not replace episode success.
- Both retained copies were rehashed and their SQLite integrity checked. They reside on
  the same local disk. There is no independent off-host backup or new restore drill in
  this review package. Raw evidence stays private; public readers can regenerate the
  published analysis, but cannot independently replay private journals from the projection.
- The replication audit and targeted offline regression tests are fresh observations.
  The older full-suite, browser, and OSWorld records remain historical evidence at their
  original revision. This supplement does not silently refresh those observations.

No gate checklist is checked off here. The owner decides the D5.10 verdict, any treatment
of the disclosed limitations, and any eventual public wording. The frozen execution plan
and historical evidence remain unchanged, and no further campaign is started.
