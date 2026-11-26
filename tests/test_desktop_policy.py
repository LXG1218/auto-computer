from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skill" / "auto-computer" / "scripts"))
import desktop_policy as policy


class ProcessPolicyTests(unittest.TestCase):
    def test_blocks_named_wechat_process_and_every_descendant(self):
        rows = [
            {"pid": 10, "parent_pid": 1, "name": "Weixin.exe"},
            {"pid": 11, "parent_pid": 10, "name": "renderer.exe"},
            {"pid": 12, "parent_pid": 11, "name": "utility-child.exe"},
            {"pid": 20, "parent_pid": 1, "name": "notepad.exe"},
        ]
        self.assertEqual(policy.protected_pids(rows), {10, 11, 12})

    def test_wechat_child_names_and_cycles_are_safe(self):
        rows = [
            {"pid": 21, "parent_pid": 20, "name": "WeChatAppEx.exe"},
            {"pid": 20, "parent_pid": 21, "name": "renderer.exe"},
        ]
        self.assertEqual(policy.protected_pids(rows), {20, 21})
        self.assertTrue(policy.is_protected_name(r"C:\Apps\WeChatBrowser.exe"))


class LaunchRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "fixture.exe"
        self.exe.write_bytes(b"synthetic placeholder; never executed")
        self.registry = self.root / "machine-policy"

    def entry(self, **changes):
        value = {
            "id": "fixture",
            "executable": str(self.exe),
            "version": "1.2.3.4",
            "route": "verified_background_switch",
            "switch": "--background",
            "arguments": [],
            "verified": True,
        }
        value.update(changes)
        return value

    def test_registration_load_and_exact_version_guard(self):
        path = policy.register_launch_target(self.entry(), self.registry)
        self.assertEqual(path.parent, self.registry)
        self.assertEqual(policy.load_launch_target("fixture", self.registry)["version"], "1.2.3.4")
        with self.assertRaises(FileExistsError):
            policy.register_launch_target(self.entry(), self.registry)
        with self.assertRaises(ValueError):
            policy.validate_launch_target(self.entry(), version="1.2.3.5")

    def test_unknown_routes_and_unverified_targets_fail_closed(self):
        with self.assertRaises(ValueError):
            policy.validate_launch_target(self.entry(route="unknown"))
        with self.assertRaises(ValueError):
            policy.validate_launch_target(self.entry(verified=False))
        with self.assertRaises(ValueError):
            policy.validate_launch_target(self.entry(route="audited_min_no_active", entrypoint_audited=False))

    def test_wechat_executable_cannot_be_registered(self):
        wechat = self.root / "WeChat.exe"
        wechat.write_bytes(b"synthetic placeholder; never executed")
        with self.assertRaises(PermissionError):
            policy.validate_launch_target(self.entry(executable=str(wechat)))


if __name__ == "__main__":
    unittest.main()
