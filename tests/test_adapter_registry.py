import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skill" / "auto-computer" / "scripts"))
import adapter_registry as registry


class AdapterRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "adapters"

    def tearDown(self):
        self.temp.cleanup()

    def test_record_get_and_list_binding(self):
        saved = registry.record_binding(
            "sample-app", "read-status", "uia",
            {"name": "Ready", "control_type": "text"}, root=self.root,
        )
        self.assertEqual("detected", saved["verification"])
        self.assertEqual(saved, registry.get_binding("sample-app", "read-status", root=self.root))
        self.assertEqual([saved], registry.list_bindings(root=self.root))
        payload = json.loads(next(self.root.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual("uia", payload["route"])

    def test_tested_record_requires_evidence_and_selector_is_route_scoped(self):
        with self.assertRaisesRegex(ValueError, "evidence"):
            registry.record_binding("sample", "status", "uia", {"name": "Ready"},
                                    verification="tested_readback", root=self.root)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            registry.record_binding("sample", "status", "msaa", {"automation_id": "x"}, root=self.root)
        self.assertEqual([], registry.list_bindings(root=self.root))

    def test_tested_readback_is_distinguished(self):
        item = registry.record_binding("sample", "read", "msaa", {"name": "Ready", "role": "statictext"},
                                       verification="tested_readback", evidence="exact text read back",
                                       root=self.root)
        self.assertEqual("tested_readback", item["verification"])

    def test_wechat_apps_cannot_be_recorded(self):
        for app in ("WeChat", "Weixin", "wechatapp"):
            with self.assertRaises(PermissionError):
                registry.record_binding(app, "send", "uia", {"name": "Send"}, root=self.root)


if __name__ == "__main__":
    unittest.main()
