#!/usr/bin/env node
"use strict";

// Fast stdio MCP bridge for Edge and Chrome.
// Supports both dedicated zero-prompt persistent automation profiles ("auto")
// and user's running browser remote-debugging port ("daily").
// Uses Node's built-in WebSocket and persistent multiplexed CDP connections.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");

const MAX_TABS = 100;
const MAX_CANDIDATES = 40;
const MAX_ACTIONS = 30;
const MAX_TEXT = 8000;
const MAX_WAIT_MS = 5000;
const MAX_WAIT_FOR_MS = 10000;
const MAX_FILES = 20;
const CDP_TIMEOUT_MS = 10000;
const DEFAULT_HOME = process.env.AUTO_COMPUTER_HOME || path.join(process.env.LOCALAPPDATA || os.homedir(), "AutoComputer");
const DEFAULT_PROFILE_ROOT = process.env.AUTO_COMPUTER_PROFILE_ROOT || path.join(DEFAULT_HOME, "profiles");

const TOOLS = [
  {
    name: "open_and_run",
    description:
      "Fast one-call browser task runner: opens an http/https URL in an isolated persistent auto profile (zero Allow popup, zero automation banner) or daily browser, waits only for DOM or wait_selector (never networkidle), optionally executes up to 30 safe CSS/CDP actions (including read_list), and returns page summary + step results.",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string", description: "HTTP or HTTPS URL to open" },
        browser: { type: "string", enum: ["edge", "chrome"], default: "edge" },
        profile: { type: "string", enum: ["auto", "daily"], default: "auto" },
        visible: { type: "boolean", default: false, description: "Show clean browser window without automation banner (e.g. for first-time login or visual review)" },
        wait_selector: { type: "string", description: "Optional CSS selector to wait for before running actions/summary" },
        wait_timeout_ms: { type: "integer", minimum: 500, maximum: 30000, default: 12000 },
        actions: { type: "array", minItems: 0, maxItems: MAX_ACTIONS, items: { type: "object" } },
        read_summary: { type: "boolean", default: true },
        close_after: { type: "boolean", description: "Defaults to true when visible=false, false when visible=true" },
      },
      required: ["url"],
      additionalProperties: false,
    },
  },
  {
    name: "list_tabs",
    description: "List open page targets for the selected profile ('auto' or 'daily') and browser ('edge' or 'chrome').",
    inputSchema: {
      type: "object",
      properties: {
        browser: { type: "string", enum: ["edge", "chrome"], default: "edge" },
        profile: { type: "string", enum: ["auto", "daily"], default: "auto" },
        search: { type: "string", description: "Case-insensitive text matched against URL and title" },
        url_contains: { type: "string" },
        title_contains: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: MAX_TABS, default: 50 },
      },
      additionalProperties: false,
    },
  },
  {
    name: "inspect_tab",
    description: "Inspect one explicitly selected page. Returns bounded title, visible body text, and CSS locators for visible semantic controls without activating the tab.",
    inputSchema: {
      type: "object",
      properties: {
        target_id: { type: "string", description: "Explicit id returned by list_tabs or open_tab" },
        browser: { type: "string", enum: ["edge", "chrome"], default: "edge" },
        profile: { type: "string", enum: ["auto", "daily"], default: "auto" },
        text_limit: { type: "integer", minimum: 0, maximum: MAX_TEXT, default: 4000 },
      },
      required: ["target_id"],
      additionalProperties: false,
    },
  },
  {
    name: "open_tab",
    description: "Open a new http/https tab in the selected profile/browser (when visible=false, opens in background or headless auto instance without stealing focus).",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string", description: "HTTP or HTTPS URL to open" },
        browser: { type: "string", enum: ["edge", "chrome"], default: "edge" },
        profile: { type: "string", enum: ["auto", "daily"], default: "auto" },
        visible: { type: "boolean", default: false },
        background: { type: "boolean", description: "Override Target.createTarget background flag" },
      },
      required: ["url"],
      additionalProperties: false,
    },
  },
  {
    name: "close_tab",
    description: "Close an explicitly selected page tab in the selected profile/browser.",
    inputSchema: {
      type: "object",
      properties: {
        target_id: { type: "string", description: "Explicit page id returned by list_tabs or open_tab" },
        browser: { type: "string", enum: ["edge", "chrome"], default: "edge" },
        profile: { type: "string", enum: ["auto", "daily"], default: "auto" },
      },
      required: ["target_id"],
      additionalProperties: false,
    },
  },
  {
    name: "run_actions",
    description: "Run up to 30 sequential CSS/CDP actions on one explicitly selected page: read, assert, fill, select, check, uncheck, click, press_enter, wait, wait_for, set_files, read_hover_menu, read_list. Arbitrary JavaScript is rejected and password values are hidden.",
    inputSchema: {
      type: "object",
      properties: {
        target_id: { type: "string" },
        browser: { type: "string", enum: ["edge", "chrome"], default: "edge" },
        profile: { type: "string", enum: ["auto", "daily"], default: "auto" },
        actions: { type: "array", minItems: 1, maxItems: MAX_ACTIONS, items: { type: "object" } },
      },
      required: ["target_id", "actions"],
      additionalProperties: false,
    },
  },
  {
    name: "stop_auto_browser",
    description: "Close managed auto-profile browser processes started by this MCP bridge.",
    inputSchema: {
      type: "object",
      properties: {
        browser: { type: "string", enum: ["edge", "chrome", "all"], default: "all" },
      },
      additionalProperties: false,
    },
  },
];

class BridgeError extends Error {}

function validString(value, name, max = 2048, allowEmpty = false) {
  if (typeof value !== "string" || value.length > max || (!allowEmpty && !value.trim()) || /[\0\r\n]/.test(value)) {
    throw new BridgeError(`${name} must be a${allowEmpty ? "" : " non-empty"} string of at most ${max} characters`);
  }
  return value;
}

function validateHttpUrl(value) {
  const raw = validString(value, "url", 2048);
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new BridgeError("url must be a valid URL");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new BridgeError(`Only http/https URLs are allowed (got ${parsed.protocol})`);
  }
  return parsed.toString();
}

function intArg(value, name, fallback, min, max) {
  if (value === undefined) return fallback;
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new BridgeError(`${name} must be an integer from ${min} to ${max}`);
  }
  return value;
}

function boolArg(value, name, fallback) {
  if (value === undefined) return fallback;
  if (typeof value !== "boolean") throw new BridgeError(`${name} must be a boolean`);
  return value;
}

function resolveScope(args) {
  const profile = args.profile === undefined ? "auto" : args.profile;
  const browser = args.browser === undefined ? "edge" : args.browser;
  if (profile !== "auto" && profile !== "daily") throw new BridgeError("profile must be 'auto' or 'daily'");
  if (browser !== "edge" && browser !== "chrome") throw new BridgeError("browser must be 'edge' or 'chrome'");
  return { profile, browser };
}

function findBrowserExe(browser) {
  const candidates = browser === "chrome"
    ? [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        path.join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
      ]
    : [
        "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        path.join(process.env.LOCALAPPDATA || "", "Microsoft", "Edge", "Application", "msedge.exe"),
      ];
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  throw new BridgeError(`Browser executable not found for ${browser}`);
}

function dailyPortPath(browser) {
  const local = process.env.LOCALAPPDATA;
  if (!local) throw new BridgeError("LOCALAPPDATA is unavailable; cannot locate browser DevToolsActivePort");
  return browser === "chrome"
    ? path.join(local, "Google", "Chrome", "User Data", "DevToolsActivePort")
    : path.join(local, "Microsoft", "Edge", "User Data", "DevToolsActivePort");
}

function autoProfileDir(browser) {
  return path.join(DEFAULT_PROFILE_ROOT, browser === "chrome" ? "chrome-auto" : "edge-auto");
}

function parseEndpointFile(file, label) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch (error) {
    if (error && error.code === "ENOENT") {
      throw new BridgeError(`${label} DevToolsActivePort was not found at ${file}`);
    }
    throw new BridgeError(`Cannot read ${label} DevToolsActivePort: ${error.message}`);
  }
  const [portLine, pathLine] = raw.trim().split(/\r?\n/);
  const port = Number(portLine);
  if (!Number.isInteger(port) || port < 1 || port > 65535 || !/^\/devtools\/browser\/[A-Za-z0-9._-]+$/.test(pathLine || "")) {
    throw new BridgeError(`${label} DevToolsActivePort contains an invalid endpoint`);
  }
  return `ws://127.0.0.1:${port}${pathLine}`;
}

class CdpConnection {
  constructor(ws, label = "Browser") {
    this.ws = ws;
    this.label = label;
    this.nextId = 1;
    this.pending = new Map();
    ws.addEventListener("message", (event) => {
      let message;
      try { message = JSON.parse(String(event.data)); } catch { return; }
      if (!message || !Number.isInteger(message.id)) return;
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new BridgeError(`${this.label} DevTools ${pending.method} failed: ${message.error.message || JSON.stringify(message.error)}`));
      else pending.resolve(message.result || {});
    });
    const onFailure = (reason) => {
      const message = reason instanceof Error ? reason.message : "connection closed";
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new BridgeError(`${this.label} DevTools connection failed: ${message}`));
      }
      this.pending.clear();
    };
    ws.addEventListener("error", () => onFailure(new Error("WebSocket error")));
    ws.addEventListener("close", () => onFailure(new Error("WebSocket closed")));
  }

  command(method, params = {}, sessionId, timeoutMs = CDP_TIMEOUT_MS) {
    if (this.ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new BridgeError(`${this.label} DevTools connection is not open`));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new BridgeError(`${this.label} DevTools ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      try {
        this.ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new BridgeError(`Could not send ${this.label} DevTools ${method}: ${error.message}`));
      }
    });
  }
}

async function openWs(endpoint, label, timeoutMs = 10000) {
  let ws;
  try {
    ws = new WebSocket(endpoint);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new BridgeError(`Timed out connecting to ${label} DevTools (${timeoutMs}ms)`)), timeoutMs);
      ws.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
      ws.addEventListener("error", () => { clearTimeout(timer); reject(new BridgeError(`Could not connect to ${label} DevTools`)); }, { once: true });
    });
    return new CdpConnection(ws, label);
  } catch (error) {
    if (ws && ws.readyState < WebSocket.CLOSING) ws.close();
    if (error instanceof BridgeError) throw error;
    throw new BridgeError(`Could not connect to ${label} DevTools: ${error.message}`);
  }
}

const pool = new Map();
const pendingPool = new Map();

async function closePoolEntry(key, forceVisible = false) {
  const entry = pool.get(key);
  if (!entry) return false;
  if (entry.visible && !forceVisible) return false;
  pool.delete(key);
  try {
    if (entry.cdp && entry.cdp.ws.readyState === WebSocket.OPEN) {
      await Promise.race([
        entry.cdp.command("Browser.close", {}, undefined, 1500).catch(() => {}),
        new Promise((r) => setTimeout(r, 800)),
      ]);
      entry.cdp.ws.close();
    }
  } catch {}
  if (entry.proc && !entry.proc.killed) {
    try { entry.proc.kill(); } catch {}
  }
  await new Promise((r) => setTimeout(r, 150));
  return true;
}

async function spawnAutoBrowser(browser, visible) {
  const exe = findBrowserExe(browser);
  const profileDir = autoProfileDir(browser);
  fs.mkdirSync(profileDir, { recursive: true });
  const portFile = path.join(profileDir, "DevToolsActivePort");
  try { fs.rmSync(portFile, { force: true }); } catch {}

  const launchArgs = [
    ...(visible ? [] : ["--headless=new"]),
    `--user-data-dir=${profileDir}`,
    "--remote-debugging-port=0",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "about:blank",
  ];
  const proc = spawn(exe, launchArgs, { stdio: "ignore", detached: Boolean(visible) });
  if (visible) proc.unref();

  let exitedEarly = false;
  let exitCode = null;
  proc.once("exit", (code) => {
    exitedEarly = true;
    exitCode = code;
    const key = `auto:${browser}`;
    const cur = pool.get(key);
    if (cur && cur.proc === proc) pool.delete(key);
  });

  const deadline = Date.now() + 8000;
  let endpoint = null;
  while (Date.now() < deadline) {
    if (exitedEarly) throw new BridgeError(`${browser} auto profile exited during startup (code ${exitCode})`);
    if (fs.existsSync(portFile)) {
      try {
        endpoint = parseEndpointFile(portFile, `${browser}-auto`);
        break;
      } catch {}
    }
    await new Promise((r) => setTimeout(r, 35));
  }
  if (!endpoint) {
    try { proc.kill(); } catch {}
    throw new BridgeError(`Timed out waiting for ${browser} auto DevToolsActivePort`);
  }

  const cdp = await openWs(endpoint, `${browser}-auto`, 6000);
  return { cdp, proc, profileDir, browser, visible: Boolean(visible), spawned: true };
}

async function getCdp(profile = "auto", browser = "edge", visible = false) {
  const key = `${profile}:${browser}`;
  const existing = pool.get(key);
  if (existing && existing.cdp && existing.cdp.ws.readyState === WebSocket.OPEN) {
    if (profile === "auto" && visible && existing.spawned && !existing.visible) {
      await closePoolEntry(key, true);
    } else {
      return existing.cdp;
    }
  }
  if (pendingPool.has(key)) return pendingPool.get(key);

  const connectPromise = (async () => {
    if (profile === "daily") {
      const endpoint = parseEndpointFile(dailyPortPath(browser), `${browser}-daily`);
      const cdp = await openWs(endpoint, `${browser}-daily`, 15000);
      pool.set(key, { cdp, proc: null, browser, visible: true, spawned: false });
      return cdp;
    }

    // auto profile: try reusing an already-running instance on the dedicated profile first
    const portFile = path.join(autoProfileDir(browser), "DevToolsActivePort");
    if (fs.existsSync(portFile)) {
      try {
        const endpoint = parseEndpointFile(portFile, `${browser}-auto`);
        const cdp = await openWs(endpoint, `${browser}-auto`, 1500);
        pool.set(key, { cdp, proc: null, browser, visible: true, spawned: false });
        return cdp;
      } catch {
        // stale DevToolsActivePort; fall through and launch fresh
      }
    }

    const entry = await spawnAutoBrowser(browser, visible);
    pool.set(key, entry);
    return entry.cdp;
  })();

  pendingPool.set(key, connectPromise);
  try {
    return await connectPromise;
  } finally {
    pendingPool.delete(key);
  }
}

async function attach(c, targetId) {
  const target = validString(targetId, "target_id", 256);
  const targets = await c.command("Target.getTargets");
  const info = (targets.targetInfos || []).find((item) => item.targetId === target && item.type === "page");
  if (!info) throw new BridgeError("target_id is not an open page; refresh list_tabs and select a page target");
  const result = await c.command("Target.attachToTarget", { targetId: target, flatten: true });
  if (!result.sessionId) throw new BridgeError("Browser did not return a session for the selected page");
  return { sessionId: result.sessionId, info };
}

function evaluate(c, sessionId, script, args, timeout = CDP_TIMEOUT_MS) {
  // script is one of this file's fixed expressions; caller arguments are JSON data only.
  const expression = `(${script})(${JSON.stringify(args)})`;
  return c.command("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: false,
    timeout,
  }, sessionId, Math.max(CDP_TIMEOUT_MS, timeout + 2000)).then((result) => {
    if (result.exceptionDetails) {
      const detail = result.exceptionDetails.exception && result.exceptionDetails.exception.description || result.exceptionDetails.text || "page evaluation failed";
      throw new BridgeError(`Selected page evaluation failed: ${detail.slice(0, 500)}`);
    }
    return result.result && result.result.value;
  });
}

const INSPECT_SCRIPT = function (args) {
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const esc = (value) => String(value).replace(/\\/g, "\\\\").replace(/"/g, "\\\"").replace(/\n/g, "\\a ").replace(/\r/g, "\\d ");
  const locator = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement && parts.length < 6) {
      let part = node.localName;
      if (node.getAttribute("data-testid")) part += `[data-testid="${esc(node.getAttribute("data-testid"))}"]`;
      else if (node.getAttribute("name")) part += `[name="${esc(node.getAttribute("name"))}"]`;
      else if (node.getAttribute("aria-label")) part += `[aria-label="${esc(node.getAttribute("aria-label"))}"]`;
      else {
        const siblings = node.parentElement ? Array.from(node.parentElement.children).filter((x) => x.localName === node.localName) : [];
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };
  const candidates = Array.from(document.querySelectorAll("button,a[href],input,textarea,select,[role=button],[role=link],[role=checkbox],[role=radio]"))
    .filter(visible).slice(0, 40).map((el) => ({
      tag: el.localName,
      role: el.getAttribute("role") || null,
      type: el.getAttribute("type") || null,
      label: (el.getAttribute("aria-label") || el.labels && Array.from(el.labels).map((x) => x.innerText).join(" ") || el.innerText || el.getAttribute("placeholder") || "").trim().slice(0, 160),
      locator: locator(el),
      enabled: !el.disabled && el.getAttribute("aria-disabled") !== "true",
    }));
  return {
    ok: true,
    url: location.href.slice(0, 2048),
    title: document.title.slice(0, 300),
    text: (document.body && document.body.innerText || "").slice(0, args.text_limit),
    candidates,
  };
};

const WAIT_READY_SCRIPT = function (args) {
  const ready = document.readyState !== "loading" && location.href !== "about:blank";
  if (!ready) return { ok: false, url: location.href, readyState: document.readyState };
  if (args.wait_selector) {
    let nodes = [];
    try { nodes = document.querySelectorAll(args.wait_selector); } catch (err) { return { ok: false, error: err.message }; }
    if (!nodes.length) return { ok: false, url: location.href, readyState: document.readyState };
  }
  return { ok: true, url: location.href.slice(0, 2048), title: document.title.slice(0, 300) };
};

const SUMMARY_SCRIPT = function (args) {
  return {
    url: location.href.slice(0, 2048),
    title: document.title.slice(0, 300),
    readyState: document.readyState,
    text: (document.body && document.body.innerText || "").slice(0, args.text_limit || 1500),
  };
};

function validateStringArray(raw, name, maxItems = 20) {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > maxItems) {
    throw new BridgeError(`${name} must be an array of 1 to ${maxItems} non-empty strings`);
  }
  const items = raw.map((v, idx) => validString(v, `${name}[${idx}]`, 1024));
  if (new Set(items).size !== items.length) {
    throw new BridgeError(`${name} must contain unique strings`);
  }
  return items;
}

function validateActions(raw, allowEmpty = false) {
  if (!Array.isArray(raw)) throw new BridgeError("actions must be an array");
  if (!allowEmpty && raw.length < 1) throw new BridgeError(`actions must contain 1 to ${MAX_ACTIONS} actions`);
  if (raw.length > MAX_ACTIONS) throw new BridgeError(`actions must contain at most ${MAX_ACTIONS} actions`);
  const allowed = new Set([
    "read", "assert", "fill", "click", "press_enter", "wait",
    "select", "check", "uncheck", "wait_for", "set_files", "read_hover_menu", "read_list",
  ]);
  const accepted = new Set([
    "op", "selector", "value", "as", "attribute", "check", "equals",
    "ms", "timeout_ms", "min_count", "files", "hover_selectors", "rule_selectors", "item_selector",
    "limit", "text_limit",
  ]);
  return raw.map((a, i) => {
    if (!a || typeof a !== "object" || Array.isArray(a)) throw new BridgeError(`actions[${i}] must be an object`);
    const unknown = Object.keys(a).filter((key) => !accepted.has(key));
    if (unknown.length) throw new BridgeError(`actions[${i}] has unsupported fields: ${unknown.join(", ")}`);
    const op = a.op;
    if (!allowed.has(op)) throw new BridgeError(`actions[${i}].op is unsupported: ${op}`);
    const action = { op };
    if (op === "wait") {
      action.ms = intArg(a.ms, `actions[${i}].ms`, 100, 0, MAX_WAIT_MS);
      return action;
    }
    if (op === "read_hover_menu") {
      action.hover_selectors = validateStringArray(a.hover_selectors, `actions[${i}].hover_selectors`, 10);
      action.rule_selectors = validateStringArray(a.rule_selectors, `actions[${i}].rule_selectors`, 20);
      action.item_selector = validString(a.item_selector, `actions[${i}].item_selector`, 1024);
      return action;
    }
    if (op === "read_list") {
      action.item_selector = validString(a.item_selector, `actions[${i}].item_selector`, 1024);
      action.limit = intArg(a.limit, `actions[${i}].limit`, 10, 1, 30);
      action.text_limit = intArg(a.text_limit, `actions[${i}].text_limit`, 240, 50, 600);
      return action;
    }
    action.selector = validString(a.selector, `actions[${i}].selector`, 1024);
    if (op === "fill") action.value = validString(a.value, `actions[${i}].value`, 4000, true);
    if (op === "select") action.value = validString(a.value, `actions[${i}].value`, 1000, true);
    if (op === "wait_for") {
      action.timeout_ms = intArg(a.timeout_ms, `actions[${i}].timeout_ms`, 3000, 100, MAX_WAIT_FOR_MS);
      action.min_count = intArg(a.min_count, `actions[${i}].min_count`, 1, 1, 1000);
    }
    if (op === "set_files") {
      const files = validateStringArray(a.files, `actions[${i}].files`, MAX_FILES);
      action.files = files.map((filePath, fIdx) => {
        const resolved = path.resolve(filePath);
        let st;
        try { st = fs.statSync(resolved); }
        catch { throw new BridgeError(`actions[${i}].files[${fIdx}] does not exist: ${resolved}`); }
        if (!st.isFile()) throw new BridgeError(`actions[${i}].files[${fIdx}] is not a regular file: ${resolved}`);
        return resolved;
      });
    }
    if (op === "read") {
      action.as = a.as || "text";
      if (!["text", "count", "attribute"].includes(action.as)) throw new BridgeError(`actions[${i}].as must be text, count, or attribute`);
      if (action.as === "attribute") action.attribute = validString(a.attribute, `actions[${i}].attribute`, 128);
    }
    if (op === "assert") {
      action.check = a.check || "text_contains";
      if (!["text_contains", "count", "enabled", "attribute"].includes(action.check)) throw new BridgeError(`actions[${i}].check is unsupported`);
      if (action.check === "text_contains") action.value = validString(a.value, `actions[${i}].value`, 1000, true);
      if (action.check === "count") action.equals = intArg(a.equals, `actions[${i}].equals`, undefined, 0, 100000);
      if (action.check === "enabled") {
        action.equals = a.equals === undefined ? true : a.equals;
        if (typeof action.equals !== "boolean") throw new BridgeError(`actions[${i}].equals must be a boolean for enabled checks`);
      }
      if (action.check === "attribute") {
        action.attribute = validString(a.attribute, `actions[${i}].attribute`, 128);
        action.equals = validString(a.equals, `actions[${i}].equals`, 1000, true);
      }
    }
    return action;
  });
}

const ACTIONS_SCRIPT = async function (actions) {
  const results = [];
  const query = (selector, i) => {
    let nodes;
    try { nodes = Array.from(document.querySelectorAll(selector)); }
    catch (error) { throw new Error(`actions[${i}] invalid CSS selector: ${error.message}`); }
    if (nodes.length > 500) throw new Error(`actions[${i}] matched more than 500 elements`);
    return nodes;
  };
  const first = (selector, i) => {
    const nodes = query(selector, i);
    if (!nodes.length) throw new Error(`actions[${i}] selector matched no elements`);
    if (nodes.length > 1) throw new Error(`actions[${i}] selector matched ${nodes.length} elements; use a more specific CSS selector`);
    return nodes[0];
  };
  const safeText = (el) => (el.innerText || el.textContent || "").trim().slice(0, 2000);
  for (let i = 0; i < actions.length; i++) {
    const a = actions[i];
    if (a.op === "wait") {
      const until = Date.now() + a.ms;
      while (Date.now() < until) await new Promise((resolve) => setTimeout(resolve, Math.min(50, until - Date.now())));
      results.push({ op: a.op, ms: a.ms });
      continue;
    }
    if (a.op === "wait_for") {
      const deadline = Date.now() + a.timeout_ms;
      let matched = 0;
      while (true) {
        matched = query(a.selector, i).length;
        if (matched >= a.min_count) break;
        if (Date.now() >= deadline) throw new Error(`actions[${i}] wait_for timed out after ${a.timeout_ms}ms (found ${matched}, expected >= ${a.min_count})`);
        await new Promise((resolve) => setTimeout(resolve, 40));
      }
      results.push({ op: a.op, selector: a.selector, count: matched });
      continue;
    }
    if (a.op === "read_list") {
      const nodes = query(a.item_selector, i).slice(0, a.limit);
      const items = nodes.map((n) => {
        if (n instanceof HTMLInputElement && n.type.toLowerCase() === "password") {
          return { text: "[password value hidden]", href: null, title: null };
        }
        const link = n.matches && n.matches("a[href]") ? n : n.querySelector("a[href]");
        return {
          text: (n.innerText || n.textContent || "").replace(/\s+/g, " ").trim().slice(0, a.text_limit),
          href: link ? String(link.href || "").slice(0, 1024) : null,
          title: link ? (link.innerText || link.getAttribute("title") || "").replace(/\s+/g, " ").trim().slice(0, 160) : null,
        };
      });
      results.push({ op: a.op, count: items.length, items });
      continue;
    }
    if (a.op === "read_hover_menu") {
      const triggers = a.hover_selectors.map((sel) => first(sel, i));
      const itemNodes = query(a.item_selector, i);
      if (!itemNodes.length) throw new Error(`actions[${i}] item_selector matched no elements`);
      const marker = "data-ac-hover-" + Math.random().toString(36).slice(2);
      const rules = [];
      const walk = (list) => {
        for (const rule of list) {
          if (typeof rule.selectorText === "string" && a.rule_selectors.includes(rule.selectorText)) rules.push(rule);
          if (rule.cssRules) { try { walk(rule.cssRules); } catch {} }
        }
      };
      for (const sheet of [...document.styleSheets, ...document.adoptedStyleSheets]) {
        try { walk(sheet.cssRules); } catch {}
      }
      const found = new Set(rules.map((r) => r.selectorText));
      const missing = a.rule_selectors.filter((sel) => !found.has(sel));
      if (missing.length) throw new Error(`actions[${i}] CSS rules unavailable: ${missing.join(", ")}`);
      const rewrite = (sel) => {
        let out = "", quote = "", count = 0;
        for (let k = 0; k < sel.length;) {
          const ch = sel[k];
          if (quote) {
            out += ch;
            if (ch === "\\") { if (k + 1 < sel.length) out += sel[++k]; }
            else if (ch === quote) quote = "";
            k++; continue;
          }
          if (ch === "\"" || ch === "'") { quote = ch; out += ch; k++; continue; }
          if (ch === "\\") throw new Error("Escaped selector is unsupported: " + sel);
          if (sel.slice(k, k + 6).toLowerCase() === ":hover" && !/[\\\w\u0080-\uFFFF-]/.test(sel[k + 6] || "")) {
            out += `:is(:hover,[${marker}])`; count++; k += 6; continue;
          }
          out += ch; k++;
        }
        if (quote || count === 0) throw new Error("Unsupported selector or no literal :hover token: " + sel);
        return out;
      };
      const originals = rules.map((rule) => ({ rule, selector: rule.selectorText }));
      const updates = originals.map((o) => ({ ...o, rewritten: rewrite(o.selector) }));
      const isVis = (node) => {
        const st = getComputedStyle(node);
        return node.getClientRects().length > 0 && st.display !== "none" && st.visibility !== "hidden" && st.visibility !== "collapse";
      };
      const initialVis = itemNodes.map(isVis);
      const marked = [];
      let items = [];
      try {
        for (const u of updates) {
          u.rule.selectorText = u.rewritten;
          if (u.rule.selectorText === u.selector) throw new Error("Browser rejected temporary CSS selector: " + u.selector);
        }
        for (const tr of triggers) {
          tr.setAttribute(marker, "");
          marked.push(tr);
        }
        items = itemNodes.filter((n, idx) => !initialVis[idx] && isVis(n)).map(safeText).filter(Boolean).slice(0, 100);
      } finally {
        for (const n of marked) n.removeAttribute(marker);
        for (const o of originals) { try { o.rule.selectorText = o.selector; } catch {} }
      }
      if (!items.length) throw new Error(`actions[${i}] no newly visible menu items`);
      results.push({ op: a.op, items, restored: originals.every((o) => o.rule.selectorText === o.selector) });
      continue;
    }
    if (a.op === "read" && a.as === "count") {
      results.push({ op: a.op, count: query(a.selector, i).length });
      continue;
    }
    const el = first(a.selector, i);
    if (a.op === "read") {
      if (a.as === "attribute") results.push({ op: a.op, attribute: a.attribute, value: el.getAttribute(a.attribute) });
      else if (el instanceof HTMLInputElement && el.type.toLowerCase() === "password") results.push({ op: a.op, text: "[password value hidden]" });
      else results.push({ op: a.op, text: (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) ? el.value.slice(0, 2000) : safeText(el) });
    } else if (a.op === "assert") {
      let actual;
      if (a.check === "text_contains") actual = safeText(el).includes(a.value);
      else if (a.check === "count") actual = query(a.selector, i).length === a.equals;
      else if (a.check === "enabled") actual = !el.disabled && el.getAttribute("aria-disabled") !== "true";
      else actual = el.getAttribute(a.attribute) === a.equals;
      if (actual !== (a.check === "text_contains" || a.check === "count" || a.check === "attribute" ? true : a.equals)) {
        throw new Error(`actions[${i}] assertion failed (${a.check})`);
      }
      results.push({ op: a.op, check: a.check, passed: true });
    } else if (a.op === "fill") {
      if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el.isContentEditable)) throw new Error(`actions[${i}] target is not an input, textarea, or contenteditable element`);
      if (el instanceof HTMLInputElement && ["password", "file", "checkbox", "radio", "button", "submit", "reset"].includes(el.type.toLowerCase())) throw new Error(`actions[${i}] fill is not allowed for input type ${el.type}`);
      if (el.isContentEditable) el.textContent = a.value;
      else {
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
        setter.call(el, a.value);
      }
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      results.push({ op: a.op, filled: true });
    } else if (a.op === "select") {
      if (!(el instanceof HTMLSelectElement)) throw new Error(`actions[${i}] target is not a <select> element`);
      const opt = Array.from(el.options).find((o) => o.value === a.value || o.text.trim() === a.value);
      if (!opt) throw new Error(`actions[${i}] option not found: ${a.value}`);
      el.value = opt.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      results.push({ op: a.op, selected: opt.value });
    } else if (a.op === "check" || a.op === "uncheck") {
      if (!(el instanceof HTMLInputElement) || (el.type.toLowerCase() !== "checkbox" && el.type.toLowerCase() !== "radio")) {
        throw new Error(`actions[${i}] target is not a checkbox or radio element`);
      }
      const want = a.op === "check";
      if (el.checked !== want) el.click();
      if (el.checked !== want) {
        el.checked = want;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
      results.push({ op: a.op, checked: el.checked });
    } else if (a.op === "click") {
      if (el.disabled || el.getAttribute("aria-disabled") === "true") throw new Error(`actions[${i}] target is disabled`);
      el.click();
      results.push({ op: a.op, clicked: true });
    } else if (a.op === "press_enter") {
      if (el.disabled || el.getAttribute("aria-disabled") === "true") throw new Error(`actions[${i}] target is disabled`);
      for (const type of ["keydown", "keypress", "keyup"]) el.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
      results.push({ op: a.op, pressed: "Enter" });
    }
  }
  return { ok: true, steps: results };
};

async function runActionsOnSession(c, sessionId, actions) {
  const steps = [];
  let chunk = [];
  const flushChunk = async () => {
    if (!chunk.length) return;
    const waitMs = chunk.reduce((sum, a) => sum + (a.op === "wait" ? a.ms : (a.op === "wait_for" ? a.timeout_ms : 0)), 0);
    const timeout = Math.min(30000, Math.max(CDP_TIMEOUT_MS, 3000 + waitMs));
    const res = await evaluate(c, sessionId, ACTIONS_SCRIPT, chunk, timeout);
    steps.push(...(res && res.steps || []));
    chunk = [];
  };
  for (const action of actions) {
    if (action.op !== "set_files") {
      chunk.push(action);
      continue;
    }
    await flushChunk();
    await c.command("DOM.enable", {}, sessionId);
    const doc = await c.command("DOM.getDocument", { depth: 0 }, sessionId);
    const q = await c.command("DOM.querySelector", { nodeId: doc.root.nodeId, selector: action.selector }, sessionId);
    if (!q || !q.nodeId) throw new BridgeError(`set_files selector matched no elements: ${action.selector}`);
    const desc = await c.command("DOM.describeNode", { nodeId: q.nodeId }, sessionId);
    const attrs = desc && desc.node && desc.node.attributes || [];
    const nodeName = (desc && desc.node && desc.node.nodeName || "").toUpperCase();
    let inputType = "";
    for (let k = 0; k + 1 < attrs.length; k += 2) {
      if (attrs[k].toLowerCase() === "type") inputType = String(attrs[k + 1] || "").toLowerCase();
    }
    if (nodeName !== "INPUT" || inputType !== "file") {
      throw new BridgeError(`set_files target is not <input type="file">: ${action.selector}`);
    }
    await c.command("DOM.setFileInputFiles", { nodeId: q.nodeId, files: action.files }, sessionId);
    steps.push({ op: "set_files", selector: action.selector, file_count: action.files.length });
  }
  await flushChunk();
  return steps;
}

async function waitForPageReady(c, sessionId, waitSelector, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastState = null;
  while (Date.now() < deadline) {
    try {
      const check = await evaluate(c, sessionId, WAIT_READY_SCRIPT, { wait_selector: waitSelector || "" }, 2500);
      lastState = check;
      if (check && check.error) throw new BridgeError(`Invalid wait_selector: ${check.error}`);
      if (check && check.ok) return check;
    } catch (err) {
      if (err instanceof BridgeError && err.message.startsWith("Invalid wait_selector")) throw err;
      // Ignore transient Execution context destroyed during cross-origin navigation
    }
    await new Promise((r) => setTimeout(r, 40));
  }
  const urlHint = lastState && lastState.url ? ` (last url: ${lastState.url})` : "";
  throw new BridgeError(
    waitSelector
      ? `Timed out after ${timeoutMs}ms waiting for selector ${waitSelector}${urlHint}`
      : `Timed out after ${timeoutMs}ms waiting for page load${urlHint}`
  );
}

async function callTool(name, args) {
  if (!args || typeof args !== "object" || Array.isArray(args)) throw new BridgeError("tool arguments must be an object");

  if (name === "stop_auto_browser") {
    const target = args.browser === undefined ? "all" : args.browser;
    if (!["edge", "chrome", "all"].includes(target)) throw new BridgeError("browser must be edge, chrome, or all");
    const keys = target === "all" ? ["auto:edge", "auto:chrome"] : [`auto:${target}`];
    const stopped = [];
    for (const k of keys) {
      if (await closePoolEntry(k, true)) stopped.push(k);
    }
    return { ok: true, stopped };
  }

  if (name === "open_and_run") {
    const t0 = performance.now();
    const safeUrl = validateHttpUrl(args.url);
    const { profile, browser } = resolveScope(args);
    const visible = boolArg(args.visible, "visible", false);
    const waitSelector = args.wait_selector === undefined ? "" : validString(args.wait_selector, "wait_selector", 1024);
    const waitTimeoutMs = intArg(args.wait_timeout_ms, "wait_timeout_ms", 12000, 500, 30000);
    const readSummary = boolArg(args.read_summary, "read_summary", true);
    const closeAfter = boolArg(args.close_after, "close_after", !visible);
    const actions = args.actions === undefined ? [] : validateActions(args.actions, true);

    const c = await getCdp(profile, browser, visible);
    const created = await c.command("Target.createTarget", { url: safeUrl, background: !visible });
    if (!created || !created.targetId) throw new BridgeError("Target.createTarget returned no targetId");
    const targetId = created.targetId;
    const attached = await c.command("Target.attachToTarget", { targetId, flatten: true });
    if (!attached || !attached.sessionId) {
      if (closeAfter) await c.command("Target.closeTarget", { targetId }).catch(() => {});
      throw new BridgeError("Browser did not return a session for created target");
    }
    const sessionId = attached.sessionId;
    let readyInfo;
    let steps = [];
    let summary = null;
    try {
      readyInfo = await waitForPageReady(c, sessionId, waitSelector, waitTimeoutMs);
      if (actions.length) {
        steps = await runActionsOnSession(c, sessionId, actions);
      }
      if (readSummary) {
        for (let attempt = 0; attempt < 5; attempt++) {
          try {
            const s = await evaluate(c, sessionId, SUMMARY_SCRIPT, { text_limit: 1500 }, 3000);
            if (s && (s.readyState !== "loading" || attempt === 4)) {
              summary = s;
              break;
            }
          } catch {}
          await new Promise((r) => setTimeout(r, 80));
        }
      }
    } finally {
      await c.command("Target.detachFromTarget", { sessionId }).catch(() => {});
      if (closeAfter) {
        await c.command("Target.closeTarget", { targetId }).catch(() => {});
      }
    }

    return {
      ok: true,
      profile,
      browser,
      visible,
      target_id: targetId,
      closed: closeAfter,
      url: (summary && summary.url) || (readyInfo && readyInfo.url) || safeUrl,
      title: (summary && summary.title) || (readyInfo && readyInfo.title) || "",
      elapsed_ms: Math.round((performance.now() - t0) * 100) / 100,
      steps,
      ...(summary ? { summary_text: summary.text } : {}),
    };
  }

  if (name === "open_tab") {
    const safeUrl = validateHttpUrl(args.url);
    const { profile, browser } = resolveScope(args);
    const visible = boolArg(args.visible, "visible", false);
    const background = args.background === undefined ? !visible : boolArg(args.background, "background", !visible);
    const c = await getCdp(profile, browser, visible);
    const res = await c.command("Target.createTarget", { url: safeUrl, background });
    if (!res || !res.targetId) throw new BridgeError("Target.createTarget returned no targetId");
    return { ok: true, profile, browser, visible, target_id: res.targetId, url: safeUrl, background };
  }

  const { profile, browser } = resolveScope(args);
  if (name === "close_tab") {
    const targetId = validString(args.target_id, "target_id", 256);
    const c = await getCdp(profile, browser, false);
    const result = await c.command("Target.getTargets");
    const target = (result.targetInfos || []).find((item) => item.targetId === targetId && item.type === "page");
    if (!target) throw new BridgeError(`Unknown page target_id: ${targetId}`);
    await c.command("Target.closeTarget", { targetId: target.targetId });
    return { ok: true, profile, browser, target_id: target.targetId };
  }

  const c = await getCdp(profile, browser, false);
  if (name === "list_tabs") {
    const search = args.search === undefined ? "" : validString(args.search, "search", 256, true).toLocaleLowerCase();
    const url = args.url_contains === undefined ? "" : validString(args.url_contains, "url_contains", 1024, true).toLocaleLowerCase();
    const title = args.title_contains === undefined ? "" : validString(args.title_contains, "title_contains", 256, true).toLocaleLowerCase();
    const limit = intArg(args.limit, "limit", 50, 1, MAX_TABS);
    const result = await c.command("Target.getTargets");
    const tabs = (result.targetInfos || []).filter((target) => target.type === "page")
      .map((target) => ({ target_id: target.targetId, url: String(target.url || "").slice(0, 2048), title: String(target.title || "").slice(0, 300) }))
      .filter((target) => (!search || `${target.url} ${target.title}`.toLocaleLowerCase().includes(search)) &&
        (!url || target.url.toLocaleLowerCase().includes(url)) && (!title || target.title.toLocaleLowerCase().includes(title)))
      .slice(0, limit);
    return { ok: true, profile, browser, tabs };
  }
  if (name === "inspect_tab") {
    const textLimit = intArg(args.text_limit, "text_limit", 4000, 0, MAX_TEXT);
    const { sessionId } = await attach(c, args.target_id);
    try { return await evaluate(c, sessionId, INSPECT_SCRIPT, { text_limit: textLimit }); }
    finally { await c.command("Target.detachFromTarget", { sessionId }).catch(() => {}); }
  }
  if (name === "run_actions") {
    const actions = validateActions(args.actions, false);
    const { sessionId } = await attach(c, args.target_id);
    try {
      const steps = await runActionsOnSession(c, sessionId, actions);
      return { ok: true, steps };
    } finally {
      await c.command("Target.detachFromTarget", { sessionId }).catch(() => {});
    }
  }
  throw new BridgeError(`unknown tool: ${name}`);
}

function toolResult(value, isError = false) {
  const text = JSON.stringify(value);
  return { content: [{ type: "text", text }], structuredContent: value, isError };
}

async function handle(req) {
  const method = req.method;
  const id = req.id;
  if (method === "notifications/initialized" || (typeof method === "string" && method.startsWith("notifications/"))) return null;
  if (method === "initialize") {
    const version = req.params && req.params.protocolVersion || "2024-11-05";
    return { jsonrpc: "2.0", id, result: { protocolVersion: version, capabilities: { tools: { listChanged: false } }, serverInfo: { name: "antigravity-edge-bridge", version: "0.2.0" } } };
  }
  if (method === "ping") return { jsonrpc: "2.0", id, result: {} };
  if (method === "tools/list") return { jsonrpc: "2.0", id, result: { tools: TOOLS } };
  if (method === "tools/call") {
    try {
      const params = req.params || {};
      const result = await callTool(params.name, params.arguments || {});
      return { jsonrpc: "2.0", id, result: toolResult(result, result && result.ok === false) };
    } catch (error) {
      return { jsonrpc: "2.0", id, result: toolResult({ ok: false, error: error.message || String(error) }, true) };
    }
  }
  if (id === undefined || id === null) return null;
  return { jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } };
}

let queue = Promise.resolve();
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity, terminal: false });
input.on("line", (line) => {
  if (!line.trim()) return;
  queue = queue.then(async () => {
    let response;
    try {
      const request = JSON.parse(line);
      if (!request || request.jsonrpc !== "2.0" || typeof request.method !== "string") throw new Error("request must be a JSON-RPC 2.0 object");
      response = await handle(request);
    } catch (error) {
      response = { jsonrpc: "2.0", id: null, error: { code: -32700, message: error.message || String(error) } };
    }
    if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
  }).catch((error) => {
    process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32603, message: error.message || String(error) } })}\n`);
  });
});

async function shutdownManagedHeadless() {
  for (const [key, entry] of Array.from(pool.entries())) {
    if (entry.spawned && !entry.visible) {
      await closePoolEntry(key, true);
    } else if (entry.cdp && entry.cdp.ws.readyState === WebSocket.OPEN) {
      entry.cdp.ws.close();
    }
  }
}

input.on("close", () => {
  queue.finally(() => shutdownManagedHeadless());
});
