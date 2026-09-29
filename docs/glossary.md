# Glossary

| Term | Meaning |
| --- | --- |
| Sprint 1, 2, 3 | The three core delivery phases: environment core, OSWorld integration and validation, and the grounding experiment. |
| Day-N (`artifacts/day-1/`, `day-2/`, `day-3/`) | Artifact directory names for Sprint 1, 2, and 3 evidence. Day-N and Sprint N refer to the same phase; `day-2-rev-*` directories are later revisions of Sprint 2 evidence. |
| DN.M (for example D1.8, D4.12, D5.9) | Deliverable M of sprint or milestone N. D1.8, D2.11, D3.11, D4.12, and D5.10 are the human-decided gates for Sprint 1, Sprint 2, Sprint 3, Milestone 4, and the v5 benchmark. |
| Milestone 4 | The local-first grounding-evaluation and policy-delivery platform built around the frozen Sprint 3 workload; see the [platform design](platform-design.md). Its deliverables are D4.1–D4.12. |
| Grounding v1 | The frozen Sprint 3 paired experiment (100 examples, raw coordinates versus set-of-marks) behind the README headline. |
| Grounding v2 | A crossed target-by-state allocation that removes v1's target/state aliasing; designed but not run against a model. |
| Grounding v3, v3a, v3b, v3c | Historical prompt-v2 calibration experiments with Haiku and Gemini. v3a uses the vendor form, v3b a dense form with near-duplicate labels and small controls, and v3c a data table with repeated identical buttons. |
| Grounding v4, v4b, v4c | Multi-step pilots on the vendor form: v4 the first pilot, v4b a multistep pilot, and v4c a longer-horizon pilot whose saturation motivated v5. |
| Grounding v5 | The stateful end-to-end agent benchmark (`pixelgym-agent-v5`); see the [v5 benchmark design](v5-benchmark-design.md). Its deliverables are D5.1–D5.10. |
| S1–S8 | Stages of the v5 stateful serving extension (`/api/v2`); S1–S5 are delivered. See the [platform design](platform-design.md#current-status). |
| Slot C | The third policy slot in the D5.6 calibration panel, after slot A (Gemini) and slot B (Qwen). It was planned for Llama and later filled by Mistral Small 4. |
| r1, r2, r3 | Trial-ID generations for the D5.9 Haiku runs: r1 is the original `d59-haiku-` prefix, r2 (`d59-haiku-r2-`) the zero-API-retry successor, and r3 (`d59-haiku-r3-`) the counted network-retry candidate. Each generation keeps its attempts separate from earlier ones. |
| Successor | A new, versioned freeze or plan that replaces an earlier one after a change. The predecessor and its evidence are kept unchanged. |
| PR196 | The Luna/Haiku calibration named after pull request #196 that introduced it; see its [report](../artifacts/grounding-v5-pr196-calibration/report.md). |
| Luna, Terra, Sol | Model codenames: Luna is `gpt-5.6-luna`, Terra is `gpt-5.6-terra`, and Sol is `gpt-5.6-sol`. They are model names, not hosts. |
| `twin_a`, `twin_b` | The two members of a robustness pair: the same task semantics rendered through two target-independent wording, order, or layout variants. `twin_a` is the primary representative; `twin_b` is resampled with it as one cluster. |
| Stateful policy (screenshot history) | A policy that keeps episode-local state, such as earlier screenshots and its own notes, between actions within one episode. |
| Stateless policy (current-frame only) | A reference policy that sees only the current screenshot and instruction at each action; used to test whether episode-local state contributes to success. |
