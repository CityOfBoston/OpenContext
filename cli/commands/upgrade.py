from __future__ import annotations

import subprocess

import questionary
import typer

from cli.utils import console, get_project_root

app = typer.Typer()

DEFAULT_UPSTREAM_URL = "https://github.com/sgarcese/OpenContext.git"

# Files that should never be automatically overwritten (city-specific)
PROTECTED_FILES = {
    "config.yaml",
    "terraform/aws/staging.tfvars",
    "terraform/aws/prod.tfvars",
    "terraform/gcp/staging.tfvars",
    "terraform/gcp/prod.tfvars",
}
PROTECTED_PREFIXES = ("examples/",)


def _run_git(
    args: list[str], cwd=None, check: bool = False, timeout: int = 30
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _is_protected(path: str) -> bool:
    if path in PROTECTED_FILES:
        return True
    return any(path.startswith(p) for p in PROTECTED_PREFIXES)


@app.callback(invoke_without_command=True)
def upgrade(
    ctx: typer.Context,
    upstream_url: str = typer.Option(
        DEFAULT_UPSTREAM_URL,
        "--upstream-url",
        help="URL of the upstream template repository",
    ),
) -> None:
    """Merge updates from the upstream OpenContext template."""
    if ctx.invoked_subcommand is not None:
        return

    project_root = get_project_root()

    # 1. Check if upstream remote exists
    result = _run_git(["remote", "-v"], cwd=project_root)
    has_upstream = "upstream" in result.stdout

    if not has_upstream:
        console.print("[yellow]No 'upstream' remote found.[/yellow]")
        provided_url = questionary.text(
            "Enter the upstream template repo URL:",
            default=upstream_url,
        ).ask()
        if provided_url is None:
            raise typer.Exit(0)
        upstream_url = provided_url

        result = _run_git(["remote", "add", "upstream", upstream_url], cwd=project_root)
        if result.returncode != 0:
            console.print(
                f"[red]Failed to add upstream remote:[/red] {result.stderr.strip()}"
            )
            raise typer.Exit(1)
        console.print(f"[green]Added upstream remote:[/green] {upstream_url}")
    else:
        console.print("[dim]Using existing 'upstream' remote.[/dim]")

    # 2. Fetch upstream
    console.print("\n[bold]Fetching upstream...[/bold]")
    with console.status("git fetch upstream"):
        result = _run_git(["fetch", "upstream"], cwd=project_root, timeout=60)
    if result.returncode != 0:
        console.print(f"[red]Failed to fetch upstream:[/red] {result.stderr.strip()}")
        raise typer.Exit(1)

    # 3. Show what's changed since last sync
    result = _run_git(
        ["log", "HEAD..upstream/main", "--oneline"],
        cwd=project_root,
    )
    if result.returncode != 0:
        console.print(
            f"[red]Could not compare with upstream/main:[/red] {result.stderr.strip()}"
        )
        raise typer.Exit(1)

    new_commits = [line for line in result.stdout.strip().splitlines() if line.strip()]
    if not new_commits:
        console.print("[green]Already up to date.[/green] Nothing to merge.")
        return

    console.print(f"\n[bold]{len(new_commits)} new commit(s) in upstream/main:[/bold]")
    for commit in new_commits:
        console.print(f"  {commit}")

    # 4. Show which files will be affected
    result = _run_git(
        ["diff", "--name-only", "HEAD", "upstream/main"],
        cwd=project_root,
    )
    changed_files = [f for f in result.stdout.strip().splitlines() if f.strip()]

    if changed_files:
        console.print(
            f"\n[bold]Files that will be affected ({len(changed_files)}):[/bold]"
        )
        for f in changed_files:
            console.print(f"  {f}")

    # 5. Warn about protected files
    protected_changes = [f for f in changed_files if _is_protected(f)]
    if protected_changes:
        console.print(
            "\n[yellow bold]⚠️  The following city-specific files have upstream changes[/yellow bold]"
        )
        console.print(
            "[yellow]These will NOT be overwritten — your local versions will be kept:[/yellow]"
        )
        for f in protected_changes:
            console.print(f"  [yellow]{f}[/yellow]")

    # 6. Confirm
    proceed = questionary.confirm(
        "\nProceed with merge from upstream/main?",
        default=False,
    ).ask()
    if not proceed:
        console.print("[yellow]Upgrade cancelled.[/yellow]")
        raise typer.Exit(0)

    # 7. Merge without committing
    console.print("\n[bold]Merging from upstream/main...[/bold]")
    with console.status("git merge upstream/main --no-commit --no-ff"):
        result = _run_git(
            ["merge", "upstream/main", "--no-commit", "--no-ff"],
            cwd=project_root,
            timeout=120,
        )

    # 8. Handle conflicts
    conflict_result = _run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        cwd=project_root,
    )
    conflicted_files = [
        f for f in conflict_result.stdout.strip().splitlines() if f.strip()
    ]

    if conflicted_files:
        console.print(
            f"\n[yellow bold]Merge conflicts in {len(conflicted_files)} file(s):[/yellow bold]"
        )

        auto_resolved = []
        needs_manual = []

        for f in conflicted_files:
            if _is_protected(f):
                # Keep current version for city-specific files
                result = _run_git(["checkout", "--ours", f], cwd=project_root)
                if result.returncode == 0:
                    _run_git(["add", f], cwd=project_root)
                    auto_resolved.append(f)
                    console.print(f"  [green]Auto-resolved (kept yours):[/green] {f}")
                else:
                    needs_manual.append(f)
                    console.print(f"  [red]Could not auto-resolve:[/red] {f}")
            else:
                needs_manual.append(f)
                console.print(f"  [yellow]Needs manual resolution:[/yellow] {f}")

        if needs_manual:
            console.print("\n[yellow]Manual steps to complete the merge:[/yellow]")
            console.print(
                "  1. Edit each conflicted file and resolve the <<<<<<< markers"
            )
            for f in needs_manual:
                console.print(f"     git add {f}")
            console.print("  2. Once all conflicts are resolved:")
            console.print("     git commit")
            console.print(
                "\nThe merge is currently staged (--no-commit). "
                "Run [bold]git merge --abort[/bold] to cancel entirely."
            )
            raise typer.Exit(1)

    # 9. Print summary
    console.print("\n[green bold]Merge complete![/green bold]")
    console.print(f"  {len(new_commits)} commit(s) merged from upstream/main")
    if protected_changes:
        console.print(
            f"  {len(protected_changes)} city-specific file(s) kept unchanged"
        )
    console.print(
        "\nRun [bold]git commit[/bold] to finalize the merge, or [bold]git merge --abort[/bold] to cancel."
    )
