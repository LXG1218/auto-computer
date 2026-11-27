import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skill" / "auto-computer" / "scripts"))
import browser_session as session


class BrowserSessionProfileTests(unittest.TestCase):
    def _data(self, item):
        return {"browsers": [item]}

    def test_default_named_session_is_scoped_to_encoded_profile(self):
        name = session._default_session("chrome", "Default")
        item = {"name": name, "browserType": "chrome", "status": "open", "attached": True, "compatible": True}
        self.assertIs(item, session._find_session(self._data(item), name, "chrome", "Default"))

    def test_custom_session_without_profile_metadata_is_not_reused(self):
        item = {"name": "my-session", "browserType": "chrome", "status": "open", "attached": True, "compatible": True}
        found = session._find_session(self._data(item), "my-session", "chrome", "Default")
        self.assertTrue(found["conflict"])
        self.assertEqual("profile_mismatch_or_unverified", found["conflict_reason"])

    def test_wrong_browser_or_explicit_profile_is_not_reused(self):
        name = session._default_session("chrome", "Default")
        wrong_browser = {"name": name, "browserType": "msedge", "status": "open"}
        wrong_profile = {"name": name, "browserType": "chrome", "status": "open", "profileDirName": "Profile 2"}
        self.assertEqual("browser_mismatch", session._find_session(self._data(wrong_browser), name, "chrome", "Default")["conflict_reason"])
        self.assertEqual("profile_mismatch_or_unverified", session._find_session(self._data(wrong_profile), name, "chrome", "Default")["conflict_reason"])

    def test_profile_metadata_accepts_exact_custom_session_and_path(self):
        item = {"name": "custom", "browserType": "msedge", "status": "open", "profile": r"C:\Users\a\User Data\Profile 2"}
        self.assertIs(item, session._find_session(self._data(item), "custom", "edge", "Profile 2"))

    def test_open_browser_sessions_surfaces_custom_authorized_sessions(self):
        data = {
            "browsers": [
                {"name": "auto-computer-edge-qa-v4", "browserType": "msedge", "status": "open", "attached": True, "compatible": True},
                {"name": "chrome-tab", "browserType": "chrome", "status": "open", "attached": True, "compatible": True},
                {"name": "closed-edge", "browserType": "msedge", "status": "closed", "attached": False, "compatible": False},
            ]
        }
        edge_sessions = session._open_browser_sessions(data, "edge")
        self.assertEqual(1, len(edge_sessions))
        self.assertEqual("auto-computer-edge-qa-v4", edge_sessions[0]["name"])
        self.assertTrue(edge_sessions[0]["attached"])

    def test_sideloaded_extension_detected_from_secure_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "Default"
            ext_dir = root / "sideloaded-ext"
            ext_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            (ext_dir / "manifest.json").write_text(
                json.dumps({"name": "Playwright Extension", "version": "0.6.0"}), encoding="utf-8"
            )
            (profile_dir / "Secure Preferences").write_text(
                json.dumps({"extensions": {"settings": {session.EXTENSION_ID: {"path": str(ext_dir), "disable_reasons": []}}}}),
                encoding="utf-8",
            )
            with mock.patch.object(session, "_profile_root", return_value=profile_dir):
                self.assertEqual("0.6.0", session._extension_version("edge", "Default"))

    def test_status_supports_both_edge_and_older_chrome_versions(self):
        for browser, ver in (("edge", (157, 0, 4341, 42)), ("chrome", (150, 0, 0, 1))):
            with (
                mock.patch.object(session, "_browser_exe", return_value="dummy.exe"),
                mock.patch.object(session, "_cli_list", return_value={"browsers": []}),
                mock.patch.object(session, "_browser_version", return_value=ver),
                mock.patch.object(session, "_is_running", return_value=True),
                mock.patch.object(session, "_token_source", return_value=("A" * 43, "dpapi_file")),
                mock.patch.object(session, "_extension_version", return_value="0.6.0"),
            ):
                res = session._status(browser, "Default", session._default_session(browser, "Default"))
                self.assertTrue(res["can_reconnect_in_background"], msg=res)
                self.assertEqual([], res["blockers"])
                self.assertFalse(res["supports_native_background_flag"])

    def test_cli_env_injects_ipv4first(self):
        with mock.patch.dict(os.environ, {"NODE_OPTIONS": "--max-old-space-size=512"}, clear=False):
            env = session._cli_env()
            self.assertIn("--dns-result-order=ipv4first", env["NODE_OPTIONS"])
            self.assertIn("--max-old-space-size=512", env["NODE_OPTIONS"])

    def test_detach_verifies_session_removal_and_is_idempotent(self):
        open_data = {"browsers": [{"name": "auto-computer-chrome-default", "browserType": "chrome", "status": "open", "attached": True, "compatible": True}]}
        empty_data = {"browsers": []}
        with (
            mock.patch.object(session, "_cli_list", side_effect=[open_data, empty_data]),
            mock.patch.object(session.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")),
        ):
            res = session._detach("chrome", "Default", "auto-computer-chrome-default")
            self.assertTrue(res["ok"])
            self.assertEqual(["auto-computer-chrome-default"], res["detached"])
            self.assertEqual([], res["remaining"])

        with mock.patch.object(session, "_cli_list", return_value=empty_data):
            res2 = session._detach("chrome", "Default", "auto-computer-chrome-default")
            self.assertTrue(res2["ok"])
            self.assertTrue(res2["already_detached"])

    def test_detach_closes_blank_extension_tabs_and_handles_auto_closed_session(self):
        open_data = {"browsers": [{"name": "auto-computer-edge-default", "browserType": "msedge", "status": "open", "attached": True, "compatible": True}]}
        empty_data = {"browsers": []}
        run_results = [
            mock.Mock(returncode=0, stdout='{"closed":1}', stderr=""),
            mock.Mock(returncode=1, stdout="", stderr="session closed"),
        ]
        with (
            mock.patch.object(session, "_cli_list", side_effect=[open_data, empty_data]),
            mock.patch.object(session.subprocess, "run", side_effect=run_results) as run_mock,
        ):
            res = session._detach("edge", "Default", "auto-computer-edge-default")
            self.assertTrue(res["ok"], msg=res)
            self.assertEqual(1, res["closed_blank_tabs"])
            self.assertEqual(["auto-computer-edge-default"], res["detached"])
            self.assertEqual(2, run_mock.call_count)

        custom_open = {"browsers": [{"name": "my-custom-edge", "browserType": "msedge", "status": "open", "attached": True, "compatible": True}]}
        with (
            mock.patch.object(session, "_cli_list", side_effect=[custom_open, empty_data]),
            mock.patch.object(session.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run_mock,
        ):
            res_custom = session._detach("edge", "Default", "my-custom-edge")
            self.assertTrue(res_custom["ok"], msg=res_custom)
            self.assertEqual(0, res_custom["closed_blank_tabs"])
            self.assertEqual(1, run_mock.call_count)

    def test_foreground_result_allows_edge_sleeping_tab_wake_caption_changes(self):
        cases = [
            (
                "全部商品页 - 睡眠 - Microsoft Edge",
                "全部商品页 - Microsoft Edge",
            ),
            (
                "全部商品页 - 睡眠 - 个人 - Microsoft\u200b Edge",
                "全部商品页 - 个人 - Microsoft\u200b Edge",
            ),
            (
                "全部商品页 和另外 3 个页面 - 睡眠 - 个人 - Microsoft\u200b Edge",
                "全部商品页 和另外 3 个页面 - 个人 - Microsoft\u200b Edge",
            ),
            (
                "全部商品页 - 睡眠 和另外 3 个页面 - 个人 - Microsoft\u200b Edge",
                "全部商品页 和另外 3 个页面 - 个人 - Microsoft\u200b Edge",
            ),
            (
                "All Products - Sleeping - Microsoft Edge",
                "All Products - Microsoft Edge",
            ),
            (
                "All Products - Sleeping and 2 more pages - Work - Microsoft Edge",
                "All Products and 2 more pages - Work - Microsoft Edge",
            ),
            (
                "睡眠研究 - 睡眠 - 睡眠 - Microsoft Edge",
                "睡眠研究 - 睡眠 - Microsoft Edge",
            ),
            (
                "Order Dashboard - 个人 - Microsoft\u200b Edge",
                "Order Dashboard 和另外 1 个页面 - 个人 - Microsoft\u200b Edge",
            ),
            (
                "Order Dashboard 和另外 3 个页面 - 个人 - Microsoft\u200b Edge",
                "Order Dashboard 和另外 4 个页面 - 个人 - Microsoft\u200b Edge",
            ),
            (
                "全部商品页 - 睡眠 和另外 3 个页面 - 个人 - Microsoft\u200b Edge",
                "全部商品页 和另外 4 个页面 - 个人 - Microsoft\u200b Edge",
            ),
            (
                "全部商品页 - 睡眠 - Microsoft Edge",
                "全部商品页 和另外 1 个页面 - Microsoft Edge",
            ),
            (
                "全部商品页 - 已丢弃 - Microsoft Edge",
                "全部商品页 和另外 1 个页面 - Microsoft Edge",
            ),
            (
                "All Products - Discarded - Microsoft Edge",
                "All Products and 1 more page - Microsoft Edge",
            ),
            (
                "All Products and 2 more pages - Work - Microsoft Edge",
                "All Products and 3 more pages - Work - Microsoft Edge",
            ),
        ]
        for before_caption, after_caption in cases:
            with self.subTest(before_caption=before_caption, after_caption=after_caption):
                fg, cap = session._foreground_result(
                    {"foreground_hwnd": 100, "target_caption": before_caption},
                    {"foreground_hwnd": 100, "target_caption": after_caption},
                )
                self.assertEqual(("preserved", True), (fg, cap))

    def test_foreground_result_rejects_real_caption_switch_and_foreground_change(self):
        rejected_cases = [
            (
                "全部商品页 - 睡眠 - Microsoft Edge",
                "Playwright Extension - Microsoft Edge",
            ),
            (
                "全部商品页 - Microsoft Edge",
                "登录 - Microsoft Edge",
            ),
            (
                "睡眠 - Microsoft Edge",
                "Microsoft Edge",
            ),
            (
                "Order Dashboard 和另外 3 个页面 - 个人 - Microsoft Edge",
                "Playwright Extension 和另外 4 个页面 - 个人 - Microsoft Edge",
            ),
            (
                "Order Dashboard 和另外 3 个页面 - 个人 - Microsoft Edge",
                "Order Dashboard 和另外 4 个页面 - 工作 - Microsoft Edge",
            ),
        ]
        for before_caption, after_caption in rejected_cases:
            with self.subTest(before_caption=before_caption, after_caption=after_caption):
                fg, cap = session._foreground_result(
                    {"foreground_hwnd": 100, "target_caption": before_caption},
                    {"foreground_hwnd": 100, "target_caption": after_caption},
                )
                self.assertEqual(("preserved", False), (fg, cap))

        fg_changed, cap_ok = session._foreground_result(
            {"foreground_hwnd": 100, "target_caption": "全部商品页 - 睡眠 - Microsoft Edge"},
            {"foreground_hwnd": 200, "target_caption": "全部商品页 - Microsoft Edge"},
        )
        self.assertEqual(("changed", True), (fg_changed, cap_ok))


if __name__ == "__main__":
    unittest.main()
