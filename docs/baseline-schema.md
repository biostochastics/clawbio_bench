# Baseline schema

The `--render-markdown` mode of `clawbio-bench` can optionally consume a
*baseline* aggregate report to classify current findings as **new**,
**resolved**, or **unchanged**. This document describes the minimum shape a
baseline file must satisfy, so downstream tooling (or a custom publisher) can
produce compatible baselines without reverse-engineering the renderer.

The baseline is just a regular `aggregate_report.json` produced by a normal
`clawbio-bench` run. The renderer only reads a strict subset of its fields;
everything else is ignored and safe to extend.

## File location

Either:

- A path to an `aggregate_report.json` file directly, or
- A path to a directory containing `aggregate_report.json` at its root.

Both forms are accepted by `--baseline`.

## Minimum required structure

```jsonc
{
  "benchmark_suite_version": "1.0.1",  // rendered in footer; optional
  "date": "2026-04-04",                 // rendered in header; optional
  "clawbio_commit": "abc12345",         // shown in the "vs. baseline" heading; optional
  "mode": "smoke",                      // informational; optional

  "harnesses": {
    "<harness-name>": {
      "critical_failures": [
        {
          "test": "<test case name>",      // REQUIRED for diffing
          "category": "<verdict category>", // REQUIRED for diffing
          "rationale": "<free text>"        // rendered but not used for identity
        }
      ]
    }
  }
}
```

Only two fields per finding are load-bearing: `test` and `category`. The
harness key (the top-level `harnesses.<name>` object key) is the third
component of a finding's identity.

## Finding identity

The renderer computes a stable identity key for each finding:

```
(harness_name, test_name, category)
```

This has two consequences downstream consumers should be aware of:

1. **Rationale changes do not count as a new finding.** If a test at the same
   `(harness, test, category)` emits a reworded rationale, the finding is
   classified as *unchanged*. This is desirable: rationale strings often
   contain volatile data (file paths, timestamps, subprocess stderr) that
   should not flap the diff.

2. **Category changes count as both a resolve AND a new finding.** Flipping a
   test from `fst_incorrect` to `fst_mislabeled` at the same `(harness, test)`
   emits one *resolved* entry for the old category and one *new* entry for the
   new category. This is desirable because clawbio-bench categories encode
   *remediation*, not severity: changing category is a meaningful transition,
   not noise.

## Producing a baseline

The simplest (and intended) path is to run the suite with `--smoke` and copy
the resulting `aggregate_report.json`:

```bash
clawbio-bench --smoke --repo /path/to/ClawBio --output /tmp/results
cp /tmp/results/aggregate_report.json ./my-baseline.json
```

In CI, `audit-baseline.yml` in this repository publishes a rolling
`main`-branch baseline as a GitHub Release asset at:

    https://github.com/biostochastics/clawbio_bench/releases/download/baseline-main/aggregate_report.json

The reusable audit workflow (`audit-reusable.yml`) downloads that URL by
default. Pass a different `baseline_url` input to override, or the empty
string to disable baseline diffing entirely.

## Notes and non-guarantees

- **No schema version is enforced.** The renderer skips missing fields rather
  than rejecting files, so older or partial baselines will still work — they
  just produce less informative output.
- **Unknown fields are ignored.** You can add your own keys (e.g. CI metadata,
  provenance information) without breaking downstream consumption.
- **Baselines are trust-on-first-read.** There is no signature verification.
  Only consume baselines you (or your CI) produced.
- **Truncation.** If a baseline was produced with a truncated
  `critical_failures` list (older `clawbio-bench` versions capped at 10 per
  harness), findings not present in the baseline will appear as *new* on the
  diffing side. Re-publish the baseline with a current version to avoid this.
