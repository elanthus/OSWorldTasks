# Platform design

- **Scope:** Milestone 4 grounding-evaluation and policy-delivery platform, and its v5 stateful
  serving extension (`pixelgym-serving-v2`)
- **Status:** Milestone 4 gate D4.12 recorded as passed for the scripted provider at revision
  `4c4a7fb` ([record](../artifacts/platform/human-gate.json)); v5 stateful serving in progress
- **Terms:** see the [glossary](glossary.md)

## Goal

The platform wraps the frozen Sprint 3 grounding workload in a local-first system that runs,
governs, and delivers grounding evaluations:

1. Run evaluations as resumable Metaflow flows.
2. Record dataset, prompt, model, inference settings, code revision, metrics, latency, cost, and
   outputs in MLflow.
3. Offer a small web UI for submitting experiments and comparing compatible runs.
4. Evaluate accuracy, cost, and latency gates mechanically, failing closed on missing evidence.
5. Require separate human approval after all gates pass.
6. Serve the approved policy through a versioned HTTP API and roll back atomically.
7. Keep raw provider responses and dataset snapshots under content hashes in write-once storage.

The reference lifecycle is: submit, compare, promotion blocked, revise, gates pass, approve,
deploy, call the API, roll back. The platform never changes Sprint 3 ground truth, repairs model
output, discards failures, or reinterprets the published Sprint 3 result.

Out of scope: model training, multi-tenant service, SSO, billing, user-supplied flows, Kubernetes
or cloud autoscaling, canary traffic, automatic promotion or rollback, and presenting scripted
demo runs as model evidence.

## Contract

### System invariants

1. **Raw before score.** A provider response is durable before it is parsed or scored.
2. **No hidden retries.** Wrong, invalid, or unparseable responses are final. Only a transport
   failure before any response may be retried, under the Sprint 3 request rules.
3. **Content identity.** Every dataset snapshot, image, prompt, raw response, prediction file, gate
   report, and policy manifest has a SHA-256 digest.
4. **Immutable authority.** Raw responses and dataset snapshots live under content-addressed keys
   in a versioned write-once store. MLflow records the pinned version, URI, size, type, and digest.
5. **No mutable inputs.** Dataset, prompt, model, parser, scorer, overlay, and price-catalog
   versions are resolved before the first provider call.
6. **Two-way lineage.** Each Metaflow run records its MLflow run ID and each MLflow run records its
   Metaflow pathspec.
7. **Compatible comparisons only.** The UI disables promotion comparisons across different dataset
   fingerprints, scorers, target semantics, or primary metrics.
8. **Fail closed.** Missing usage, unknown price, missing latency, incomplete examples, forbidden
   dirty code, or unverifiable artifacts fail the relevant gate.
9. **Conjunctive gates.** Accuracy, cost, and latency must all pass the same versioned gate policy.
10. **Separate approval.** Passing gates makes a candidate eligible; it never approves or deploys
    it.
11. **Exact-version serving.** A deployment pins one immutable policy version; the server never
    resolves a moving alias per request.
12. **Atomic pointer change.** Deploy and rollback use a transactional compare-and-swap and append
    an audit event.
13. **Approved rollback only.** Rollback may select only a previously approved, loadable, healthy
    version.
14. **Registry aliases are views.** MLflow aliases and tags mirror control-plane state; the control
    plane is the ledger.
15. **Sprint 3 boundaries hold.** Target boxes stay build-time data, answers never reach the served
    policy, and invalid outputs are scored incorrect.
16. **Secrets stay out of evidence.** Credentials are injected at runtime and never enter flow
    artifacts, MLflow, logs, envelopes, screenshots, or Git.

### Components

| Component | Responsibility |
| --- | --- |
| Metaflow | Execution order, fan-out, joins, resume, task artifacts |
| MLflow | Experiment metadata, parameters, metrics, dataset references, prompt versions, policy packages |
| Control plane | Submissions, state transitions, gate decisions, approvals, deployments, rollback history, audit events; reads MLflow through `MlflowClient` only |
| Immutable store | Authoritative bytes for dataset snapshots, raw responses, and signed-off manifests |
| Serving API | Loads one exact approved manifest, passes a health check, then becomes active |

### Promotion gates and approval

A versioned, schema-validated gate policy is approved before candidate outputs are inspected. The
gate report records accuracy (point-inside-box on the full frozen set, with invalid and failed
requests scored incorrect), cost per 100 examples, p95 provider latency, and completeness. Equality
with a threshold passes. Gates evaluate stored full-precision values and round only for display.

Policy states: `Evaluated` → `GateFailed` (terminal) or `Eligible` → `Approved` (human, with a
reason) → `Deploying` → `Deployed` → `Superseded`, with rollback reactivating a superseded version.
Approval records actor, reason, time, gate-report digest, and policy ID. Deploy verifies the
approved immutable report rather than recomputing gates.

### Serving API v1

`POST /api/v1/ground`, `GET /api/v1/policy`, `GET /health/live`, `GET /health/ready`. Requests are
validated and bounded before any provider call. Responses carry the prediction, parse status, API
schema version, policy ID, deployment ID, exact policy version, and provider request ID, in both JSON
and headers. Provider failures surface as explicit errors with no fallback model. User screenshots
are not retained by default.

### Deploy and rollback

Deploy locks the candidate, verifies approval and all bound digests, starts a pinned instance, runs a
no-cost smoke request, then activates it in one compare-and-swap transaction before mirroring the
MLflow alias. Any earlier failure leaves traffic unchanged. Rollback resolves the previous deployment
from append-only history, reverifies it, and activates it the same way. Rollback appends events and
never rewrites history. Concurrent transitions produce one winner and a clear conflict.

### v5 stateful serving (`/api/v2`)

The v1 API is stateless and returns one point. A v5 policy is a session that needs the result of its
previous action before acting again. The v2 extension serves one approved v5 policy package per
deployment through the same approval, deployment, and rollback controls. It reuses the v5 per-step
transaction (journal, reducer, parser, checkpoints, retry classifier) described in the
[v5 benchmark design](v5-benchmark-design.md). The caller owns the environment. The server
validates every action against the PixelGym action contract, never executes an action, never sees
the task application, and caps provider spend per episode and per deployment. Policy code runs in
a credential-free subprocess under OS-level egress enforcement.

## Analysis and evidence

Platform evidence comes from the deterministic `scripted-demo` provider. It validates controls and
orchestration, not model quality, cost, or latency. A reviewer-safe bundle is exported from stored
data without rerunning a model or recomputing gates
([architecture](../artifacts/platform/architecture.md),
[known limitations](../artifacts/platform/known-limitations.md),
[fan-out evidence](../artifacts/platform/seed-policy-fanout-evidence-v1.json)). The
[million-episode design note](million-episode-design-note.md) separates measured local fan-out
behavior from capacity estimates.

A real-provider run needs an approved ten-example pilot with a hard call cap, a review of parser,
latency, cost, and redaction, and a second approval before any full run.

## Current status

| Stage | State |
| --- | --- |
| D4.1–D4.11: contracts, stack, storage, tracking, flow, gates, UI, control plane, serving, deploy and rollback, lifecycle rehearsal | Delivered |
| D4.12: milestone gate | Recorded as passed for the scripted provider at revision `4c4a7fb` only |
| v5 serving S1: scope and `/api/v2` shape | Approved |
| S2: frozen schemas and contracts | Delivered (`pixelgym/platform/stateful_contracts.py`) |
| S3: durable episode host with fake-policy coverage | Delivered (`pixelgym/platform/serving_episode.py`) |
| S4: `/api/v2` HTTP adapter and operational records | Delivered (`pixelgym/platform/stateful_service.py`) |
| S5: credential-free policy subprocess with local egress-denial proof | Delivered (`pixelgym/platform/policy_subprocess.py`) |
| S6: control-plane wiring and kind-aware runtime | Not delivered |
| S7–S8: v5 gate policy, first package, rehearsal, and gate | Not delivered; human-owned |

Known limitations: single-user local control plane; provider aliases can drift; write-once
retention protects versions but not availability; one synthetic form cannot establish general
grounding performance; approval is only as strong as the deployment's identity and access control;
local Compose does not demonstrate cloud availability or recovery. The control plane has no caller
authentication and must stay on loopback ([deployment guide](../deploy/README.md)).
