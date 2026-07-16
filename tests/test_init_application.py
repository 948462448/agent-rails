#!/usr/bin/env python3

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails import cli as python_cli  # noqa: E402
from agent_rails.init_application import (  # noqa: E402
    InitInputError,
    InitRequest,
    InitShell,
    run_init,
)


def _expected_guide(
    shell: InitShell,
    *,
    kit_home: Path,
    user_home: Path,
    project: Path | None,
    profile: Path | None,
) -> str:
    if shell is InitShell.ZSH:
        rc_file = user_home / ".zshrc"
        home_line = f'export AGENT_RAILS_HOME="{kit_home}"'
        path_line = 'export PATH="$AGENT_RAILS_HOME/bin:$PATH"'
        project_line = (
            "" if project is None else f'export AGENT_RAILS_PROJECT="{project}"\n'
        )
        profile_line = (
            "" if profile is None else f'export AGENT_RAILS_PROFILE="{profile}"\n'
        )
        reload_command = "source ~/.zshrc"
    elif shell is InitShell.BASH:
        rc_file = user_home / ".bashrc"
        home_line = f'export AGENT_RAILS_HOME="{kit_home}"'
        path_line = 'export PATH="$AGENT_RAILS_HOME/bin:$PATH"'
        project_line = (
            "" if project is None else f'export AGENT_RAILS_PROJECT="{project}"\n'
        )
        profile_line = (
            "" if profile is None else f'export AGENT_RAILS_PROFILE="{profile}"\n'
        )
        reload_command = "source ~/.bashrc"
    else:
        rc_file = user_home / ".config/fish/config.fish"
        home_line = f'set -gx AGENT_RAILS_HOME "{kit_home}"'
        path_line = 'fish_add_path "$AGENT_RAILS_HOME/bin"'
        project_line = (
            "" if project is None else f'set -gx AGENT_RAILS_PROJECT "{project}"\n'
        )
        profile_line = (
            "" if profile is None else f'set -gx AGENT_RAILS_PROFILE "{profile}"\n'
        )
        reload_command = "source ~/.config/fish/config.fish"

    doctor = ""
    if project is not None and profile is not None:
        doctor = (
            'ar doctor --project "$AGENT_RAILS_PROJECT" '
            '--profile "$AGENT_RAILS_PROFILE"\n'
        )
    return (
        "Agent Rails Init\n\n"
        f"1. Add this block to {rc_file}:\n\n"
        "# Agent Rails\n"
        f"{home_line}\n"
        f"{path_line}\n"
        'alias ar="agent-rails"\n'
        f"{project_line}"
        f"{profile_line}"
        "\n2. Reload your shell:\n\n"
        f"{reload_command}\n"
        "\n3. Verify:\n\n"
        "agent-rails --help\n"
        "agent-rails home\n"
        f"{doctor}"
        "\n4. Connect a project:\n\n"
        "cd /path/to/project\n"
        "agent-rails setup --tool claude  # or codex / opencode\n"
        "\n"
        "# Restart the selected coding agent, then work normally.\n"
        "# Before delivery:\n"
        "agent-rails verify\n"
    )


class InitApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-init-application-"
        )
        self.root = Path(self.temporary.name)
        self.kit_home = self.root / "kit"
        self.user_home = self.root / "user"
        self.config_home = self.root / "config"
        self.project = self.root / "projects/sample-project"
        self.profile = self.root / "profiles/sample-project.profile"
        self.environment = {
            "HOME": str(self.user_home),
            "PATH": os.environ.get("PATH", ""),
            "SHELL": "/bin/zsh",
            "AGENT_RAILS_CONFIG_HOME": str(self.config_home),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(
        self,
        *,
        requested_shell: InitShell | None = InitShell.ZSH,
        requested_project: Path | None = None,
        explicit_profile: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> InitRequest:
        return InitRequest(
            requested_shell=requested_shell,
            requested_project=requested_project,
            explicit_profile=explicit_profile,
            kit_home=self.kit_home,
            environment=(
                dict(self.environment) if environment is None else environment
            ),
        )

    def invoke_cli(
        self,
        arguments: tuple[str, ...],
        environment: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        values = dict(self.environment if environment is None else environment)
        values["AGENT_RAILS_HOME"] = str(self.kit_home)
        with (
            patch.dict(os.environ, values, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = python_cli.main(("init-application", *arguments))
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_zsh_bash_and_fish_render_the_exact_existing_guide(self) -> None:
        for shell in (InitShell.ZSH, InitShell.BASH, InitShell.FISH):
            with self.subTest(shell=shell.value):
                result = run_init(
                    self.request(
                        requested_shell=shell,
                        requested_project=self.project,
                        explicit_profile=self.profile,
                    )
                )

                self.assertEqual(result.exit_code, 0)
                self.assertIs(result.shell, shell)
                self.assertEqual(result.project_path, self.project)
                self.assertEqual(result.profile_path, self.profile)
                self.assertEqual(
                    result.output,
                    _expected_guide(
                        shell,
                        kit_home=self.kit_home,
                        user_home=self.user_home,
                        project=self.project,
                        profile=self.profile,
                    ),
                )

    def test_plain_init_stays_project_neutral(self) -> None:
        result = run_init(self.request())

        self.assertIsNone(result.project_path)
        self.assertIsNone(result.profile_path)
        self.assertNotIn("AGENT_RAILS_PROJECT=", result.output)
        self.assertNotIn("AGENT_RAILS_PROFILE=", result.output)
        self.assertIn("cd /path/to/project", result.output)
        self.assertIn("agent-rails setup --tool claude", result.output)
        self.assertIn("agent-rails verify", result.output)

    def test_explicit_values_override_shell_and_project_environment(self) -> None:
        environment = {
            **self.environment,
            "SHELL": "/usr/local/bin/fish",
            "AGENT_RAILS_PROJECT": str(self.root / "environment-project"),
            "AGENT_RAILS_PROFILE": str(self.root / "environment.profile"),
        }
        result = run_init(
            self.request(
                requested_shell=InitShell.BASH,
                requested_project=self.project,
                explicit_profile=self.profile,
                environment=environment,
            )
        )

        self.assertIs(result.shell, InitShell.BASH)
        self.assertEqual(result.project_path, self.project)
        self.assertEqual(result.profile_path, self.profile)
        self.assertIn(str(self.project), result.output)
        self.assertIn(str(self.profile), result.output)
        self.assertNotIn("environment-project", result.output)
        self.assertNotIn("environment.profile", result.output)

    def test_omitted_values_fall_back_to_environment(self) -> None:
        environment = {
            **self.environment,
            "SHELL": "/usr/local/bin/fish",
            "AGENT_RAILS_PROJECT": str(self.project),
            "AGENT_RAILS_PROFILE": str(self.profile),
        }
        result = run_init(
            self.request(requested_shell=None, environment=environment)
        )

        self.assertIs(result.shell, InitShell.FISH)
        self.assertEqual(result.project_path, self.project)
        self.assertEqual(result.profile_path, self.profile)
        self.assertIn(
            f'set -gx AGENT_RAILS_PROJECT "{self.project}"', result.output
        )
        self.assertIn(
            f'set -gx AGENT_RAILS_PROFILE "{self.profile}"', result.output
        )

    def test_project_without_profile_derives_user_project_profile(self) -> None:
        project = self.root / "projects/team project"
        result = run_init(
            self.request(
                requested_project=project,
                explicit_profile=None,
            )
        )
        expected = self.config_home / "profiles/projects/team project.profile"

        self.assertEqual(result.project_path, project)
        self.assertEqual(result.profile_path, expected)
        self.assertIn(f'export AGENT_RAILS_PROFILE="{expected}"', result.output)

    def test_rendering_never_creates_or_modifies_shell_rc_files(self) -> None:
        files = {
            self.user_home / ".zshrc": "zsh sentinel\n",
            self.user_home / ".bashrc": "bash sentinel\n",
            self.user_home / ".config/fish/config.fish": "fish sentinel\n",
        }
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        before = {path: path.stat() for path in files}

        for shell in (InitShell.ZSH, InitShell.BASH, InitShell.FISH):
            run_init(
                self.request(
                    requested_shell=shell,
                    requested_project=self.project,
                    explicit_profile=self.profile,
                )
            )

        for path, content in files.items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)
            self.assertEqual(path.stat().st_ino, before[path].st_ino)
            self.assertEqual(path.stat().st_mtime_ns, before[path].st_mtime_ns)

    def test_bash_and_zsh_guidance_treats_special_paths_as_literal_data(self) -> None:
        marker = self.root / "injected"
        hostile = (
            'segment"; touch "$INIT_MARKER"; #\n'
            '$(touch "$INIT_MARKER")`touch "$INIT_MARKER"`\rend'
        )
        kit_home = self.root / f"kit-{hostile}"
        project = self.root / f"project-{hostile}"
        profile = self.root / f"profile-{hostile}"
        environment = {
            **self.environment,
            "INIT_MARKER": str(marker),
        }

        for shell in (InitShell.BASH, InitShell.ZSH):
            with self.subTest(shell=shell.value):
                result = run_init(
                    replace(
                        self.request(
                            requested_shell=shell,
                            requested_project=project,
                            explicit_profile=profile,
                            environment=environment,
                        ),
                        kit_home=kit_home,
                    )
                )
                block = result.output.split("1. Add this block to ", 1)[1]
                block = block.split("# Agent Rails\n", 1)[1]
                block = block.split("\n2. Reload your shell:", 1)[0]
                probe = (
                    "# Agent Rails\n"
                    f"{block}\n"
                    'printf "%s\\0%s\\0%s\\0" "$AGENT_RAILS_HOME" '
                    '"$AGENT_RAILS_PROJECT" "$AGENT_RAILS_PROFILE"\n'
                )
                completed = subprocess.run(
                    (shell.value, "-c", probe),
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr.decode("utf-8", errors="replace"),
                )
                self.assertEqual(
                    completed.stdout.split(b"\0")[:3],
                    [
                        os.fsencode(kit_home),
                        os.fsencode(project),
                        os.fsencode(profile),
                    ],
                )
                self.assertFalse(marker.exists())
                self.assertNotIn("\r", result.output)

    def test_fish_guidance_escapes_control_characters_and_command_payloads(self) -> None:
        hostile = 'value (touch "$INIT_MARKER")\r\x1b]0;owned\x07'
        result = run_init(
            replace(
                self.request(
                    requested_shell=InitShell.FISH,
                    requested_project=self.root / hostile,
                    explicit_profile=self.root / f"profile-{hostile}",
                ),
                kit_home=self.root / f"kit-{hostile}",
            )
        )

        self.assertNotIn("\r", result.output)
        self.assertNotIn("\x1b", result.output)
        self.assertNotIn("\x07", result.output)
        self.assertNotIn('"; touch "$INIT_MARKER"; "', result.output)

    def test_cli_preserves_argument_over_environment_precedence(self) -> None:
        environment = {
            **self.environment,
            "SHELL": "/usr/local/bin/fish",
            "AGENT_RAILS_PROJECT": str(self.root / "environment-project"),
            "AGENT_RAILS_PROFILE": str(self.root / "environment.profile"),
        }
        exit_code, stdout, stderr = self.invoke_cli(
            (
                "--shell",
                "bash",
                "--project",
                str(self.project),
                "--profile",
                str(self.profile),
            ),
            environment,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn(str(self.user_home / ".bashrc"), stdout)
        self.assertIn(f'export AGENT_RAILS_PROJECT="{self.project}"', stdout)
        self.assertIn(f'export AGENT_RAILS_PROFILE="{self.profile}"', stdout)
        self.assertNotIn("environment-project", stdout)
        self.assertNotIn("environment.profile", stdout)

    def test_help_and_invalid_cli_arguments_use_shell_exit_semantics(self) -> None:
        exit_code, stdout, stderr = self.invoke_cli(("--help",))
        self.assertEqual(exit_code, 0)
        self.assertIn("Usage: agent-rails init", stdout)
        self.assertIn("does not edit shell rc files", stdout)
        self.assertEqual(stderr, "")

        invalid_cases = (
            (("--shell", "tcsh"), "Unsupported shell: tcsh"),
            (("--shell",), "Usage: agent-rails init"),
            (("--project",), "Usage: agent-rails init"),
            (("--profile",), "Usage: agent-rails init"),
            (("--unknown",), "Usage: agent-rails init"),
        )
        for arguments, message in invalid_cases:
            with self.subTest(arguments=arguments):
                exit_code, stdout, stderr = self.invoke_cli(arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout, "")
                self.assertIn(message, stderr)

    def test_invalid_typed_requests_fail_before_rendering(self) -> None:
        invalid_requests = (
            replace(self.request(), requested_shell="zsh"),
            replace(self.request(), requested_project="project"),
            replace(self.request(), explicit_profile="profile"),
            replace(self.request(), kit_home="kit"),
            replace(self.request(), environment={"HOME": object()}),
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(InitInputError) as raised:
                    run_init(request)  # type: ignore[arg-type]
                self.assertEqual(raised.exception.exit_code, 2)


if __name__ == "__main__":
    unittest.main()
