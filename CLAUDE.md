# CLAUDE.md

<!-- The next line imports AGENTS.md into Claude Code's context so both files share one policy source. -->
@AGENTS.md

All project instructions for this repository live in [AGENTS.md](AGENTS.md) — read it in full before making any change. It is the single source of truth for scope, invariants, human gates, and reporting standards.

Claude-specific notes:

- **Reasoning effort.** Tasks touching environment semantics, the evaluator boundary, OSWorld integration, determinism interpretation, or statistics warrant extended thinking; bounded implementation with an explicit test does not.
- **Stop at human gates.** Scope changes, milestone gates, provider/cloud spend, any paid model call, and public claims require explicit approval. Prepare the work, report, and wait.
- **Gates get raw results, not verdicts.** For the Sprint 1/2/3 acceptance gates (D1.8, D2.11, D3.11), the Milestone 4 platform gate (D4.12), and the v5 gates (D5.x, ending with the D5.10 verdict), run the documented checks and hand back the raw evidence: command, exit status, counts, full output. Do not summarize as passing, do not offer a provisional PASS/FAIL, do not tick the checklist boxes. The human grades the gate.
- **Do not weaken an invariant to make a task pass.** The invariants in [docs/environment-contract.md](docs/environment-contract.md) are the project's claim. If one blocks you, surface it as a question.
- **Report honestly.** State the tests actually run with counts and runtime, the files changed, and anything left incomplete.
