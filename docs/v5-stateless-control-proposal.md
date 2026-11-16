# Proposal: a v5 control in which the stateless arm can succeed

- **Status:** proposal only, written 2026-11-15. Nothing here has been run, admitted, or approved.
  Running it needs a new generator version (a scope decision) and a separate approval for every
  model call.
- **Why:** in all three D5.9 Haiku campaigns the two consumer choices show only "The source
  <request|verification> reference is no longer visible." A stateless policy that is otherwise
  perfect succeeds 1 time in 9 (21.3 of 192), the best fixed on-screen positions reach 26 of 192,
  and the best fixed label-order rule pair, chosen in-sample, reaches 30 of 192
  ([chance supplement v2](../artifacts/grounding-v5-stateless-chance-ceiling-v2.md)). The
  stateful-versus-stateless comparison therefore could not have come out the other way. It cannot
  tell "the history arm remembers" apart from "the stateless harness fails for some other reason".

## Design

Add one task variant and run both arms on it.

| Cell | Task variant | Arm | Can the stateless arm succeed? |
| --- | --- | --- | --- |
| A | deferred (current D5.9 tasks) | history | not applicable |
| B | deferred (current D5.9 tasks) | stateless | only by guessing (1/9) |
| C | reference-visible (new) | history | not applicable |
| D | reference-visible (new) | stateless | yes, from the current screen |

The reference-visible variant is the same 192 frozen seeds with one change. On each of the two
consumer stages, the fact "The source <request|verification> reference is no longer visible." is
replaced by the reference itself, for example "Request reference: G73.". Controls, their order,
targets, step limits, the instruction, and every other stage stay byte-identical. It is a new
generator version, so it needs no-cost admission (golden policy earns 1.0 exactly once, near-miss
and mutation policies earn 0.0, bitwise-identical resets) before any call.

What each reading would mean, stated before any result exists:

- Memory explains the gap if D is close to C. The deferred gap A − B then exceeds the visible gap
  C − D, which is the interaction contrast (A − B) − (C − D).
- The stateless harness fails for another reason if D stays near B. The D5.9 gap then says little
  about memory.
- A null result is possible: C − D can be zero or negative, and so can the interaction.

The primary test and minimum relevant difference would be frozen in a pre-registration beside the
D5.9 one before calls. A natural choice is the existing two-sided exact McNemar test at alpha 0.05
on cells C and D, with the 20-point minimum relevant difference, plus the interaction with a
family-stratified cluster bootstrap. This proposal does not pick one.

## Calls and time

Estimates come from the stored campaign reports. The history arm averaged 15.2 (replication) to
25.0 (parser sensitivity) model attempts per primary episode. The stateless arm averaged 25.9 to
27.6, mostly by running to the step limit. The frozen step limits sum to 5,415 actions per arm,
and two attempts are allowed per action. Both campaigns ran at about 3.8–4.0 s of wall time per
attempt with four workers.

| Option | Cells | Expected attempts | Mechanical cap | Expected wall time | Wall time at cap |
| --- | --- | ---: | ---: | ---: | ---: |
| Control cells only | C, D | 5,800–10,100 | 21,660 | 6–11 h | about 24 h |
| Full 2×2, fresh | A, B, C, D | 15,900–20,200 | 43,320 | 17–22 h | about 47 h |

The expected range for C and D assumes both arms behave like the history arm once the reference is
visible (15.2–25.0 attempts each, 192 episodes per cell). The upper end assumes D still runs to the
step limit (27.6). For the full 2×2, A and B are taken at the parser-sensitivity campaign's means
(25.0 and 27.6). A four-assignment pilot before either option would cost at most 256 attempts (four episodes,
step limit at most 32, two attempts per action).

The full 2×2 avoids comparing cells run weeks apart under different CLI versions and network
conditions; the control-only option is cheaper but compares C and D with the archived A and B
campaigns.

## Not decided here

Provider route, model snapshot, harness revision, caps, runtime window, the pre-registration text,
and whether to run at all are the owner's decisions.
