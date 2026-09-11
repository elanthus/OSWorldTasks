# Verify the canonical grounding report

Use this check to confirm that the `gpt-5.4-mini` headline matches the frozen evidence without
changing tracked artifacts. Python 3.12 and the repository's development environment are the only
prerequisites; the command makes no network or model calls.

```bash
.venv/bin/python scripts/verify_grounding_report.py
```

A successful run exits 0 and prints one JSON object with `"status": "verified"` and
`"wrote_files": false`. A missing file or any identity, sample-size, statistic, or digest mismatch
exits 1 and names the failed check. The command does not create a temporary directory because it
does not generate intermediate artifacts.

The verifier checks three distinct properties:

1. It recomputes the sample counts, raw and marks accuracy, paired difference, fixed-seed bootstrap
   interval, exact McNemar value, proposal coverage, conditional mark-selection accuracy,
   invalid-output and request-failure counts, slices, error taxonomy, latency, and every stored
   per-example outcome from the frozen dataset, predictions, and error reviews.
2. It checks that the canonical report and results identify the same protocol, prompt, provider,
   model, and experiment recorded in the versioned provenance file.
3. It verifies the recorded SHA-256 digest of every frozen input image and the checked-in report,
   results, figures, and gallery manifest. This proves integrity of the stored rendered files.

The third check is not a cross-environment rendering guarantee. Pillow, FreeType, and the platform
rendering stack are not fully pinned, so this repository does not claim that regenerating a PNG on
another machine produces identical bytes. Numerical recomputation is supported; rendered-image
verification means comparing the checked-in bytes with their recorded SHA-256 digests.

## Intentional report generation

Report generation is a separate, write-producing maintenance operation:

```bash
.venv/bin/python scripts/generate_grounding_report.py
```

The canonical report's embedded "Offline reproduction" block predates this verify/generate split
and therefore names the writing command. Reviewers should use the verification command at the top
of this guide.

That command writes the results JSON, Markdown report, figures, gallery images, and gallery
manifest under `artifacts/`. It is not the reviewer verification path. Because analysis code and
derived schemas can evolve while the raw evidence remains frozen, review every generated diff and
version a changed derivative or provenance record explicitly; do not replace historical evidence
or update an expected digest only to make verification succeed.

The [report provenance](../artifacts/grounding-report-provenance-v1.json) records the canonical
report/results revisions and hashes, the preserved Haiku/Gemini report lineage, and the precise
rendered-image comparison supported here.
