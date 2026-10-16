"""Tests for CLI upgrade command."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import click
import pytest

_DEFAULT_URL = "https://github.com/sgarcese/OpenContext.git"


# ---------------------------------------------------------------------------
# _is_protected
# ---------------------------------------------------------------------------


class TestIsProtected:
    def test_config_yaml_is_protected(self):
        from cli.commands.upgrade import _is_protected

        assert _is_protected("config.yaml") is True

    def test_staging_tfvars_is_protected(self):
        from cli.commands.upgrade import _is_protected

        assert _is_protected("terraform/aws/staging.tfvars") is True

    def test_prod_tfvars_is_protected(self):
        from cli.commands.upgrade import _is_protected

        assert _is_protected("terraform/aws/prod.tfvars") is True

    def test_gcp_staging_tfvars_is_protected(self):
        from cli.commands.upgrade import _is_protected

        assert _is_protected("terraform/gcp/staging.tfvars") is True

    def test_gcp_prod_tfvars_is_protected(self):
        from cli.commands.upgrade import _is_protected

        assert _is_protected("terraform/gcp/prod.tfvars") is True

    def test_examples_prefix_is_protected(self):
        from cli.commands.upgrade import _is_protected

        assert _is_protected("examples/boston/config.yaml") is True
        assert _is_protected("examples/README.md") is True

    def test_regular_files_not_protected(self):
        from cli.commands.upgrade import _is_protected

        assert _is_protected("cli/main.py") is False
        assert _is_protected("README.md") is False
        assert _is_protected("terraform/aws/main.tf") is False

    def test_partial_match_not_protected(self):
        from cli.commands.upgrade import _is_protected

        # "config.yaml" must be an exact match, not a prefix/suffix
        assert _is_protected("some/config.yaml") is False

    def test_examples_prefix_strict(self):
        from cli.commands.upgrade import _is_protected

        # Must start with "examples/" not just contain "examples"
        assert _is_protected("not_examples/foo.py") is False


# ---------------------------------------------------------------------------
# upgrade — no upstream remote, user adds one
# ---------------------------------------------------------------------------


class TestUpgradeAddsUpstreamRemote:
    @patch("cli.commands.upgrade.get_project_root")
    @patch("cli.commands.upgrade.questionary")
    @patch("cli.commands.upgrade._run_git")
    def test_adds_upstream_remote_when_missing(
        self, mock_git, mock_questionary, mock_root, tmp_path
    ):
        from cli.commands.upgrade import upgrade

        mock_root.return_value = tmp_path

        mock_questionary.text.return_value.ask.return_value = (
            "https://github.com/sgarcese/OpenContext.git"
        )
        mock_questionary.confirm.return_value.ask.return_value = (
            False  # cancel at confirm
        )

        def git_side_effect(args, **kwargs):
            if args == ["remote", "-v"]:
                return subprocess.CompletedProcess(args, 0, "", "")  # no upstream
            if args[:2] == ["remote", "add"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "fetch":
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "log":
                return subprocess.CompletedProcess(args, 0, "", "")  # up to date
            return subprocess.CompletedProcess(args, 0, "", "")

        mock_git.side_effect = git_side_effect

        ctx = MagicMock()
        ctx.invoked_subcommand = None

        # "Already up to date" — returns cleanly without raising
        upgrade(ctx=ctx, upstream_url=_DEFAULT_URL)

        # Verify "remote add upstream" was called
        remote_add_calls = [
            c for c in mock_git.call_args_list if c[0][0][:2] == ["remote", "add"]
        ]
        assert len(remote_add_calls) == 1


# ---------------------------------------------------------------------------
# upgrade — already up to date
# ---------------------------------------------------------------------------


class TestUpgradeAlreadyUpToDate:
    @patch("cli.commands.upgrade.get_project_root")
    @patch("cli.commands.upgrade._run_git")
    def test_exits_cleanly_when_up_to_date(self, mock_git, mock_root, tmp_path):
        from cli.commands.upgrade import upgrade

        mock_root.return_value = tmp_path

        def git_side_effect(args, **kwargs):
            if args == ["remote", "-v"]:
                return subprocess.CompletedProcess(
                    args, 0, "upstream\tgit@... (fetch)", ""
                )
            if args[0] == "fetch":
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "log":
                return subprocess.CompletedProcess(args, 0, "", "")  # no new commits
            return subprocess.CompletedProcess(args, 0, "", "")

        mock_git.side_effect = git_side_effect

        ctx = MagicMock()
        ctx.invoked_subcommand = None

        # Should return without error (no confirm prompt, no merge)
        upgrade(ctx=ctx, upstream_url=_DEFAULT_URL)


# ---------------------------------------------------------------------------
# upgrade — user cancels at confirm prompt
# ---------------------------------------------------------------------------


class TestUpgradeCancelledByUser:
    @patch("cli.commands.upgrade.get_project_root")
    @patch("cli.commands.upgrade.questionary")
    @patch("cli.commands.upgrade._run_git")
    def test_cancel_at_confirm_exits_0(
        self, mock_git, mock_questionary, mock_root, tmp_path
    ):
        from cli.commands.upgrade import upgrade

        mock_root.return_value = tmp_path
        mock_questionary.confirm.return_value.ask.return_value = False

        def git_side_effect(args, **kwargs):
            if args == ["remote", "-v"]:
                return subprocess.CompletedProcess(
                    args, 0, "upstream\tgit@... (fetch)", ""
                )
            if args[0] == "fetch":
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "log":
                return subprocess.CompletedProcess(
                    args, 0, "abc1234 Fix bug\ndef5678 Add feature", ""
                )
            if args[:2] == ["diff", "--name-only"] and "HEAD" in args:
                return subprocess.CompletedProcess(args, 0, "cli/main.py\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        mock_git.side_effect = git_side_effect

        ctx = MagicMock()
        ctx.invoked_subcommand = None

        with pytest.raises(click.exceptions.Exit) as exc_info:
            upgrade(ctx=ctx, upstream_url=_DEFAULT_URL)

        assert exc_info.value.exit_code == 0


# ---------------------------------------------------------------------------
# upgrade — fetch fails
# ---------------------------------------------------------------------------


class TestUpgradeFetchFails:
    @patch("cli.commands.upgrade.get_project_root")
    @patch("cli.commands.upgrade._run_git")
    def test_exits_1_when_fetch_fails(self, mock_git, mock_root, tmp_path):
        from cli.commands.upgrade import upgrade

        mock_root.return_value = tmp_path

        def git_side_effect(args, **kwargs):
            if args == ["remote", "-v"]:
                return subprocess.CompletedProcess(
                    args, 0, "upstream\tgit@... (fetch)", ""
                )
            if args[0] == "fetch":
                return subprocess.CompletedProcess(args, 1, "", "fatal: no upstream")
            return subprocess.CompletedProcess(args, 0, "", "")

        mock_git.side_effect = git_side_effect

        ctx = MagicMock()
        ctx.invoked_subcommand = None

        with pytest.raises(click.exceptions.Exit) as exc_info:
            upgrade(ctx=ctx, upstream_url=_DEFAULT_URL)

        assert exc_info.value.exit_code == 1


# ---------------------------------------------------------------------------
# upgrade — merge with protected file conflict auto-resolved
# ---------------------------------------------------------------------------


class TestUpgradeMergeProtectedConflict:
    @patch("cli.commands.upgrade.get_project_root")
    @patch("cli.commands.upgrade.questionary")
    @patch("cli.commands.upgrade._run_git")
    def test_protected_conflict_auto_resolved(
        self, mock_git, mock_questionary, mock_root, tmp_path
    ):
        from cli.commands.upgrade import upgrade

        mock_root.return_value = tmp_path
        mock_questionary.confirm.return_value.ask.return_value = (
            True  # proceed with merge
        )

        call_count = {"merge": 0}

        def git_side_effect(args, **kwargs):
            if args == ["remote", "-v"]:
                return subprocess.CompletedProcess(
                    args, 0, "upstream\tgit@... (fetch)", ""
                )
            if args[0] == "fetch":
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "log" and "HEAD..upstream/main" in " ".join(args):
                return subprocess.CompletedProcess(
                    args, 0, "abc1234 Update template\n", ""
                )
            if (
                args[:2] == ["diff", "--name-only"]
                and "HEAD" in args
                and "upstream/main" in args
            ):
                return subprocess.CompletedProcess(args, 0, "config.yaml\n", "")
            if args[:2] == ["merge", "upstream/main"]:
                call_count["merge"] += 1
                return subprocess.CompletedProcess(
                    args, 1, "", "CONFLICT"
                )  # merge conflict
            if args[:3] == ["diff", "--name-only", "--diff-filter=U"]:
                return subprocess.CompletedProcess(
                    args, 0, "config.yaml\n", ""
                )  # conflict in protected
            if args[:2] == ["checkout", "--ours"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "add":
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        mock_git.side_effect = git_side_effect

        ctx = MagicMock()
        ctx.invoked_subcommand = None

        # Should complete without error (conflict auto-resolved, no manual conflicts)
        upgrade(ctx=ctx, upstream_url=_DEFAULT_URL)


# ---------------------------------------------------------------------------
# upgrade — merge with non-protected conflict requires manual resolution
# ---------------------------------------------------------------------------


class TestUpgradeMergeManualConflict:
    @patch("cli.commands.upgrade.get_project_root")
    @patch("cli.commands.upgrade.questionary")
    @patch("cli.commands.upgrade._run_git")
    def test_manual_conflict_exits_1(
        self, mock_git, mock_questionary, mock_root, tmp_path
    ):
        from cli.commands.upgrade import upgrade

        mock_root.return_value = tmp_path
        mock_questionary.confirm.return_value.ask.return_value = True

        def git_side_effect(args, **kwargs):
            if args == ["remote", "-v"]:
                return subprocess.CompletedProcess(
                    args, 0, "upstream\tgit@... (fetch)", ""
                )
            if args[0] == "fetch":
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "log" and "HEAD..upstream/main" in " ".join(args):
                return subprocess.CompletedProcess(args, 0, "abc1234 Update\n", "")
            if (
                args[:2] == ["diff", "--name-only"]
                and "HEAD" in args
                and "upstream/main" in args
            ):
                return subprocess.CompletedProcess(args, 0, "cli/main.py\n", "")
            if args[:2] == ["merge", "upstream/main"]:
                return subprocess.CompletedProcess(args, 1, "", "CONFLICT")
            if args[:3] == ["diff", "--name-only", "--diff-filter=U"]:
                return subprocess.CompletedProcess(
                    args, 0, "cli/main.py\n", ""
                )  # non-protected
            return subprocess.CompletedProcess(args, 0, "", "")

        mock_git.side_effect = git_side_effect

        ctx = MagicMock()
        ctx.invoked_subcommand = None

        with pytest.raises(click.exceptions.Exit) as exc_info:
            upgrade(ctx=ctx, upstream_url=_DEFAULT_URL)

        assert exc_info.value.exit_code == 1


# ---------------------------------------------------------------------------
# upgrade — questionary returns None (user hits Ctrl+C)
# ---------------------------------------------------------------------------


class TestUpgradeUserAbortsPrompt:
    @patch("cli.commands.upgrade.get_project_root")
    @patch("cli.commands.upgrade.questionary")
    @patch("cli.commands.upgrade._run_git")
    def test_ctrl_c_on_url_prompt_exits_0(
        self, mock_git, mock_questionary, mock_root, tmp_path
    ):
        from cli.commands.upgrade import upgrade

        mock_root.return_value = tmp_path
        mock_questionary.text.return_value.ask.return_value = (
            None  # Ctrl+C returns None
        )

        def git_side_effect(args, **kwargs):
            if args == ["remote", "-v"]:
                return subprocess.CompletedProcess(args, 0, "", "")  # no upstream
            return subprocess.CompletedProcess(args, 0, "", "")

        mock_git.side_effect = git_side_effect

        ctx = MagicMock()
        ctx.invoked_subcommand = None

        with pytest.raises(click.exceptions.Exit) as exc_info:
            upgrade(ctx=ctx, upstream_url=_DEFAULT_URL)

        assert exc_info.value.exit_code == 0
