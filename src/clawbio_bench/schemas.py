# SPDX-License-Identifier: MIT
"""msgspec Struct definitions for verdict documents.

These Structs serve three purposes:

1. **Runtime validation** via ``msgspec.convert(dict, type=Struct)``, which
   replaces the hand-rolled type checks in ``core.validate_verdict_schema``.
2. **Deterministic serialization** via ``msgspec.json.encode(struct, order="sorted")``
   for the v2 verdict format (see ``core._canonical_verdict_bytes``).
3. **Auto-generated JSON Schema artifacts** via ``msgspec.json.schema(Struct)``.
   The generated schemas are committed under ``schemas/`` as the canonical
   external contract so auditors can validate verdicts without running Python.

Two-tier design preserves the ``harness_error`` escape hatch:

    MinimalVerdictDoc — the contract honored by ``harness_error_verdict()``
        and early-return paths inside harnesses. Accepts unknown fields so
        full verdicts still satisfy it.

    FullVerdictDoc — the shape emitted by ``core.build_verdict_doc()`` for
        every successfully-executed test case. Strict: unknown fields raise.

Validation flow in ``core.validate_verdict_schema``:
    1. Always run ``MinimalVerdictDoc`` (preserves guarantees).
    2. Enforce the rubric-category allowlist (unchanged behavior).
    3. If ``strict=True`` and ``category != "harness_error"``, also run
       ``FullVerdictDoc``. This gate catches schema drift in real verdicts
       emitted by ``build_verdict_doc`` without rejecting minimal docs used
       by ad-hoc callers and tests.

We deliberately avoid msgspec tagged unions for the verdict category: the
allowed set varies per harness, which would fight the ``harness_error``
escape hatch and make adding categories a schema change. Plain ``str`` +
runtime allowlist check is the right tool.
"""

from __future__ import annotations

from typing import Annotated, Any

import msgspec
from msgspec import Meta, Struct


class VerdictInfo(Struct, frozen=True):
    """The ``verdict`` sub-document.

    Category is a free-form string at this layer; allowlist enforcement lives
    in ``core.validate_verdict_schema`` because the allowed set depends on
    which harness emitted the verdict. ``details`` is an open dict because
    harnesses embed heterogeneous scoring data here.
    """

    category: str
    rationale: str
    details: dict[str, Any] = {}


class MinimalVerdictDoc(Struct, frozen=True):
    """Required-minimum contract honored by every verdict shape.

    ``harness_error_verdict()`` returns only {test_case, commit, ground_truth,
    verdict}. Some harnesses also return this shape directly on early returns.
    Unknown fields are intentionally allowed (``forbid_unknown_fields=False``
    by default) so full verdicts can also validate against this model.
    """

    verdict: VerdictInfo
    test_case: dict[str, Any]  # flexible: minimum path only requires 'name'
    commit: dict[str, Any]


class FullTestCaseInfo(Struct, frozen=True, forbid_unknown_fields=True):
    """Test case metadata inside a fully-populated verdict.

    Fields match what ``core.build_verdict_doc()`` writes: ``name`` is always
    present, and the driver/payload SHA-256 pairs appear iff the corresponding
    files existed at build time.
    """

    name: str
    driver: str | None = None
    driver_sha256: str | None = None
    payload: str | None = None
    payload_sha256: str | None = None


# Non-negative float constraint for wall-clock time. Expressed via Annotated +
# Meta so the generated JSON Schema picks up the ``minimum: 0`` constraint
# automatically — no manual schema wrangling.
NonNegativeFloat = Annotated[float, Meta(ge=0)]


class FullVerdictDoc(Struct, frozen=True, forbid_unknown_fields=True):
    """The complete shape emitted by ``core.build_verdict_doc()``.

    ``forbid_unknown_fields=True`` means unknown keys raise
    ``msgspec.ValidationError``, catching schema drift immediately. Harness-
    specific data lives inside the flexible dict fields (``ground_truth``,
    ``outputs``, ``report_analysis``, ``environment``).
    """

    benchmark_version: str
    benchmark_name: str
    start_time_utc: str
    timestamp_utc: str
    wall_clock_seconds: NonNegativeFloat

    commit: dict[str, Any]
    test_case: FullTestCaseInfo

    ground_truth: dict[str, Any]
    ground_truth_references: dict[str, str]
    reference_genome: str

    execution: dict[str, Any]
    outputs: dict[str, Any]
    report_analysis: dict[str, Any]
    verdict: VerdictInfo
    environment: dict[str, Any]


def generate_json_schemas() -> dict[str, dict[str, Any]]:
    """Generate JSON Schema 2020-12 artifacts for the public verdict types.

    Returns a mapping of ``{schema_name: schema_dict}``. Called by
    ``scripts/gen_schemas.py`` to refresh the committed artifacts under
    ``schemas/`` and by CI to detect drift between the committed schemas
    and the msgspec Struct definitions.
    """
    return {
        "verdict-minimal": msgspec.json.schema(MinimalVerdictDoc),
        "verdict-full": msgspec.json.schema(FullVerdictDoc),
    }
