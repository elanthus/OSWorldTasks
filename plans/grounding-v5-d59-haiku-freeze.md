# D5.9 Haiku confirmatory freeze successor

**Status:** superseded after the authorized campaign stopped on an unauthorized Claude CLI API
retry event. The run is excluded from scoring and retained as invalid infrastructure evidence. The
[zero-API-retry successor](grounding-v5-d59-haiku-api-retry-successor.md) is the only candidate for
any future execution decision.

This successor replaces the Gemini candidate for the next D5.9 execution decision. It preserves
the admitted confirmatory tasks and statistical design while binding the exact Haiku policies that
produced the calibration evidence. It does not alter either calibration, make a provider call,
authorize subscription use, or declare a D5.10 verdict.

## Frozen comparison

- Model: `claude-haiku-4-5-20251001` through Claude Code CLI 2.1.267 and the `claude.ai` Max
  subscription route.
- Screenshot-history policy: `policy-79441db33362e00a1ac6`.
- Current-frame-only policy: `policy-e1d746cd6a83ffb12a20`.
- Sample: 168 independent representatives and 24 robustness twins, or 192 episodes per arm.
- Primary test: two-sided exact McNemar at alpha 0.05, with the unchanged 20-point minimum
  relevant absolute difference and 80% target power.
- Reliability schedule: two additional trials per arm on the first two independent
  representatives in each workflow family.

The successor reuses the existing 192-task
[manifest](../artifacts/grounding-v5-d59-freeze/task-manifest.json) and no-cost
[admission evidence](../artifacts/grounding-v5-d59-freeze/admission.json) by exact file and content
digest. It assigns new `d59-haiku-*` trial IDs so Haiku attempts cannot collide with the historical
Gemini candidate.

## Approved security exception

The [structured exception](../artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json) waives
OS-level sandbox enforcement only for this exact D5.9 Haiku pair. The calibrated controls remain
mandatory: no tools, no MCP servers, one turn, safe mode, disabled customizations and session
persistence, and a sanitized process environment.

Both frozen policy manifests truthfully retain `os_sandbox_applied: false`. The resulting evidence
may support the matched screenshot-history comparison, but it cannot support a claim that the
policy process was isolated by the operating system or independently prevented from accessing
local process capabilities. A CLI defect remains a disclosed residual risk.

## Candidate caps and runtime

Each Haiku action permits at most two model attempts: the original attempt and the already frozen
single retry after a confirmed-stopped timeout. The candidate mechanical ceilings are:

| Phase | Environment actions | Model attempts | Provider wire requests |
| --- | ---: | ---: | ---: |
| Primary | 10,830 | 21,660 | 21,660 |
| Reliability | 1,304 | 2,608 | 2,608 |
| Aggregate | 12,134 | 24,268 | 24,268 |

These are refusal ceilings, not expected usage. The route has zero incremental experiment charge,
so the candidate dollar cap is USD 0.00; subscription usage is controlled by the attempt cap, not
treated as free or unbounded.

The proposed aggregate window is 168 hours. The derivation uses the slower of two stored Haiku CLI
continuation rates, applies a 1.25 margin to the full mechanical attempt ceiling, and rounds up to
seven days. Full completion is not guaranteed within that window because subscription rate limits
remain external to the runner.

## Remaining human boundary

The [execution-plan candidate](../artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json) is
non-executable. Before any confirmatory call, the owner must separately approve:

1. the exact execution-plan digest;
2. nonzero model-attempt and provider-wire caps no larger than the candidate ceilings; and
3. the proposed 168-hour aggregate runtime window or a replacement window.

Any policy, CLI version, provider route, prompt, parser, task, admission, retry, cap, reliability
subset, or security-boundary change requires another versioned successor. The D5.10 verdict and
all public model-quality or security wording remain human-owned.

## Reproduction

The builder makes no provider calls. Use the source revision recorded in the execution plan:

```sh
.venv/bin/python -m scripts.prepare_grounding_v5_d59_haiku_freeze \
  --source-revision <recorded-source-revision> --verify
```

The command reconstructs the exception and execution plan entirely from checked-in evidence.
