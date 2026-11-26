import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
