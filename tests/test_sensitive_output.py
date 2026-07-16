#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.security.sensitive_output import (  # noqa: E402
    SensitiveOutputError,
    redact_sensitive_output,
    scan_sensitive_output,
    scan_sensitive_records,
)


SENSITIVE_TEXT = '''SERVICE_ACCESS_KEY=unit-test-secret-shell-123456
authorization: Bearer unit-test-secret-header-123456
"api_key": "unit-test-secret-json-123456",
SERVICE_TOKEN_ENV="${SERVICE_TOKEN_ENV:-SERVICE_ACCESS_KEY}"
AGENT_RAILS_TIKTOKEN_ENCODING=cl100k_base
AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="$(normalize_positive_int "$AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE" 2)"
token = substr(content, 1, 1)
token=raw_tokens[i]
secret_findings_file="$tmp_dir/secret-findings"
api_token=build_token(config)
password=options.password
cookie="${!COOKIE_ENV-}"
-----BEGIN PRIVATE KEY-----
unit-test-private-key-material-123456
-----END PRIVATE KEY-----
'''


class SensitiveOutputTest(unittest.TestCase):
    def test_redaction_is_conservative_and_fail_closed(self) -> None:
        redacted = redact_sensitive_output(SENSITIVE_TEXT, format_name="text")

        self.assertEqual(
            redacted,
            '''SERVICE_ACCESS_KEY=<redacted>
authorization: <redacted>
"api_key": "<redacted>",
SERVICE_TOKEN_ENV="${SERVICE_TOKEN_ENV:-SERVICE_ACCESS_KEY}"
AGENT_RAILS_TIKTOKEN_ENCODING=cl100k_base
AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="<redacted>"
token = <redacted>
token=<redacted>
secret_findings_file="<redacted>"
api_token=<redacted>
password=<redacted>
cookie="<redacted>"
<redacted private key block>
''',
        )
        self.assertNotIn("unit-test-secret", redacted)
        self.assertNotIn("private-key-material", redacted)

    def test_scan_suppresses_placeholders_and_code_expressions(self) -> None:
        findings = scan_sensitive_output(
            SENSITIVE_TEXT, source_name="config/runtime.env", format_name="text"
        )

        self.assertEqual(
            findings,
            (
                "config/runtime.env:1: SERVICE_ACCESS_KEY=<redacted>",
                "config/runtime.env:2: authorization: <redacted>",
                'config/runtime.env:3: "api_key": "<redacted>",',
                "config/runtime.env:13: <redacted private key block>",
            ),
        )

    def test_diff_scan_maps_only_added_lines_to_source_locations(self) -> None:
        diff = '''diff --git config/runtime.env config/runtime.env
--- config/runtime.env
+++ config/runtime.env
@@ -10,1 +10,4 @@
-LEGACY_TOKEN=unit-test-removed-secret-123456
+API_TOKEN=unit-test-added-secret-123456
+-----BEGIN PRIVATE KEY-----
+unit-test-added-private-key-material-123456
+-----END PRIVATE KEY-----
'''

        findings = scan_sensitive_output(
            diff, source_name="input.diff", format_name="diff"
        )

        self.assertEqual(
            findings,
            (
                "config/runtime.env:10: API_TOKEN=<redacted>",
                "config/runtime.env:11: <redacted private key block>",
            ),
        )

    def test_diff_scan_does_not_confuse_added_content_with_file_header(self) -> None:
        diff = '''diff --git config/runtime.env config/runtime.env
--- config/runtime.env
+++ config/runtime.env
@@ -0,0 +1 @@
+++ {"api_token": "unit-test-added-prefix-secret-123456"}
'''

        findings = scan_sensitive_output(
            diff, source_name="input.diff", format_name="diff"
        )

        self.assertEqual(
            findings,
            ('config/runtime.env:1: ++ {"api_token": "<redacted>"',),
        )

    def test_diff_redaction_preserves_markers_and_hides_both_sides(self) -> None:
        diff = '''diff --git config.env config.env
--- config.env
+++ config.env
@@ -1 +1 @@
-OLD_TOKEN=removed-secret
+NEW_TOKEN=added-secret
'''
        self.assertEqual(
            redact_sensitive_output(diff, format_name="diff"),
            '''diff --git config.env config.env
--- config.env
+++ config.env
@@ -1 +1 @@
-OLD_TOKEN=<redacted>
+NEW_TOKEN=<redacted>
''',
        )

        private_key_diff = ''' -----BEGIN PRIVATE KEY-----
-old-private-key-material
+new-private-key-material
 -----END PRIVATE KEY-----
'''
        self.assertEqual(
            redact_sensitive_output(private_key_diff, format_name="diff"),
            " <redacted private key block>\n",
        )

    def test_pgp_private_key_blocks_are_redacted_in_text_and_diff(self) -> None:
        block = '''-----BEGIN PGP PRIVATE KEY BLOCK-----
unit-test-pgp-private-key-material-123456
-----END PGP PRIVATE KEY BLOCK-----
'''
        self.assertEqual(
            redact_sensitive_output(block, format_name="text"),
            "<redacted private key block>\n",
        )
        self.assertEqual(
            scan_sensitive_output(
                block, source_name="private.asc", format_name="text"
            ),
            ("private.asc:1: <redacted private key block>",),
        )

        diff = '''diff --git private.asc private.asc
--- private.asc
+++ private.asc
@@ -0,0 +1,3 @@
+-----BEGIN PGP PRIVATE KEY BLOCK-----
+unit-test-pgp-private-key-material-123456
+-----END PGP PRIVATE KEY BLOCK-----
'''
        self.assertEqual(
            scan_sensitive_output(diff, source_name="input.diff", format_name="diff"),
            ("private.asc:1: <redacted private key block>",),
        )

    def test_unknown_format_is_rejected(self) -> None:
        with self.assertRaisesRegex(SensitiveOutputError, "Unknown sensitive-output format"):
            redact_sensitive_output("TOKEN=value\n", format_name="json")
        with self.assertRaisesRegex(SensitiveOutputError, "Unknown sensitive-output format"):
            scan_sensitive_output(
                "TOKEN=value\n", source_name="input", format_name="json"
            )

    def test_assignment_spacing_quotes_and_trailing_comma_are_preserved(self) -> None:
        self.assertEqual(
            redact_sensitive_output(
                "export API_TOKEN:\t'secret-value',  \n", format_name="text"
            ),
            "export API_TOKEN:\t'<redacted>',\n",
        )

    def test_scan_treats_a_lone_quote_as_an_empty_placeholder(self) -> None:
        for quote in ('"', "'", "`"):
            with self.subTest(quote=quote):
                self.assertEqual(
                    scan_sensitive_output(
                        f"api_token={quote}\n",
                        source_name="assertions.sh",
                        format_name="text",
                    ),
                    (),
                )

    def test_record_scanner_yields_without_consuming_the_whole_input(self) -> None:
        def records():
            yield "API_TOKEN=unit-test-streaming-secret-123456"
            raise AssertionError("scanner consumed beyond the first finding")

        findings = scan_sensitive_records(
            records(), source_name="large.env", format_name="text"
        )

        self.assertEqual(next(findings), "large.env:1: API_TOKEN=<redacted>")

    def test_cli_rejects_in_place_redaction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-sensitive-") as temp_dir:
            path = Path(temp_dir) / "input.txt"
            path.write_text("API_TOKEN=secret\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    "python3",
                    "-E",
                    str(ROOT / "scripts" / "agent-python-cli.py"),
                    "sensitive-output",
                    "redact",
                    "--input",
                    str(path),
                    "--output",
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn(
                "Sensitive-output redaction requires different input and output paths.",
                completed.stderr,
            )
            self.assertEqual(path.read_text(encoding="utf-8"), "API_TOKEN=secret\n")

    def test_cli_redacts_non_utf8_input_without_losing_unrelated_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-sensitive-bytes-") as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.txt"
            output_path = temp / "output.txt"
            input_path.write_bytes(b"API_TOKEN=secret\xff\nLABEL=value\xff\n")
            output_path.write_text("sentinel\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    "python3",
                    "-E",
                    str(ROOT / "scripts" / "agent-python-cli.py"),
                    "sensitive-output",
                    "redact",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(
                output_path.read_bytes(),
                b"API_TOKEN=<redacted>\nLABEL=value\xff\n",
            )


if __name__ == "__main__":
    unittest.main()
