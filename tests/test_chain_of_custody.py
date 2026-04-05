"""Tests for verdict schema validation and chain of custody."""

from __future__ import annotations

import json

import pytest

from clawbio_bench.core import (
    VerdictSchemaError,
    collect_verdict_hashes,
    save_verdict,
    validate_verdict_schema,
    verify_verdict_file,
    write_verdict_hashes,
)

# ---------------------------------------------------------------------------
# validate_verdict_schema
# ---------------------------------------------------------------------------


def _valid_verdict() -> dict:
    return {
        "verdict": {"category": "fst_correct", "rationale": "good"},
        "test_case": {"name": "eq_01"},
        "commit": {"sha": "abc123"},
    }


class TestValidateVerdictSchema:
    def test_accepts_valid(self):
        validate_verdict_schema(_valid_verdict())  # no exception

    def test_rejects_non_dict(self):
        with pytest.raises(VerdictSchemaError, match="must be a dict"):
            validate_verdict_schema("not a dict")  # type: ignore[arg-type]

    def test_rejects_missing_verdict_key(self):
        v = _valid_verdict()
        del v["verdict"]
        with pytest.raises(VerdictSchemaError, match="missing required key"):
            validate_verdict_schema(v)

    def test_rejects_missing_test_case(self):
        v = _valid_verdict()
        del v["test_case"]
        with pytest.raises(VerdictSchemaError, match="missing required key"):
            validate_verdict_schema(v)

    def test_rejects_missing_category(self):
        v = _valid_verdict()
        del v["verdict"]["category"]
        with pytest.raises(VerdictSchemaError, match="missing 'category'"):
            validate_verdict_schema(v)

    def test_rejects_non_string_category(self):
        v = _valid_verdict()
        v["verdict"]["category"] = None
        with pytest.raises(VerdictSchemaError, match="category must be str"):
            validate_verdict_schema(v)

    def test_rejects_unknown_category_with_rubric(self):
        v = _valid_verdict()
        v["verdict"]["category"] = "completely_invented_category"
        with pytest.raises(VerdictSchemaError, match="Unknown verdict category"):
            validate_verdict_schema(v, rubric_categories=["fst_correct", "fst_incorrect"])

    def test_harness_error_always_allowed(self):
        v = _valid_verdict()
        v["verdict"]["category"] = "harness_error"
        validate_verdict_schema(v, rubric_categories=["fst_correct"])

    def test_test_case_must_have_name(self):
        v = _valid_verdict()
        v["test_case"] = {}
        with pytest.raises(VerdictSchemaError, match="test_case"):
            validate_verdict_schema(v)


# ---------------------------------------------------------------------------
# save_verdict self-hashing
# ---------------------------------------------------------------------------


class TestSaveVerdictHashing:
    def test_writes_verdict_with_self_hash(self, tmp_path):
        verdict = _valid_verdict()
        path = save_verdict(verdict, tmp_path)
        assert path.exists()
        with open(path) as f:
            saved = json.load(f)
        assert "_verdict_sha256" in saved
        assert len(saved["_verdict_sha256"]) == 64

    def test_in_memory_dict_gets_hash(self, tmp_path):
        verdict = _valid_verdict()
        save_verdict(verdict, tmp_path)
        assert "_verdict_sha256" in verdict

    def test_idempotent_save(self, tmp_path):
        """Saving twice should produce the same hash."""
        v1 = _valid_verdict()
        save_verdict(v1, tmp_path)
        h1 = v1["_verdict_sha256"]

        # Fresh copy of same content
        v2 = _valid_verdict()
        save_verdict(v2, tmp_path)
        h2 = v2["_verdict_sha256"]

        assert h1 == h2


class TestVerifyVerdictFile:
    def test_unmodified_verdict_verifies(self, tmp_path):
        verdict = _valid_verdict()
        path = save_verdict(verdict, tmp_path)
        ok, msg = verify_verdict_file(path)
        assert ok, msg

    def test_tampered_verdict_fails(self, tmp_path):
        verdict = _valid_verdict()
        path = save_verdict(verdict, tmp_path)

        # Tamper with verdict category post-hoc
        with open(path) as f:
            saved = json.load(f)
        saved["verdict"]["category"] = "fst_incorrect"  # change verdict
        with open(path, "w") as f:
            json.dump(saved, f, indent=2, sort_keys=True)

        ok, msg = verify_verdict_file(path)
        assert not ok
        assert "mismatch" in msg.lower() or "hash" in msg.lower()

    def test_missing_self_hash(self, tmp_path):
        path = tmp_path / "verdict.json"
        with open(path, "w") as f:
            json.dump({"verdict": {"category": "x"}}, f)
        ok, msg = verify_verdict_file(path)
        assert not ok
        assert "_verdict_sha256" in msg

    def test_nonexistent_file(self, tmp_path):
        ok, msg = verify_verdict_file(tmp_path / "missing.json")
        assert not ok

    def test_byte_level_tamper_whitespace_appended(self, tmp_path):
        """Appending a trailing byte should be detectable."""
        verdict = _valid_verdict()
        path = save_verdict(verdict, tmp_path)
        # Append whitespace bytes — does not break JSON parse but changes content
        with open(path, "ab") as f:
            f.write(b"\n\n\n")
        ok, msg = verify_verdict_file(path)
        assert not ok, f"Trailing bytes should be detected, got: {msg}"

    def test_byte_level_tamper_field_added(self, tmp_path):
        """Adding a new field post-hoc must be detected."""
        verdict = _valid_verdict()
        path = save_verdict(verdict, tmp_path)
        with open(path) as f:
            loaded = json.load(f)
        loaded["attacker_field"] = "injected"
        with open(path, "w") as f:
            json.dump(loaded, f, indent=2, sort_keys=True)
        ok, msg = verify_verdict_file(path)
        assert not ok
        assert "mismatch" in msg.lower() or "hash" in msg.lower()


class TestCollectAndWriteHashes:
    def test_collects_multiple_verdicts(self, tmp_path):
        for i in range(3):
            d = tmp_path / f"case_{i}"
            d.mkdir()
            v = _valid_verdict()
            v["test_case"]["name"] = f"case_{i}"
            save_verdict(v, d)

        hashes = collect_verdict_hashes(tmp_path)
        assert len(hashes) == 3
        for path_key, h in hashes.items():
            assert "case_" in path_key
            assert len(h) == 64

    def test_write_verdict_hashes_produces_sidecar(self, tmp_path):
        d = tmp_path / "case_1"
        d.mkdir()
        save_verdict(_valid_verdict(), d)

        sidecar = write_verdict_hashes(tmp_path)
        assert sidecar.exists()
        with open(sidecar) as f:
            data = json.load(f)
        assert data["count"] == 1
        assert "hashes" in data
        assert "collected_at_utc" in data


# ---------------------------------------------------------------------------
# verify_results_directory — deep chain-of-custody verification
# ---------------------------------------------------------------------------


class TestVerifyResultsDirectory:
    def _make_results(self, tmp_path):
        """Create a results dir with two verdicts + sidecar index."""
        from clawbio_bench.core import write_verdict_hashes

        for i in range(2):
            d = tmp_path / f"case_{i}"
            d.mkdir()
            v = _valid_verdict()
            v["test_case"]["name"] = f"case_{i}"
            save_verdict(v, d)
        write_verdict_hashes(tmp_path)
        return tmp_path

    def test_clean_results_pass(self, tmp_path):
        from clawbio_bench.core import verify_results_directory

        self._make_results(tmp_path)
        ok, fail, errors = verify_results_directory(tmp_path)
        assert fail == 0, errors
        assert ok > 0
        assert errors == []

    def test_missing_directory_fails_cleanly(self, tmp_path):
        from clawbio_bench.core import verify_results_directory

        ok, fail, errors = verify_results_directory(tmp_path / "nonexistent")
        assert ok == 0
        assert fail == 1
        assert "not found" in errors[0]

    def test_empty_directory_fails(self, tmp_path):
        from clawbio_bench.core import verify_results_directory

        ok, fail, errors = verify_results_directory(tmp_path)
        assert ok == 0
        assert fail == 1
        assert "No verdict.json" in errors[0]

    def test_sidecar_tampering_detected(self, tmp_path):
        """Rewriting a hash in verdict_hashes.json must be caught."""
        from clawbio_bench.core import verify_results_directory

        self._make_results(tmp_path)
        sidecar = tmp_path / "verdict_hashes.json"
        data = json.loads(sidecar.read_text())
        # Replace every recorded hash with zeros — none of them match anymore
        data["hashes"] = {k: "0" * 64 for k in data["hashes"]}
        sidecar.write_text(json.dumps(data, sort_keys=True, indent=2))

        ok, fail, errors = verify_results_directory(tmp_path)
        assert fail >= 2, f"expected sidecar mismatches, got {errors}"
        assert any("sidecar" in e for e in errors)

    def test_log_file_tampering_detected(self, tmp_path):
        """Modifying stdout.log after the run must be caught because its
        hash is recorded in the verdict's execution.stdout_sha256 field.

        We build a verdict with a fake execution block that points at a
        local stdout.log and then mutate the log file after saving.
        """
        from clawbio_bench.core import sha256_string, verify_results_directory

        d = tmp_path / "case_log"
        d.mkdir()
        log_content = "line one\nline two\n"
        (d / "stdout.log").write_text(log_content)
        v = {
            "verdict": {"category": "fst_correct", "rationale": "ok"},
            "test_case": {"name": "case_log"},
            "commit": {"sha": "abc123"},
            "execution": {
                "stdout_sha256": sha256_string(log_content),
                "stderr_sha256": sha256_string(""),
            },
        }
        save_verdict(v, d)
        # Baseline: should verify cleanly
        ok, fail, errors = verify_results_directory(tmp_path)
        assert fail == 0, errors
        # Now tamper with the log file
        (d / "stdout.log").write_text("tampered line\n")
        ok, fail, errors = verify_results_directory(tmp_path)
        assert fail >= 1
        assert any("stdout.log" in e and "mismatch" in e for e in errors)
