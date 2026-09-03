# v5 D5.6 twin_b floor-audit evidence — Gemini full calibration run

**Status:** evidence preparation only. This packet renders no verdict on whether twin_b variants
are defective, on task admission, or on any difficulty change. Those decisions are owned by the
human under the v5 plan's floor-audit routing.

## Provenance

- Source journal: `artifacts/grounding-v5-d56-gemini-full-calibration-run/attempts.sqlite`
  (frozen run; opened read-only via SQLite URI `mode=ro`; no frozen artifact was modified).
- Task metadata: `artifacts/grounding-v5-manifests/calibration-d56.json`
  (manifest digest `sha256:41034ec1ddaf31be37e54d0fe2889cb1b9e48763504ec04857b03cae3aa8f6f9`).
- Episode classifications: `artifacts/grounding-v5-d56-gemini-full-calibration-run/summary.json`.
- Screenshots are decoded from the journal's raw RGB `1024x768x3` screenshot objects and saved as
  PNG without scaling, masking, or tolerance. No provider calls were made. No canonical provider
  response bytes were extracted into this packet (they remain restricted in the local journal).
- Machine-readable index with every digest-derived fact: `audit-index.json`.

## Scope

All robustness twins with an attempted `twin_b` in the Gemini v2 full calibration run:

| Logical pair | twin_a outcome | twin_b outcome | twin_b stall stage |
|---|---|---|---:|
| conditional_precedence-logical-01 | success | step-limit truncation | 4 |
| deferred_join-logical-01 | success | step-limit truncation | 1 |
| review_and_commit-logical-01 | success | step-limit truncation | 1 |
| revision_after_reveal-logical-01 | success | step-limit truncation | 4 |
| visible_error_recovery-logical-01 | success | step-limit truncation | 1 |
| review_and_commit-logical-00 | (twin_a not in this partition) | step-limit truncation | 1 |
| visible_error_recovery-logical-00 | (twin_a not in this partition) | step-limit truncation | 1 |

"Stall stage" is the stage index of the episode's final dispatch. The two `logical-00` twin_a
variants were excluded from the calibration partition by the Qwen-pilot exposure rule, so no
twin_a comparison exists for them in this run.

## Files

For each group (43 PNGs total):

- `<pair>__twin_b__initial__stage0.png` — initial screenshot before the first action.
- `<pair>__twin_b__stall-entry__stage<k>__step<NN>.png` — first frame at the stall stage.
- `<pair>__twin_b__post-focus__stage<k>__step<NN>.png` — first frame after the policy's first
  `text_input_focused` action at the stall stage.
- `<pair>__twin_b__final-frame__stage<k>__step<NN>.png` — last frame of the episode.
- `<pair>__twin_a__initial__stage0.png`, `<pair>__twin_a__same-stage__stage<k>__step<NN>.png`,
  `<pair>__twin_a__post-focus__stage<k>__step<NN>.png` — the paired variant's corresponding
  frames, where a twin_a run exists.

## Raw observations (no interpretation)

All values are raw differences before any tolerance; full numbers per pair in `audit-index.json`.

1. **Instructions are identical within every pair** (byte-equal strings recovered from the initial
   policy checkpoints).
2. **The rendered screens differ only by the designed control-order swap.** At the stall stage the
   text-entry control is the top control (center ~y=459) in twin_a and the bottom control
   (center ~y=531) in twin_b; the cross-variant post-focus diff is 70,372 pixels with max
   per-channel delta 225, confined to the two control rectangles.
3. **Focus is rendered with identical contrast in both variants:** entry-frame vs post-focus-frame
   differs by exactly 34,679 pixels with max per-channel delta 11 in every audited episode of both
   variants, localized to the focused field's own rectangle (twin_a x192–832, y432–486;
   twin_b x192–832, y504–558).
4. **The policy hit the semantically correct control in both variants.** Parsed actions at the
   stall stage: twin_a episodes click the field once or twice, then emit KEY actions
   (`text_input_focused` dispatches) and proceed. twin_b episodes emit only CLICKs on the field
   (e.g., `CLICK(512,530)`/`CLICK(512,531)` 25–29 times) and no KEY action at the stall stage,
   with one exception (conditional_precedence twin_b emitted one KEY at stage 4 before truncation).
5. **The screen was bitwise static during the twin_b loops:** post-focus vs final frame differs by
   0 pixels in 6 of 7 twin_b episodes (conditional_precedence twin_b, which progressed late in the
   episode, differs by 1,699 pixels).
6. Both attempted regression-canary tasks in the run were twin_b variants and both truncated;
   7 of 8 attempted ceiling probes succeeded. Robustness pairs with both variants attempted are
   0/5 concordant, discordant in the same direction (twin_a success, twin_b truncation) each time.

## Extraction counts

- Episodes audited: 7 twin_b, 5 twin_a (12 trials).
- PNGs written: 43. Index entries: 7. Journal rows read: dispatch_committed, attempt_started,
  initial_screenshot, parsed_action_candidate events plus referenced objects for these 12 trials.
- Frozen run directories modified: none.
