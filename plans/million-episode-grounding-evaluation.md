# What changes at 10^6 grounding episodes

This note deliberately separates a measured local demonstration from capacity estimates and a
production design. It makes no production-throughput claim from one laptop.

## Measured behavior

The checked-in structured evidence `artifacts/platform/seed-policy-fanout-evidence-v1.json` is the
source for this section. Revision `a817d611f5c62a7aebaf12cc3b8034d2768b34f1` ran an explicit 16-assignment
seed-by-policy plan with a worker cap of 4. The branches contain
80 deterministic scripted-provider records, including
4 invalid-output assignments and
4 request-failure assignments.

The stored timestamps yield 24 intersecting branch pairs and a
maximum of 4 simultaneously active branch intervals.
The generator computed 1140.025 ms of serial-equivalent
branch work, 3065.983 ms of observed parallel branch-window time,
5756.461 ms total flow wall time, and
4.598 ms in the join. These are local measurements, not capacity or
service-level objectives. CPU count, Python and Metaflow versions, raw branch timestamps, queue
durations, and resume events are retained in the same evidence file.

The injected-failure run resumed once. Its provider ledger records
80 attempts,
80 unique request IDs, and
80 completed scripted operations. The
equality of these counts is the evidence that resume did not repeat completed provider work.

## Estimated capacity (not measured throughput)

The canonical aggregate is 17711 bytes for 16 assignments, or
1106.9 bytes per assignment in this small fixture. A linear metadata-only
estimate for 1,000,000 assignments is 1106937500 bytes. This estimate excludes screenshots,
raw model payload growth, indexes, replication, object-version overhead, logs, and compression; its
basis is only the checked-in aggregate byte count.

A planning partition size of 1000 assignments would create approximately
1000 partitions for 1,000,000 assignments. Both values are design estimates chosen to
bound retry and listing scope; they are not derived from local throughput and must be load-tested
against the selected orchestrator and stores.

## Required architectural changes

- **Orchestration and partitioning:** materialize the canonical plan in a durable scheduler, split it
  into estimated 1000-assignment partitions, and use hierarchical joins rather than a
  million-way local foreach. Preserve assignment IDs as idempotency keys.
- **Object and metadata stores:** move raw envelopes and aggregates to versioned object storage and
  assignment state to a transactional metadata store. Never pass a million results through one task
  artifact or one filesystem directory.
- **Backpressure, rate limits, and spend governance:** admit work through bounded queues with
  per-provider and per-tenant limits. Reserve budget before dispatch, reconcile actual usage after
  responses, and stop dispatch when rate or spend ceilings are reached.
- **Retries:** distinguish transport attempts from billable operations, require provider-side
  idempotency where available, use capped exponential backoff with jitter, and send exhausted
  assignments to a reviewable dead-letter state. Completed assignment evidence remains immutable.
- **Observability:** emit queue age, active partitions, attempts, cache hits, provider latency, spend,
  failure class, and join lag keyed by plan, partition, assignment, seed, and policy identities.
- **Retention:** define separate lifecycle policies for prompts, compact aggregates, raw responses,
  screenshots, and operational logs; legal/privacy review sets the periods. The local put-once store
  does not establish production WORM retention.
- **Failure domains:** isolate provider, region, scheduler, object-store, metadata-store, and policy
  failures. Use checkpointed hierarchical joins, reconcile orphaned leases, and test regional and
  store outages before assigning an availability objective.

No paid calls, external infrastructure, production time estimate, or D4.12 verdict is represented
by this note.
