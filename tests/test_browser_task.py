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
        self.assertIn("if (!samePage()) return fail('page_url_changed'); steps.push({ op: action.op, text:", code)
        self.assertIn("steps.push({ op: action.op, count: cnt })", code)
        self.assertIn("steps.push({ op: action.op, attribute: action.attribute, value })", code)
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

    def test_read_modes_validation_and_runtime_parity(self):
        validated = browser_task._validate({
            "url": "https://shop.example/orders",
            "actions": [
                {"op": "read", "selector": "#title"},
                {"op": "read", "selector": ".row", "as": "count"},
                {"op": "read", "selector": "a#detail", "as": "attribute", "attribute": "href"},
            ],
        })
        self.assertEqual("text", validated["actions"][0]["as"])
        self.assertEqual("count", validated["actions"][1]["as"])
        self.assertEqual("attribute", validated["actions"][2]["as"])
        self.assertEqual("href", validated["actions"][2]["attribute"])

        for bad in (
            {"op": "read", "selector": "#title", "as": "html"},
            {"op": "read", "selector": "#title", "as": "attribute"},
            {"op": "read", "selector": "#title", "as": "attribute", "attribute": "   "},
            {"op": "read", "selector": "#title", "as": "attribute", "attribute": "a\nb"},
            {"op": "read", "selector": "#title", "as": "attribute", "attribute": "x" * 129},
        ):
            with self.assertRaises(ValueError, msg=str(bad)):
                browser_task._validate({"url": "https://shop.example/orders", "actions": [bad]})

        actions = [
            {"op": "read", "selector": "#title"},
            {"op": "read", "selector": ".row", "as": "count"},
            {"op": "read", "selector": ".none", "as": "count"},
            {"op": "read", "selector": "a#detail", "as": "attribute", "attribute": "href"},
            {"op": "read", "selector": "a#detail", "as": "attribute", "attribute": "data-missing"},
            {"op": "read", "selector": "input#pwd", "as": "attribute", "attribute": "value"},
            {"op": "read", "selector": "input#qty"},
        ]
        code = browser_task._task_code("https://shop.example/orders", browser_task._validate({"url": "https://shop.example/orders", "actions": actions})["actions"], False)
        harness = """
class MockInput {
  constructor(attrs, value) { this.attrs = attrs; this.value = value; this.hidden = false; }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
}
globalThis.HTMLInputElement = MockInput;
globalThis.HTMLTextAreaElement = class MockTextarea {};
const elements = {
  '#title': [{ hidden: false, innerText: '  Order #42  ', getAttribute() { return null; } }],
  '.row': [{}, {}, {}],
  '.none': [],
  'a#detail': [{ hidden: false, innerText: 'View', getAttribute(k) { return k === 'href' ? 'https://shop.example/42' : null; } }],
  'input#pwd': [new MockInput({ type: 'password', value: 'secret123' }, 'secret123')],
  'input#qty': [new MockInput({ type: 'text' }, '5')],
};
const fn = eval('(' + process.argv[1] + ')');
const targetPage = {
  url: () => 'https://shop.example/orders',
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
        proc = subprocess.run(
            ["node", "-e", harness, code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=True,
        )
        res = json.loads(proc.stdout)
        self.assertTrue(res["ok"], res)
        self.assertEqual([
            {"op": "read", "text": "Order #42"},
            {"op": "read", "count": 3},
            {"op": "read", "count": 0},
            {"op": "read", "attribute": "href", "value": "https://shop.example/42"},
            {"op": "read", "attribute": "data-missing", "value": None},
            {"op": "read", "attribute": "value", "value": "[redacted]"},
            {"op": "read", "text": "5"},
        ], res["steps"])

    def test_close_blank_on_miss_closes_blank_tabs_only_on_new_managed_attach(self):
        code_new = browser_task._task_code("https://shop.example/orders", [], False, close_blank_on_miss=True)
        code_reused = browser_task._task_code("https://shop.example/orders", [], False, close_blank_on_miss=False)
        harness = """
let closedUrls = [];
const makePage = (url) => {
  let closed = false;
  return {
    url: () => url,
    isClosed: () => closed,
    close: async () => { closed = true; closedUrls.push(url); },
  };
};
const blankPage = makePage('about:blank');
blankPage.context = () => ({ pages: () => [blankPage] });
const fn = eval('(' + process.argv[1] + ')');
fn(blankPage).then(res => { process.stdout.write(JSON.stringify({ res, closedUrls })); });
"""
        for code, expected_closed in ((code_new, 1), (code_reused, 0)):
            proc = subprocess.run(
                ["node", "-e", harness, code],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=True,
            )
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["res"]["ok"])
            self.assertEqual("target_tab_unavailable", payload["res"]["reason"])
            self.assertEqual(expected_closed, len(payload["closedUrls"]))
            if expected_closed:
                self.assertEqual(expected_closed, payload["res"].get("blank_tabs_closed"))
            else:
                self.assertNotIn("blank_tabs_closed", payload["res"])

        with (
            mock.patch.object(browser_task, "_cli_version", return_value=(0, 1, 25)),
            mock.patch.object(browser_task.sessions, "_status", return_value={"can_reconnect_in_background": True, "supports_native_background_flag": True, "blockers": []}),
            mock.patch.object(browser_task.sessions, "_reconnect", return_value={"ok": True, "session_verified": True}),
            mock.patch.object(browser_task.sessions, "_detach", return_value={"ok": True, "closed_blank_tabs": 0}) as detach,
            mock.patch.object(browser_task, "_command", return_value='{"ok":false,"available":false,"reason":"target_tab_unavailable","open_urls":["about://"],"hint":"only_blank_tab_authorize_target_in_extension","blank_tabs_closed":1,"steps":[]}'),
        ):
            res = browser_task.run_task({"browser": "edge", "profile": "Default", "url": "https://shop.example/orders", "read_summary": False})
        self.assertEqual(1, res["blank_tabs_closed"])
        self.assertTrue(res["detached"])
        detach.assert_called_once_with("edge", "Default", browser_task.sessions._default_session("edge", "Default"), close_blank_tabs=False)


if __name__ == "__main__":
    unittest.main()
