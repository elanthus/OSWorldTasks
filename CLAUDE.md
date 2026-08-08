# CLAUDE.md

@AGENTS.md

All project instructions for this repository live in [AGENTS.md](AGENTS.md) — read it in full before making any change. It is the single source of truth for scope, invariants, human gates, and reporting standards.

Claude-specific notes:

- **Before coding**, read the relevant day plan in [plans/](plans/) and follow its `Owner`, `Suggested agent handoff`, and `Done when:` clauses. The plan defines the task; AGENTS.md defines how to work.
- **Reasoning effort** is assigned per task in the plans. `AGENT · high` tasks (environment semantics, evaluator boundary, OSWorld integration, determinism interpretation, statistics) warrant extended thinking; `AGENT · medium` tasks do not.
- **Stop at human gates.** Tasks marked `YOU` or `PAIR` in the plans — scope changes, day acceptance gates, provider/cloud spend, any paid model call, public claims — require explicit approval. Prepare the work, report, and wait.
- **Gates get raw results, not verdicts.** For the Day 1/2/3 acceptance gates, run the documented checks and hand back the raw evidence: command, exit status, counts, full output. Do not summarize as passing, do not offer a provisional PASS/FAIL, do not tick the checklist boxes. The human grades the gate.
- **Do not weaken an invariant to make a task pass.** The invariants in §3 of AGENTS.md are the project's claim. If one blocks you, surface it as a question.
- **Report honestly.** State the tests actually run with counts and runtime, the files changed, and anything left incomplete.
