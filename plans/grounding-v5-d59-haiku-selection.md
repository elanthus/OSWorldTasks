# Select Haiku for the D5.9 freeze successor

**Status:** accepted owner decision on 2026-09-23. The exact Haiku policy pair and the 168-independent-representative design are selected for a new D5.9 freeze. The owner subsequently approved a D5.9-specific OS-sandbox exception; the [versioned freeze successor](grounding-v5-d59-haiku-freeze.md) records that boundary. Execution remains disabled.

## Decision

The owner selected exact model `claude-haiku-4-5-20251001` through Claude Code CLI 2.1.267 and the `claude.ai` Max subscription route. The matched policies are:

- screenshot history: `policy-79441db33362e00a1ac6`;
- stateless current frame: `policy-e1d746cd6a83ffb12a20`.

The selected design contains 168 independent representatives and 24 robustness twins, for 192 episodes per arm. The [Haiku successor analysis](../artifacts/grounding-v5-d58-haiku-successor/analysis.json) calculates 87.0% power at observed discordance and 81.2% at the upper Wilson sensitivity endpoint under the unchanged two-sided exact McNemar alpha of 0.05, 20-point minimum relevant absolute difference, and 80% target power.

The [structured owner-selection record](../artifacts/grounding-v5-d59-haiku-selection/owner-selection.json) binds the exact policies, design, source analysis, and remaining execution boundary.

## Supersession boundary

This decision supersedes the Gemini candidate-policy selection for the next D5.9 freeze. It does not rewrite or invalidate the historical Gemini calibration, owner decision, admitted task package, or freeze artifacts.

The earlier Gemini budget, route, prices, runtime proposal, and execution-plan digest do not carry forward. This selection records no Haiku planning budget or runtime window and authorizes no paid or subscription-backed execution.

## Required successor work

D5.9 remains non-executable. Both calibration manifests record `os_sandbox_applied: false`. The owner approved an explicit exception for this exact pair rather than changing the calibrated authentication route. The versioned Haiku freeze successor must:

1. preserve and disclose `os_sandbox_applied: false` and the narrower claim boundary;
2. bind the admitted confirmatory tasks and exact source, policy, prompt, parser, adapter, CLI, and runtime digests;
3. bind count caps, a planning budget, a runtime window, and the subscription execution boundary; and
4. preserve zero approved model-attempt and provider-wire caps until the owner separately approves the exact frozen plan.

Any provider call before that separate approval is outside this decision.

## Verification

This command reconstructs the selection record from checked-in response-free analysis:

```sh
.venv/bin/python -m scripts.prepare_grounding_v5_d59_haiku_selection --verify
```

It makes no provider calls and generates no confirmatory tasks.
