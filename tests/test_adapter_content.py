#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.adapters.content import (
    AdapterArtifact,
    AdapterContentError,
    AdapterContentRequest,
    AdapterType,
    extract_adapter_profile,
    render_adapter_content,
)


class AdapterContentTest(unittest.TestCase):
    def test_all_legacy_artifacts_remain_byte_compatible(self) -> None:
        expected = {
            (AdapterType.CLAUDE, AdapterArtifact.GUIDE): "b0ab83f5fee566c69a7b971221a0b67e0b0c1c40d2dc54d75796ad20b74c1895",
            (AdapterType.CLAUDE, AdapterArtifact.PACK): "42f0e9f691edf367d883b86919a98ebb36bc9fd8a22208003c8d91dc4572a06b",
            (AdapterType.CLAUDE, AdapterArtifact.LITE): "25724f2f80c325008d7a89dd7490c38cef87230dd84db3428f236d4ce5f6d601",
            (AdapterType.CLAUDE, AdapterArtifact.CHECK): "391ecdcd02c31fd16d80f5895098f9e5bf3d9de07c3c28fc68c6f157dd85bc57",
            (AdapterType.CLAUDE, AdapterArtifact.CLAUDE_BLOCK): "093baa52047c56e94cc33cfe72c952e8cc459d1fdde763da29babfceefacf48f",
            (AdapterType.OPENCODE, AdapterArtifact.GUIDE): "da14d61432805039692208e13cca62c6e3e849b9b20597f282aceec78626f121",
            (AdapterType.OPENCODE, AdapterArtifact.PACK): "5df78df2706e1fd2326d2fd55479140c38fc9a192356adb4a2ed672a7357758c",
            (AdapterType.OPENCODE, AdapterArtifact.LITE): "3a97eea5b9f15986f4209c9222f36d27da0c42348dc3807c0f127baecbf9bb83",
            (AdapterType.OPENCODE, AdapterArtifact.CHECK): "b23b2527d25e1276a04a500a589e636280e16936c36c07e197c48c8bfff8866f",
        }
        for (adapter, artifact), digest in expected.items():
            with self.subTest(adapter=adapter.value, artifact=artifact.value):
                rendered = render_adapter_content(
                    AdapterContentRequest(
                        adapter=adapter,
                        version="9.9.9",
                        executable="/kit/bin/agent-rails",
                        profile="/profiles/demo.profile",
                    ),
                    artifact,
                )
                self.assertEqual(
                    hashlib.sha256(rendered.encode("utf-8")).hexdigest(), digest
                )

    def test_empty_profile_omits_profile_argument(self) -> None:
        rendered = render_adapter_content(
            AdapterContentRequest(
                adapter=AdapterType.CLAUDE,
                version="1.0.0",
                executable="agent-rails",
            ),
            AdapterArtifact.PACK,
        )

        self.assertNotIn("--profile", rendered)
        self.assertIn('agent-rails pack --project "$project_root"', rendered)

    def test_executable_is_shell_quoted(self) -> None:
        executable = "/kit path/bin/agent-rails; false"
        fixtures = (
            (AdapterType.CLAUDE, AdapterArtifact.GUIDE),
            (AdapterType.CLAUDE, AdapterArtifact.PACK),
            (AdapterType.CLAUDE, AdapterArtifact.LITE),
            (AdapterType.CLAUDE, AdapterArtifact.CHECK),
            (AdapterType.CLAUDE, AdapterArtifact.CLAUDE_BLOCK),
            (AdapterType.OPENCODE, AdapterArtifact.GUIDE),
            (AdapterType.OPENCODE, AdapterArtifact.PACK),
            (AdapterType.OPENCODE, AdapterArtifact.LITE),
            (AdapterType.OPENCODE, AdapterArtifact.CHECK),
        )
        for adapter, artifact in fixtures:
            with self.subTest(adapter=adapter.value, artifact=artifact.value):
                rendered = render_adapter_content(
                    AdapterContentRequest(
                        adapter=adapter,
                        version="1.0.0",
                        executable=executable,
                    ),
                    artifact,
                )
                self.assertIn("'/kit path/bin/agent-rails; false'", rendered)
                self.assertNotIn("/kit path/bin/agent-rails; false check", rendered)

    def test_shell_active_profile_round_trips_through_safe_metadata(self) -> None:
        profile = '/profiles/x"; printf PWNED; $HOME `false` \\ demo.profile'
        fixtures = (
            (AdapterType.CLAUDE, AdapterArtifact.GUIDE),
            (AdapterType.CLAUDE, AdapterArtifact.CLAUDE_BLOCK),
            (AdapterType.OPENCODE, AdapterArtifact.GUIDE),
        )
        for adapter, artifact in fixtures:
            with self.subTest(adapter=adapter.value, artifact=artifact.value):
                rendered = render_adapter_content(
                    AdapterContentRequest(
                        adapter=adapter,
                        version="1.0.0",
                        executable="agent-rails",
                        profile=profile,
                    ),
                    artifact,
                )
                self.assertEqual(extract_adapter_profile(rendered), profile)
                self.assertIn("agent-rails:profile-b64:", rendered)
                self.assertNotIn('--profile "/profiles/x";', rendered)

    def test_multiline_and_surrogate_values_are_rejected(self) -> None:
        for profile in ("line\nnext", "surrogate-\udcff"):
            with self.subTest(profile=profile):
                with self.assertRaises(AdapterContentError):
                    render_adapter_content(
                        AdapterContentRequest(
                            adapter=AdapterType.CLAUDE,
                            version="1.0.0",
                            executable="agent-rails",
                            profile=profile,
                        ),
                        AdapterArtifact.PACK,
                    )

    def test_multiline_version_and_executable_are_rejected(self) -> None:
        for field in ("version", "executable"):
            values = {
                "adapter": AdapterType.CLAUDE,
                "version": "1.0.0",
                "executable": "agent-rails",
            }
            values[field] = "safe\nforged"
            with self.subTest(field=field):
                with self.assertRaises(AdapterContentError):
                    render_adapter_content(
                        AdapterContentRequest(**values), AdapterArtifact.GUIDE
                    )

    def test_invalid_version_empty_executable_and_wrong_types_are_rejected(self) -> None:
        with self.assertRaises(AdapterContentError):
            render_adapter_content(
                AdapterContentRequest(
                    adapter=AdapterType.CLAUDE,
                    version="1.0<!--",
                    executable="agent-rails",
                ),
                AdapterArtifact.GUIDE,
            )
        with self.assertRaises(AdapterContentError):
            render_adapter_content(
                AdapterContentRequest(
                    adapter=AdapterType.CLAUDE,
                    version="1.0.0",
                    executable="",
                ),
                AdapterArtifact.GUIDE,
            )
        with self.assertRaises(AdapterContentError):
            render_adapter_content(  # type: ignore[arg-type]
                AdapterContentRequest(
                    adapter="claude",  # type: ignore[arg-type]
                    version="1.0.0",
                    executable="agent-rails",
                ),
                AdapterArtifact.GUIDE,
            )

    def test_profile_metadata_and_legacy_fallback_fail_closed(self) -> None:
        self.assertEqual(
            extract_adapter_profile('command --profile "/profiles/legacy.profile"'),
            "/profiles/legacy.profile",
        )
        with self.assertRaises(AdapterContentError):
            extract_adapter_profile(
                "<!-- agent-rails:profile-b64:not-valid-*** -->\n"
            )
        with self.assertRaises(AdapterContentError):
            extract_adapter_profile("<!-- agent-rails:profile-b64: -->\n")

    def test_trusted_cli_ignores_target_project_shadow_package(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-adapter-shadow-") as temp_dir:
            shadow = Path(temp_dir) / "agent_rails" / "adapters"
            shadow.mkdir(parents=True)
            (shadow.parent / "__init__.py").write_text("", encoding="utf-8")
            (shadow / "__init__.py").write_text("", encoding="utf-8")
            (shadow / "content.py").write_text(
                "raise RuntimeError('shadow package loaded')\n", encoding="utf-8"
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = temp_dir
            completed = subprocess.run(
                (
                    sys.executable,
                    "-E",
                    str(ROOT / "scripts" / "agent-python-cli.py"),
                    "adapter-content",
                    "--adapter",
                    "claude",
                    "--artifact",
                    "guide",
                    "--version",
                    "1.0.0",
                    "--bin",
                    "agent-rails",
                ),
                cwd=temp_dir,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("<!-- agent-rails:generated -->", completed.stdout)
        self.assertNotIn("shadow package loaded", completed.stderr)


if __name__ == "__main__":
    unittest.main()
