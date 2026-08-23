# V4b Error Review — Seed 43 Raw-Condition Failure

- Protocol: `pixelgym-grounding-v4b-pilot`
- Model: `gpt-5.6-luna` (`codex-cli`, reasoning effort `low`)
- Scope: the single unsuccessful episode condition in the canonical v4b run (seed 43, family
  `triage_route_confirm`, raw condition)
- Method: derived entirely from stored evidence; no new model calls, no recapture, no rescoring
- Reviewed: 2026-08-23

## Evidence inputs

| Artifact | Role | SHA-256 |
|---|---|---|
| `artifacts/grounding-v4b-pilot-predictions-luna.jsonl` | canonical per-action records | `900917802b962585c0a5098d8a8a15e079a4ea67e903a4f43fe14790310c616e` |
| `artifacts/grounding-v4b-pilot-conditions-luna.jsonl` | per-condition outcomes | `d4609a9e7b00f18f3dd74c6b25571cf67ce253327a67d95a31db8dc40abb53db` |
| `artifacts/grounding-v4b-pilot-episodes.json` | frozen episode specification | committed pre-evaluation |
| `artifacts/grounding-v4b-pilot-states.jsonl` | state-ID ↔ screenshot-hash map | committed pre-evaluation |
| `artifacts/grounding-v4b-pilot-candidates.jsonl` | target-independent control boxes | committed pre-evaluation |

Hashes for the first two files are copied from
`artifacts/grounding-v4b-pilot-results-luna.json`. Control identification below joins each
recorded click point to the candidate bounding boxes for the observed state hash; no box was
consulted that the scorer did not already store.

## Episode specification (seed 43, "Request VR-3043")

Three stages, each requiring one correct semantic decision:

1. **Triage the queue.** Instruction: open the pending request with the highest amount. Visible
   facts: "Ignore approved requests", "Compare the visible pending amounts". Options include
   Kinetic Freight — Approved — $91,300 (distractor) and three pending requests at $18,200,
   $42,900, and $54,600. Target: `open_redwood` ($54,600).
2. **Apply the approval policy.** Visible facts: "Opened: Redwood Labs · $54,600 · medium risk",
   "Policy: high risk → Compliance", "Otherwise amount over $25,000 → Finance; all others →
   Standard". Target: `route_finance`.
3. **Confirm the routing decision.** Target: `confirm_route`.

## Raw-condition trajectory

| Action | Observed state | Click (x, y) | Control hit | Correct control | Checkpoint after | Result |
|---|---|---|---|---|---|---|
| 1 | `v4b-43-s0-main` | (778, 358) | `open_redwood` | `open_redwood` | 1 | advanced |
| 2 | `v4b-43-s1-main` | (433, 453) | `route_compliance` | `route_finance` | 1 | recovery state entered |
| 3 | `v4b-43-s1-recovery` | (433, 517) | `route_compliance` | `route_finance` | 1 | observation unchanged |
| 4 | `v4b-43-s1-recovery` | (433, 517) | `route_compliance` | `route_finance` | 1 | truncated at step limit |

Recorded outcome: `terminated=false`, `truncated=true`, `success=false`, checkpoint 1 of 3.
All four responses have `parse_status="parsed"`, no `request_failure`, and a valid in-bounds
CLICK action. There is nothing to attribute to transport, parsing, or action-schema failure.

Two geometric observations from the stored boxes:

- Every click landed inside a valid candidate box. No click was a near-miss or an ambiguous
  point between controls.
- The recovery layout shifts the route buttons down 64 pixels (`route_compliance` moves from
  y ∈ [424, 482) to y ∈ [488, 546)). The action-3 click moved from y=453 to y=517 — it tracked
  the shifted position of the same control. Localization remained accurate; the *choice*
  repeated.

Actions 3 and 4 share the same observation hash
(`f5ac7f69…`), and their records carry identical parsed actions, identical latency values, and
`cache_hit=true`. Under the documented same-state cache policy (canonical rerun: 59 cached
responses, 4 new calls; plan §8), action 4 is by construction a replay of the stored response
for that observation. Given a wrong action 3 that left the screenshot unchanged, action 4 could
not differ.

## Marks-condition trajectory (same episode, for contrast)

| Action | Observed state | Click (x, y) | Control hit | Checkpoint after |
|---|---|---|---|---|
| 1 | `v4b-43-s0-main` | (778, 358) | `open_redwood` | 1 |
| 2 | `v4b-43-s1-main` | (778, 383) | `route_finance` | 2 |
| 3 | `v4b-43-s2-main` | (427, 358) | `confirm_route` | 3 → reward 1.0, terminated |

The stage-1 responses are identical points in both conditions (both fresh calls,
`cache_hit=false`). The conditions diverge only at stage 2.

## Classification

**Semantic policy misapplication** (stage 2), with correct localization throughout.

The stage-2 screen states the policy and the opened request's attributes explicitly: medium
risk, $54,600, "high risk → Compliance", "otherwise amount over $25,000 → Finance". The correct
branch is Finance; the model selected Compliance twice (once on the main layout, once on the
shifted recovery layout). This is not a grounding, coordinate-frame, small-target, or
crowded-layout error — the categories that dominated the v1 raw error review
(`artifacts/grounding-error-review-decisions.json`) are all absent here.

Why the model preferred the Compliance branch is not recoverable from the stored records: the
raw response contains only the action JSON, with no rationale channel. Any mechanism story
(e.g., anchoring on "risk" or on the largest visible amount) would be speculation and is not
asserted.

## Attribution limits

This is the only discordant episode between conditions in v4b (marks 10/10 vs raw 9/10). One
discordant pair cannot distinguish an overlay effect from run-to-run variation, and no such
distinction is claimed. The v4b results artifact draws no marks-benefit conclusion; neither
does this review.

## Implications for a longer-horizon successor (proposals, not findings)

1. **The recovery step only helps when the observation changes.** A repeated wrong choice that
   leaves the screenshot unchanged is deterministically absorbing for a stateless, same-state
   cached policy. A successor's recovery state could change visibly after each rejected
   attempt (e.g., an attempt counter or progressively explicit feedback) so the extra step is a
   genuine second chance rather than a structural replay.
2. **Failure mode has shifted from localization to policy application.** Consistent with the
   v4/v4b saturation pattern, the residual frontier error here is a visible-rule application
   error, not a grounding error. A successor that wants headroom should scale rule complexity
   or cross-screen information carry, not visual clutter.

No environment code, frozen artifact, or invariant was modified for this review.
