#!/usr/bin/env python3
"""No-GUI integration tests for the Edit Anywhere supervisor and remote UI."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
sys.path.insert(0, str(TESTS_DIR))

from benchmark import Harness  # noqa: E402


class StaticContractTests(unittest.TestCase):
    def test_schemas_are_strict_json_objects(self) -> None:
        schema_dir = REPO_ROOT / "edit-anywhere" / "schema"
        for name in ("request-v1.json", "decision-v1.json", "result-v1.json"):
            with self.subTest(name=name):
                schema = json.loads((schema_dir / name).read_text(encoding="utf-8"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(schema["properties"]["protocol_version"]["const"], 1)

    def test_shell_entrypoints_parse(self) -> None:
        for name in (
            "edit-anywhere-server",
            "edit-anywhere-nvim",
            "edit-anywhere-quick-terminal",
        ):
            with self.subTest(name=name):
                completed = subprocess.run(
                    ["zsh", "-n", str(REPO_ROOT / "bin" / name)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_entrypoint_has_one_server_backend(self) -> None:
        source = (REPO_ROOT / "bin" / "edit-anywhere-nvim").read_text(encoding="utf-8")
        self.assertNotIn("edit-anywhere-spawn", source)
        self.assertNotIn("EDIT_ANYWHERE_NVIM_MODE", source)
        self.assertNotIn("admit-cold", source)

    def test_ocr_uses_vision_mode_with_chinese_support(self) -> None:
        source = (REPO_ROOT / "bin" / "edit-anywhere-ocr").read_text(encoding="utf-8")
        self.assertIn("request.recognitionLevel = .accurate", source)
        self.assertNotIn("request.recognitionLevel = .fast", source)
        self.assertIn('"zh-Hans"', source)
        self.assertIn('"zh-Hant"', source)
        self.assertIn('"en-US"', source)
        self.assertIn("request.usesLanguageCorrection = true", source)

    def test_deepseek_fim_uses_focused_sampling(self) -> None:
        source = (REPO_ROOT / "edit-anywhere" / "nvim" / "lua" / "edit_anywhere" / "bootstrap.lua").read_text(
            encoding="utf-8"
        )
        self.assertIn("temperature = 0.2", source)
        self.assertNotIn("top_p =", source)
        self.assertNotIn("stop =", source)

    def test_supervisor_has_one_fail_closed_admission_path(self) -> None:
        source = (REPO_ROOT / "bin" / "edit-anywhere-server").read_text(encoding="utf-8")
        self.assertNotIn("admit_cold_command()", source)
        self.assertNotIn("fallback_allowed\\\":true", source)
        self.assertIn("publish_supervisor_rejection", source)

    def test_resume_requires_an_accepted_active_session(self) -> None:
        for name in ("edit-anywhere-nvim", "edit-anywhere-server"):
            with self.subTest(name=name):
                source = (REPO_ROOT / "bin" / name).read_text(encoding="utf-8")
                self.assertIn("if [[ \"${response}\" == *'\"accepted\":true'* ]]", source)

    def test_supervisor_failure_never_launches_a_second_backend(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ea-entry-test.", dir="/tmp") as temporary:
            root = Path(temporary)
            cache = root / "cache"
            session_id = "20260901-193125-CA438737"
            session_dir = cache / "sessions" / session_id
            session_dir.mkdir(parents=True)
            request = {
                "protocol_version": 1,
                "session_id": session_id,
                "nonce": "0123456789abcdefghijklmn",
            }
            (session_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")

            supervisor = root / "supervisor"
            supervisor.write_text(
                "#!/bin/zsh\n[[ \"$1\" == admit ]] || exit 99\nexit 70\n",
                encoding="utf-8",
            )
            marker = root / "spawned"
            supervisor.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                {
                    "EDIT_ANYWHERE_CACHE_ROOT": str(cache),
                    "EDIT_ANYWHERE_SERVER_BIN": str(supervisor),
                    "EDIT_ANYWHERE_NVIM_BIN": "/usr/bin/true",
                }
            )
            completed = subprocess.run(
                [str(REPO_ROOT / "bin" / "edit-anywhere-nvim"), session_id],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 70)
            self.assertFalse(marker.exists())

    def test_orphaned_session_does_not_attempt_resume_or_print_terminal_noise(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ea-orphan-test.", dir="/tmp") as temporary:
            root = Path(temporary)
            cache = root / "cache"
            session_id = "20260901-193125-CA438737"
            session_dir = cache / "sessions" / session_id
            server_dir = cache / "server"
            session_dir.mkdir(parents=True)
            server_dir.mkdir(parents=True)
            request = {
                "protocol_version": 1,
                "session_id": session_id,
                "nonce": "0123456789abcdefghijklmn",
            }
            (session_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")

            socket_path = server_dir / "nvim.sock"
            import socket

            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(socket_path))
            fake_nvim = root / "nvim"
            invocation_log = root / "nvim-invocations"
            fake_nvim.write_text(
                "#!/bin/zsh\n"
                f"print -r -- \"$*\" >> {invocation_log!s}\n"
                "print -r -- '{\"accepted\":false,\"state\":\"recovery_required\","
                "\"reason\":\"ACCEPTED_SESSION_ORPHANED\"}'\n",
                encoding="utf-8",
            )
            fake_nvim.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                {
                    "EDIT_ANYWHERE_CACHE_ROOT": str(cache),
                    "EDIT_ANYWHERE_NVIM_BIN": str(fake_nvim),
                    "EDIT_ANYWHERE_SERVER_BIN": "/usr/bin/false",
                }
            )
            try:
                completed = subprocess.run(
                    [str(REPO_ROOT / "bin" / "edit-anywhere-nvim"), session_id],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
            finally:
                listener.close()

            self.assertEqual(completed.returncode, 75)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(len(invocation_log.read_text(encoding="utf-8").splitlines()), 1)


class FullHostServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cache_root = Path(tempfile.mkdtemp(prefix="ea-test.", dir="/tmp"))
        cls.harness = Harness(cls.cache_root)
        cls.harness.start()
        cls.initial_pid = cls.harness.server_pid()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.harness.stop()
        shutil.rmtree(cls.cache_root, ignore_errors=True)

    def test_01_identity_and_health(self) -> None:
        identity = json.loads(self.harness.manager("identity").stdout)
        health = self.harness.health()
        self.assertEqual(identity["name"], "edit-anywhere")
        self.assertEqual(identity["protocol_version"], 1)
        self.assertEqual(identity["server_uuid"], health["server_uuid"])
        self.assertEqual(identity["generation"], health["generation"])
        self.assertEqual(identity["config_fingerprint"], health["config_fingerprint"])
        self.assertEqual(health["state"], "IDLE")
        self.assertTrue(health["prewarmed"])
        self.assertTrue(health["adapters_ok"])
        self.assertTrue(health["layout_ok"])

    def test_02_cancel_detaches_without_output(self) -> None:
        sample = self.harness.run_session(body="cancel-me", action="cancel")
        self.assertEqual(sample["status"], "cancelled")
        self.assertEqual(sample["server_pid"], self.initial_pid)
        self.assertTrue(sample["ready_screen_has_ocr_status"])

    def test_03_commit_preserves_body_and_digest(self) -> None:
        sample = self.harness.run_session(
            body="alpha",
            action="commit",
            inserted_text="-omega",
        )
        session_dir = self.cache_root / "sessions" / sample["session_id"]
        output = session_dir / "output.md"
        result = json.loads((session_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(output.read_text(encoding="utf-8"), "alpha-omega")
        self.assertEqual(result["output_sha256"], hashlib.sha256(b"alpha-omega").hexdigest())
        self.assertEqual(sample["server_pid"], self.initial_pid)

    def test_04_no_replace_supervisor_decision(self) -> None:
        session_id, _, session_dir = self.harness.create_session("never-opened")
        first = self.harness.manager("reject", session_id, check=False)
        same = self.harness.manager("reject", session_id, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(same.returncode, 0, same.stderr)
        decision = json.loads((session_dir / "decision.json").read_text(encoding="utf-8"))
        self.assertEqual(decision["reason"], "DECISION_LOST")
        self.assertFalse(decision["fallback_allowed"])

    def test_05_sequential_session_reuses_server(self) -> None:
        sample = self.harness.run_session(body="second-cancel", action="cancel")
        self.assertEqual(sample["status"], "cancelled")
        self.assertEqual(sample["server_pid"], self.initial_pid)

    def test_06_server_has_no_attached_ui_after_terminal_result(self) -> None:
        completed = subprocess.run(
            [
                self.harness.nvim,
                "--server",
                str(self.harness.socket),
                "--remote-expr",
                "len(nvim_list_uis())",
            ],
            env=self.harness.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "0")
        self.assertEqual(self.harness.server_pid(), self.initial_pid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
