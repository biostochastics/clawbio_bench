from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from clawbio_bench.core import BenchmarkConfigError, resolve_skill_bench_config
from clawbio_bench.harnesses.finemapping_harness import run_single_finemapping
from clawbio_bench.harnesses.nutrigx_harness import run_single_nutrigx

DRIVER = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "clawbio_bench"
    / "drivers"
    / "finemapping_driver.py"
)


def _commit_meta() -> dict[str, str]:
    return {
        "sha": "abc123",
        "short": "abc123",
        "full_sha": "abc123",
        "date": "2026-04-24",
        "message": "test commit",
    }


def _write_skill_md(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")


def _write_finemapping_core(skill_dir: Path, package: str = "fine_mapping_core") -> None:
    package_dir = skill_dir / package
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "abf.py").write_text(
        "def compute_abf(df, w=0.04):\n    return [0.25, 0.75]\n",
        encoding="utf-8",
    )
    (package_dir / "susie.py").write_text(
        "def run_susie(**kwargs):\n"
        "    return {'pip': [0.25, 0.75], 'alpha': [[0.25, 0.75]], "
        "'mu': [[0.0, 0.0]], 'mu2': [[0.0, 0.0]], 'converged': True, "
        "'n_iter': 1, 'elbo': []}\n",
        encoding="utf-8",
    )
    (package_dir / "credible_sets.py").write_text(
        "def build_credible_sets_susie(**kwargs):\n"
        "    return []\n"
        "def build_credible_set_abf(*args, **kwargs):\n"
        "    return []\n",
        encoding="utf-8",
    )


def test_resolves_manifest_named_renamed_skill_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "nutrigx-advisor"
    _write_skill_md(skill_dir, "nutrigx-advisor")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "nutrigx-advisor"\nentrypoint = "bin/run.py"\ninvoke_as = "script"\n',
        encoding="utf-8",
    )
    (skill_dir / "bin").mkdir()
    (skill_dir / "bin" / "run.py").write_text("", encoding="utf-8")

    config = resolve_skill_bench_config(
        repo,
        skill_name="nutrigx-advisor",
        legacy_dir_name="nutrigx_advisor",
        legacy_entrypoint="nutrigx_advisor.py",
    )

    assert config.skill_dir == skill_dir
    assert config.entrypoint == skill_dir / "bin" / "run.py"
    assert config.invoke_as == "script"


def test_explicit_manifest_takes_precedence_over_stale_legacy_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    legacy_dir = repo / "skills" / "nutrigx_advisor"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "nutrigx_advisor.py").write_text("", encoding="utf-8")

    skill_dir = repo / "skills" / "nutrigx-advisor"
    _write_skill_md(skill_dir, "nutrigx-advisor")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "nutrigx-advisor"\nentrypoint = "runner.py"\ninvoke_as = "script"\n',
        encoding="utf-8",
    )
    (skill_dir / "runner.py").write_text("", encoding="utf-8")

    config = resolve_skill_bench_config(
        repo,
        skill_name="nutrigx-advisor",
        legacy_dir_name="nutrigx_advisor",
        legacy_entrypoint="nutrigx_advisor.py",
    )

    assert config.skill_dir == skill_dir
    assert config.entrypoint == skill_dir / "runner.py"


def test_manifest_without_name_works_in_known_legacy_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "nutrigx_advisor"
    skill_dir.mkdir(parents=True)
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nentrypoint = "runner.py"\ninvoke_as = "script"\n',
        encoding="utf-8",
    )
    (skill_dir / "runner.py").write_text("", encoding="utf-8")

    config = resolve_skill_bench_config(
        repo,
        skill_name="nutrigx-advisor",
        legacy_dir_name="nutrigx_advisor",
        legacy_entrypoint="nutrigx_advisor.py",
    )

    assert config.entrypoint == skill_dir / "runner.py"
    assert config.source.endswith(".bench-config.toml")


def test_rejects_matching_dir_with_manifest_for_another_skill(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "nutrigx-advisor"
    _write_skill_md(skill_dir, "nutrigx-advisor")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "other-skill"\nentrypoint = "runner.py"\ninvoke_as = "script"\n',
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkConfigError, match="other-skill"):
        resolve_skill_bench_config(
            repo,
            skill_name="nutrigx-advisor",
            legacy_dir_name="nutrigx_advisor",
            legacy_entrypoint="nutrigx_advisor.py",
        )


def test_unrelated_malformed_manifest_does_not_block_explicit_match(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    unrelated = repo / "skills" / "unrelated"
    _write_skill_md(unrelated, "unrelated")
    (unrelated / ".bench-config.toml").write_text("[bench\n", encoding="utf-8")

    skill_dir = repo / "skills" / "nutrigx-advisor"
    _write_skill_md(skill_dir, "nutrigx-advisor")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "nutrigx-advisor"\nentrypoint = "runner.py"\ninvoke_as = "script"\n',
        encoding="utf-8",
    )
    (skill_dir / "runner.py").write_text("", encoding="utf-8")

    config = resolve_skill_bench_config(
        repo,
        skill_name="nutrigx-advisor",
        legacy_dir_name="nutrigx_advisor",
        legacy_entrypoint="nutrigx_advisor.py",
    )

    assert config.skill_dir == skill_dir


def test_resolves_legacy_skill_dir_when_manifest_is_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    legacy_dir = repo / "skills" / "nutrigx_advisor"
    legacy_dir.mkdir(parents=True)

    config = resolve_skill_bench_config(
        repo,
        skill_name="nutrigx-advisor",
        legacy_dir_name="nutrigx_advisor",
        legacy_entrypoint="nutrigx_advisor.py",
    )

    assert config.skill_dir == legacy_dir
    assert config.entrypoint == legacy_dir / "nutrigx_advisor.py"
    assert config.source == "legacy"


def test_resolves_alias_skill_name_and_entrypoint_without_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "nutrigx"
    _write_skill_md(skill_dir, "nutrigx")
    (skill_dir / "nutrigx.py").write_text("", encoding="utf-8")

    config = resolve_skill_bench_config(
        repo,
        skill_name="nutrigx-advisor",
        skill_aliases=("nutrigx",),
        legacy_dir_name="nutrigx_advisor",
        legacy_entrypoint="nutrigx_advisor.py",
        legacy_entrypoint_aliases=("nutrigx.py",),
    )

    assert config.skill_dir == skill_dir
    assert config.entrypoint == skill_dir / "nutrigx.py"
    assert config.source == "legacy"


def test_resolves_alias_import_package_without_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "fine-mapping"
    _write_skill_md(skill_dir, "fine-mapping")
    (skill_dir / "fine_mapping_core").mkdir()

    config = resolve_skill_bench_config(
        repo,
        skill_name="fine-mapping",
        legacy_dir_name="fine-mapping",
        legacy_imports_package="core",
        legacy_imports_package_aliases=("fine_mapping_core",),
    )

    assert config.skill_dir == skill_dir
    assert config.imports_package == "fine_mapping_core"
    assert config.source == "legacy"


def test_rejects_manifest_entrypoint_that_escapes_skill_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "nutrigx-advisor"
    _write_skill_md(skill_dir, "nutrigx-advisor")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "nutrigx-advisor"\nentrypoint = "../escape.py"\n',
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkConfigError, match="entrypoint"):
        resolve_skill_bench_config(
            repo,
            skill_name="nutrigx-advisor",
            legacy_dir_name="nutrigx_advisor",
            legacy_entrypoint="nutrigx_advisor.py",
        )


def test_rejects_manifest_entrypoint_symlink_that_escapes_skill_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "nutrigx-advisor"
    _write_skill_md(skill_dir, "nutrigx-advisor")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "nutrigx-advisor"\nentrypoint = "runner.py"\n',
        encoding="utf-8",
    )
    outside = tmp_path / "outside.py"
    outside.write_text("", encoding="utf-8")
    (skill_dir / "runner.py").symlink_to(outside)

    with pytest.raises(BenchmarkConfigError, match="entrypoint"):
        resolve_skill_bench_config(
            repo,
            skill_name="nutrigx-advisor",
            legacy_dir_name="nutrigx_advisor",
            legacy_entrypoint="nutrigx_advisor.py",
        )


def test_rejects_outside_repo_skill_symlink_before_reading_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skills_root = repo / "skills"
    skills_root.mkdir(parents=True)
    outside_skill = tmp_path / "outside-skill"
    _write_skill_md(outside_skill, "nutrigx-advisor")
    (outside_skill / ".bench-config.toml").write_text(
        '[bench]\nname = "nutrigx-advisor"\nentrypoint = "runner.py"\n',
        encoding="utf-8",
    )
    (skills_root / "nutrigx-advisor").symlink_to(outside_skill, target_is_directory=True)

    with pytest.raises(BenchmarkConfigError, match="escapes repository"):
        resolve_skill_bench_config(
            repo,
            skill_name="nutrigx-advisor",
            legacy_dir_name="nutrigx_advisor",
            legacy_entrypoint="nutrigx_advisor.py",
        )


def test_rejects_import_package_symlink_that_escapes_skill_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "fine-mapping"
    _write_skill_md(skill_dir, "fine-mapping")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "fine-mapping"\ninvoke_as = "driver"\n'
        '[bench.imports]\npackage = "fine_mapping_core"\n',
        encoding="utf-8",
    )
    outside_package = tmp_path / "fine_mapping_core"
    outside_package.mkdir()
    (skill_dir / "fine_mapping_core").symlink_to(outside_package, target_is_directory=True)

    with pytest.raises(BenchmarkConfigError, match="imports.package"):
        resolve_skill_bench_config(
            repo,
            skill_name="fine-mapping",
            legacy_dir_name="fine-mapping",
            legacy_imports_package="core",
            legacy_imports_package_aliases=("fine_mapping_core",),
        )


def test_rejects_default_import_package_symlink_that_escapes_skill_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "fine-mapping"
    _write_skill_md(skill_dir, "fine-mapping")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "fine-mapping"\ninvoke_as = "driver"\n',
        encoding="utf-8",
    )
    (skill_dir / "core").symlink_to(tmp_path / "missing-outside-core", target_is_directory=True)

    with pytest.raises(BenchmarkConfigError, match="imports.package"):
        resolve_skill_bench_config(
            repo,
            skill_name="fine-mapping",
            legacy_dir_name="fine-mapping",
            legacy_imports_package="core",
            legacy_imports_package_aliases=("fine_mapping_core",),
        )


def test_rejects_mismatched_invoke_as_for_harness_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "nutrigx-advisor"
    _write_skill_md(skill_dir, "nutrigx-advisor")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "nutrigx-advisor"\nentrypoint = "runner.py"\ninvoke_as = "driver"\n',
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkConfigError, match="invoke_as"):
        resolve_skill_bench_config(
            repo,
            skill_name="nutrigx-advisor",
            legacy_dir_name="nutrigx_advisor",
            legacy_entrypoint="nutrigx_advisor.py",
            expected_invoke_as="script",
        )


def test_rejects_ambiguous_manifest_matches(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    for dirname in ("skill-a", "skill-b"):
        skill_dir = repo / "skills" / dirname
        _write_skill_md(skill_dir, "nutrigx-advisor")
        (skill_dir / ".bench-config.toml").write_text(
            '[bench]\nname = "nutrigx-advisor"\nentrypoint = "run.py"\n',
            encoding="utf-8",
        )
        (skill_dir / "run.py").write_text("", encoding="utf-8")

    with pytest.raises(BenchmarkConfigError, match="Ambiguous"):
        resolve_skill_bench_config(
            repo,
            skill_name="nutrigx-advisor",
            legacy_dir_name="nutrigx_advisor",
            legacy_entrypoint="nutrigx_advisor.py",
        )


def test_nutrigx_harness_invokes_manifest_entrypoint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "nutrigx-advisor"
    _write_skill_md(skill_dir, "nutrigx-advisor")
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\nname = "nutrigx-advisor"\nentrypoint = "runner.py"\ninvoke_as = "script"\n',
        encoding="utf-8",
    )
    (skill_dir / "runner.py").write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--input')\n"
        "parser.add_argument('--output')\n"
        "parser.add_argument('--no-figures', action='store_true')\n"
        "args = parser.parse_args()\n"
        "out = Path(args.output)\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "payload = {'data': {'risk_scores': {'folate': {'score': 7.0, "
        "'category': 'Elevated'}}}, 'summary': {'panel_snps_tested': 1, "
        "'domains_assessed': 1, 'elevated_domains': ['folate']}}\n"
        "(out / 'result.json').write_text(json.dumps(payload), encoding='utf-8')\n"
        "(out / 'nutrigx_report.md').write_text('# report\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    payload = tmp_path / "input.txt"
    payload.write_text("rs1801133 AA\n", encoding="utf-8")

    verdict = run_single_nutrigx(
        repo_path=repo,
        commit_sha="abc123",
        test_case_path=tmp_path,
        ground_truth={
            "FINDING_CATEGORY": "score_correct",
            "GROUND_TRUTH_DOMAIN": "folate",
            "GROUND_TRUTH_SCORE": "7.0",
            "SCORE_TOLERANCE": "0.01",
        },
        payload_path=payload,
        output_base=tmp_path / "out",
        commit_meta=_commit_meta(),
    )

    assert verdict["verdict"]["category"] == "score_correct"
    assert str(skill_dir / "runner.py") in verdict["execution"]["cmd"]


def test_finemapping_harness_uses_manifest_import_package(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "fine-mapping"
    _write_skill_md(skill_dir, "fine-mapping")
    _write_finemapping_core(skill_dir)
    (skill_dir / ".bench-config.toml").write_text(
        "[bench]\n"
        'name = "fine-mapping"\n'
        'invoke_as = "driver"\n'
        "[bench.imports]\n"
        'package = "fine_mapping_core"\n',
        encoding="utf-8",
    )
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    inputs = case_dir / "inputs.json"
    inputs.write_text('{"method": "abf", "z": [1.0, 2.0]}', encoding="utf-8")
    (case_dir / "ground_truth.txt").write_text("# PAYLOAD: inputs.json\n", encoding="utf-8")

    verdict = run_single_finemapping(
        repo_path=repo,
        commit_sha="abc123",
        test_case_path=case_dir,
        ground_truth={
            "FINDING_CATEGORY": "finemap_correct",
            "EXPECTED_PIPS": "[0.25, 0.75]",
            "PIP_TOLERANCE": "0.0",
        },
        payload_path=inputs,
        output_base=tmp_path / "out",
        commit_meta=_commit_meta(),
    )

    assert verdict["verdict"]["category"] == "finemap_correct"
    assert verdict["report_analysis"]["imports_package"] == "fine_mapping_core"
    assert verdict["report_analysis"]["driver_result"]["pips"] == [0.25, 0.75]


def test_driver_treats_missing_optional_susie_inf_as_method_import_error(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skill"
    _write_finemapping_core(skill_dir)
    inputs = tmp_path / "inputs.json"
    output = tmp_path / "result.json"
    inputs.write_text('{"method": "susie_inf", "z": [0.0, 0.0]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(DRIVER),
            "--skill-dir",
            str(skill_dir),
            "--imports-package",
            "fine_mapping_core",
            "--inputs",
            str(inputs),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["status"] == "raised"
    assert payload["error"]["type"] == "ImportError"
    assert "susie_inf" in payload["error"]["message"]


def test_driver_does_not_hide_transitive_susie_inf_import_failure(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    _write_finemapping_core(skill_dir)
    (skill_dir / "fine_mapping_core" / "susie_inf.py").write_text(
        "import missing_dep_for_manifest_test\n",
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs.json"
    output = tmp_path / "result.json"
    inputs.write_text('{"method": "susie_inf", "z": [0.0, 0.0]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(DRIVER),
            "--skill-dir",
            str(skill_dir),
            "--imports-package",
            "fine_mapping_core",
            "--inputs",
            str(inputs),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 2
    assert payload["status"] == "import_error"
    assert payload["error"]["type"] == "ModuleNotFoundError"
    assert "missing_dep_for_manifest_test" in payload["error"]["message"]


def test_manifest_package_must_exist_in_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "fine-mapping"
    skill_dir.mkdir(parents=True)
    (skill_dir / ".bench-config.toml").write_text(
        '[bench]\n[bench.imports]\npackage = "fine_mapping_core"\n'
    )
    with pytest.raises(BenchmarkConfigError, match="package.*missing"):
        resolve_skill_bench_config(
            tmp_path,
            skill_name="fine-mapping",
            legacy_dir_name="fine-mapping",
            legacy_imports_package="core",
        )


@pytest.mark.parametrize("local_namespace", [False, True])
def test_driver_never_scores_package_from_pythonpath(
    tmp_path: Path, local_namespace: bool
) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    external = tmp_path / "external"
    _write_finemapping_core(external)
    if local_namespace:
        (skill_dir / "fine_mapping_core").mkdir()
        (external / "fine_mapping_core" / "__init__.py").unlink()
    inputs = tmp_path / "inputs.json"
    inputs.write_text('{"method": "abf", "z": [1.0, 2.0]}')
    result = subprocess.run(
        [
            sys.executable,
            str(DRIVER),
            "--skill-dir",
            str(skill_dir),
            "--imports-package",
            "fine_mapping_core",
            "--inputs",
            str(inputs),
        ],
        env={**os.environ, "PYTHONPATH": str(external)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 2, payload
    assert payload["status"] == "import_error", payload
    assert payload["pips"] is None


@pytest.mark.parametrize("harness", ["nutrigx", "finemapping"])
def test_invalid_manifest_produces_harness_error(tmp_path: Path, harness: str) -> None:
    name = "nutrigx-advisor" if harness == "nutrigx" else "fine-mapping"
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / ".bench-config.toml").write_text(
        f'[bench]\nname = "{name}"\ninvoke_as = "module"\n'
    )
    inputs = tmp_path / "inputs.json"
    inputs.write_text('{"method": "abf", "z": [1.0, 2.0]}')
    run = run_single_nutrigx if harness == "nutrigx" else run_single_finemapping
    verdict = run(
        repo_path=tmp_path,
        commit_sha="abc123",
        test_case_path=tmp_path,
        ground_truth={"FINDING_CATEGORY": "score_correct"},
        payload_path=inputs,
        output_base=tmp_path / "out",
        commit_meta=_commit_meta(),
    )
    assert verdict["verdict"]["category"] == "harness_error"
    assert "invoke_as" in verdict["verdict"]["rationale"]
