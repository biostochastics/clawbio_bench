# Verdict Schemas

This directory contains **auto-generated JSON Schema 2020-12 artifacts** that
describe the canonical shape of clawbio_bench verdict documents. These files
are the **external, language-agnostic contract** for the chain-of-custody
JSON format: auditors can validate verdict files against these schemas
without running any Python code.

## Files

| File | Describes | Generator |
|---|---|---|
| `verdict-minimal.schema.json` | Minimum required shape. All verdicts — including stripped-down `harness_error` docs — must validate against this. | `clawbio_bench.schemas.MinimalVerdictDoc` |
| `verdict-full.schema.json` | Full shape emitted by `core.build_verdict_doc()`. Every successfully-executed test case must validate against this. `additionalProperties: false`. | `clawbio_bench.schemas.FullVerdictDoc` |

## Regenerating

The schemas are generated from the `msgspec.Struct` definitions in
`src/clawbio_bench/schemas.py`. Whenever those Structs change, regenerate the
artifacts and commit them:

```bash
python scripts/gen_schemas.py
git add schemas/
git commit -m "chore: regenerate verdict schemas"
```

## CI drift gate

`tests/test_schemas.py::TestCommittedSchemas::test_committed_schemas_match_generator`
runs in CI and fails if the committed schemas ever drift from what the
current `msgspec.Struct` definitions would produce. This catches the case
where someone modifies a Struct without regenerating the schema — a silent
contract break that would otherwise only surface when a downstream auditor
tries to validate a verdict.

## Why JSON Schema as a committed artifact?

clawbio_bench is an **audit tool**. Its whole value proposition rests on
producing verdict documents that are tamper-evident and independently
verifiable. Shipping the schema as a versioned JSON file means:

1. **Language-agnostic**: an auditor using Rust, Go, or a pure JSON Schema
   validator can verify verdicts without running Python.
2. **Diff-reviewable**: `git diff schemas/verdict-full.schema.json` shows
   exactly what changed in the contract when the Structs evolve.
3. **Versionable as a first-class artifact**: future schema breakage gets a
   deliberate version bump visible in git history, not a buried Python diff.
4. **Minimal trust surface**: auditors don't need to trust `msgspec`, the
   Python interpreter, or any of our code — just the committed JSON Schema
   file. The Python layer is an implementation detail.

## Schema versioning

The current schema tracks the verdict format documented by `BENCHMARK_VERSION`
constants in each harness. When a breaking change lands (e.g., the Phase 5
orjson/msgspec serializer switch), the schema will be versioned as
`verdict-full-v2.schema.json` alongside the existing v1 file, and
`core.verify_verdict_file()` will route by the `verdict_format` field inside
the document itself.
