"""Tests for reproducibility signature and capture truncation."""

from __future__ import annotations

from clawbio_bench.core import (
    MAX_CAPTURE_BYTES,
    _environment_signature,
    capture_execution,
)


class TestEnvironmentSignature:
    def test_stable_fields_present(self):
        sig = _environment_signature()
        for key in (
            "python_version",
            "python_executable",
            "platform",
            "hostname_hash",
            "package_count",
            "package_set_sha256",
        ):
            assert key in sig, f"missing key: {key}"

    def test_package_count_positive(self):
        sig = _environment_signature()
        assert sig["package_count"] > 0
        # The hash should be a 64-char hex string
        assert len(sig["package_set_sha256"]) == 64

    def test_deterministic(self):
        """Calling twice with no env changes produces identical signatures."""
        s1 = _environment_signature()
        s2 = _environment_signature()
        assert s1 == s2

    def test_hostname_is_hashed_not_leaked(self):
        import platform as plat

        sig = _environment_signature()
        assert plat.node() not in sig["hostname_hash"]
        assert len(sig["hostname_hash"]) == 12


class TestOutputTruncation:
    def test_normal_output_not_truncated(self, tmp_path):
        script = tmp_path / "small.py"
        script.write_text("print('hello world')")

        import sys

        result = capture_execution(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=30,
        )
        assert result.exit_code == 0
        assert "hello world" in result.stdout
        assert "TRUNCATED" not in result.stdout

    def test_huge_stdout_gets_truncated(self, tmp_path):
        # Emit ~11 MB so we exceed MAX_CAPTURE_BYTES (10 MB)
        script = tmp_path / "big.py"
        target_bytes = MAX_CAPTURE_BYTES + 1024 * 1024  # 11 MB
        script.write_text(
            f"import sys\n"
            f"chunk = 'A' * 1024\n"
            f"for _ in range({target_bytes // 1024}):\n"
            f"    sys.stdout.write(chunk)\n"
        )

        import sys

        result = capture_execution(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=60,
        )
        assert result.exit_code == 0
        # Truncated stdout should contain the marker and be bounded
        assert "TRUNCATED" in result.stdout
        # Length should be roughly MAX_CAPTURE_BYTES + small marker suffix
        assert len(result.stdout) < MAX_CAPTURE_BYTES + 2048
        # Chain-of-custody: the ExecutionResult must carry the full
        # pre-truncation hash and byte length, and the truncated flag.
        assert result.stdout_truncated is True
        assert result.stdout_full_byte_len >= MAX_CAPTURE_BYTES
        assert len(result.stdout_full_sha256) == 64

    def test_multibyte_output_truncates_at_byte_boundary(self, tmp_path):
        """Regression: character-based truncation could leave multibyte
        streams over the byte cap. Byte-exact truncation must keep the
        encoded text <= MAX_CAPTURE_BYTES + a small marker suffix."""
        # Each emoji is 4 UTF-8 bytes. Emitting MAX_CAPTURE_BYTES/2 of them
        # would be 2 * MAX_CAPTURE_BYTES bytes under character-based slicing.
        script = tmp_path / "multibyte.py"
        target_chars = MAX_CAPTURE_BYTES // 2  # ~5M chars => ~20M bytes
        script.write_text(
            f"import sys\n"
            f"chunk = '\u4e00' * 256\n"  # 3 bytes per char
            f"for _ in range({target_chars // 256}):\n"
            f"    sys.stdout.write(chunk)\n"
        )

        import sys

        result = capture_execution(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=60,
        )
        assert result.exit_code == 0
        assert result.stdout_truncated is True
        # Encoded bytes (after truncation) must be within cap + marker overhead
        encoded = result.stdout.encode("utf-8", errors="replace")
        assert len(encoded) < MAX_CAPTURE_BYTES + 2048, (
            f"Truncated stdout is {len(encoded)} bytes; expected <= {MAX_CAPTURE_BYTES + 2048}"
        )

    def test_truncation_hash_is_original_not_truncated(self, tmp_path):
        """The full_sha256 must describe the ORIGINAL pre-truncation bytes.

        This is the chain-of-custody fix: previously ``stdout_sha256``
        hashed the truncated text, so an auditor couldn't verify the tool's
        original output against the recorded hash.
        """
        import hashlib
        import sys

        script = tmp_path / "big.py"
        # Emit a deterministic 11 MB blob
        script.write_text(
            "import sys\n"
            "chunk = 'A' * 1024\n"
            f"for _ in range({(MAX_CAPTURE_BYTES + 1024 * 1024) // 1024}):\n"
            "    sys.stdout.write(chunk)\n"
        )
        result = capture_execution(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=60,
        )
        assert result.stdout_truncated is True
        # Recompute the expected original-bytes hash locally. The script's
        # loop runs ((MAX_CAPTURE_BYTES + 1024*1024) // 1024) iterations and
        # writes exactly 1024 bytes per iteration.
        iterations = (MAX_CAPTURE_BYTES + 1024 * 1024) // 1024
        total_bytes = iterations * 1024
        expected_hash = hashlib.sha256(b"A" * total_bytes).hexdigest()
        assert result.stdout_full_sha256 == expected_hash
        # to_dict() must expose both hashes and flag truncation
        d = result.to_dict()
        assert d["stdout_truncated"] is True
        assert d["stdout_full_sha256"] == expected_hash
        assert d["stdout_full_byte_len"] == total_bytes
        # The post-truncation sha must differ (this is what the old code
        # would have recorded — now it's clearly labelled as such).
        assert d["stdout_sha256"] != expected_hash


class TestCaptureExecutionReproducibility:
    def test_same_command_same_exit_code(self, tmp_path):
        script = tmp_path / "deterministic.py"
        script.write_text("print('stable output')\n")

        import sys

        r1 = capture_execution([sys.executable, str(script)], cwd=tmp_path, timeout=30)
        r2 = capture_execution([sys.executable, str(script)], cwd=tmp_path, timeout=30)
        assert r1.exit_code == r2.exit_code
        assert r1.stdout == r2.stdout
        assert r1.stderr == r2.stderr
