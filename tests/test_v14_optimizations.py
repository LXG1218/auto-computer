from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = REPO_ROOT / "mcp"
SCRIPTS_DIR = REPO_ROOT / "skill" / "auto-computer" / "scripts"
for p in (str(MCP_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import adapter_registry  # noqa: E402
import browser_task  # noqa: E402
import desktop_mcp  # noqa: E402
import table_ops  # noqa: E402
import win_uia_worker  # noqa: E402


class _FakeOcrHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "<!doctype html><html><head><title>KeptFallbackPage</title></head>"
            "<body><div id='status'>kept-ok</div></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        return


class V14OptimizationsTests(unittest.TestCase):
    def test_uia_daemon_lazy_start_and_idle_reap(self):
        worker = SCRIPTS_DIR / "win_uia_worker.py"
        client = desktop_mcp._UiaDaemonClient(worker, idle_timeout_s=0.35)
        try:
            self.assertIsNone(client._proc)
            res1 = client.request({"mode": "windows", "process": "DefinitelyNotRunningXYZ_Test"})
            self.assertTrue(res1["ok"])
            self.assertIsNotNone(client._proc)
            time.sleep(0.75)
            with client._lock:
                self.assertIsNone(client._proc)
            res2 = client.request({"mode": "windows", "process": "DefinitelyNotRunningXYZ_Test"})
            self.assertTrue(res2["ok"])
            self.assertIsNotNone(client._proc)
        finally:
            client.close()

    def test_list_windows_filters_noise_and_zero_bounds(self):
        raw_windows = [
            {"hwnd": "0x1001", "pid": 100, "process": "Weixin", "className": "Qt51514QWindowIcon", "bounds": {"x": 10, "y": 20, "width": 1200, "height": 900}},
            {"hwnd": "0x1002", "pid": 100, "process": "Weixin", "className": "Qt51514QWindowIcon", "bounds": {"x": -32000, "y": -32000, "width": 237, "height": 39}},
            {"hwnd": "0x1003", "pid": 100, "process": "Weixin", "className": "IME", "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}},
            {"hwnd": "0x1004", "pid": 100, "process": "Weixin", "className": "MSCTFIME UI", "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}},
            {"hwnd": "0x1005", "pid": 100, "process": "Weixin", "className": "Base_PowerMessageWindow", "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}},
            {"hwnd": "0x1006", "pid": 100, "process": "Weixin", "className": "CustomZeroShell", "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}},
            {"hwnd": "0x1007", "pid": 200, "process": "python", "className": "ACGlow_Overlay", "bounds": {"x": 0, "y": 0, "width": 100, "height": 100}},
        ]
        with (
            mock.patch.object(desktop_mcp, "_enum_windows_fast", return_value=None),
            mock.patch.object(desktop_mcp, "_run_uia", return_value={"ok": True, "windows": raw_windows}),
        ):
            filtered = desktop_mcp._call("list_windows", {"process": "Weixin"})
            all_wins = desktop_mcp._call("list_windows", {"process": "Weixin", "include_hidden": True})
        self.assertEqual(["0x1001", "0x1002"], [w["hwnd"] for w in filtered["windows"]])
        self.assertEqual(["0x1001", "0x1002", "0x1003", "0x1004", "0x1005", "0x1006"], [w["hwnd"] for w in all_wins["windows"]])

    def test_list_windows_uses_fast_win32_without_starting_uia_daemon(self):
        with mock.patch.object(desktop_mcp, "_run_uia") as run_uia:
            res = desktop_mcp._call("list_windows", {"process": "DefinitelyNotRunningXYZ_Test"})
        self.assertTrue(res["ok"])
        self.assertEqual([], res["windows"])
        run_uia.assert_not_called()

    def test_pixel_diff_allows_font_agnostic_send_without_ocr_match(self):
        before_img = Image.new("RGB", (600, 500), (20, 20, 20))
        input_img = before_img.copy()
        ImageDraw.Draw(input_img).rectangle([230, 415, 390, 445], fill=(235, 235, 235))
        after_img = before_img.copy()
        ImageDraw.Draw(after_img).rectangle([320, 290, 540, 330], fill=(149, 236, 105))

        caps = [
            {"left": 100, "top": 100, "width": 600, "height": 500, "method": "bitblt", "raw_bytes": b"", "image": before_img},
            {"left": 100, "top": 100, "width": 600, "height": 500, "method": "bitblt", "raw_bytes": b"", "image": input_img},
            {"left": 100, "top": 100, "width": 600, "height": 500, "method": "bitblt", "raw_bytes": b"", "image": input_img},
            {"left": 100, "top": 100, "width": 600, "height": 500, "method": "bitblt", "raw_bytes": b"", "image": after_img},
            {"left": 100, "top": 100, "width": 600, "height": 500, "method": "bitblt", "raw_bytes": b"", "image": after_img},
        ]
        header_only = [{"label": "陈小明", "x": 240.0, "y": 30.0, "width": 60.0, "height": 20.0, "cx": 270, "cy": 40}]
        with (
            mock.patch.object(win_uia_worker, "_set_dpi_aware"),
            mock.patch.object(win_uia_worker, "_bring_foreground"),
            mock.patch.object(win_uia_worker, "_restore_foreground"),
            mock.patch.object(win_uia_worker, "_cursor_pos", return_value=(10, 10)),
            mock.patch.object(win_uia_worker, "_click_phys"),
            mock.patch.object(win_uia_worker, "_send_unicode_text"),
            mock.patch.object(win_uia_worker, "_send_hotkey"),
            mock.patch.object(win_uia_worker, "_send_vk") as send_vk,
            mock.patch.object(win_uia_worker, "_capture_window_image", side_effect=caps),
            mock.patch.object(win_uia_worker, "_ocr_image", return_value=header_only),
            mock.patch.object(win_uia_worker.user32, "GetForegroundWindow", return_value=0x10668),
            mock.patch.object(win_uia_worker.user32, "GetClipboardSequenceNumber", return_value=7),
            mock.patch.object(win_uia_worker.time, "sleep"),
        ):
            res = win_uia_worker._fg_send_message(0x10668, "测试消息", "陈小明")

        self.assertTrue(res["ok"])
        self.assertTrue(res["typedObserved"])
        self.assertEqual("pixel-diff", res["typedVerifyMode"])
        self.assertGreater(res["inputPixelChangedRatio"], 0.002)
        self.assertGreater(res["chatAreaChangedRatio"], 0.002)
        self.assertTrue(res["inputCleared"])
        self.assertEqual("enter-fallback", res["sendMethod"])
        send_vk.assert_called_with(0x0D)

    def test_single_foreground_search_and_send_and_mismatch_abort(self):
        before_img = Image.new("RGB", (1000, 800), (245, 245, 245))
        search_img = before_img.copy()
        ImageDraw.Draw(search_img).rectangle([80, 140, 320, 260], fill=(210, 210, 210))
        chat_img = before_img.copy()
        ImageDraw.Draw(chat_img).rectangle([420, 40, 620, 80], fill=(220, 220, 220))
        input_img = chat_img.copy()
        ImageDraw.Draw(input_img).rectangle([450, 660, 700, 710], fill=(30, 30, 30))
        after_img = chat_img.copy()
        ImageDraw.Draw(after_img).rectangle([650, 500, 900, 540], fill=(149, 236, 105))

        caps = [
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": before_img},
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": search_img},
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": search_img},
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": chat_img},
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": chat_img},
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": input_img},
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": input_img},
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": after_img},
            {"left": 0, "top": 0, "width": 1000, "height": 800, "method": "bitblt", "raw_bytes": b"", "image": after_img},
        ]
        ocr_pages = [
            [{"label": "搜索", "x": 80.0, "y": 50.0, "width": 40.0, "height": 18.0, "cx": 100, "cy": 59}],
            [{"label": "陈小明", "x": 150.0, "y": 170.0, "width": 60.0, "height": 20.0, "cx": 180, "cy": 180}],
            [{"label": "陈小明", "x": 450.0, "y": 50.0, "width": 60.0, "height": 20.0, "cx": 480, "cy": 60}],
            [
                {"label": "陈小明", "x": 450.0, "y": 50.0, "width": 60.0, "height": 20.0, "cx": 480, "cy": 60},
                {"label": "测轼回复", "x": 460.0, "y": 670.0, "width": 70.0, "height": 20.0, "cx": 495, "cy": 680},
            ],
            [
                {"label": "陈小明", "x": 450.0, "y": 50.0, "width": 60.0, "height": 20.0, "cx": 480, "cy": 60},
                {"label": "测试回复", "x": 700.0, "y": 510.0, "width": 70.0, "height": 20.0, "cx": 735, "cy": 520},
            ],
        ]
        with (
            mock.patch.object(win_uia_worker, "_set_dpi_aware"),
            mock.patch.object(win_uia_worker, "_bring_foreground") as bring_fg,
            mock.patch.object(win_uia_worker, "_restore_foreground") as restore_fg,
            mock.patch.object(win_uia_worker, "_cursor_pos", return_value=(10, 10)),
            mock.patch.object(win_uia_worker, "_click_phys"),
            mock.patch.object(win_uia_worker, "_send_unicode_text"),
            mock.patch.object(win_uia_worker, "_send_hotkey"),
            mock.patch.object(win_uia_worker, "_send_vk"),
            mock.patch.object(win_uia_worker, "_capture_window_image", side_effect=caps),
            mock.patch.object(win_uia_worker, "_ocr_image", side_effect=ocr_pages),
            mock.patch.object(win_uia_worker.user32, "GetForegroundWindow", return_value=0x10668),
            mock.patch.object(win_uia_worker.user32, "GetClipboardSequenceNumber", return_value=1),
            mock.patch.object(win_uia_worker.time, "sleep"),
        ):
            res = win_uia_worker._fg_search_and_send(0x10668, "陈小明", "测试回复")

        self.assertTrue(res["ok"])
        self.assertEqual(1, bring_fg.call_count)
        self.assertEqual(1, restore_fg.call_count)
        self.assertEqual(1, res["send"]["afterMsgCount"])

        # Mismatch header aborts immediately before typing the message
        mismatch_search = {"ok": True, "headerMatched": False, "headerLines": [{"label": "错误群聊"}]}
        with (
            mock.patch.object(win_uia_worker, "_set_dpi_aware"),
            mock.patch.object(win_uia_worker, "_bring_foreground"),
            mock.patch.object(win_uia_worker, "_restore_foreground"),
            mock.patch.object(win_uia_worker, "_cursor_pos", return_value=(10, 10)),
            mock.patch.object(win_uia_worker, "_fg_search_open_active", return_value=(mismatch_search, None, [])),
            mock.patch.object(win_uia_worker, "_send_unicode_text") as typed,
            mock.patch.object(win_uia_worker.user32, "GetForegroundWindow", return_value=0x10668),
            mock.patch.object(win_uia_worker.user32, "GetClipboardSequenceNumber", return_value=1),
            mock.patch.object(win_uia_worker.time, "sleep"),
        ):
            aborted = win_uia_worker._fg_search_and_send(0x10668, "陈小明", "测试回复")

        self.assertFalse(aborted["ok"])
        self.assertEqual("verify_header", aborted["stage"])
        typed.assert_not_called()

    def test_generic_click_text_and_type_verify_validation(self):
        validated = desktop_mcp._validate_actions([
            {"type": "invoke", "automationId": "__fg_click_text__", "name": "导出"},
            {"action": "set_value", "automationId": "__fg_type_verify__", "name": "请输入", "value": "测试", "submit": True},
        ])
        self.assertEqual("invoke", validated[0]["op"])
        self.assertTrue(validated[1]["submit"])

        img_a = Image.new("RGB", (400, 300), (240, 240, 240))
        img_b = img_a.copy()
        ImageDraw.Draw(img_b).rectangle([50, 50, 150, 90], fill=(10, 120, 220))
        with (
            mock.patch.object(win_uia_worker, "_set_dpi_aware"),
            mock.patch.object(win_uia_worker, "_bring_foreground"),
            mock.patch.object(win_uia_worker, "_restore_foreground"),
            mock.patch.object(win_uia_worker, "_cursor_pos", return_value=(0, 0)),
            mock.patch.object(win_uia_worker, "_click_phys") as click_phys,
            mock.patch.object(win_uia_worker, "_capture_window_image", side_effect=[
                {"left": 100, "top": 50, "width": 400, "height": 300, "method": "bitblt", "raw_bytes": b"", "image": img_a},
                {"left": 100, "top": 50, "width": 400, "height": 300, "method": "bitblt", "raw_bytes": b"", "image": img_b},
                {"left": 100, "top": 50, "width": 400, "height": 300, "method": "bitblt", "raw_bytes": b"", "image": img_b},
            ]),
            mock.patch.object(win_uia_worker, "_ocr_image", return_value=[{"label": "导出报表", "x": 60.0, "y": 55.0, "width": 80.0, "height": 20.0, "cx": 100, "cy": 65}]),
            mock.patch.object(win_uia_worker.user32, "GetForegroundWindow", return_value=0x1234),
            mock.patch.object(win_uia_worker.time, "sleep"),
        ):
            res = win_uia_worker._fg_click_text(0x1234, "导出")
        self.assertTrue(res["ok"])
        self.assertEqual([200, 115], res["clickedAt"])
        self.assertTrue(res["visualChanged"])
        click_phys.assert_called_once_with(200, 115)

    def test_browser_alias_and_daily_fallback_to_kept_and_idle_reap(self):
        validated = browser_task._validate({
            "browser": "edge",
            "url": "https://example.com/",
            "actions": [
                {"action": "read", "selector": "#title"},
                {"type": "click", "selector": "#go"},
            ],
        })
        self.assertEqual(["read", "click"], [a["op"] for a in validated["actions"]])

        server = HTTPServer(("127.0.0.1", 0), _FakeOcrHandler)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                env["AUTO_COMPUTER_BROWSER_IDLE_MS"] = "900"
                reqs = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "daily_browser_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "NonExistentDailyProfileForTest_XYZ",
                                "actions": [{"op": "read", "selector": "#status"}],
                                "fallback_to_kept": True,
                            },
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "stop_auto_browser", "arguments": {"browser": "edge", "profile": "kept"}},
                    },
                ]
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input="\n".join(json.dumps(r) for r in reqs) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                responses = {obj["id"]: obj for obj in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())}
                res1 = responses[1]["result"]["structuredContent"]
                self.assertTrue(res1["ok"], msg=res1)
                self.assertEqual("kept", res1["fallback_used"])
                self.assertEqual([{"op": "read", "text": "kept-ok"}], res1["steps"])
        finally:
            server.shutdown()
            server.server_close()

    def test_table_header_auto_and_profile_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left = root / "left.csv"
            right = root / "right.csv"
            left.write_text("2026年11月对账单\n导出人：系统,,\n订单号,金额,状态\nA1,100,已付\nA2,200,待付\n", encoding="utf-8")
            right.write_text("财务核对表\n备注：含税,,\n说明：单位元,,\n订单号,金额,状态\nA1,100,已付\nA2,250,待付\n", encoding="utf-8")

            loaded = table_ops.load_table(str(right), header_auto=True)
            self.assertEqual(4, loaded["detected_header_row"])
            self.assertEqual(["订单号", "金额", "状态"], loaded["headers"])

            rec = table_ops.reconcile_tables(str(left), str(right), "订单号", columns_spec="金额,状态", header_auto=True)
            self.assertEqual(1, len(rec["field_diffs"]))
            self.assertEqual("A2", rec["field_diffs"][0]["key"])

            adapters_dir = root / "adapters"
            saved1 = adapter_registry.record_profile(
                "Weixin",
                "app",
                {"process": "Weixin", "main_class": "Qt51514QWindowIcon", "ui_kind": "self_drawn_ocr", "quirks": ["gray send"]},
                evidence="first",
                root=adapters_dir,
            )
            self.assertEqual("Weixin", saved1["name"])
            saved2 = adapter_registry.record_profile(
                "Weixin",
                "app",
                {"process": "Weixin", "main_class": "Qt51514QWindowIcon", "preferred_route": "__fg_search_and_send__"},
                evidence="updated",
                root=adapters_dir,
            )
            self.assertEqual("__fg_search_and_send__", saved2["profile"]["preferred_route"])
            profiles = adapter_registry.list_profiles("app", root=adapters_dir)
            self.assertEqual(1, len(profiles))
            self.assertEqual("updated", adapter_registry.get_profile("Weixin", "app", root=adapters_dir)["evidence"])

    def test_headless_open_and_run_closes_browser_immediately(self):
        server = HTTPServer(("127.0.0.1", 0), _FakeOcrHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                reqs = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [{"op": "read", "selector": "#status"}],
                            },
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "stop_auto_browser", "arguments": {"browser": "edge", "profile": "auto"}},
                    },
                ]
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input="\n".join(json.dumps(r) for r in reqs) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                responses = {obj["id"]: obj for obj in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())}
                res1 = responses[1]["result"]["structuredContent"]
                self.assertTrue(res1["ok"], msg=res1)
                self.assertTrue(res1.get("browser_closed"))
                self.assertEqual([], responses[2]["result"]["structuredContent"]["stopped"])
        finally:
            server.shutdown()
            server.server_close()

    def test_visible_browser_never_auto_reaped(self):
        edge_src = (MCP_DIR / "edge_mcp.cjs").read_text(encoding="utf-8")
        self.assertIn("if (entry.visible) return;", edge_src)
        self.assertIn("if (!visible && closeAfter && poolEntry && !poolEntry.visible)", edge_src)

    def test_run_actions_resolves_hwnd_from_process_and_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapters_dir = Path(tmp) / "adapters"
            adapter_registry.record_profile(
                "SampleApp",
                "app",
                {"process": "SampleApp", "main_class": "SampleMainClass"},
                evidence="test",
                root=adapters_dir,
            )
            windows = [
                {"hwnd": "0x2001", "pid": 300, "process": "SampleApp", "className": "SampleMainClass", "physicalBounds": [0, 0, 800, 600]},
                {"hwnd": "0x2002", "pid": 300, "process": "SampleApp", "className": "SampleToolSaveBits", "physicalBounds": [10, 10, 110, 110]},
            ]
            with (
                mock.patch.object(desktop_mcp.adapter_registry, "REGISTRY_DIR", adapters_dir),
                mock.patch.object(desktop_mcp, "_enum_process_windows_fast", return_value=windows) as fast_enum,
                mock.patch.object(desktop_mcp, "_run_uia", return_value={"ok": True, "results": [{"index": 0, "op": "wait"}]}) as run_uia,
            ):
                res = desktop_mcp._call("run_actions", {"process": "SampleApp", "actions": [{"op": "wait", "ms": 1}]})
            self.assertTrue(res["ok"])
            self.assertEqual("0x2001", res["resolvedHwnd"])
            fast_enum.assert_called_once_with("SampleApp")
            run_uia.assert_called_once()
            self.assertEqual("0x2001", run_uia.call_args.args[0]["hwnd"])
            self.assertIsNone(desktop_mcp._UIA_DAEMON._proc)

            ambiguous_windows = [
                {"hwnd": "0x2001", "pid": 300, "process": "SampleApp", "className": "SampleMainClass", "physicalBounds": [0, 0, 800, 600]},
                {"hwnd": "0x2003", "pid": 301, "process": "SampleApp", "className": "SampleMainClass", "physicalBounds": [10, 10, 810, 610]},
            ]
            with (
                mock.patch.object(desktop_mcp.adapter_registry, "REGISTRY_DIR", adapters_dir),
                mock.patch.object(desktop_mcp, "_enum_process_windows_fast", return_value=ambiguous_windows),
            ):
                with self.assertRaises(desktop_mcp.BridgeError) as ctx:
                    desktop_mcp._call("run_actions", {"process": "SampleApp", "actions": [{"op": "wait", "ms": 1}]})
            self.assertIn("Multiple windows matched", str(ctx.exception))

    def test_fg_ocr_runs_without_foreground_and_error_message_is_clear(self):
        with mock.patch.object(desktop_mcp, "_run_uia", return_value={"ok": True, "results": [{"index": 0, "op": "read"}]}) as run_uia:
            res = desktop_mcp._call(
                "run_actions",
                {"hwnd": "0x1234", "actions": [{"op": "read", "automationId": "__fg_ocr__"}]},
            )
        self.assertTrue(res["ok"])
        self.assertFalse(run_uia.call_args.kwargs["allow_foreground"])

        with self.assertRaises(desktop_mcp.BridgeError) as ctx:
            desktop_mcp._call(
                "run_actions",
                {"hwnd": "0x1234", "actions": [{"op": "invoke", "automationId": "__fg_click_text__", "name": "确定"}]},
            )
        self.assertIn("allow_foreground=true", str(ctx.exception))
        self.assertNotIn("foreground_desktop", str(ctx.exception))

    def test_headless_open_and_run_does_not_close_existing_visible_kept_window(self):
        server = HTTPServer(("127.0.0.1", 0), _FakeOcrHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                reqs = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_tab",
                            "arguments": {"url": f"http://127.0.0.1:{port}/", "browser": "edge", "profile": "kept", "visible": True},
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "kept",
                                "visible": False,
                                "close_after": True,
                                "actions": [{"op": "read", "selector": "#status"}],
                            },
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "stop_auto_browser", "arguments": {"browser": "edge", "profile": "kept"}},
                    },
                ]
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input="\n".join(json.dumps(r) for r in reqs) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=45,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                responses = {obj["id"]: obj for obj in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())}
                self.assertTrue(responses[1]["result"]["structuredContent"]["ok"])
                res2 = responses[2]["result"]["structuredContent"]
                self.assertTrue(res2["ok"], msg=res2)
                self.assertFalse(bool(res2.get("browser_closed")))
                self.assertEqual(["kept:edge"], responses[3]["result"]["structuredContent"]["stopped"])
        finally:
            server.shutdown()
            server.server_close()

    def test_concurrent_auto_profile_instances_do_not_collide(self):
        server = HTTPServer(("127.0.0.1", 0), _FakeOcrHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                profile_root = Path(tmp) / "profiles"
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(profile_root)
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "open_and_run",
                        "arguments": {
                            "url": f"http://127.0.0.1:{port}/",
                            "browser": "edge",
                            "profile": "auto",
                            "actions": [{"op": "read", "selector": "#status"}],
                        },
                    },
                }
                payload = json.dumps(req) + "\n"
                procs = [
                    subprocess.Popen(
                        ["node", str(MCP_DIR / "edge_mcp.cjs")],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        env=env,
                    )
                    for _ in range(2)
                ]
                outputs = []
                for p in procs:
                    assert p.stdin is not None
                    p.stdin.write(payload)
                    p.stdin.flush()
                for p in procs:
                    out, err = p.communicate(timeout=45)
                    self.assertEqual(0, p.returncode, msg=err or out)
                    outputs.append((p.pid, json.loads(out.strip().splitlines()[-1])["result"]["structuredContent"]))
                for pid, res in outputs:
                    self.assertTrue(res["ok"], msg=res)
                    self.assertTrue(res.get("browser_closed"), msg=res)
                    self.assertFalse((profile_root / f"edge-auto-{pid}").exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_open_and_run_dismisses_blocking_js_alert_without_hanging(self):
        class _AlertHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    "<!doctype html><html><head><title>AlertPage</title></head>"
                    "<body><script>window.alert('blocking-test');</script><div id='status'>after-alert</div></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _AlertHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "open_and_run",
                        "arguments": {
                            "url": f"http://127.0.0.1:{port}/",
                            "browser": "edge",
                            "profile": "auto",
                            "actions": [{"op": "read", "selector": "#status"}],
                        },
                    },
                }
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps(req) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertTrue(res["ok"], msg=res)
                self.assertEqual([{"op": "read", "text": "after-alert"}], res["steps"])
                self.assertTrue(any(d.get("message") == "blocking-test" for d in res.get("dismissed_dialogs", [])), msg=res)
        finally:
            server.shutdown()
            server.server_close()

    def test_daily_fallback_to_kept_aborts_write_on_login_redirect(self):
        clicked_login = {"hit": False}

        class _RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/order":
                    self.send_response(302)
                    self.send_header("Location", "/login")
                    self.end_headers()
                    return
                if self.path.startswith("/login"):
                    body = (
                        "<!doctype html><html><head><title>LoginRequired</title></head>"
                        "<body><button id='submit' onclick=\"fetch('/submitted')\">Submit</button></body></html>"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/submitted":
                    clicked_login["hit"] = True
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _RedirectHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "daily_browser_run",
                        "arguments": {
                            "url": f"http://127.0.0.1:{port}/order",
                            "browser": "edge",
                            "profile": "NonExistentDailyProfileForTest_XYZ",
                            "actions": [{"op": "click", "selector": "#submit"}],
                            "fallback_to_kept": True,
                        },
                    },
                }
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps(req) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res["ok"])
                self.assertEqual("fallback_url_mismatch", res["reason"])
                self.assertTrue(res["needs_login_or_verify"])
                self.assertEqual([], res["completed_steps"])
                self.assertFalse(clicked_login["hit"])
        finally:
            server.shutdown()
            server.server_close()

    def test_confirm_dialog_marks_not_ok_and_batch_preserves_completed_steps(self):
        class _DialogAndBatchHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    "<!doctype html><html><head><title>DialogAndBatch</title></head>"
                    "<body><input id='box' value=''><button id='del' onclick=\"window.confirm('confirm delete?')\">Del</button></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _DialogAndBatchHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                reqs = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [{"op": "click", "selector": "#del"}],
                            },
                        },
                    },
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
                                "actions": [
                                    {"op": "fill", "selector": "#box", "value": "filled-1"},
                                    {"op": "wait_for", "selector": "#missing", "timeout_ms": 200},
                                ],
                            },
                        },
                    },
                ]
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input="\n".join(json.dumps(r) for r in reqs) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                responses = {obj["id"]: obj["result"]["structuredContent"] for obj in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())}
                res1 = responses[1]
                self.assertFalse(res1["ok"])
                self.assertEqual("action_interrupted_by_dialog", res1["reason"])
                self.assertTrue(res1["needs_readback"])
                self.assertTrue(any(d.get("type") == "confirm" for d in res1.get("dismissed_dialogs", [])))

                res2 = responses[2]
                self.assertFalse(res2["ok"])
                self.assertEqual("batch_interrupted", res2["reason"])
                self.assertEqual(1, res2["failed_index"])
                self.assertEqual(1, res2["resume_from"])
                self.assertEqual([{"op": "fill"}], res2["completed_steps"])
                self.assertTrue(res2["needs_readback"])
        finally:
            server.shutdown()
            server.server_close()

    def test_foreground_restore_respects_user_third_window_and_checks_focus_before_submit(self):
        with (
            mock.patch.object(win_uia_worker.user32, "GetForegroundWindow", return_value=0x9999),
            mock.patch.object(win_uia_worker.user32, "SetCursorPos") as set_cursor,
            mock.patch.object(win_uia_worker, "_bring_foreground") as bring_fg,
        ):
            win_uia_worker._restore_foreground(0x1111, (10, 10), expected_fg=0x2222)
        set_cursor.assert_not_called()
        bring_fg.assert_not_called()

        base_img = Image.new("RGB", (500, 400), (240, 240, 240))
        typed_img = base_img.copy()
        ImageDraw.Draw(typed_img).rectangle([120, 280, 340, 340], fill=(20, 20, 20))
        fg_sequence = [
            0x1111,  # orig_fg
            0x2222,  # before_click_input
            0x2222,  # before_type
            0x9999,  # user switched to 3rd window right before submit!
            0x9999,  # _restore_foreground sees cur != expected_fg
        ]
        with (
            mock.patch.object(win_uia_worker, "_set_dpi_aware"),
            mock.patch.object(win_uia_worker, "_bring_foreground") as bring_fg2,
            mock.patch.object(win_uia_worker, "_cursor_pos", return_value=(10, 10)),
            mock.patch.object(win_uia_worker, "_click_phys"),
            mock.patch.object(win_uia_worker, "_send_unicode_text"),
            mock.patch.object(win_uia_worker, "_send_vk") as send_vk,
            mock.patch.object(win_uia_worker, "_capture_window_image", side_effect=[
                {"left": 0, "top": 0, "width": 500, "height": 400, "method": "bitblt", "raw_bytes": b"", "image": base_img},
                {"left": 0, "top": 0, "width": 500, "height": 400, "method": "bitblt", "raw_bytes": b"", "image": typed_img},
                {"left": 0, "top": 0, "width": 500, "height": 400, "method": "bitblt", "raw_bytes": b"", "image": typed_img},
            ]),
            mock.patch.object(win_uia_worker, "_ocr_image", return_value=[]),
            mock.patch.object(win_uia_worker.user32, "GetForegroundWindow", side_effect=fg_sequence),
            mock.patch.object(win_uia_worker.time, "sleep"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                win_uia_worker._fg_type_verify(0x2222, "hello", submit=True)
        self.assertIn("before_submit", str(ctx.exception))
        send_vk.assert_not_called()
        self.assertEqual(1, bring_fg2.call_count)

    def test_edge_mcp_ping_not_blocked_by_wait_and_kept_reports_profile_in_use(self):
        server = HTTPServer(("127.0.0.1", 0), _FakeOcrHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable

                # Part A: ping responds immediately while open_and_run waits 1.2s
                p1 = subprocess.Popen(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                try:
                    assert p1.stdin is not None and p1.stdout is not None
                    p1.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [{"op": "wait", "ms": 1200}],
                            },
                        },
                    }) + "\n")
                    p1.stdin.flush()
                    time.sleep(0.15)
                    t_ping = time.monotonic()
                    p1.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}) + "\n")
                    p1.stdin.flush()
                    first_line = json.loads(p1.stdout.readline())
                    ping_elapsed = time.monotonic() - t_ping
                    self.assertEqual(2, first_line["id"])
                    self.assertLess(ping_elapsed, 0.5)
                    second_line = json.loads(p1.stdout.readline())
                    self.assertEqual(1, second_line["id"])
                    self.assertTrue(second_line["result"]["structuredContent"]["ok"])
                finally:
                    if p1.stdin:
                        p1.stdin.close()
                    p1.wait(timeout=15)

                # Part B: second agent touching kept profile returns profile_in_use
                holder = subprocess.Popen(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                try:
                    assert holder.stdin is not None and holder.stdout is not None
                    holder.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "tools/call",
                        "params": {"name": "open_tab", "arguments": {"url": f"http://127.0.0.1:{port}/", "browser": "edge", "profile": "kept"}},
                    }) + "\n")
                    holder.stdin.flush()
                    open_resp = json.loads(holder.stdout.readline())
                    self.assertTrue(open_resp["result"]["structuredContent"]["ok"], msg=open_resp)

                    contender = subprocess.run(
                        ["node", str(MCP_DIR / "edge_mcp.cjs")],
                        input=json.dumps({
                            "jsonrpc": "2.0",
                            "id": 20,
                            "method": "tools/call",
                            "params": {"name": "open_and_run", "arguments": {"url": f"http://127.0.0.1:{port}/", "browser": "edge", "profile": "kept"}},
                        }) + "\n",
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        env=env,
                    )
                    self.assertEqual(0, contender.returncode, msg=contender.stderr or contender.stdout)
                    c_res = json.loads(contender.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                    self.assertFalse(c_res["ok"])
                    self.assertEqual("profile_in_use", c_res["reason"])
                    self.assertEqual(holder.pid, c_res["occupied_by_pid"])
                finally:
                    if holder.stdin:
                        holder.stdin.close()
                    holder.wait(timeout=15)
        finally:
            server.shutdown()
            server.server_close()

    def test_run_task_batch_preserves_receipt_on_ref_error_and_reads_bounded_bytes(self):
        import files_fast
        import run_task

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "note.txt"
            target.write_text("status=pending", encoding="utf-8")
            backup_root = root / "backups"
            res = run_task._run_batch([
                {"op": "edit-text", "file": str(target), "backup_root": str(backup_root), "before": "pending", "after": "done"},
                {"op": "read-text", "file": "$prev.missing_field"},
            ])
            self.assertFalse(res["ok"])
            self.assertEqual(1, res["failed_index"])
            self.assertEqual(1, res["completed_count"])
            self.assertEqual(1, res["resume_from"])
            self.assertTrue(res["steps"][0]["ok"])
            self.assertEqual("status=done", target.read_text(encoding="utf-8"))

            big = root / "big.txt"
            big.write_bytes(b"A" * 100)
            read_res = run_task._run_batch([{"op": "read-text", "file": str(big), "max_bytes": 16}])
            self.assertTrue(read_res["ok"])
            self.assertTrue(read_res["steps"][0]["truncated"])
            self.assertEqual(16, len(read_res["steps"][0]["text"]))
            self.assertEqual("A" * 12, files_fast._read_text_safe(big, max_bytes=12))

    def test_daily_fallback_blocks_write_when_query_account_changes(self):
        clicked_wrong_account = {"hit": False}

        class _AccountRedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/order?account=A":
                    self.send_response(302)
                    self.send_header("Location", "/order?account=B")
                    self.end_headers()
                    return
                if self.path == "/order?account=B":
                    body = (
                        "<!doctype html><html><head><title>OrderAccountB</title></head>"
                        "<body><button id='submit' onclick=\"fetch('/submitted-b')\">Submit</button></body></html>"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/submitted-b":
                    clicked_wrong_account["hit"] = True
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _AccountRedirectHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "daily_browser_run",
                        "arguments": {
                            "url": f"http://127.0.0.1:{port}/order?account=A",
                            "browser": "edge",
                            "profile": "NonExistentDailyProfileForTest_XYZ",
                            "actions": [{"op": "click", "selector": "#submit"}],
                            "fallback_to_kept": True,
                        },
                    },
                }
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps(req) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res["ok"])
                self.assertEqual("fallback_url_mismatch", res["reason"])
                self.assertFalse(clicked_wrong_account["hit"])
        finally:
            server.shutdown()
            server.server_close()

    def test_confirm_dialog_stops_before_subsequent_click_action(self):
        after_clicked = {"hit": False}

        class _ConfirmSeqHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/after-clicked":
                    after_clicked["hit"] = True
                    self.send_response(200)
                    self.end_headers()
                    return
                body = (
                    "<!doctype html><html><head><title>ConfirmSeq</title></head>"
                    "<body><button id='del' onclick=\"window.confirm('proceed?')\">Del</button>"
                    "<button id='after' onclick=\"fetch('/after-clicked')\">After</button></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _ConfirmSeqHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "open_and_run",
                        "arguments": {
                            "url": f"http://127.0.0.1:{port}/",
                            "browser": "edge",
                            "profile": "auto",
                            "actions": [
                                {"op": "click", "selector": "#del"},
                                {"op": "click", "selector": "#after"},
                            ],
                        },
                    },
                }
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps(req) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res["ok"])
                self.assertEqual("action_interrupted_by_dialog", res["reason"])
                self.assertEqual(1, res.get("stopped_before_index"))
                self.assertEqual([{"op": "click"}], res["completed_steps"])
                self.assertFalse(after_clicked["hit"])
        finally:
            server.shutdown()
            server.server_close()

    def test_cancel_notification_and_hard_total_timeout_stop_execution(self):
        submit_clicked = {"hit": False}

        class _CancelTimeoutHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/submitted":
                    submit_clicked["hit"] = True
                    self.send_response(200)
                    self.end_headers()
                    return
                body = (
                    "<!doctype html><html><head><title>CancelTimeout</title></head>"
                    "<body><div id='status'>ready</div><button id='submit' onclick=\"fetch('/submitted')\">Submit</button></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _CancelTimeoutHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable

                # Part A: notifications/cancelled aborts wait immediately before click #submit
                proc = subprocess.Popen(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                try:
                    assert proc.stdin is not None and proc.stdout is not None
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [
                                    {"op": "wait", "ms": 2000},
                                    {"op": "click", "selector": "#submit"},
                                ],
                            },
                        },
                    }) + "\n")
                    proc.stdin.flush()
                    time.sleep(0.25)
                    t_cancel = time.monotonic()
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {"requestId": 1, "reason": "user cancelled"},
                    }) + "\n")
                    proc.stdin.flush()
                    line = proc.stdout.readline()
                    cancel_elapsed = time.monotonic() - t_cancel
                    res_cancel = json.loads(line)["result"]["structuredContent"]
                    self.assertFalse(res_cancel["ok"], msg=res_cancel)
                    self.assertEqual("cancelled", res_cancel["reason"])
                    self.assertLess(cancel_elapsed, 3.0)
                    self.assertFalse(submit_clicked["hit"])
                finally:
                    if proc.stdin:
                        proc.stdin.close()
                    proc.wait(timeout=15)

                # Part B: total_timeout_ms=2000 hard stops wait=2500ms + click without returning ok
                # Warm up once or measure open_and_run's own elapsed_ms (shutdown cleanup after timeout waits up to ~1.5s for Browser.close)
                proc2 = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "total_timeout_ms": 2000,
                                "actions": [
                                    {"op": "wait", "ms": 2500},
                                    {"op": "click", "selector": "#submit"},
                                ],
                            },
                        },
                    }) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=15,
                    env=env,
                )
                self.assertEqual(0, proc2.returncode, msg=proc2.stderr or proc2.stdout)
                res_timeout = json.loads(proc2.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res_timeout["ok"], msg=res_timeout)
                self.assertEqual("timeout", res_timeout["reason"])
                self.assertLess(res_timeout["elapsed_ms"], 3600)
                self.assertFalse(submit_clicked["hit"])
        finally:
            server.shutdown()
            server.server_close()

    def test_open_and_run_multipage_reuses_single_headless_browser_and_closes_at_end(self):
        class _MultiPageHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                prices = {"/p1": "101", "/p2": "202", "/p3": "303"}
                val = prices.get(self.path, "0")
                body = (
                    f"<!doctype html><html><head><title>Page {self.path}</title></head>"
                    f"<body><span id='price'>{val}</span><p>Long body summary omitted</p></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _MultiPageHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                reqs = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "urls": [
                                    f"http://127.0.0.1:{port}/p1",
                                    f"http://127.0.0.1:{port}/p2",
                                    f"http://127.0.0.1:{port}/p3",
                                ],
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [{"op": "read", "selector": "#price"}],
                            },
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "stop_auto_browser", "arguments": {"browser": "edge", "profile": "auto"}},
                    },
                ]
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input="\n".join(json.dumps(r) for r in reqs) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                responses = {obj["id"]: obj["result"]["structuredContent"] for obj in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())}
                res1 = responses[1]
                self.assertTrue(res1["ok"], msg=res1)
                self.assertEqual(3, res1["count"])
                self.assertTrue(res1.get("browser_closed"))
                self.assertEqual(["101", "202", "303"], [p["steps"][0]["text"] for p in res1["pages"]])
                self.assertTrue(all("summary_text" not in p for p in res1["pages"]))
                self.assertEqual([], responses[2]["stopped"])
        finally:
            server.shutdown()
            server.server_close()

    def test_mid_batch_url_account_switch_blocks_next_write(self):
        submitted_b = {"hit": False}

        class _SwitchHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/submitted-b":
                    submitted_b["hit"] = True
                    self.send_response(200)
                    self.end_headers()
                    return
                body = (
                    "<!doctype html><html><head><title>SwitchAccount</title></head>"
                    "<body>"
                    "<button id='switch' onclick=\"history.pushState({}, '', '/order?account=B')\">Switch</button>"
                    "<button id='submit' onclick=\"fetch('/submitted-b')\">Submit</button>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _SwitchHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "daily_browser_run",
                        "arguments": {
                            "url": f"http://127.0.0.1:{port}/order?account=A",
                            "browser": "edge",
                            "profile": "NonExistentDailyProfileForTest_XYZ",
                            "actions": [
                                {"op": "click", "selector": "#switch"},
                                {"op": "click", "selector": "#submit"},
                            ],
                            "fallback_to_kept": True,
                        },
                    },
                }
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps(req) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res["ok"])
                self.assertEqual("page_url_changed", res["reason"])
                self.assertEqual(1, res["failed_index"])
                self.assertEqual(1, res["stopped_before_index"])
                self.assertEqual([{"op": "click"}], res["completed_steps"])
                self.assertFalse(submitted_b["hit"])
        finally:
            server.shutdown()
            server.server_close()

    def test_multipage_write_stops_after_first_page_interrupted_while_readonly_continues(self):
        p2_clicked = {"hit": False}

        class _MultiStopHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/p2-clicked":
                    p2_clicked["hit"] = True
                    self.send_response(200)
                    self.end_headers()
                    return
                if self.path == "/p1":
                    body = (
                        "<!doctype html><html><head><title>P1</title></head>"
                        "<body><button id='del' onclick=\"window.confirm('ok?')\">Del</button></body></html>"
                    ).encode("utf-8")
                else:
                    body = (
                        "<!doctype html><html><head><title>P2</title></head>"
                        "<body><span id='price'>999</span><button id='p2_btn' onclick=\"fetch('/p2-clicked')\">P2</button></body></html>"
                    ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _MultiStopHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable

                # Part A: write multi-page stops at page 1 after confirm dialog; page 2 is never clicked
                proc_w = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "browser": "edge",
                                "profile": "auto",
                                "pages": [
                                    {"url": f"http://127.0.0.1:{port}/p1", "actions": [{"op": "click", "selector": "#del"}]},
                                    {"url": f"http://127.0.0.1:{port}/p2", "actions": [{"op": "click", "selector": "#p2_btn"}]},
                                ],
                            },
                        },
                    }) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc_w.returncode, msg=proc_w.stderr or proc_w.stdout)
                res_w = json.loads(proc_w.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res_w["ok"])
                self.assertEqual(1, res_w["count"])
                self.assertEqual(1, res_w["stopped_before_page"])
                self.assertEqual(0, res_w["resume_from_page"])
                self.assertTrue(res_w["needs_readback"])
                self.assertFalse(p2_clicked["hit"])

                # Part B: read-only multi-page continues to page 2 even if page 1 read fails
                proc_r = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "browser": "edge",
                                "profile": "auto",
                                "pages": [
                                    {"url": f"http://127.0.0.1:{port}/p1", "actions": [{"op": "read", "selector": "#missing"}]},
                                    {"url": f"http://127.0.0.1:{port}/p2", "actions": [{"op": "read", "selector": "#price"}]},
                                ],
                            },
                        },
                    }) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc_r.returncode, msg=proc_r.stderr or proc_r.stdout)
                res_r = json.loads(proc_r.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res_r["ok"])
                self.assertEqual(2, res_r["count"])
                self.assertFalse(res_r["pages"][0]["ok"])
                self.assertTrue(res_r["pages"][1]["ok"])
                self.assertEqual("999", res_r["pages"][1]["steps"][0]["text"])
        finally:
            server.shutdown()
            server.server_close()

    def test_daily_browser_run_propagates_cancel_and_timeout_to_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "browser_task.py"
            session_mod = root / "browser_session.py"
            done_flag = root / "done.flag"
            detach_flag = root / "detach.flag"
            helper.write_text(
                "import json, sys, time\n"
                f"from pathlib import Path\n"
                "json.load(sys.stdin)\n"
                "time.sleep(2.0)\n"
                f"Path({str(done_flag)!r}).write_text('done', encoding='utf-8')\n"
                "print(json.dumps({'ok': True, 'available': True}))\n",
                encoding="utf-8",
            )
            session_mod.write_text(
                "import sys\n"
                "from pathlib import Path\n"
                f"Path({str(detach_flag)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AUTO_COMPUTER_HOME"] = tmp
            env["AUTO_COMPUTER_PYTHON"] = sys.executable
            env["AUTO_COMPUTER_DAILY_HELPER"] = str(helper)

            # Part A: cancel stops running daily helper and invokes detach
            proc = subprocess.Popen(
                ["node", str(MCP_DIR / "edge_mcp.cjs")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            try:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "daily_browser_run",
                        "arguments": {
                            "url": "https://example.com/",
                            "browser": "edge",
                            "actions": [{"op": "click", "selector": "#go"}],
                        },
                    },
                }) + "\n")
                proc.stdin.flush()
                time.sleep(0.25)
                t0 = time.monotonic()
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": 1, "reason": "stop daily"},
                }) + "\n")
                proc.stdin.flush()
                res_c = json.loads(proc.stdout.readline())["result"]["structuredContent"]
                self.assertLess(time.monotonic() - t0, 2.0)
                self.assertFalse(res_c["ok"])
                self.assertEqual("cancelled", res_c["reason"])
                self.assertTrue(res_c["needs_readback"])
                self.assertTrue(detach_flag.is_file())
                self.assertFalse(done_flag.exists())
            finally:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=10)

            # Part B: total_timeout_ms stops daily helper and invokes detach
            detach_flag.unlink(missing_ok=True)
            proc2 = subprocess.run(
                ["node", str(MCP_DIR / "edge_mcp.cjs")],
                input=json.dumps({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "daily_browser_run",
                        "arguments": {
                            "url": "https://example.com/",
                            "browser": "edge",
                            "total_timeout_ms": 600,
                            "actions": [{"op": "click", "selector": "#go"}],
                        },
                    },
                }) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                env=env,
            )
            self.assertEqual(0, proc2.returncode, msg=proc2.stderr or proc2.stdout)
            res_t = json.loads(proc2.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
            self.assertFalse(res_t["ok"])
            self.assertEqual("timeout", res_t["reason"])
            self.assertTrue(res_t["needs_readback"])
            self.assertTrue(detach_flag.is_file())
            self.assertFalse(done_flag.exists())

    def test_consecutive_readonly_actions_batch_cleanly(self):
        class _MultiReadHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    "<!doctype html><html><head><title>MultiRead</title></head>"
                    "<body><span id='a'>alpha</span><span id='b'>beta</span><span id='c'>gamma</span></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _MultiReadHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [
                                    {"op": "read", "selector": "#a"},
                                    {"op": "read", "selector": "#b"},
                                    {"op": "read", "selector": "#c"},
                                ],
                            },
                        },
                    }) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertTrue(res["ok"], msg=res)
                self.assertEqual(["alpha", "beta", "gamma"], [s["text"] for s in res["steps"]])
        finally:
            server.shutdown()
            server.server_close()

    def test_daily_fallback_shares_single_total_timeout_deadline_with_kept(self):
        server = HTTPServer(("127.0.0.1", 0), _FakeOcrHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                helper = root / "browser_task.py"
                helper.write_text(
                    "import json, sys, time\n"
                    "json.load(sys.stdin)\n"
                    "time.sleep(0.65)\n"
                    "print(json.dumps({'ok': False, 'available': False, 'reason': 'daily_unavailable'}))\n",
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(root / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                env["AUTO_COMPUTER_DAILY_HELPER"] = str(helper)
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "daily_browser_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "fallback_to_kept": True,
                                "total_timeout_ms": 1000,
                                "actions": [
                                    {"op": "wait", "ms": 600},
                                    {"op": "read", "selector": "#status"},
                                ],
                            },
                        },
                    }) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res["ok"], msg=res)
                self.assertEqual("timeout", res["reason"])
        finally:
            server.shutdown()
            server.server_close()

    def test_daily_fallback_to_kept_serializes_behind_active_kept_task(self):
        click_order: list[str] = []

        class _OrderHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/hit/first":
                    click_order.append("first")
                    self.send_response(200)
                    self.end_headers()
                    return
                if self.path == "/hit/second":
                    click_order.append("second")
                    self.send_response(200)
                    self.end_headers()
                    return
                body = (
                    "<!doctype html><html><head><title>OrderTest</title></head>"
                    "<body>"
                    "<button id='first' onclick=\"fetch('/hit/first')\">First</button>"
                    "<button id='second' onclick=\"fetch('/hit/second')\">Second</button>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _OrderHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                helper = root / "browser_task.py"
                helper.write_text(
                    "import json, sys\n"
                    "json.load(sys.stdin)\n"
                    "print(json.dumps({'ok': False, 'available': False, 'reason': 'daily_unavailable'}))\n",
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(root / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                env["AUTO_COMPUTER_DAILY_HELPER"] = str(helper)
                proc = subprocess.Popen(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                try:
                    assert proc.stdin is not None and proc.stdout is not None
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "kept",
                                "actions": [
                                    {"op": "wait", "ms": 700},
                                    {"op": "click", "selector": "#first"},
                                ],
                            },
                        },
                    }) + "\n")
                    proc.stdin.flush()
                    time.sleep(0.15)
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "daily_browser_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "fallback_to_kept": True,
                                "actions": [{"op": "click", "selector": "#second"}],
                            },
                        },
                    }) + "\n")
                    proc.stdin.flush()
                    r1 = json.loads(proc.stdout.readline())
                    r2 = json.loads(proc.stdout.readline())
                    resps = {r1["id"]: r1["result"]["structuredContent"], r2["id"]: r2["result"]["structuredContent"]}
                    self.assertTrue(resps[1]["ok"], msg=resps[1])
                    self.assertTrue(resps[2]["ok"], msg=resps[2])
                    self.assertEqual("kept", resps[2].get("fallback_used"))
                    self.assertEqual(["first", "second"], click_order)
                finally:
                    if proc.stdin:
                        proc.stdin.close()
                    proc.wait(timeout=15)
        finally:
            server.shutdown()
            server.server_close()

    def test_inspect_tab_targeted_filters_shrink_candidates(self):
        class _InspectFilterHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                noise = "".join(f"<a href='/nav{i}'>Menu Item {i}</a><button id='n{i}'>Noise {i}</button>" for i in range(10))
                body = (
                    "<!doctype html><html><head><title>InspectFilter</title></head>"
                    f"<body><div id='nav'>{noise}</div>"
                    "<section id='checkout'><input id='qty' placeholder='Qty'/><button id='confirm'>Confirm Order</button><button id='cancel'>Cancel</button></section>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _InspectFilterHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                reqs = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "open_tab", "arguments": {"url": f"http://127.0.0.1:{port}/", "browser": "edge", "profile": "auto"}},
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "list_tabs", "arguments": {"browser": "edge", "profile": "auto"}},
                    },
                ]
                proc = subprocess.Popen(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                try:
                    assert proc.stdin is not None and proc.stdout is not None
                    proc.stdin.write(json.dumps(reqs[0]) + "\n")
                    proc.stdin.flush()
                    open_res = json.loads(proc.stdout.readline())["result"]["structuredContent"]
                    tid = open_res["target_id"]
                    time.sleep(0.35)

                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "inspect_tab", "arguments": {"target_id": tid, "browser": "edge", "profile": "auto"}},
                    }) + "\n")
                    proc.stdin.flush()
                    unfiltered = json.loads(proc.stdout.readline())["result"]["structuredContent"]

                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "inspect_tab",
                            "arguments": {
                                "target_id": tid,
                                "browser": "edge",
                                "profile": "auto",
                                "selector": "#checkout",
                                "control_type": "button",
                                "text_contains": "confirm",
                            },
                        },
                    }) + "\n")
                    proc.stdin.flush()
                    filtered = json.loads(proc.stdout.readline())["result"]["structuredContent"]

                    self.assertTrue(unfiltered["ok"])
                    self.assertTrue(filtered["ok"])
                    self.assertGreater(len(unfiltered["candidates"]), 10)
                    self.assertEqual(1, len(filtered["candidates"]))
                    self.assertEqual("#confirm", filtered["candidates"][0]["locator"])
                    self.assertLess(len(json.dumps(filtered)), len(json.dumps(unfiltered)))
                finally:
                    if proc.stdin:
                        proc.stdin.close()
                    proc.wait(timeout=15)
        finally:
            server.shutdown()
            server.server_close()

    def test_queued_timeout_and_cancel_return_immediately_preserving_order(self):
        click_order: list[str] = []
        page_hits = 0
        first_loaded = threading.Event()

        class _QueueHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                nonlocal page_hits
                if self.path == "/hit/first":
                    click_order.append("first")
                    self.send_response(200)
                    self.end_headers()
                    return
                if self.path == "/hit/second":
                    click_order.append("second")
                    self.send_response(200)
                    self.end_headers()
                    return
                if self.path == "/hit/third":
                    click_order.append("third")
                    self.send_response(200)
                    self.end_headers()
                    return
                page_hits += 1
                first_loaded.set()
                body = (
                    "<!doctype html><html><head><title>QueueTest</title></head>"
                    "<body>"
                    "<button id='first' onclick=\"fetch('/hit/first').then(() => { document.body.dataset.doneFirst = '1'; })\">First</button>"
                    "<button id='second' onclick=\"fetch('/hit/second').then(() => { document.body.dataset.doneSecond = '1'; })\">Second</button>"
                    "<button id='third' onclick=\"fetch('/hit/third').then(() => { document.body.dataset.doneThird = '1'; })\">Third</button>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _QueueHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                helper = root / "browser_task.py"
                helper.write_text(
                    "import json, sys\n"
                    "json.load(sys.stdin)\n"
                    "print(json.dumps({'ok': False, 'available': False, 'reason': 'daily_unavailable'}))\n",
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(root / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                env["AUTO_COMPUTER_DAILY_HELPER"] = str(helper)

                # Part A: daily fallback waiting in kept:edge queue returns immediately on 550ms timeout while preserving order
                proc_a = subprocess.Popen(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                try:
                    assert proc_a.stdin is not None and proc_a.stdout is not None
                    proc_a.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/page",
                                "browser": "edge",
                                "profile": "kept",
                                "actions": [
                                    {"op": "wait", "ms": 1400},
                                    {"op": "click", "selector": "#first"},
                                    {"op": "wait_for", "selector": "body[data-done-first='1']", "timeout_ms": 2000},
                                ],
                            },
                        },
                    }) + "\n")
                    proc_a.stdin.flush()
                    self.assertTrue(first_loaded.wait(timeout=15))

                    t2_sent = time.monotonic()
                    proc_a.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "daily_browser_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/page",
                                "browser": "edge",
                                "fallback_to_kept": True,
                                "total_timeout_ms": 550,
                                "actions": [{"op": "click", "selector": "#second"}],
                            },
                        },
                    }) + "\n")
                    proc_a.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/page",
                                "browser": "edge",
                                "profile": "kept",
                                "actions": [
                                    {"op": "click", "selector": "#third"},
                                    {"op": "wait_for", "selector": "body[data-done-third='1']", "timeout_ms": 2000},
                                ],
                            },
                        },
                    }) + "\n")
                    proc_a.stdin.flush()

                    resps_a: dict[int, dict] = {}
                    recv_times_a: dict[int, float] = {}
                    for _ in range(3):
                        obj = json.loads(proc_a.stdout.readline())
                        recv_times_a[obj["id"]] = time.monotonic()
                        resps_a[obj["id"]] = obj["result"]["structuredContent"]

                    self.assertFalse(resps_a[2]["ok"])
                    self.assertEqual("timeout", resps_a[2]["reason"])
                    self.assertEqual("kept", resps_a[2]["fallback_used"])
                    self.assertLess(recv_times_a[2] - t2_sent, 1.1)
                    self.assertLess(recv_times_a[2], recv_times_a[1])
                    self.assertTrue(resps_a[1]["ok"], msg=resps_a[1])
                    self.assertTrue(resps_a[3]["ok"], msg=resps_a[3])
                    self.assertEqual(["first", "third"], click_order)
                finally:
                    for pipe in (proc_a.stdin, proc_a.stdout, proc_a.stderr):
                        if pipe:
                            pipe.close()
                    proc_a.wait(timeout=15)

                # Part B: top-level queued cancel and timeout return immediately while keeping remaining queue order
                click_order.clear()
                first_loaded.clear()
                proc_b = subprocess.Popen(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                try:
                    assert proc_b.stdin is not None and proc_b.stdout is not None
                    proc_b.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/page",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [
                                    {"op": "wait", "ms": 1800},
                                    {"op": "click", "selector": "#first"},
                                    {"op": "wait_for", "selector": "body[data-done-first='1']", "timeout_ms": 2000},
                                ],
                            },
                        },
                    }) + "\n")
                    proc_b.stdin.flush()
                    self.assertTrue(first_loaded.wait(timeout=15))

                    t3_sent = time.monotonic()
                    proc_b.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/page",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [{"op": "click", "selector": "#second"}],
                            },
                        },
                    }) + "\n")
                    proc_b.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/page",
                                "browser": "edge",
                                "profile": "auto",
                                "total_timeout_ms": 1000,
                                "actions": [{"op": "click", "selector": "#second"}],
                            },
                        },
                    }) + "\n")
                    proc_b.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/page",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [
                                    {"op": "click", "selector": "#third"},
                                    {"op": "wait_for", "selector": "body[data-done-third='1']", "timeout_ms": 2000},
                                ],
                            },
                        },
                    }) + "\n")
                    proc_b.stdin.flush()

                    time.sleep(0.1)
                    t2_cancel = time.monotonic()
                    proc_b.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {"requestId": 2, "reason": "abort queued request"},
                    }) + "\n")
                    proc_b.stdin.flush()

                    resps_b: dict[int, dict] = {}
                    recv_times_b: dict[int, float] = {}
                    for _ in range(4):
                        obj = json.loads(proc_b.stdout.readline())
                        recv_times_b[obj["id"]] = time.monotonic()
                        resps_b[obj["id"]] = obj["result"]["structuredContent"]

                    self.assertFalse(resps_b[2]["ok"])
                    self.assertEqual("cancelled", resps_b[2]["reason"])
                    self.assertLess(recv_times_b[2] - t2_cancel, 0.8)
                    self.assertLess(recv_times_b[2], recv_times_b[1])

                    self.assertFalse(resps_b[3]["ok"])
                    self.assertEqual("timeout", resps_b[3]["reason"])
                    self.assertLess(recv_times_b[3] - t3_sent, 1.45)
                    self.assertLess(recv_times_b[3], recv_times_b[1])

                    self.assertTrue(resps_b[1]["ok"], msg=resps_b[1])
                    self.assertTrue(resps_b[4]["ok"], msg=resps_b[4])
                    self.assertEqual(["first", "third"], click_order)
                finally:
                    for pipe in (proc_b.stdin, proc_b.stdout, proc_b.stderr):
                        if pipe:
                            pipe.close()
                    proc_b.wait(timeout=15)
        finally:
            server.shutdown()
            server.server_close()

    def test_inspect_tab_selector_includes_self_and_text_limit_zero_skips_text_extraction(self):
        class _InspectSelfHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    "<!doctype html><html><head><title>InspectSelf</title></head>"
                    "<body>"
                    "<button id='target'>Target Action</button>"
                    "<section id='panel'><button id='inside'>Inside</button><span>Panel Text</span></section>"
                    "<div id='reads'>0</div>"
                    "<script>"
                    "window.__textReads = 0;"
                    "const panel = document.getElementById('panel');"
                    "const origDesc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'innerText');"
                    "Object.defineProperty(panel, 'innerText', {"
                    "  configurable: true,"
                    "  get() {"
                    "    window.__textReads += 1;"
                    "    document.getElementById('reads').textContent = String(window.__textReads);"
                    "    return origDesc.get.call(this);"
                    "  }"
                    "});"
                    "</script>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _InspectSelfHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                proc = subprocess.Popen(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                try:
                    assert proc.stdin is not None and proc.stdout is not None
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "open_tab", "arguments": {"url": f"http://127.0.0.1:{port}/", "browser": "edge", "profile": "auto"}},
                    }) + "\n")
                    proc.stdin.flush()
                    tid = json.loads(proc.stdout.readline())["result"]["structuredContent"]["target_id"]
                    time.sleep(0.35)

                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "inspect_tab",
                            "arguments": {"target_id": tid, "browser": "edge", "profile": "auto", "selector": "#target", "text_limit": 0},
                        },
                    }) + "\n")
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "inspect_tab",
                            "arguments": {"target_id": tid, "browser": "edge", "profile": "auto", "selector": "#panel", "text_limit": 0},
                        },
                    }) + "\n")
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "run_actions",
                            "arguments": {
                                "target_id": tid,
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [{"op": "read", "selector": "#reads"}],
                            },
                        },
                    }) + "\n")
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {
                            "name": "inspect_tab",
                            "arguments": {"target_id": tid, "browser": "edge", "profile": "auto", "selector": "#panel", "text_limit": 50},
                        },
                    }) + "\n")
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "tools/call",
                        "params": {
                            "name": "run_actions",
                            "arguments": {
                                "target_id": tid,
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [{"op": "read", "selector": "#reads"}],
                            },
                        },
                    }) + "\n")
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "tools/call",
                        "params": {"name": "stop_auto_browser", "arguments": {"browser": "edge", "profile": "auto"}},
                    }) + "\n")
                    proc.stdin.flush()

                    resps = {}
                    for _ in range(6):
                        obj = json.loads(proc.stdout.readline())
                        resps[obj["id"]] = obj["result"]["structuredContent"]

                    self.assertTrue(resps[2]["ok"], msg=resps[2])
                    self.assertEqual("", resps[2]["text"])
                    self.assertEqual(1, len(resps[2]["candidates"]))
                    self.assertEqual("#target", resps[2]["candidates"][0]["locator"])

                    self.assertTrue(resps[3]["ok"], msg=resps[3])
                    self.assertEqual("", resps[3]["text"])
                    self.assertEqual("#inside", resps[3]["candidates"][0]["locator"])
                    self.assertEqual("0", resps[4]["steps"][0]["text"])

                    self.assertTrue(resps[5]["ok"], msg=resps[5])
                    self.assertIn("Panel Text", resps[5]["text"])
                    self.assertEqual("1", resps[6]["steps"][0]["text"])
                finally:
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        if pipe:
                            pipe.close()
                    proc.wait(timeout=15)
        finally:
            server.shutdown()
            server.server_close()

    def test_read_list_large_list_over_500_respects_limit(self):
        class _LargeListHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                rows = "".join(f"<li class='row'>Row {i}</li>" for i in range(510))
                body = (
                    "<!doctype html><html><head><title>LargeList</title></head>"
                    f"<body><ul>{rows}</ul></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _LargeListHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "url": f"http://127.0.0.1:{port}/",
                                "browser": "edge",
                                "profile": "auto",
                                "actions": [
                                    {"op": "wait_for", "selector": "li.row", "min_count": 2, "timeout_ms": 2000},
                                    {"op": "read", "selector": "li.row", "as": "count"},
                                    {"op": "read_list", "item_selector": "li.row", "limit": 2},
                                ],
                            },
                        },
                    }) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertTrue(res["ok"], msg=res)
                self.assertEqual("wait_for", res["steps"][0]["op"])
                self.assertEqual(510, res["steps"][0]["count"])
                self.assertEqual("read", res["steps"][1]["op"])
                self.assertEqual(510, res["steps"][1]["count"])
                self.assertEqual("read_list", res["steps"][2]["op"])
                self.assertEqual(2, res["steps"][2]["count"])
                self.assertEqual(["Row 0", "Row 1"], [item["text"] for item in res["steps"][2]["items"]])
        finally:
            server.shutdown()
            server.server_close()

    def test_daily_and_dedicated_read_modes_format_parity(self):
        class _ParityHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    "<!doctype html><html><head><title>Parity</title></head>"
                    "<body><h1 id='title'>Order #99</h1>"
                    "<ul><li class='row'>A</li><li class='row'>B</li><li class='row'>C</li></ul>"
                    "<a id='item' href='https://shop.example/99' data-sku='SKU-99'>Item 99</a>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        actions = [
            {"op": "read", "selector": "#title"},
            {"op": "read", "selector": ".row", "as": "count"},
            {"op": "read", "selector": "a#item", "as": "attribute", "attribute": "data-sku"},
            {"op": "read", "selector": "a#item", "as": "attribute", "attribute": "data-missing"},
        ]
        server = HTTPServer(("127.0.0.1", 0), _ParityHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                url = f"http://127.0.0.1:{port}/"
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {"url": url, "browser": "edge", "profile": "auto", "actions": actions},
                        },
                    }) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                dedicated_res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertTrue(dedicated_res["ok"], msg=dedicated_res)

                validated = browser_task._validate({"url": url, "actions": actions})
                code = browser_task._task_code(url, validated["actions"], False)
                harness = """
const elements = {
  '#title': [{ hidden: false, innerText: 'Order #99', getAttribute() { return null; } }],
  '.row': [{}, {}, {}],
  'a#item': [{ hidden: false, innerText: 'Item 99', getAttribute(k) { return k === 'data-sku' ? 'SKU-99' : (k === 'href' ? 'https://shop.example/99' : null); } }],
};
const fn = eval('(' + process.argv[1] + ')');
const targetPage = {
  url: () => process.argv[2],
  isClosed: () => false,
  locator: (sel) => ({
    count: async () => (elements[sel] || []).length,
    evaluate: async (cb, arg) => {
      const list = elements[sel] || [];
      if (list.length !== 1) throw new Error('strict mode violation: ' + list.length);
      return cb(list[0], arg);
    },
  }),
};
targetPage.context = () => ({ pages: () => [targetPage] });
fn(targetPage).then(res => { process.stdout.write(JSON.stringify(res)); });
"""
                daily_proc = subprocess.run(
                    ["node", "-e", harness, code, url],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=15,
                    check=True,
                )
                daily_res = json.loads(daily_proc.stdout)
                self.assertTrue(daily_res["ok"], msg=daily_res)
                self.assertEqual(dedicated_res["steps"], daily_res["steps"])
        finally:
            server.shutdown()
            server.server_close()

    def test_run_task_batch_edit_csv_supports_header_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            left = root / "a.csv"
            right = root / "b.csv"
            left.write_text("说明行\n编号,价格,状态\n1001,99,待核对\n1002,120,已核对\n", encoding="utf-8")
            right.write_text("编号,价格,状态\n1001,105,待核对\n1002,120,已核对\n", encoding="utf-8")
            steps = json.dumps([
                {
                    "op": "table-reconcile",
                    "left": str(left),
                    "right": str(right),
                    "left_header_row": 2,
                    "right_header_row": 1,
                    "key": "编号",
                    "columns": "价格,状态",
                },
                {
                    "op": "edit-csv",
                    "file": str(left),
                    "backup_root": str(root / "bak"),
                    "key_column": "编号",
                    "key": "1001",
                    "column": "状态",
                    "before": "待核对",
                    "after": "已核对",
                    "header_row": 2,
                },
                {"op": "read-text", "file": str(left), "contains": "1001,99,已核对"},
            ], ensure_ascii=False)
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(SCRIPTS_DIR / "run_task.py"), "batch", "--steps", steps],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=True,
            )
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"], msg=payload)
            self.assertEqual(2, payload["steps"][1]["change"]["header_row"])
            self.assertTrue(left.read_text(encoding="utf-8").startswith("说明行\n编号,价格,状态\n1001,99,已核对\n"))

    def test_open_and_run_multipage_preserves_results_on_page_timeout(self):
        class _MultiTimeoutHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/p1":
                    body = b"<!doctype html><html><body><span id='price'>101</span></body></html>"
                elif self.path == "/p2":
                    body = b"<!doctype html><html><body><span>missing</span></body></html>"
                else:
                    body = b"<!doctype html><html><body><span id='price'>303</span></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                return

        server = HTTPServer(("127.0.0.1", 0), _MultiTimeoutHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["AUTO_COMPUTER_HOME"] = tmp
                env["AUTO_COMPUTER_PROFILE_ROOT"] = str(Path(tmp) / "profiles")
                env["AUTO_COMPUTER_PYTHON"] = sys.executable
                proc = subprocess.run(
                    ["node", str(MCP_DIR / "edge_mcp.cjs")],
                    input=json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_and_run",
                            "arguments": {
                                "urls": [
                                    f"http://127.0.0.1:{port}/p1",
                                    f"http://127.0.0.1:{port}/p2",
                                    f"http://127.0.0.1:{port}/p3",
                                ],
                                "browser": "edge",
                                "profile": "auto",
                                "wait_selector": "#price",
                                "wait_timeout_ms": 700,
                                "actions": [{"op": "read", "selector": "#price"}],
                            },
                        },
                    }) + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35,
                    env=env,
                )
                self.assertEqual(0, proc.returncode, msg=proc.stderr or proc.stdout)
                res = json.loads(proc.stdout.strip().splitlines()[-1])["result"]["structuredContent"]
                self.assertFalse(res["ok"], msg=res)
                self.assertEqual("page_timeout", res["reason"], msg=res)
                self.assertEqual(3, res["count"], msg=res)
                self.assertTrue(res["pages"][0]["ok"], msg=res["pages"][0])
                self.assertEqual("101", res["pages"][0]["steps"][0]["text"])
                self.assertFalse(res["pages"][1]["ok"], msg=res["pages"][1])
                self.assertEqual("page_timeout", res["pages"][1]["reason"])
                self.assertTrue(res["pages"][2]["ok"], msg=res["pages"][2])
                self.assertEqual("303", res["pages"][2]["steps"][0]["text"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
