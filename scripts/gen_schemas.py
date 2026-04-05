#!/usr/bin/env python3
"""Generate JSON Schema artifacts from the msgspec Struct definitions.

Writes JSON Schema 2020-12 documents under ``schemas/`` so auditors have a
language-agnostic canonical contract for verdict documents. Run this script
whenever ``src/clawbio_bench/schemas.py`` is modified, then commit the
updated files. CI runs ``tests/test_schemas.py::test_committed_schemas_match``
to detect drift between the committed artifacts and the msgspec Structs.

Usage:
    python scripts/gen_schemas.py

Output:
    schemas/verdict-minimal.schema.json
    schemas/verdict-full.schema.json
"""

from __future__ import annotations

import sys
from pathlib import Path

import msgspec

# Make the src/ layout importable when running this script directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from clawbio_bench.schemas import generate_json_schemas  # noqa: E402

SCHEMAS_DIR = REPO_ROOT / "schemas"


def _canonical_bytes(obj: dict) -> bytes:
    """Stable pretty-printed JSON bytes for diff-friendly committed files.

    Uses stdlib json for the schema files specifically (not msgspec) because
    we want 2-space indentation and a trailing newline for clean git diffs —
    which msgspec.json.encode doesn't produce. These files are NOT verdict
    bytes, so they don't participate in chain of custody.
    """
    import json

    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main() -> int:
    SCHEMAS_DIR.mkdir(exist_ok=True)
    schemas = generate_json_schemas()
    for name, schema in schemas.items():
        path = SCHEMAS_DIR / f"{name}.schema.json"
        payload = _canonical_bytes(schema)
        path.write_bytes(payload)
        print(f"  wrote {path.relative_to(REPO_ROOT)} ({len(payload)} bytes)")
    # Sanity: round-trip through msgspec to prove the schemas parse back.
    for schema in schemas.values():
        msgspec.json.encode(schema)  # raises if anything is unserializable
    print(f"\nGenerated {len(schemas)} schema artifacts in {SCHEMAS_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
