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
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = REPO_ROOT / "mcp"
sys.path.insert(0, str(MCP_DIR))

import desktop_mcp  # noqa: E402
import foreground_guard  # noqa: E402


class DesktopMcpBudgetAndDeltaTests(unittest.TestCase):
    def test_binding_run_actions_resolves_one_exact_background_selector(self):
        action = {"op": "read", "binding": "status"}
        binding = {"schema_version": 1, "app": "Sample", "action": "status", "route": "uia",
                   "verification": "tested_background", "selector": {"automation_id": "ready", "expect_control_type": "Text"}}
        with mock.patch.object(desktop_mcp.os, "name", "nt"), \
             mock.patch.object(desktop_mcp.adapter_registry, "window_owner", return_value=(77, "Sample")) as owner, \
             mock.patch.object(desktop_mcp.adapter_registry, "get_binding", return_value=binding) as lookup, \
             mock.patch.object(desktop_mcp, "_run_uia", return_value={"ok": True, "results": [{"index": 0, "op": "read"}]}) as run:
            result = desktop_mcp._call("run_actions", {"hwnd": "0x1234", "binding_app": "Sample", "actions": [action]})
        self.assertTrue(result["ok"])
        owner.assert_called_once_with(0x1234)
        lookup.assert_called_once_with("Sample", "status")
        payload = run.call_args.args[0]
        self.assertEqual(77, payload["expectedPid"])
        self.assertEqual({"automationId": "ready", "controlType": "Text", "op": "read", "_requireUnique": True}, payload["actions"][0])

    def test_inspect_budget_truncates_and_computes_delta(self):
        controls_v1 = [
            {"controlType": "Button", "automationId": f"btn_{i}", "name": f"Action {i}", "depth": 1, "className": ""}
            for i in range(40)
        ]
        partial = desktop_mcp._apply_inspect_budget({"ok": True, "controls": controls_v1, "count": 40}, "12345", max_bytes=2000)
        self.assertTrue(partial.get("truncatedByBytes"))
        self.assertLessEqual(len(json.dumps(partial, separators=(",", ":")).encode("utf-8")), 2000)
        res1 = desktop_mcp._apply_inspect_budget({"ok": True, "controls": controls_v1, "count": 40}, "12345", max_bytes=12000)
        self.assertTrue(res1.get("tree_id"))

        partial_delta = desktop_mcp._apply_inspect_budget(
            {"ok": True, "controls": controls_v1, "count": 40}, "12345",
            max_bytes=12000, delta_base=partial["tree_id"],
        )
        self.assertEqual("baseline_not_found", partial_delta.get("delta_fallback"))

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
        next_delta = desktop_mcp._apply_inspect_budget(
            {"ok": True, "controls": controls_v2, "count": 40}, "12345",
            delta_base=delta["tree_id"],
        )
        self.assertTrue(next_delta["delta"])
        self.assertEqual([], next_delta["changed"])
        wrong_view = desktop_mcp._apply_inspect_budget(
            {"ok": True, "controls": controls_v2, "count": 40}, "12345",
            delta_base=delta["tree_id"], view=("new-filter",),
        )
        self.assertEqual("view_mismatch", wrong_view["delta_fallback"])
        incomplete_view = desktop_mcp._apply_inspect_budget(
            {"ok": True, "controls": controls_v2[:5], "count": 5, "truncated": True}, "12345",
            delta_base=delta["tree_id"],
        )
        self.assertEqual("incomplete_view", incomplete_view["delta_fallback"])

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

        worker_script = REPO_ROOT / "skill" / "auto-computer" / "scripts" / "win_uia_worker.py"
        env = os.environ.copy()
        env["WIN_UIA_SCRIPT_DIR"] = str(worker_script.parent)
        env["WIN_UIA_ARGS"] = json.dumps({"mode": "windows"})
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(worker_script)],
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
        self.assertIn("急停", foreground_guard.ABORT_BANNER_TEXT)

    def test_foreground_guard_aurora_render_and_duration_exit(self):
        self.assertGreaterEqual(foreground_guard.GLOW_DEPTH, 60)
        self.assertGreaterEqual(foreground_guard.BANNER_W, 560)
        self.assertGreaterEqual(foreground_guard.BANNER_H, 48)

        assets = foreground_guard.prepare_banner_assets()
        normal_bgra = foreground_guard.render_banner_bgra(assets, 0.5, aborted=False, fade=1.0)
        abort_bgra = foreground_guard.render_banner_bgra(assets, 0.5, aborted=True, fade=1.0)
        self.assertEqual((assets["h"], assets["w"], 4), normal_bgra.shape)
        self.assertTrue(int(normal_bgra[..., 3].max()) > 200)
        # Premultiplied constraint: BGR <= Alpha
        self.assertTrue((normal_bgra[..., :3] <= normal_bgra[..., 3:4]).all())
        self.assertTrue((abort_bgra[..., :3] <= abort_bgra[..., 3:4]).all())
        # Abort flash shifts red channel strongly above blue channel
        self.assertGreater(float(abort_bgra[..., 2].mean()), float(abort_bgra[..., 0].mean()))

        # Scaled 2.5K/1.5x banner & corner geometry
        scaled_assets = foreground_guard.prepare_banner_assets(width=870, height=75, scale=1.5)
        scaled_frame = foreground_guard.render_banner_bgra(scaled_assets, 0.25, aborted=False, fade=1.0)
        self.assertEqual((scaled_assets["h"], scaled_assets["w"], 4), scaled_frame.shape)

        # Check seamless 45-degree miter corner coverage on a 120x120 square
        total_corner = None
        for side in ("top", "right", "bottom", "left"):
            g = foreground_guard.build_edge_geometry(side, 120, 120, depth=20, scale=1.5)
            frame = foreground_guard.render_edge_bgra(g, 0.25, aborted=False, fade=1.0)
            self.assertTrue((frame[..., :3] <= frame[..., 3:4]).all())
            canvas = foreground_guard.np.zeros((120, 120), dtype=int)
            rx, ry, w, h = g["rel_x"], g["rel_y"], g["w"], g["h"]
            canvas[ry:ry + h, rx:rx + w] = g["miter"].astype(int)
            total_corner = canvas if total_corner is None else (total_corner + canvas)
        # Top-left 20x20 corner should have exact coverage 1 everywhere (zero overlap, zero gap)
        self.assertTrue((total_corner[:20, :20] == 1).all())

        # Live layered window creation + clean --duration exit
        guard_py = REPO_ROOT / "mcp" / "foreground_guard.py"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(guard_py), "--duration", "0.6"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)

    def test_release_stuck_inputs_and_kill_tree_safe(self):
        desktop_mcp._release_stuck_inputs()
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        desktop_mcp._kill_process_tree(proc)
        self.assertIsNotNone(proc.wait(timeout=3))

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
                                "close_after": False,
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
                # Named pipe should not rely on DevToolsActivePort being parsed; ephemeral auto profile cleans up after close
                self.assertEqual([], list(Path(tmp).glob("edge-auto*")))
        finally:
            server.shutdown()
            server.server_close()

    def test_open_and_run_auto_skips_summary_and_compacts_write_steps(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = "<!doctype html><html><head><title>CompactTest</title></head><body><button id='btn' onclick=\"document.getElementById('out').textContent='¥299.00'\">Go</button><div id='out'>idle</div><p>Long body summary filler</p></body></html>".encode("utf-8")
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
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [{"type": "click", "selector": "#btn"}, {"op": "read", "selector": "#out"}],
                            },
                        },
                    },
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "stop_auto_browser", "arguments": {"browser": "edge", "profile": "auto"}}},
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
                res = responses[2]["result"]["structuredContent"]
                self.assertTrue(res["ok"], msg=res)
                self.assertNotIn("summary_text", res)
                self.assertNotIn("target_id", res)
                self.assertEqual([{"op": "click"}, {"op": "read", "text": "¥299.00"}], res["steps"])
        finally:
            server.shutdown()
            server.server_close()

        # Verify desktop _validate_actions accepts op aliases ("type"/"action")
        aliased = desktop_mcp._validate_actions([{"type": "read", "automationId": "lblCount"}])
        self.assertEqual("read", aliased[0]["op"])

    def test_bindings_allow_wechat_app_name(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            adapters_dir = Path(tmp) / "adapters"
            desktop_mcp.adapter_registry.record_binding(
                "Weixin",
                "search_box",
                "uia",
                {"automation_id": "search_id", "control_type": "Edit"},
                verification="tested_background",
                evidence="verified in test",
                root=adapters_dir,
            )
            with (
                mock.patch.object(desktop_mcp.adapter_registry, "REGISTRY_DIR", adapters_dir),
                mock.patch.object(desktop_mcp.adapter_registry, "window_owner", return_value=(4321, "Weixin")),
                mock.patch.object(desktop_mcp.os, "name", "nt"),
            ):
                actions, pid = desktop_mcp._resolve_action_bindings("12345", [{"op": "read", "binding": "search_box"}], "Weixin")
                self.assertEqual(4321, pid)
                self.assertEqual("search_id", actions[0]["automationId"])

    def test_ocr_message_fuzzy_match(self):
        scripts_dir = str(REPO_ROOT / "skill" / "auto-computer" / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import win_uia_worker  # noqa: E402

        self.assertTrue(win_uia_worker._matches_ocr_message("测轼消息内容", "测试消息内容"))
        self.assertTrue(win_uia_worker._matches_ocr_message("测试消息内容", "测试消息内容"))
        self.assertFalse(win_uia_worker._matches_ocr_message("完全无关文本", "测试消息内容"))
        self.assertFalse(win_uia_worker._matches_ocr_message("啊", "测试消息内容"))

    def test_fg_send_message_handles_gray_button_ocr_typo_and_dynamic_boundary(self):
        scripts_dir = str(REPO_ROOT / "skill" / "auto-computer" / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import win_uia_worker  # noqa: E402

        from PIL import Image, ImageDraw

        before_img = Image.new("RGB", (1353, 971), (245, 245, 245))
        input_img = before_img.copy()
        ImageDraw.Draw(input_img).rectangle([490, 767, 650, 795], fill=(30, 30, 30))
        after_img = before_img.copy()
        ImageDraw.Draw(after_img).rectangle([1080, 655, 1220, 695], fill=(149, 236, 105))

        caps = [
            {"left": 671, "top": 390, "width": 1353, "height": 971, "method": "bitblt", "raw_bytes": b"", "image": before_img},
            {"left": 671, "top": 390, "width": 1353, "height": 971, "method": "bitblt", "raw_bytes": b"", "image": input_img},
            {"left": 671, "top": 390, "width": 1353, "height": 971, "method": "bitblt", "raw_bytes": b"", "image": input_img},
            {"left": 671, "top": 390, "width": 1353, "height": 971, "method": "bitblt", "raw_bytes": b"", "image": after_img},
            {"left": 671, "top": 390, "width": 1353, "height": 971, "method": "bitblt", "raw_bytes": b"", "image": after_img},
        ]
        before_lines = [
            {"label": "陈小明", "x": 481.0, "y": 73.0, "width": 62.0, "height": 20.0, "cx": 512, "cy": 83},
            {"label": "666", "x": 1173.0, "y": 379.0, "width": 35.0, "height": 16.0, "cx": 1190, "cy": 387},
        ]
        input_lines = [
            *before_lines,
            {"label": "测轼消息内容", "x": 490.0, "y": 767.0, "width": 104.0, "height": 19.0, "cx": 542, "cy": 776},
        ]
        after_lines = [
            {"label": "陈小明", "x": 481.0, "y": 73.0, "width": 62.0, "height": 20.0, "cx": 512, "cy": 83},
            {"label": "666", "x": 1173.0, "y": 234.0, "width": 35.0, "height": 16.0, "cx": 1190, "cy": 242},
            {"label": "02：20", "x": 873.0, "y": 623.0, "width": 45.0, "height": 13.0, "cx": 895, "cy": 629},
            {"label": "测轼消息内容", "x": 1100.0, "y": 665.0, "width": 104.0, "height": 19.0, "cx": 1152, "cy": 674},
        ]
        with (
            mock.patch.object(win_uia_worker, "_set_dpi_aware"),
            mock.patch.object(win_uia_worker, "_bring_foreground"),
            mock.patch.object(win_uia_worker, "_restore_foreground"),
            mock.patch.object(win_uia_worker, "_cursor_pos", return_value=(100, 100)),
            mock.patch.object(win_uia_worker, "_click_phys"),
            mock.patch.object(win_uia_worker, "_send_unicode_text"),
            mock.patch.object(win_uia_worker, "_send_hotkey"),
            mock.patch.object(win_uia_worker, "_send_vk") as send_vk,
            mock.patch.object(win_uia_worker, "_capture_window_image", side_effect=caps),
            mock.patch.object(win_uia_worker, "_ocr_image", side_effect=[before_lines, input_lines, after_lines]),
            mock.patch.object(win_uia_worker.user32, "GetForegroundWindow", return_value=0x10668),
            mock.patch.object(win_uia_worker.user32, "GetClipboardSequenceNumber", return_value=42),
            mock.patch.object(win_uia_worker.time, "sleep"),
        ):
            res = win_uia_worker._fg_send_message(0x10668, "测试消息内容", "陈小明")

        self.assertTrue(res["ok"])
        self.assertTrue(res["headerVerified"])
        self.assertTrue(res["typedObserved"])
        self.assertEqual("enter-fallback", res["sendMethod"])
        self.assertTrue(res["inputCleared"])
        self.assertEqual(0, res["beforeMsgCount"])
        self.assertEqual(1, res["afterMsgCount"])
        self.assertGreaterEqual(len(res["afterBottomLines"]), 1)
        send_vk.assert_called_with(0x0D)


if __name__ == "__main__":
    unittest.main()
