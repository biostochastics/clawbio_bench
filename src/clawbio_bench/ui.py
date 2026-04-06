# SPDX-License-Identifier: MIT
"""Thin UI helper layer for optional ``rich`` output.

Design constraints (matters for an audit tool):

* **Plain-text fallback**: ``rich`` is declared as an optional extra under
  ``[project.optional-dependencies].ui``. If it isn't installed, ``ui.py``
  still imports cleanly and every helper returns a plain-text result.
* **Pipe-safe by default**: when stdout isn't a TTY, rich output is
  disabled automatically so logs piped to files stay parseable. Progress
  bars and spinners never fire outside interactive shells.
* **No rich in core**: ``core.py`` never imports ``rich`` directly.
  It may import this module (``ui``) because every helper here handles
  the optional-dep fallback internally. Presentation flows exclusively
  through this module so swap-outs are a single-file change.
* **Kill switch**: ``--no-rich`` CLI flag sets ``CLAWBIO_BENCH_NO_RICH=1``
  which disables rich unconditionally, even in interactive shells.
* **Byte-stable plain output**: the plain-text branch of every renderer
  is intentionally byte-identical to the pre-rich CLI output. Downstream
  log parsers (CI, grep pipelines, reviewer tooling) depend on exact
  wording, so any change to a plain branch is a breaking format change.
* **Exit codes untouched**: nothing in this module affects ``sys.exit``
  codes. It's presentation only.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console


_ENV_DISABLE = "CLAWBIO_BENCH_NO_RICH"


def rich_available() -> bool:
    """Return True iff rich is importable and not disabled by env/flag."""
    if os.environ.get(_ENV_DISABLE):
        return False
    try:
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


def disable_rich() -> None:
    """Set the kill-switch env var. Called when --no-rich is passed."""
    os.environ[_ENV_DISABLE] = "1"


def get_console(*, stderr: bool = False) -> Console | None:
    """Return a ``rich.console.Console`` or ``None``.

    Returns ``None`` when rich is unavailable, disabled via kill switch, OR
    when the target stream is not a TTY. Piped / non-TTY callers get the
    plain ``print()`` fallback inside the renderers, which keeps CI log
    output byte-stable across rich/no-rich configurations.

    The previous implementation returned a no-color Console for non-TTY
    streams, but that still emits box-drawing Unicode characters for
    ``rich.table.Table`` and friends — the tables render visually even
    without color. Returning ``None`` unconditionally on non-TTY is the
    correct kill switch.
    """
    if not rich_available():
        return None

    stream = sys.stderr if stderr else sys.stdout
    is_tty = getattr(stream, "isatty", lambda: False)()
    if not is_tty:
        return None

    from rich.console import Console

    return Console(stderr=stderr, color_system="auto")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def render_error(message: str, *, stderr: bool = True) -> None:
    """Print an error message, using rich color on TTY or plain on pipe.

    ``message`` is written **verbatim** (caller owns the label, e.g.
    ``"ERROR: ..."`` or ``"HARNESS FAILED: ..."``). The plain branch emits
    exactly the caller's string followed by a newline so CI log diffs stay
    byte-stable; the rich branch wraps the whole message in red+bold so the
    label is still visually prominent without duplicating it.
    """
    console = get_console(stderr=stderr)
    if console is None:
        print(message, file=sys.stderr if stderr else sys.stdout)
        return
    console.print(f"[red bold]{message}[/red bold]")


# ---------------------------------------------------------------------------
# Project metadata (--about / --version / --list footer)
# ---------------------------------------------------------------------------


def render_about(metadata: dict[str, str], *, core_version: str) -> None:
    """Render the full project metadata block (``--about``).

    Shows author, email, license, and all project URLs — including the
    audit target repo — so reviewers and auditors can pivot quickly. The
    plain branch emits ``key: value`` lines so downstream tools can grep
    fields like ``homepage`` or ``audit_target`` without parsing box art.
    """
    # Ordered fields — controls both rich and plain layout.
    rows: list[tuple[str, str]] = [
        ("name", metadata.get("name", "clawbio-bench")),
        ("version", metadata.get("version", "?")),
        ("core", core_version),
        ("description", metadata.get("description", "")),
        ("author", metadata.get("author", "")),
        ("email", metadata.get("email", "")),
        ("license", metadata.get("license", "")),
        ("homepage", metadata.get("homepage", "")),
        ("issues", metadata.get("issues", "")),
        ("audit target", metadata.get("audit_target", "")),
    ]

    console = get_console()
    if console is None:
        for label, value in rows:
            if not value:
                continue
            print(f"  {label:<12} {value}")
        return

    from rich.panel import Panel
    from rich.table import Table

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True, justify="right")
    grid.add_column(style="white")
    for label, value in rows:
        if not value:
            continue
        # Highlight URLs as links so TTYs that support them are clickable.
        if value.startswith(("http://", "https://")):
            value_cell = f"[link={value}]{value}[/link]"
        elif label == "email":
            value_cell = f"[link=mailto:{value}]{value}[/link]"
        else:
            value_cell = value
        grid.add_row(label, value_cell)

    console.print()
    console.print(
        Panel(
            grid,
            title="[bold]clawbio-bench[/bold] [dim]metadata[/dim]",
            border_style="cyan",
            expand=False,
        )
    )


# ---------------------------------------------------------------------------
# Harness listing (--list)
# ---------------------------------------------------------------------------


def render_harness_list(
    harnesses: list[tuple[str, int, str]],
    *,
    title: str | None = None,
) -> None:
    """Render the --list output as a table (rich) or aligned columns (plain).

    Args:
        harnesses: list of (name, test_case_count, description) triples.
        title: optional table title. Only used for the rich-rendered path;
            the plain fallback emits rows only (the CLI prints its own
            heading immediately above this call, and duplicating it would
            break byte-stable output parsers).
    """
    console = get_console()
    if console is None:
        # Plain fallback — row-only so the CLI's own heading above this
        # call is not duplicated. Byte-stable across rich/no-rich.
        for name, count, desc in harnesses:
            print(f"  {name:<16} {count:>3} tests   {desc}")
        return

    from rich.table import Table

    table = Table(
        title=title,
        title_style="bold",
        header_style="bold cyan",
        show_edge=True,
    )
    table.add_column("harness", style="cyan", no_wrap=True)
    table.add_column("tests", justify="right", style="green")
    table.add_column("description", style="white")
    for name, count, desc in harnesses:
        table.add_row(name, str(count), desc)
    console.print(table)


# ---------------------------------------------------------------------------
# Startup / framing renderers used by cli.py
# ---------------------------------------------------------------------------


def render_startup_banner(
    *,
    suite_version: str,
    repo_path: Any,
    commit_count: int,
    mode: str,
    harness_names: list[str],
    output_base: Any,
) -> None:
    """Emit the pre-run header describing what the suite is about to do.

    Plain branch is byte-identical to the pre-rich CLI. Rich branch wraps
    the same fields in a titled ``Panel`` using a key-value grid so the
    user sees the plan at a glance.
    """
    console = get_console()
    if console is None:
        print(f"\nClawBio Benchmark Suite v{suite_version}")
        print(f"  Repo: {repo_path}")
        print(f"  Commits: {commit_count}")
        print(f"  Mode: {mode}")
        print(f"  Harnesses: {', '.join(harness_names)}")
        print(f"  Output: {output_base}")
        print()
        return

    from rich.panel import Panel
    from rich.table import Table

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True, justify="right")
    grid.add_column(style="white")
    grid.add_row("Repo", str(repo_path))
    grid.add_row("Commits", str(commit_count))
    grid.add_row("Mode", mode)
    grid.add_row("Harnesses", ", ".join(harness_names))
    grid.add_row("Output", str(output_base))

    console.print()
    console.print(
        Panel(
            grid,
            title=f"[bold]ClawBio Benchmark Suite[/bold] [dim]v{suite_version}[/dim]",
            border_style="cyan",
            expand=False,
        )
    )
    console.print()


def render_harness_header(benchmark_name: str, description: str) -> None:
    """Emit the ``#### HARNESS: ...`` separator between harnesses.

    Plain branch is byte-identical to the pre-rich CLI. Rich branch uses
    a ``Rule`` with the harness name bolded and the description dimmed,
    keeping the visual hierarchy but dropping the hash-mark noise.
    """
    console = get_console()
    if console is None:
        print(f"\n{'#' * 60}")
        print(f"# HARNESS: {benchmark_name} — {description}")
        print(f"{'#' * 60}")
        return

    from rich.rule import Rule

    label = f"[bold cyan]HARNESS[/bold cyan] {benchmark_name} [dim]— {description}[/dim]"
    console.print()
    console.print(Rule(label, style="cyan"))


# ---------------------------------------------------------------------------
# Dry-run planner
# ---------------------------------------------------------------------------


def render_dry_run_plan(
    harness_plans: list[tuple[str, list[str]]],
    *,
    total_runs: int,
    commit_count: int,
) -> None:
    """Render ``--dry-run`` output.

    Args:
        harness_plans: list of (benchmark_name, [test_case_name, ...]) pairs.
        total_runs: total number of (commit, test case) pairs that would run.
        commit_count: how many commits are in the sweep.
    """
    console = get_console()
    if console is None:
        for benchmark_name, test_cases in harness_plans:
            print(f"  {benchmark_name}: {len(test_cases)} test cases")
            for tc_name in test_cases:
                print(f"    - {tc_name}")
        print(f"\n  Total runs: {total_runs} ({commit_count} commits x test cases)")
        print("  (dry run — nothing executed)")
        return

    from rich.tree import Tree

    root = Tree(
        f"[bold]Planned runs[/bold] "
        f"[dim]({total_runs} total — {commit_count} commit(s) × test cases)[/dim]"
    )
    for benchmark_name, test_cases in harness_plans:
        branch = root.add(
            f"[cyan]{benchmark_name}[/cyan] [dim]({len(test_cases)} test cases)[/dim]"
        )
        for tc_name in test_cases:
            branch.add(f"[dim]•[/dim] {tc_name}")
    console.print(root)
    console.print("[dim italic](dry run — nothing executed)[/dim italic]")


# ---------------------------------------------------------------------------
# Verify mode summary
# ---------------------------------------------------------------------------


def render_verify_result(
    *,
    ok_count: int,
    fail_count: int,
    errors: list[str],
) -> None:
    """Render the ``--verify`` summary.

    Plain branch is byte-identical to the pre-rich CLI. Errors stream to
    stderr in both branches so redirecting them is still easy.
    """
    total = ok_count + fail_count
    err_console = get_console(stderr=True)
    out_console = get_console()

    if err_console is None:
        for err in errors:
            print(f"  [FAIL] {err}", file=sys.stderr)
    else:
        for err in errors:
            err_console.print(f"  [red bold][FAIL][/red bold] {err}")

    if out_console is None:
        print(f"\nDeep verification: {ok_count}/{total} checks passed ({fail_count} failed)")
        return

    from rich.panel import Panel

    verdict_color = "green" if fail_count == 0 else "red"
    summary = (
        f"[bold]Chain-of-custody check:[/bold] "
        f"[{verdict_color}]{ok_count}/{total}[/{verdict_color}] "
        f"passed [dim]({fail_count} failed)[/dim]"
    )
    out_console.print()
    out_console.print(
        Panel(
            summary,
            title="[bold]--verify[/bold]",
            border_style=verdict_color,
            expand=False,
        )
    )


# ---------------------------------------------------------------------------
# Suite summary (final table after a run)
# ---------------------------------------------------------------------------


def render_suite_summary(
    results: dict[str, dict[str, Any]],
    *,
    total_pass: int,
    total_evaluated: int,
    total_pass_rate: float,
    total_harness_errors: int,
    wall_clock_seconds: float,
    blocking: list[str],
    infra_crashes: list[str],
) -> None:
    """Render the final suite summary with a rich table, falling back to the
    plain aligned layout from the legacy CLI when rich is unavailable."""
    console = get_console()
    if console is None:
        _render_suite_summary_plain(
            results,
            total_pass=total_pass,
            total_evaluated=total_evaluated,
            total_pass_rate=total_pass_rate,
            total_harness_errors=total_harness_errors,
            wall_clock_seconds=wall_clock_seconds,
            blocking=blocking,
            infra_crashes=infra_crashes,
        )
        return

    from rich.panel import Panel
    from rich.table import Table

    overall_color = "green" if not blocking and total_evaluated > 0 else "red"
    header = (
        f"[bold]Wall clock:[/bold] {wall_clock_seconds:.1f}s    "
        f"[bold]Overall:[/bold] {total_pass}/{total_evaluated} pass "
        f"([{overall_color}]{total_pass_rate:.1f}%[/{overall_color}])    "
        f"[bold]Errors:[/bold] {total_harness_errors}"
    )
    console.print(Panel(header, title="BENCHMARK SUITE COMPLETE", expand=False))

    table = Table(
        header_style="bold cyan",
        show_edge=True,
    )
    table.add_column("harness", style="cyan", no_wrap=True)
    table.add_column("status", justify="center")
    table.add_column("pass/eval", justify="right")
    table.add_column("rate", justify="right")
    table.add_column("categories", style="white")

    for name, r in results.items():
        passed = r.get("pass", False)
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        pass_count = r.get("pass_count", 0)
        evaluated = r.get("evaluated", 0)
        rate = r.get("pass_rate", 0.0)
        cats = r.get("categories", {})
        cat_str = ", ".join(f"{k}:{v}" for k, v in sorted(cats.items()))
        table.add_row(
            name,
            status,
            f"{pass_count}/{evaluated}",
            f"{rate:.1f}%",
            cat_str,
        )
    console.print(table)

    if blocking:
        console.print(f"[red]BLOCKING:[/red] {', '.join(blocking)}")
    if infra_crashes:
        err = get_console(stderr=True)
        if err is not None:
            err.print(
                f"[red bold]HARNESS CRASH (infrastructure failure):[/red bold] "
                f"{', '.join(infra_crashes)}"
            )
        else:
            print(
                f"\n  HARNESS CRASH (infrastructure failure): {', '.join(infra_crashes)}",
                file=sys.stderr,
            )


def _render_suite_summary_plain(
    results: dict[str, dict[str, Any]],
    *,
    total_pass: int,
    total_evaluated: int,
    total_pass_rate: float,
    total_harness_errors: int,
    wall_clock_seconds: float,
    blocking: list[str],
    infra_crashes: list[str],
) -> None:
    """Legacy plain-text layout. Byte-identical to the pre-rich version so
    downstream log parsers continue to match. Any change here is a breaking
    output format change."""
    print(f"\n{'=' * 60}")
    print("BENCHMARK SUITE COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Wall clock: {wall_clock_seconds:.1f}s")
    print(
        f"  Overall: {total_pass}/{total_evaluated} pass "
        f"({total_pass_rate:.1f}%) "
        f"[{total_harness_errors} harness errors]"
    )
    for name, r in results.items():
        status = "PASS" if r.get("pass") else "FAIL"
        print(f"\n  {name}: {status}")
        print(
            f"    {r.get('pass_count', 0)}/{r.get('evaluated', 0)} pass "
            f"({r.get('pass_rate', 0):.1f}%)"
        )
        for cat, count in sorted(r.get("categories", {}).items()):
            print(f"      {cat}: {count}")
    if blocking:
        print(f"\n  BLOCKING: {', '.join(blocking)}")
    if infra_crashes:
        print(
            f"\n  HARNESS CRASH (infrastructure failure): {', '.join(infra_crashes)}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Matrix progress — used by core.run_benchmark_matrix
# ---------------------------------------------------------------------------


class MatrixProgress:
    """Context-manager wrapping per-test progress for the (commits × tests)
    sweep in ``core.run_benchmark_matrix``.

    Rich mode: spinner + bar + MofN + elapsed + ETA live display, with each
    completed test logged above the bar and colored by whether its verdict
    category is a pass-category, fail-category, or other.

    Plain mode (non-TTY, ``--no-rich``, ``rich`` not installed, or
    ``quiet=True``): byte-identical legacy prints —
    ``  [N/total] tc_name... [cat]`` and ``COMMIT: ...`` banners. CI log
    parsers and reviewer tooling continue to match.

    The caller is responsible for calling ``start_test`` before dispatching
    and exactly one of ``end_test`` / ``test_failed`` / ``test_schema_error``
    / ``test_interrupted`` afterward — mirroring the control flow in
    ``run_benchmark_matrix``.
    """

    def __init__(
        self,
        total_runs: int,
        *,
        quiet: bool = False,
        pass_categories: list[str] | None = None,
        fail_categories: list[str] | None = None,
    ) -> None:
        self.total_runs = total_runs
        self.quiet = quiet
        self._pass = set(pass_categories or [])
        self._fail = set(fail_categories or [])
        self._console: Console | None = None
        self._progress: Any = None
        self._task: Any = None
        self._run_count: int = 0
        self._current_tc: str | None = None
        # Summary counters for the end-of-sweep footer.
        self._pass_seen: int = 0
        self._fail_seen: int = 0
        self._err_seen: int = 0

    # -- context manager -------------------------------------------------

    def __enter__(self) -> MatrixProgress:
        # Quiet mode: no rich display at all; legacy plain branch suppresses
        # per-test lines while still emitting commit headers (matches the
        # pre-rich behavior where ``quiet`` only gated the per-test prints).
        if self.quiet:
            return self

        self._console = get_console()
        if self._console is None:
            return self
        try:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TaskProgressColumn,
                TextColumn,
                TimeElapsedColumn,
                TimeRemainingColumn,
            )
        except ImportError:
            self._console = None
            return self

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("ETA"),
            TimeRemainingColumn(),
            console=self._console,
            transient=False,
        )
        self._progress.__enter__()
        self._task = self._progress.add_task("[cyan]running matrix[/cyan]", total=self.total_runs)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._progress is not None:
            self._progress.__exit__(exc_type, exc, tb)
            self._progress = None
            self._task = None
            self._console = None

    # -- commit / test lifecycle ----------------------------------------

    def commit_header(self, short_sha: str, date: str, message: str) -> None:
        """Announce a new commit in the sweep."""
        if self._progress is not None:
            from rich.rule import Rule

            label = f"[bold cyan]COMMIT[/bold cyan] {short_sha} [dim]({date})[/dim] — {message}"
            self._progress.console.print()
            self._progress.console.print(Rule(label, style="cyan"))
            return

        # Plain branch — byte-identical to the pre-rich CLI. Even quiet mode
        # keeps the commit banner so operators can see which SHA is running.
        print(f"\n{'=' * 60}")
        print(f"COMMIT: {short_sha} ({date})")
        print(f"  {message}")
        print(f"{'=' * 60}")

    def start_test(self, tc_name: str) -> None:
        """Called immediately before ``run_single_fn`` dispatches."""
        self._run_count += 1
        self._current_tc = tc_name
        if self.quiet:
            return
        if self._progress is not None and self._task is not None:
            self._progress.update(
                self._task,
                description=f"[cyan]{tc_name}[/cyan]",
            )
            return
        # Plain branch — byte-identical: no trailing newline so the verdict
        # category can be appended on the same line by end_test().
        print(
            f"  [{self._run_count}/{self.total_runs}] {tc_name}...",
            end=" ",
            flush=True,
        )

    def end_test(self, category: str) -> None:
        """Finalize a test with its verdict category."""
        if category in self._pass:
            self._pass_seen += 1
        elif category == "harness_error":
            self._err_seen += 1
        else:
            self._fail_seen += 1

        if self.quiet:
            return
        if self._progress is not None and self._task is not None:
            color = self._category_color(category)
            self._progress.console.log(
                f"[dim]{self._run_count:>3}/{self.total_runs}[/dim] "
                f"[cyan]{self._current_tc or '?'}[/cyan] "
                f"[{color}][{category}][/{color}]"
            )
            self._progress.advance(self._task)
            return
        print(f"[{category}]")

    def test_schema_error(self) -> None:
        """Called when ``validate_verdict_schema`` rejects a harness output."""
        self._err_seen += 1
        if self.quiet:
            return
        if self._progress is not None and self._task is not None:
            self._progress.console.log(
                f"[dim]{self._run_count:>3}/{self.total_runs}[/dim] "
                f"[cyan]{self._current_tc or '?'}[/cyan] "
                f"[red bold][harness_error][/red bold]"
            )
            self._progress.advance(self._task)
            return
        print("[harness_error]")

    def test_failed(self, exception: BaseException) -> None:
        """Called when the test dispatcher itself raised."""
        self._err_seen += 1
        if self.quiet:
            return
        if self._progress is not None and self._task is not None:
            self._progress.console.log(
                f"[dim]{self._run_count:>3}/{self.total_runs}[/dim] "
                f"[cyan]{self._current_tc or '?'}[/cyan] "
                f"[red bold]HARNESS_ERROR[/red bold] [dim]{exception}[/dim]"
            )
            self._progress.advance(self._task)
            return
        print(f"HARNESS_ERROR: {exception}")

    def test_interrupted(self) -> None:
        """Called on ``KeyboardInterrupt`` / ``SystemExit``."""
        if self.quiet:
            return
        if self._progress is not None:
            self._progress.console.log(
                f"[red bold]INTERRUPTED[/red bold] at [cyan]{self._current_tc or '?'}[/cyan]"
            )
            return
        print("INTERRUPTED")

    def warn(self, message: str) -> None:
        """Surface a non-fatal warning through the active display.

        Used by ``core`` for ``clean_workspace`` / ``restore_ref`` failures
        and other infrastructure hiccups that shouldn't abort the sweep.
        In rich mode the warning is logged above the progress bar in yellow;
        in plain mode it goes to stderr to match the pre-rich behavior.
        """
        if self._progress is not None:
            self._progress.console.log(f"[yellow]WARNING:[/yellow] {message}")
            return
        print(f"  WARNING: {message}", file=sys.stderr)

    # -- helpers ----------------------------------------------------------

    def _category_color(self, category: str) -> str:
        """Return a rich style for a verdict category.

        Uses the explicit ``pass_categories`` / ``fail_categories`` sets when
        provided (authoritative), falling back to heuristic colors for
        categories the caller did not register.
        """
        if category in self._pass:
            return "green"
        if category == "harness_error":
            return "red bold"
        if category in self._fail:
            return "red"
        # Unknown category — don't guess green. Neutral yellow is the right
        # signal: "saw a category, couldn't classify it".
        return "yellow"
