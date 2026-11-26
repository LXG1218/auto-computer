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


if __name__ == "__main__":
    unittest.main()
