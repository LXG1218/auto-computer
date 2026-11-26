import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skill" / "auto-computer" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import setup_auth


class SetupAndUpgradeTests(unittest.TestCase):
    def test_collect_status_reports_persistence_dirs(self):
        status = setup_auth.collect_status()
        self.assertTrue(status["ok"])
        self.assertIn("persistence", status)
        self.assertTrue(status["persistence"]["adaptersDir"].endswith("adapters"))
        self.assertTrue(status["persistence"]["secretsDir"].endswith("secrets"))

    def test_pair_browser_cmd_executes_check_json(self):
        cmd_path = REPO_ROOT / "pair-browser.cmd"
        self.assertTrue(cmd_path.is_file())
        proc = subprocess.run(
            ["cmd.exe", "/d", "/c", str(cmd_path), "--check", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("persistence", payload)

    def test_install_ps1_preserves_adapters_and_secrets_across_upgrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            home_dir = Path(tmp) / "AutoComputer"
            adapter_file = home_dir / "adapters" / "custom-app.json"
            secret_file = home_dir / "secrets" / "chrome-default.dpapi"
            adapter_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            adapter_bytes = b'{"app":"custom","action":"play","route":"uia"}\n'
            secret_bytes = b"DPAPI-PLACEHOLDER-SECRET-DATA"
            adapter_file.write_bytes(adapter_bytes)
            secret_file.write_bytes(secret_bytes)

            install_ps1 = REPO_ROOT / "install.ps1"
            tools_dir = r"E:\CodexTools\AutoComputerCandidate"
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(install_ps1),
                "-Target",
                "generic",
                "-HomeDir",
                str(home_dir),
                "-PythonExe",
                sys.executable,
                "-ToolsDir",
                tools_dir if Path(tools_dir).is_dir() else str(home_dir / "tools"),
                "-SkipNode",
                "-NoUserEnv",
            ]
            # Run twice to verify idempotent upgrade safety
            for run_idx in range(2):
                proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                self.assertEqual(0, proc.returncode, msg=f"Run {run_idx + 1} failed:\n{proc.stdout}\n{proc.stderr}")

            self.assertEqual(adapter_bytes, adapter_file.read_bytes())
            self.assertEqual(secret_bytes, secret_file.read_bytes())
            self.assertTrue((home_dir / "pair-browser.cmd").is_file())
            self.assertTrue((home_dir / "setup-auth.ps1").is_file())
            self.assertTrue((home_dir / "mcp-config.json").is_file())
            self.assertTrue((home_dir / "skills" / "auto-computer" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
