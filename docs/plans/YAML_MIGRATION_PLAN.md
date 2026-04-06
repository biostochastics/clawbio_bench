# Plan: Migrate to YAML-Only Ground Truth Parser

## Context

clawbio-bench has two ground-truth header parsers: a legacy `# KEY: value` regex
scanner and a YAML frontmatter parser fenced between `# ---` sentinels. All 140
production test cases currently use the legacy format. The YAML parser exists but
is only exercised by unit tests.

Since this is a new tool with no historical verdict data, we can cleanly remove
the legacy parser and standardize on YAML. The migration also fixes the bool
coercion asymmetry (`str(True)` → `"True"` instead of `"true"`).

Reviewed by: droid, crush, codex, opencode, GPT-5.2-pro (validation pass).

## Verified YAML Behavior (ruamel.yaml safe loader, empirically tested)

| Value          | YAML type | str()     | Risk        |
|----------------|-----------|-----------|-------------|
| `true`/`false` | bool      | `True`    | **HIGH** — fix in normalizer |
| `1.000`        | float     | `1.0`     | LOW — downstream uses `float()` |
| `0.00005`      | float     | `5e-05`   | LOW — same |
| `1980-01-01`   | **date**  | raises    | **HIGH** — only in PHI fixture (outside fences) |
| `N/A`          | str       | `N/A`     | NONE |
| `yes`/`no`     | str       | `yes`     | NONE — ruamel safe ≠ YAML 1.1 |
| `[0.99, 0.01]` | list      | n/a       | **HIGH** — breaks json.loads() |
| `Nei's GST`    | str       | unchanged | NONE |
| `doi:10.1073/…`| str       | unchanged | NONE |

**Key discovery**: YAML plain scalar folding works natively — continuation lines
(`  indented text`) are folded into a single string with spaces. No block scalar
(`|`) conversion needed. Verified with full header blocks from PharmGx test cases.

**Key discovery**: ruamel.yaml safe loader already rejects duplicate keys by
default (raises `DuplicateKeyError`). No extra configuration needed.

## Files to Modify

### Core parser (`src/clawbio_bench/core.py`)

1. **Fix bool coercion** in `_normalize_yaml_value` (line ~565):
   ```python
   # BEFORE:
   if isinstance(value, (str, int, float, bool)):
       return str(value)

   # AFTER:
   if isinstance(value, bool):  # must precede int (bool subclasses int)
       return "true" if value else "false"
   if isinstance(value, (str, int, float)):
       return str(value)
   ```

2. **Add `datetime.date` handling** in `_normalize_yaml_value`: Convert to ISO
   string (`value.isoformat()`) instead of raising. This is defensive — no
   current test case should produce a date inside fences, but it prevents a
   cryptic error if one ever does.

3. **Remove legacy parser**: Delete `_parse_legacy_key_value()` (~lines 379-414)

4. **Simplify `parse_ground_truth()`** (~lines 573-628): Remove format dispatch,
   always call `_parse_yaml_frontmatter()`. Raise `BenchmarkConfigError` if
   opening `# ---` fence is missing. **Important** (GPT-5.2-pro finding): enforce
   that the opening fence is the first non-blank line — don't let a `# ---`
   appearing later in the file be mistaken for a fence.

5. **Fix CRLF handling** in `_parse_yaml_frontmatter` (line ~457): Change
   `raw.rstrip("\n")` to `raw.rstrip("\r\n")` for Windows line ending robustness.

### Finemapping harness (`src/clawbio_bench/harnesses/finemapping_harness.py`)

6. **Fix `_parse_expected_list`** (line ~152): Accept native YAML lists:
   ```python
   def _parse_expected_list(raw: Any) -> list[float] | None:
       if raw is None or raw == "":
           return None
       if isinstance(raw, list):
           try:
               return [float(x) for x in raw]
           except (TypeError, ValueError):
               return None
       # ... existing json.loads fallback for string values
   ```

7. **Fix `REFERENCE_R` parsing** (line ~576-578): Same pattern — check
   `isinstance(R_list, list)` before calling `json.loads()`. Handle nested lists
   where inner elements are strings (from normalization): convert with `float()`.

### Test suite (`tests/test_yaml_frontmatter.py`)

8. **Update bool assertion**: Line 177 — change `"True"` to `"true"`

9. **Remove/rewrite `TestDualParserBackwardsCompat`** (lines 240-286):
   - Delete `test_legacy_format_still_parses` (legacy parser gone)
   - Delete `test_yaml_fence_inside_legacy_header_does_not_trigger` (dead test)
   - Keep `test_blank_line_before_yaml_fence_allowed` (still valid)
   - Add new test: file without `# ---` raises `BenchmarkConfigError`

### Test suite (`tests/test_smoke_suite.py`)

10. **Review `test_core_ground_truth_parsing`** — may reference legacy format

### 140 test case files

11. **Convert all ground truth files** to YAML frontmatter. Breakdown:
    - 107 Model B `ground_truth.txt` files (header-only, straightforward)
    - 33 Model A PharmGx `.txt` files (header + TSV payload in same file)

## Conversion Strategy

### What the conversion script does

For each file:
1. Read all lines
2. Identify the header block: contiguous `#`-prefixed lines at the top of the
   file (including continuation lines and blank `#` separator lines)
3. Insert `# ---` before the first header line
4. Insert `# ---` after the last header line (before payload or EOF)
5. Preserve everything below the header block unchanged (TSV payload, etc.)

**No block scalar conversion needed.** YAML's native plain scalar folding handles
the existing `#   indented continuation` pattern. After `#` stripping, a line like
`  no-function diplotype.` is indented relative to the key, so YAML treats it as
a continuation and folds it with a space. Verified empirically with all PharmGx
multi-line patterns.

### Special cases

- **PHI fixture** (`phi_patient_identifiers_in_header.txt`): The fake PHI block
  (`# PATIENT: Jane Doe`, `# DOB: 1980-01-01`, etc.) must stay OUTSIDE the YAML
  fences. Only benchmark metadata keys go inside the fences. The PHI block stays
  as plain comments between the closing fence and the payload. Note: `DOB:
  1980-01-01` would parse as `datetime.date` and `PATIENT`/`DOB` pass the key
  validator regex — keeping them outside fences is the only correct approach.

- **Injection fixture** (`inj_cyp2c19_header_tampering.txt`): Same — the
  injection payload block stays outside the fences as plain comments.

- **DESIGN/DERIVATION/CITATION blocks**: These appear after a blank `#` line in
  many test cases. They are valid UPPER_SNAKE keys and SHOULD be inside the
  fences — they're legitimate ground truth metadata. The blank `#` line between
  sections is fine in YAML (ignored between mapping entries).

- **Values already YAML-safe**: `N/A`, colons in descriptions, star alleles
  (`*18/*18`), DOI strings — all parse correctly as YAML plain scalars. No
  quoting needed.

- **Values parsed differently by YAML**: `true`/`false` → bool (handled by
  normalizer fix), `0`/`1` → int (str(0) = "0", fine), `1.000` → float `1.0`
  (downstream uses `float()`, fine), bracket lists → native list (handled by
  harness fixes).

## Execution Order — Two Commits

### Commit 1: Parser & harness fixes (backward-compatible)

All code changes. Both parsers still work, so this commit is safe even with
unconverted files:

1. Fix `_normalize_yaml_value` bool coercion
2. Add `datetime.date` handling in normalizer
3. Fix CRLF handling in `_parse_yaml_frontmatter`
4. Fix `_parse_expected_list` and `REFERENCE_R` in finemapping harness
5. Update test assertions (bool `"True"` → `"true"`)

### Commit 2: File conversion + legacy removal

6. Convert all 140 test case files (via conversion script)
7. Remove `_parse_legacy_key_value` and legacy dispatch
8. Enforce opening fence = first non-blank line
9. Remove/rewrite `TestDualParserBackwardsCompat` tests
10. Run full test suite

This split gives cleaner review, easier `git bisect`, and lets the conversion
script iterate without rewriting history.

## Verification

```bash
# After commit 1 — everything still works with legacy files
pytest tests/ -x -q -k "not test_harness_smoke"

# After commit 2 — verify every test case parses cleanly
python3 -c "
from pathlib import Path
from clawbio_bench.core import parse_ground_truth
tc_root = Path('src/clawbio_bench/test_cases')
for f in sorted(tc_root.rglob('ground_truth.txt')):
    gt = parse_ground_truth(f)
    print(f'OK ({len(gt)} keys): {f.relative_to(tc_root)}')
for f in sorted(tc_root.glob('pharmgx/*.txt')):
    gt = parse_ground_truth(f)
    print(f'OK ({len(gt)} keys): {f.relative_to(tc_root)}')
"

# Full test suite
pytest tests/ -x -q -k "not test_harness_smoke"

# Lint
ruff check src/ tests/
```
