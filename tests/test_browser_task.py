import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skill" / "auto-computer" / "scripts"))
import browser_task


class BrowserTaskTests(unittest.TestCase):
    def test_run_code_is_fixed_and_payload_is_json_encoded(self):
        code = browser_task._task_code("https://shop.example/a", [{"op": "fill", "selector": "input[name=q]", "value": "x'; throw 1; //"}], False)
        self.assertIn("async page =>", code)
        self.assertIn("new URL(targetPage.url()).href === exactUrl", code)
        self.assertIn("targetPage.locator(action.selector)", code)
        self.assertIn("timeout: 3000", code)
        self.assertIn("type === 'password'", code)
        self.assertIn("if (!samePage()) return fail('page_url_changed'); steps.push({ op: action.op, value:", code)
        self.assertIn("x'; throw 1; //", code)
        self.assertNotIn("eval(", code)

    def test_run_code_result_accepts_cli_raw_json_fixture(self):
        self.assertEqual({"ok": True, "verified_url": "https://shop.example/"}, browser_task._run_code_result('{"ok":true,"verified_url":"https://shop.example/"}'))

    def test_task_attaches_once_runs_on_exact_current_tab_and_detaches(self):
        output = json.dumps({"content": [{"type": "text", "text": "### Open tabs\n\n- 0: (current) [Shop](https://shop.example/orders)"}]})
        with (
            mock.patch.object(browser_task, "_cli_version", return_value=(0, 1, 25)),
            mock.patch.object(browser_task.sessions, "_status", return_value={"can_reconnect_in_background": True, "supports_native_background_flag": True, "blockers": []}) as status,
            mock.patch.object(browser_task.sessions, "_reconnect", return_value={"ok": True, "session_verified": True}) as attach,
            mock.patch.object(browser_task.sessions, "_detach", return_value={"ok": True}) as detach,
            mock.patch.object(browser_task, "_command", return_value='{"ok":true,"available":true,"verified_url":"https://shop.example/orders","final_url":"https://shop.example/orders","target_tab_verified":true,"active_tab_verified":true,"steps":[{"op":"click","selector":"button#refresh","ok":true}]}') as command,
        ):
            result = browser_task.run_task({
                "browser": "edge", "profile": "Profile 2", "url": "https://shop.example/orders",
                "actions": [{"op": "click", "selector": "button#refresh"}], "read_summary": False,
            })
        status.assert_called_once()
        attach.assert_called_once()
        self.assertTrue(attach.call_args.kwargs["strict_foreground"])
        detach.assert_called_once()
        self.assertEqual(1, command.call_count)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["profile_verified"])
        self.assertTrue(result["active_tab_verified"])
        self.assertTrue(result["detached"])
        self.assertNotIn("summary", result)
        self.assertEqual({"op": "click", "selector": "button#refresh", "ok": True}, result["steps"][0])

    def test_task_refuses_inactive_or_non_exact_url_without_actions(self):
        with (
            mock.patch.object(browser_task, "_cli_version", return_value=(0, 1, 25)),
            mock.patch.object(browser_task.sessions, "_status", return_value={"can_reconnect_in_background": True, "supports_native_background_flag": True, "blockers": []}),
            mock.patch.object(browser_task.sessions, "_reconnect", return_value={"ok": True, "session_verified": True}),
            mock.patch.object(browser_task.sessions, "_detach", return_value={"ok": True}),
            mock.patch.object(browser_task, "_command", return_value='{"ok":false,"available":false,"reason":"active_tab_url_mismatch","verified_url":"about:blank","steps":[]}') as command,
        ):
            result = browser_task.run_task({"url": "https://shop.example/orders", "actions": [{"op": "click", "selector": "#submit"}]})
        command.assert_called_once()
        self.assertFalse(result["ok"])
        self.assertEqual("active_tab_url_mismatch", result["reason"])
        self.assertFalse(result["active_tab_verified"])

    def test_unavailable_profile_never_attaches(self):
        with (
            mock.patch.object(browser_task, "_cli_version", return_value=(0, 1, 25)),
            mock.patch.object(browser_task.sessions, "_status", return_value={"can_reconnect_in_background": False, "supports_native_background_flag": True, "blockers": ["profile_mismatch_or_unverified"]}) as status,
            mock.patch.object(browser_task.sessions, "_reconnect") as attach,
        ):
            result = browser_task.run_task({"url": "https://shop.example/"})
        status.assert_called_once()
        attach.assert_not_called()
        self.assertFalse(result["available"])
        self.assertEqual(["profile_mismatch_or_unverified"], result["blockers"])

    def test_cli_version_check_precedes_browser_status(self):
        with (
            mock.patch.object(browser_task, "_cli_version", return_value=(0, 1, 24)),
            mock.patch.object(browser_task.sessions, "_status") as status,
        ):
            result = browser_task.run_task({"url": "https://shop.example/"})
        status.assert_not_called()
        self.assertEqual(["unsupported_or_unverified_playwright_cli_version"], result["blockers"])

    def test_interrupted_batch_requires_readback_and_releases_managed_session(self):
        with (
            mock.patch.object(browser_task, "_cli_version", return_value=(0, 1, 25)),
            mock.patch.object(browser_task.sessions, "_status", return_value={"session_reusable": True}),
            mock.patch.object(browser_task.sessions, "_detach", return_value={"ok": True}) as detach,
            mock.patch.object(browser_task, "_command", side_effect=subprocess.TimeoutExpired("run-code", 40)) as command,
        ):
            result = browser_task.run_task({
                "url": "https://shop.example", "actions": [{"op": "click", "selector": "#submit"}],
            })
        self.assertEqual("task_result_unknown", result["reason"])
        self.assertTrue(result["needs_readback"])
        self.assertTrue(result["detached"])
        self.assertEqual(1, command.call_count)
        detach.assert_called_once()

    def test_unverified_browser_version_blocks_a_new_attach(self):
        with (
            mock.patch.object(browser_task, "_cli_version", return_value=(0, 1, 25)),
            mock.patch.object(browser_task.sessions, "_status", return_value={"session_reusable": False, "supports_native_background_flag": False, "can_reconnect_in_background": True, "blockers": []}),
            mock.patch.object(browser_task.sessions, "_reconnect") as attach,
        ):
            result = browser_task.run_task({"url": "https://shop.example/"})
        attach.assert_not_called()
        self.assertEqual(["unsupported_browser_version_for_background_attach"], result["blockers"])

    def test_custom_exact_profile_session_is_reused_without_detach(self):
        status = {
            "session_reusable": False, "supports_native_background_flag": False, "can_reconnect_in_background": False,
            "open_browser_sessions": [{"name": "authorized-session", "profile": "Profile 2", "attached": True, "compatible": True}],
        }
        with (
            mock.patch.object(browser_task, "_cli_version", return_value=(0, 1, 25)),
            mock.patch.object(browser_task.sessions, "_status", return_value=status),
            mock.patch.object(browser_task.sessions, "_reconnect") as attach,
            mock.patch.object(browser_task.sessions, "_detach") as detach,
            mock.patch.object(browser_task, "_command", return_value='{"ok":true,"available":true,"verified_url":"https://shop.example/","final_url":"https://shop.example/","target_tab_verified":true,"active_tab_verified":true,"steps":[]}') as command,
        ):
            result = browser_task.run_task({"profile": "Profile 2", "url": "https://shop.example/", "read_summary": False})
        attach.assert_not_called()
        detach.assert_not_called()
        self.assertEqual("authorized-session", command.call_args[0][0])
        self.assertTrue(result["session_reused"])

    def test_wait_for_and_read_list_and_slash_code_generation(self):
        validated = browser_task._validate({
            "url": "https://shop.example/items/",
            "actions": [
                {"op": "wait_for", "selector": ".sku-row", "timeout_ms": 2000, "min_count": 2},
                {"op": "read_list", "selector": ".sku-row", "limit": 5, "text_limit": 120},
            ],
        })
        self.assertFalse(validated["read_summary"])
        self.assertEqual(2000, validated["actions"][0]["timeout_ms"])
        self.assertEqual(5, validated["actions"][1]["limit"])
        code = browser_task._task_code(validated["url"], validated["actions"], validated["read_summary"])
        self.assertIn("normUrl", code)
        self.assertIn("open_urls: openUrls", code)
        self.assertIn("only_blank_tab_authorize_target_in_extension", code)
        self.assertIn("action.op === 'read_list'", code)


if __name__ == "__main__":
    unittest.main()
