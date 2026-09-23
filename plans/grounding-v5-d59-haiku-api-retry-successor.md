# D5.9 Haiku zero-API-retry successor

**Status:** response-free successor candidate awaiting a new execution decision. The stopped
campaign is excluded from benchmark scoring and retained only as invalid infrastructure evidence.
No assignment was restarted or replayed, no provider call was made while preparing this successor,
and execution remains disabled.

The stopped campaign exposed a transport-policy defect: Claude Code CLI performed two hidden API
retries inside one runner attempt. The runner correctly stopped when it observed the unauthorized
`system/api_retry` event, but its process-level counters could not count those internal provider
requests. The public, response-free
[discard receipt](../artifacts/grounding-v5-d59-haiku-api-retry-successor/discarded-run.json)
records 18 runner attempts, two CLI retry events, and therefore at least 20 provider API attempts.
It excludes prompts, screenshots, responses, session identifiers, process identifiers, and private
paths.

## Narrow bugfix

The successor sets `CLAUDE_CODE_MAX_RETRIES=0` in the sanitized Claude child-process environment
and binds that value into each policy manifest. This prevents the CLI from initiating an
unbudgeted retry. The parser remains fail-closed: any future `api_retry` event is still an
unauthorized system event and stops the campaign.

Nothing else changes. The model, provider route, prompt, action parser, admitted task manifest,
history reducer, runner-level timeout retry, reliability schedule, statistical comparison,
mechanical caps, runtime proposal, and approved OS-sandbox exception are inherited unchanged from
the predecessor. The successor uses new `d59-haiku-r2-*` trial identifiers and reuses no outcome
from the stopped run.

## Candidate and human boundary

The new [execution plan](../artifacts/grounding-v5-d59-haiku-api-retry-successor/execution-plan.json)
has digest
`sha256:c9535097f9ca23703c72fea3ed0bb32532e59582daddea0f716344c6d07f80ae`.
Its candidate ceilings remain:

| Phase | Environment actions | Model attempts | Provider wire requests |
| --- | ---: | ---: | ---: |
| Primary | 10,830 | 21,660 | 21,660 |
| Reliability | 1,304 | 2,608 | 2,608 |
| Aggregate | 12,134 | 24,268 | 24,268 |

Provider control requests remain capped at zero. The proposed aggregate runtime window remains 168
hours. These values are candidates only: approved attempt and wire-request caps are zero, and
subscription execution is not authorized.

Before any call, the owner must approve the exact successor digest, nonzero caps no larger than the
candidate ceilings, and the runtime window. Any other policy or workload change requires another
versioned successor. The D5.10 verdict and public model-quality or security claims remain
human-owned.

## Reproduction

The verification command reads only checked-in response-free evidence and makes no provider calls:

```sh
.venv/bin/python -m scripts.prepare_grounding_v5_d59_haiku_retry_successor \
  --source-revision 4f0b62d64293443d68154f5880d0136d1de7bc74 --verify
```
