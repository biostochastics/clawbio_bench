"""Tests for the canonical verdict serializer.

Single format: ``msgspec.json.encode(order="sorted")`` with a trailing newline.
These tests pin the determinism guarantees ``save_verdict`` / ``verify_verdict_file``
rely on.
"""

from __future__ import annotations

import hashlib

from clawbio_bench.core import (
    _canonical_verdict_bytes,
    save_verdict,
    verify_verdict_file,
)


def _fixture_doc() -> dict:
    return {
        "verdict": {"category": "correct_determinate", "rationale": "matched"},
        "test_case": {"name": "fixture_01"},
        "commit": {"sha": "abc123"},
    }


class TestCanonicalBytes:
    def test_deterministic_across_calls(self):
        """Same input -> same bytes, byte-for-byte."""
        doc = _fixture_doc()
        b1 = _canonical_verdict_bytes(doc)
        b2 = _canonical_verdict_bytes(doc)
        assert b1 == b2

    def test_key_order_independent(self):
        """Dict key order in the input must not affect output — the serializer
        sorts keys before encoding."""
        doc1 = {
            "verdict": {"category": "x", "rationale": "y"},
            "test_case": {"name": "t"},
            "commit": {"sha": "abc"},
        }
        doc2 = {
            "commit": {"sha": "abc"},
            "test_case": {"name": "t"},
            "verdict": {"rationale": "y", "category": "x"},
        }
        assert _canonical_verdict_bytes(doc1) == _canonical_verdict_bytes(doc2)

    def test_ends_with_newline(self):
        """On-disk verdict files end with a trailing newline for POSIX
        compliance. The hash covers the newline."""
        doc = _fixture_doc()
        out = _canonical_verdict_bytes(doc)
        assert out.endswith(b"\n")

    def test_handles_path_objects_via_enc_hook(self):
        """Non-native types are stringified by the enc_hook, mirroring the
        old stdlib ``default=str`` behavior."""
        from pathlib import Path as P

        doc = _fixture_doc()
        doc["verdict"]["details"] = {"some_path": P("/tmp/x")}
        out = _canonical_verdict_bytes(doc)  # must not raise
        assert b"/tmp/x" in out


class TestSaveAndVerify:
    def test_round_trip_verifies(self, tmp_path):
        doc = _fixture_doc()
        path = save_verdict(doc, tmp_path)
        ok, msg = verify_verdict_file(path)
        assert ok, msg

    def test_embedded_hash_matches_stored_bytes(self, tmp_path):
        """The ``_verdict_sha256`` field on disk must match SHA-256 of the
        hash-stripped canonical bytes."""
        doc = _fixture_doc()
        path = save_verdict(doc, tmp_path)
        saved_bytes = path.read_bytes()
        import json as _json

        saved_doc = _json.loads(saved_bytes)
        stored_hash = saved_doc["_verdict_sha256"]

        stripped = {k: v for k, v in saved_doc.items() if k != "_verdict_sha256"}
        recomputed = hashlib.sha256(_canonical_verdict_bytes(stripped)).hexdigest()
        assert recomputed == stored_hash

    def test_tamper_detection_byte_level(self, tmp_path):
        """Modifying a verdict on disk must fail verification."""
        doc = _fixture_doc()
        path = save_verdict(doc, tmp_path)
        raw = path.read_bytes()
        tampered = raw.replace(b"correct_determinate", b"incorrect_determinate")
        path.write_bytes(tampered)
        ok, msg = verify_verdict_file(path)
        assert not ok
        assert "mismatch" in msg.lower() or "hash" in msg.lower()

    def test_tamper_detection_trailing_whitespace(self, tmp_path):
        """Appending whitespace must be detected — the full file bytes are
        covered by the hash and the byte-level canonical comparison."""
        doc = _fixture_doc()
        path = save_verdict(doc, tmp_path)
        with open(path, "ab") as f:
            f.write(b"\n\n")
        ok, msg = verify_verdict_file(path)
        assert not ok
