#!/usr/bin/env python3

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


NVIM_AGENT = Path(__file__).with_name("nvim-agent")


class NvimAgentTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.recovery_dir = self.root / "recovery"

        self._write_executable(
            "tmux",
            r"""
            #!/usr/bin/env python3
            import os
            import subprocess
            import sys
            import time

            command = sys.argv[1]
            if command == "split-window":
                subprocess.Popen(
                    ["/bin/sh", "-c", sys.argv[-1]],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print("%2")
            elif command == "display-message":
                # Let the editor publish its status after the parent checks the
                # empty file, then report that tmux has already removed the pane.
                time.sleep(0.15)
            else:
                sys.exit(f"unexpected tmux command: {command}")
            """,
        )
        self.editor = self._write_executable(
            "fake-editor",
            r"""
            #!/bin/sh
            printf '%s\n' 'edited prompt' >>"$1"
            sleep 0.05
            exit "${FAKE_EDITOR_STATUS:-0}"
            """,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_executable(self, name: str, content: str) -> Path:
        path = self.bin_dir / name
        path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run_agent(self, editor_status: int = 0) -> tuple[subprocess.CompletedProcess[str], Path]:
        prompt = self.root / "prompt.txt"
        prompt.write_text("original prompt\n", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env['PATH']}",
                "TMUX": "/tmp/fake-tmux,1,0",
                "TMUX_PANE": "%1",
                "DOTAGENT_AGENT": "codex",
                "NVIM_AGENT_LAYOUT": "right",
                "NVIM_AGENT_NVIM": str(self.editor),
                "NVIM_AGENT_RECOVERY_DIR": str(self.recovery_dir),
                "NVIM_AGENT_SHELL_CONTEXT": "0",
                "FAKE_EDITOR_STATUS": str(editor_status),
            }
        )
        result = subprocess.run(
            [str(NVIM_AGENT), str(prompt)],
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result, prompt

    def test_clean_exit_survives_pane_disappearance_race(self):
        result, prompt = self._run_agent()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(prompt.read_text(encoding="utf-8"), "original prompt\nedited prompt\n")
        self.assertFalse(self.recovery_dir.exists())

    def test_failed_edit_is_preserved_for_recovery(self):
        result, prompt = self._run_agent(editor_status=129)

        self.assertEqual(result.returncode, 129)
        recovery_files = list(self.recovery_dir.glob("edit-*.txt"))
        self.assertEqual(len(recovery_files), 1)
        self.assertEqual(
            recovery_files[0].read_text(encoding="utf-8"),
            prompt.read_text(encoding="utf-8"),
        )
        self.assertIn(str(recovery_files[0]), result.stderr)


if __name__ == "__main__":
    unittest.main()
