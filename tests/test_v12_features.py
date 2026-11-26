from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = REPO_ROOT / "mcp"
sys.path.insert(0, str(MCP_DIR))

import desktop_mcp  # noqa: E402
import foreground_guard  # noqa: E402


class DesktopMcpBudgetAndDeltaTests(unittest.TestCase):
    def test_inspect_budget_truncates_and_computes_delta(self):
        controls_v1 = [
            {"controlType": "Button", "automationId": f"btn_{i}", "name": f"Action {i}", "depth": 1, "className": ""}
            for i in range(40)
        ]
        res1 = desktop_mcp._apply_inspect_budget({"ok": True, "controls": controls_v1, "count": 40}, "12345", max_bytes=2000)
        self.assertTrue(res1.get("tree_id"))
        self.assertTrue(res1.get("truncatedByBytes"))
        self.assertLessEqual(len(json.dumps(res1, separators=(",", ":")).encode("utf-8")), 2000)

        controls_v2 = [dict(c) for c in controls_v1]
        controls_v2[3]["name"] = "Action 3 Updated"
        delta = desktop_mcp._apply_inspect_budget(
            {"ok": True, "controls": controls_v2, "count": 40},
            "12345",
            max_bytes=12000,
            delta_base=res1["tree_id"],
        )
        self.assertTrue(delta.get("delta"))
        self.assertEqual([], delta["added"])
        self.assertEqual([], delta["removed"])
        self.assertEqual(1, len(delta["changed"]))
        self.assertEqual("Action 3 Updated", delta["changed"][0]["name"])

        miss = desktop_mcp._apply_inspect_budget(
            {"ok": True, "controls": controls_v2[:5], "count": 5},
            "99999",
            delta_base=res1["tree_id"],
        )
        self.assertEqual("hwnd_mismatch", miss.get("delta_fallback"))

    def test_compact_control_omits_defaults_and_flattens_bounds(self):
        compacted = desktop_mcp._compact_control({
            "name": "OK",
            "automationId": "btnOk",
            "controlType": "Button",
            "className": "",
            "isEnabled": True,
            "isOffscreen": False,
            "bounds": {"x": 10, "y": 20, "width": 80, "height": 30},
            "patterns": ["Invoke"],
        })
        self.assertNotIn("isEnabled", compacted)
        self.assertNotIn("isOffscreen", compacted)
        self.assertNotIn("className", compacted)
        self.assertEqual([10, 20, 80, 30], compacted["bounds"])
        self.assertEqual(["Invoke"], compacted["patterns"])

        non_default = desktop_mcp._compact_control({
            "name": "Disabled",
            "isEnabled": False,
            "isOffscreen": True,
        })
        self.assertFalse(non_default["isEnabled"])
        self.assertTrue(non_default["isOffscreen"])

    def test_persistent_uia_daemon_and_ps_wrapper(self):
        worker = REPO_ROOT / "skill" / "auto-computer" / "scripts" / "win_uia_worker.py"
        client = desktop_mcp._UiaDaemonClient(worker)
        try:
            r1 = client.request({"mode": "windows", "includeUntitled": True})
            self.assertTrue(r1.get("ok"), msg=r1)
            self.assertIsInstance(r1.get("windows"), list)

            # Error in one request must not kill the daemon
            r_err = client.request({"mode": "inspect", "hwnd": "0x0"})
            self.assertFalse(r_err.get("ok"))

            r2 = client.request({"mode": "windows", "process": "explorer"})
            self.assertTrue(r2.get("ok"), msg=r2)
        finally:
            client.close()

        ps_script = REPO_ROOT / "skill" / "auto-computer" / "scripts" / "win_uia.ps1"
        env = os.environ.copy()
        env["AUTO_COMPUTER_PYTHON"] = sys.executable
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-File", str(ps_script), "-Mode", "windows"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            env=env,
        )
        self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
        parsed = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(parsed.get("ok"), msg=parsed)

    def test_foreground_guard_exports_banner_text(self):
        self.assertIn("Esc", foreground_guard.BANNER_TEXT)

    def test_check_point_not_covered_blocks_obscuring_window(self):
        from unittest import mock
        import ctypes

        fake_user32 = mock.MagicMock()
        fake_user32.WindowFromPoint.return_value = 999
        fake_user32.GetAncestor.return_value = 888
        with (
            mock.patch.object(desktop_mcp.os, "name", "nt"),
            mock.patch.object(ctypes, "WinDLL", return_value=fake_user32),
        ):
            def fill_pid(_hwnd, ptr):
                ptr._obj.value = 4321
                return 1
            fake_user32.GetWindowThreadProcessId.side_effect = fill_pid
            with self.assertRaisesRegex(desktop_mcp.BridgeError, "POINT_COVERED"):
                desktop_mcp._check_point_not_covered(111, 1000, 10, 20)
            # Same window HWND should pass
            fake_user32.GetAncestor.return_value = 111
            desktop_mcp._check_point_not_covered(111, 1000, 10, 20)

    def test_run_watched_handles_large_output_without_deadlock(self):
        script = "import sys; sys.stdout.write('O' * 40000); sys.stderr.write('E' * 12000)"
        rc, out, err = desktop_mcp._run_watched(
            [sys.executable, "-c", script],
            timeout_s=5.0,
            abort_file=None,
            label="large-pipe-test",
        )
        self.assertEqual(0, rc)
        self.assertEqual(40000, len(out))
        self.assertEqual(12000, len(err))


class EdgeMcpNamedPipeTests(unittest.TestCase):
    def test_edge_mcp_tools_list_and_live_named_pipe(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<!doctype html><html><head><title>PipeTest</title></head><body><h1>Named Pipe OK</h1><button id='go'>Click</button></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_PROFILE_ROOT"] = tmp
                reqs = [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "visible": False,
                                "wait_selector": "#go",
                            },
                        },
                    },
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "stop_auto_browser", "arguments": {"browser": "edge", "profile": "auto"}}},
                ]
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input="\n".join(json.dumps(r) for r in reqs) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                responses = {obj["id"]: obj for obj in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())}

                tools = responses[2]["result"]["tools"]
                open_tool = next(t for t in tools if t["name"] == "open_and_run")
                self.assertEqual(["auto", "kept"], open_tool["inputSchema"]["properties"]["profile"]["enum"])

                run_res = responses[3]["result"]["structuredContent"]
                self.assertTrue(run_res["ok"], msg=run_res)
                self.assertEqual("PipeTest", run_res["title"])
                self.assertIn("Named Pipe OK", run_res.get("summary_text", ""))

                stop_res = responses[4]["result"]["structuredContent"]
                self.assertTrue(stop_res["ok"])
                self.assertEqual(["auto:edge"], stop_res["stopped"])
                # Named pipe should not rely on DevToolsActivePort being parsed; profile dir exists cleanly
                self.assertTrue((Path(tmp) / "edge-auto").is_dir())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
