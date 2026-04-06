"""Tests for tagged-commit resolution, tag metadata enrichment, and
hierarchical heatmap rendering.

These are unit tests that do NOT require a real ClawBio repo — they
exercise core functions with synthetic data and mock git output.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fake_verdicts(
    commits: list[dict], test_cases: list[str], categories: list[str]
) -> list[dict]:
    """Build a list of synthetic verdicts spanning commits × test_cases."""
    verdicts = []
    for ci, commit in enumerate(commits):
        for ti, tc in enumerate(test_cases):
            cat = categories[(ci + ti) % len(categories)]
            verdicts.append(
                {
                    "commit": {
                        "sha": commit["sha"],
                        "date": commit.get("date", "2026-01-01"),
                        "message": commit.get("message", "synthetic"),
                    },
                    "test_case": {"name": tc},
                    "verdict": {"category": cat, "rationale": f"auto {cat}"},
                }
            )
    return verdicts


SAMPLE_COMMITS = [
    {"sha": "a" * 40, "date": "2026-01-01", "message": "initial"},
    {"sha": "b" * 40, "date": "2026-02-01", "message": "v0.1.0 release"},
    {"sha": "c" * 40, "date": "2026-03-01", "message": "fix bug"},
    {"sha": "d" * 40, "date": "2026-04-01", "message": "v0.2.0 release"},
]

SAMPLE_TAG_MAP = {
    "b" * 40: ["v0.1.0"],
    "d" * 40: ["v0.2.0"],
}

SAMPLE_TEST_CASES = ["tc_alpha", "tc_beta", "tc_gamma"]

SAMPLE_LEGEND = {
    "correct_determinate": {"color": "#22c55e", "label": "Correct"},
    "incorrect_determinate": {"color": "#ef4444", "label": "Incorrect"},
    "harness_error": {"color": "#9ca3af", "label": "Harness Error"},
}


# ---------------------------------------------------------------------------
# build_heatmap_data with tag enrichment
# ---------------------------------------------------------------------------


class TestBuildHeatmapDataWithTags:
    def test_tags_added_to_commits(self):
        verdicts = _fake_verdicts(
            SAMPLE_COMMITS,
            SAMPLE_TEST_CASES,
            ["correct_determinate", "incorrect_determinate"],
        )
        heatmap = harness_core.build_heatmap_data(verdicts, SAMPLE_LEGEND, tag_map=SAMPLE_TAG_MAP)
        commits_by_sha = {c["sha"]: c for c in heatmap["commits"]}

        assert commits_by_sha["b" * 40]["tags"] == ["v0.1.0"]
        assert commits_by_sha["d" * 40]["tags"] == ["v0.2.0"]

    def test_untagged_commits_have_no_tags_key(self):
        verdicts = _fake_verdicts(
            SAMPLE_COMMITS,
            SAMPLE_TEST_CASES,
            ["correct_determinate"],
        )
        heatmap = harness_core.build_heatmap_data(verdicts, SAMPLE_LEGEND, tag_map=SAMPLE_TAG_MAP)
        commits_by_sha = {c["sha"]: c for c in heatmap["commits"]}

        assert "tags" not in commits_by_sha["a" * 40]
        assert "tags" not in commits_by_sha["c" * 40]

    def test_no_tag_map_is_safe(self):
        verdicts = _fake_verdicts(
            SAMPLE_COMMITS[:1],
            SAMPLE_TEST_CASES[:1],
            ["correct_determinate"],
        )
        heatmap = harness_core.build_heatmap_data(verdicts, SAMPLE_LEGEND)
        assert "tags" not in heatmap["commits"][0]

    def test_empty_tag_map_is_safe(self):
        verdicts = _fake_verdicts(
            SAMPLE_COMMITS[:1],
            SAMPLE_TEST_CASES[:1],
            ["correct_determinate"],
        )
        heatmap = harness_core.build_heatmap_data(verdicts, SAMPLE_LEGEND, tag_map={})
        assert "tags" not in heatmap["commits"][0]


# ---------------------------------------------------------------------------
# get_tagged_commits
# ---------------------------------------------------------------------------


class TestGetTaggedCommits:
    def test_filters_to_tagged_only(self):
        """get_tagged_commits returns only SHAs that carry tags."""
        all_shas = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
        tag_output = f"{'b' * 40} \n{'d' * 40} \n"

        with (
            mock.patch.object(
                harness_core,
                "get_all_commits",
                return_value=all_shas,
            ),
            mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=tag_output, stderr=""),
            ),
        ):
            result = harness_core.get_tagged_commits(Path("/fake"), branch="main")

        assert result == ["b" * 40, "d" * 40]

    def test_preserves_chronological_order(self):
        """Tagged commits come back in oldest-first order."""
        all_shas = ["a" * 40, "b" * 40, "c" * 40]
        # c and a are tagged — should come back as [a, c] not [c, a]
        tag_output = f"{'c' * 40} \n{'a' * 40} \n"

        with (
            mock.patch.object(
                harness_core,
                "get_all_commits",
                return_value=all_shas,
            ),
            mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=tag_output, stderr=""),
            ),
        ):
            result = harness_core.get_tagged_commits(Path("/fake"), branch="main")

        assert result == ["a" * 40, "c" * 40]

    def test_no_tags_returns_empty(self):
        all_shas = ["a" * 40]
        with (
            mock.patch.object(
                harness_core,
                "get_all_commits",
                return_value=all_shas,
            ),
            mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="\n", stderr=""),
            ),
        ):
            result = harness_core.get_tagged_commits(Path("/fake"))
        assert result == []


# ---------------------------------------------------------------------------
# get_commit_tags
# ---------------------------------------------------------------------------


class TestGetCommitTags:
    def test_lightweight_tags(self):
        output = textwrap.dedent(f"""\
            v0.1.0 commit  {"a" * 40}
            v0.2.0 commit  {"b" * 40}
        """)
        with mock.patch(
            "subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=output, stderr=""),
        ):
            result = harness_core.get_commit_tags(Path("/fake"))

        assert result["a" * 40] == ["v0.1.0"]
        assert result["b" * 40] == ["v0.2.0"]

    def test_annotated_tags_dereference(self):
        # Annotated tag: objecttype=tag, *objectname=commit, objectname=tag-obj
        tag_obj = "f" * 40
        commit_sha = "a" * 40
        output = f"v1.0.0 tag {commit_sha} {tag_obj}\n"
        with mock.patch(
            "subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=output, stderr=""),
        ):
            result = harness_core.get_commit_tags(Path("/fake"))

        assert result[commit_sha] == ["v1.0.0"]

    def test_git_failure_returns_empty(self):
        with mock.patch(
            "subprocess.run",
            return_value=mock.Mock(returncode=1, stdout="", stderr="error"),
        ):
            result = harness_core.get_commit_tags(Path("/fake"))
        assert result == {}

    def test_multiple_tags_on_same_commit(self):
        sha = "a" * 40
        output = f"v1.0.0 commit  {sha}\nv1.0.0-rc1 commit  {sha}\n"
        with mock.patch(
            "subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=output, stderr=""),
        ):
            result = harness_core.get_commit_tags(Path("/fake"))

        assert sorted(result[sha]) == ["v1.0.0", "v1.0.0-rc1"]


# ---------------------------------------------------------------------------
# resolve_commits with --tagged-commits
# ---------------------------------------------------------------------------


class TestResolveTaggedCommits:
    def test_tagged_commits_mode(self):
        args = mock.Mock(
            smoke=False,
            all_commits=False,
            regression_window=None,
            commits=None,
            tagged_commits=True,
            branch="main",
        )
        tagged = ["b" * 40, "d" * 40]
        with mock.patch.object(harness_core, "get_tagged_commits", return_value=tagged):
            result = harness_core.resolve_commits(args, Path("/fake"))

        assert result == tagged

    def test_tagged_commits_empty_raises(self):
        args = mock.Mock(
            smoke=False,
            all_commits=False,
            regression_window=None,
            commits=None,
            tagged_commits=True,
            branch="main",
        )
        with (
            mock.patch.object(harness_core, "get_tagged_commits", return_value=[]),
            pytest.raises(harness_core.BenchmarkConfigError, match="No tagged commits"),
        ):
            harness_core.resolve_commits(args, Path("/fake"))


# ---------------------------------------------------------------------------
# Heatmap rendering (viz.py)
# ---------------------------------------------------------------------------


def _can_import_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401

        return True
    except ImportError:
        return False


class TestHeatmapRendering:
    def _write_heatmap_data(
        self, path: Path, commits: list[dict], test_cases: list[str], legend: dict
    ):
        """Write a synthetic heatmap_data.json."""
        matrix = {}
        cats = list(legend.keys())
        for ci, c in enumerate(commits):
            for ti, tc in enumerate(test_cases):
                matrix[f"{c['sha']}:{tc}"] = {
                    "category": cats[(ci + ti) % len(cats)],
                    "rationale": "synth",
                }
        data = {
            "commits": commits,
            "test_cases": test_cases,
            "matrix": matrix,
            "category_legend": legend,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    @pytest.mark.skipif(
        not _can_import_matplotlib(),
        reason="matplotlib not installed",
    )
    def test_aggregate_heatmap_with_tags(self, tmp_path):
        from clawbio_bench.viz import render_heatmap

        commits_with_tags = [
            {"sha": "a" * 40, "short": "a" * 8, "date": "2026-01-01", "message": "init"},
            {
                "sha": "b" * 40,
                "short": "b" * 8,
                "date": "2026-02-01",
                "message": "release",
                "tags": ["v0.1.0"],
            },
        ]
        harness_dir = tmp_path / "suite" / "harness1"
        self._write_heatmap_data(
            harness_dir / "heatmap_data.json",
            commits_with_tags,
            ["tc_1", "tc_2"],
            {"correct_determinate": {"color": "#22c55e", "label": "Correct"}},
        )
        png = render_heatmap(tmp_path / "suite")
        assert png.exists()
        assert png.stat().st_size > 0

    @pytest.mark.skipif(
        not _can_import_matplotlib(),
        reason="matplotlib not installed",
    )
    def test_multi_harness_produces_per_harness_pngs(self, tmp_path):
        from clawbio_bench.viz import render_heatmap

        commits = [
            {"sha": "a" * 40, "short": "a" * 8, "date": "2026-01-01", "message": "init"},
        ]
        for h in ["orchestrator", "equity"]:
            harness_dir = tmp_path / "suite" / h
            self._write_heatmap_data(
                harness_dir / "heatmap_data.json",
                commits,
                [f"{h}_tc1", f"{h}_tc2"],
                {"correct_determinate": {"color": "#22c55e", "label": "Correct"}},
            )

        agg_png = render_heatmap(tmp_path / "suite")
        assert agg_png.exists()
        # Per-harness PNGs should also be created
        assert (tmp_path / "suite" / "orchestrator" / "heatmap.png").exists()
        assert (tmp_path / "suite" / "equity" / "heatmap.png").exists()
