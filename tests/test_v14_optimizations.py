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
        with mock.patch.object(desktop_mcp, "_run_uia", return_value={"ok": True, "windows": raw_windows}):
            filtered = desktop_mcp._call("list_windows", {"process": "Weixin"})
            all_wins = desktop_mcp._call("list_windows", {"process": "Weixin", "include_hidden": True})
        self.assertEqual(["0x1001", "0x1002"], [w["hwnd"] for w in filtered["windows"]])
        self.assertEqual(["0x1001", "0x1002", "0x1003", "0x1004", "0x1005", "0x1006"], [w["hwnd"] for w in all_wins["windows"]])

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
        self.assertIn("if (!visible && closeAfter)", edge_src)

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
                {"hwnd": "0x2001", "pid": 300, "process": "SampleApp", "className": "SampleMainClass", "bounds": {"x": 0, "y": 0, "width": 800, "height": 600}},
                {"hwnd": "0x2002", "pid": 300, "process": "SampleApp", "className": "SampleToolSaveBits", "bounds": {"x": 10, "y": 10, "width": 100, "height": 100}},
            ]
            with (
                mock.patch.object(desktop_mcp.adapter_registry, "REGISTRY_DIR", adapters_dir),
                mock.patch.object(desktop_mcp, "_run_uia", side_effect=[{"ok": True, "windows": windows}, {"ok": True, "results": [{"index": 0, "op": "wait"}]}]) as run_uia,
            ):
                res = desktop_mcp._call("run_actions", {"process": "SampleApp", "actions": [{"op": "wait", "ms": 1}]})
            self.assertTrue(res["ok"])
            self.assertEqual("0x2001", res["resolvedHwnd"])
            self.assertEqual("0x2001", run_uia.call_args_list[1].args[0]["hwnd"])

            ambiguous_windows = [
                {"hwnd": "0x2001", "pid": 300, "process": "SampleApp", "className": "SampleMainClass", "bounds": {"x": 0, "y": 0, "width": 800, "height": 600}},
                {"hwnd": "0x2003", "pid": 301, "process": "SampleApp", "className": "SampleMainClass", "bounds": {"x": 10, "y": 10, "width": 800, "height": 600}},
            ]
            with (
                mock.patch.object(desktop_mcp.adapter_registry, "REGISTRY_DIR", adapters_dir),
                mock.patch.object(desktop_mcp, "_run_uia", return_value={"ok": True, "windows": ambiguous_windows}),
            ):
                with self.assertRaises(desktop_mcp.BridgeError) as ctx:
                    desktop_mcp._call("run_actions", {"process": "SampleApp", "actions": [{"op": "wait", "ms": 1}]})
            self.assertIn("Multiple windows matched", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
