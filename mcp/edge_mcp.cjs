#!/usr/bin/env node
"use strict";

// Lightweight stdio MCP bridge for Edge and Chrome using Chromium --remote-debugging-pipe.
// Supports isolated anonymous ("auto") and persistent signed-in ("kept") profiles with zero TCP ports and zero automation banners.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");

const MAX_TABS = 100;
const MAX_CANDIDATES = 30;
const MAX_ACTIONS = 30;
const MAX_TEXT = 6000;
const MAX_WAIT_MS = 5000;
const MAX_WAIT_FOR_MS = 10000;
const MAX_FILES = 20;
const CDP_TIMEOUT_MS = 10000;
const POOL_IDLE_TIMEOUT_MS = Math.max(0, Number(process.env.AUTO_COMPUTER_BROWSER_IDLE_MS ?? 600000));
const DEFAULT_HOME = process.env.AUTO_COMPUTER_HOME || path.join(process.env.LOCALAPPDATA || os.homedir(), "AutoComputer");
const DEFAULT_PROFILE_ROOT = process.env.AUTO_COMPUTER_PROFILE_ROOT || path.join(DEFAULT_HOME, "profiles");

const TOOLS = [
  {
    name: "daily_browser_run",
    description: "Run actions on active daily Chrome/Edge tab; auto-detaches.",
    inputSchema: {
      type: "object",
      properties: {
        browser: { type: "string", enum: ["chrome", "edge"] },
        profile: { type: "string" },
        url: { type: "string" },
        actions: { type: "array", items: { type: "object" } },
        read_summary: { type: "boolean" },
        fallback_to_kept: { type: "boolean" },
        total_timeout_ms: { type: "integer" },
      },
      required: ["url"],
    },
  },
  {
    name: "open_and_run",
    description: "Open URL/urls/pages via pipe ('auto'|'kept'), wait, run actions.",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string" },
        urls: { type: "array", items: { type: "string" } },
        pages: { type: "array", items: { type: "object" } },
        expected_url: { type: "string" },
        browser: { type: "string" },
        profile: { type: "string", enum: ["auto", "kept"] },
        visible: { type: "boolean" },
        wait_selector: { type: "string" },
        wait_timeout_ms: { type: "integer" },
        total_timeout_ms: { type: "integer" },
        actions: { type: "array", items: { type: "object" } },
        read_summary: { type: "boolean" },
        close_after: { type: "boolean" },
      },
    },
  },
  {
    name: "list_tabs",
    description: "List tabs ('auto'|'kept').",
    inputSchema: {
      type: "object",
      properties: {
        browser: { type: "string" },
        profile: { type: "string" },
        search: { type: "string" },
        url_contains: { type: "string" },
        title_contains: { type: "string" },
        limit: { type: "integer" },
      },
    },
  },
  {
    name: "inspect_tab",
    description: "Inspect tab text/locators (filterable).",
    inputSchema: {
      type: "object",
      properties: {
        target_id: { type: "string" },
        browser: { type: "string" },
        profile: { type: "string" },
        text_limit: { type: "integer" },
        selector: { type: "string" },
        text_contains: { type: "string" },
        control_type: { type: "string" },
        max_candidates: { type: "integer" },
      },
      required: ["target_id"],
    },
  },
  {
    name: "open_tab",
    description: "Open tab ('auto'|'kept').",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string" },
        browser: { type: "string" },
        profile: { type: "string" },
        visible: { type: "boolean" },
        background: { type: "boolean" },
      },
      required: ["url"],
    },
  },
  {
    name: "close_tab",
    description: "Close tab by target_id.",
    inputSchema: {
      type: "object",
      properties: {
        target_id: { type: "string" },
        browser: { type: "string" },
        profile: { type: "string" },
      },
      required: ["target_id"],
    },
  },
  {
    name: "run_actions",
    description: "Run DOM actions on target_id.",
    inputSchema: {
      type: "object",
      properties: {
        target_id: { type: "string" },
        browser: { type: "string" },
        profile: { type: "string" },
        expected_url: { type: "string" },
        total_timeout_ms: { type: "integer" },
        actions: { type: "array", items: { type: "object" } },
      },
      required: ["target_id", "actions"],
    },
  },
  {
    name: "stop_auto_browser",
    description: "Stop managed browser(s).",
    inputSchema: {
      type: "object",
      properties: {
        browser: { type: "string" },
        profile: { type: "string" },
      },
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
  if (profile !== "auto" && profile !== "kept") throw new BridgeError("profile must be 'auto' or 'kept'");
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

function normUrlFull(raw) {
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    const pathname = parsed.pathname.replace(/\/+$/, "") || "/";
    const params = Array.from(parsed.searchParams.entries()).sort((a, b) => (
      a[0] === b[0] ? (a[1] < b[1] ? -1 : (a[1] > b[1] ? 1 : 0)) : (a[0] < b[0] ? -1 : 1)
    ));
    const query = params.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
    return query ? `${parsed.origin}${pathname}?${query}` : `${parsed.origin}${pathname}`;
  } catch {
    return "";
  }
}

const READ_ONLY_OPS = new Set(["read", "read_list", "read_hover_menu", "assert", "wait", "wait_for"]);
function isWriteOp(op) {
  return !READ_ONLY_OPS.has(String(op || ""));
}

function hasWriteBefore(actions, endExclusive) {
  for (let idx = 0; idx < Math.min(endExclusive, actions.length); idx++) {
    if (isWriteOp(actions[idx] && actions[idx].op)) return true;
  }
  return false;
}

const activeRequests = new Map();

function remainingBudget(deadlineMs, label = "operation") {
  if (deadlineMs === undefined || deadlineMs === null) return CDP_TIMEOUT_MS;
  const rem = deadlineMs - Date.now();
  if (rem <= 0) {
    const err = new BridgeError(`Total timeout exceeded during ${label}`);
    err.timeoutCode = "TOTAL_TIMEOUT";
    throw err;
  }
  return rem;
}

function checkCancelled(reqCtx) {
  if (reqCtx && reqCtx.cancelled) {
    const err = new BridgeError(reqCtx.reason || "Cancelled by client");
    err.cancelCode = "CANCELLED";
    throw err;
  }
}

function abortableSleep(ms, reqCtx, deadlineMs) {
  let waitMs = Math.max(0, ms);
  if (deadlineMs !== undefined && deadlineMs !== null) {
    waitMs = Math.min(waitMs, Math.max(0, deadlineMs - Date.now()));
  }
  if (waitMs <= 0 || (reqCtx && reqCtx.cancelled)) return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (reqCtx && reqCtx.waiters) reqCtx.waiters.delete(finish);
      resolve();
    };
    const timer = setTimeout(finish, waitMs);
    if (reqCtx && reqCtx.waiters) reqCtx.waiters.add(finish);
  });
}

function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return Boolean(err && err.code === "EPERM");
  }
}

function acquireKeptLock(profileDir, browser) {
  const lockPath = path.join(profileDir, ".ac-owner.json");
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const fd = fs.openSync(lockPath, "wx");
      try {
        fs.writeFileSync(fd, JSON.stringify({ pid: process.pid, browser, startedAt: Date.now() }), "utf8");
      } finally {
        fs.closeSync(fd);
      }
      return lockPath;
    } catch (err) {
      if (!err || err.code !== "EEXIST") throw err;
      let existingPid = 0;
      try {
        const raw = JSON.parse(fs.readFileSync(lockPath, "utf8"));
        if (Number.isInteger(raw && raw.pid)) existingPid = raw.pid;
      } catch {}
      if (existingPid && existingPid !== process.pid && isProcessAlive(existingPid)) {
        const e = new BridgeError(`kept profile (${browser}) is currently in use by another agent process (pid=${existingPid})`);
        e.structured = {
          ok: false,
          reason: "profile_in_use",
          profile: "kept",
          browser,
          occupied_by_pid: existingPid,
          error: e.message,
        };
        throw e;
      }
      try { fs.unlinkSync(lockPath); } catch {}
    }
  }
  const e = new BridgeError(`kept profile (${browser}) lock contention`);
  e.structured = { ok: false, reason: "profile_in_use", profile: "kept", browser, error: e.message };
  throw e;
}

function releaseKeptLock(lockPath) {
  if (!lockPath) return;
  try {
    const raw = JSON.parse(fs.readFileSync(lockPath, "utf8"));
    if (raw && raw.pid && raw.pid !== process.pid) return;
  } catch {}
  try { fs.unlinkSync(lockPath); } catch {}
}

function profileDirFor(profile, browser) {
  if (profile === "kept") {
    return path.join(DEFAULT_PROFILE_ROOT, `${browser}-kept`);
  }
  return path.join(DEFAULT_PROFILE_ROOT, `${browser}-auto-${process.pid}`);
}

// Chromium --remote-debugging-pipe speaks NUL-delimited UTF-8 JSON-RPC over fd 3 (out) / fd 4 (in).
class PipeCdpConnection {
  constructor(child, label = "Browser") {
    this.child = child;
    this.writer = child.stdio[3];
    this.reader = child.stdio[4];
    this.label = label;
    this.nextId = 1;
    this.pending = new Map();
    this.eventListeners = new Map();
    this.chunks = [];
    this.chunkBytes = 0;
    this.maxFrameBytes = 64 * 1024 * 1024;
    this.closed = false;

    const failAll = (reason) => {
      if (this.closed) return;
      this.closed = true;
      const msg = reason instanceof Error ? reason.message : String(reason || "pipe closed");
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new BridgeError(`${this.label} DevTools pipe failed: ${msg}`));
      }
      this.pending.clear();
      this.eventListeners.clear();
      this.chunks = [];
      this.chunkBytes = 0;
      try { this.writer.destroy(); } catch {}
      try { this.reader.destroy(); } catch {}
    };

    this.reader.on("data", (chunk) => {
      if (this.closed) return;
      let start = 0;
      while (true) {
        const end = chunk.indexOf(0, start);
        const part = chunk.subarray(start, end < 0 ? chunk.length : end);
        this.chunkBytes += part.length;
        if (this.chunkBytes > this.maxFrameBytes) {
          failAll("CDP pipe message exceeded 64MB");
          return;
        }
        if (part.length) this.chunks.push(part);
        if (end < 0) return;
        const text = Buffer.concat(this.chunks, this.chunkBytes).toString("utf8");
        this.chunks = [];
        this.chunkBytes = 0;
        if (text) this._onFrame(text);
        if (this.closed) return;
        start = end + 1;
        if (start === chunk.length) return;
      }
    });
    this.reader.on("end", () => failAll("pipe closed"));
    this.reader.on("error", (e) => failAll(e));
    this.writer.on("error", (e) => failAll(e));
    child.once("exit", (code) => failAll(`browser exited (${code})`));
    child.once("error", (e) => failAll(e));
  }

  onEvent(method, fn) {
    let set = this.eventListeners.get(method);
    if (!set) {
      set = new Set();
      this.eventListeners.set(method, set);
    }
    set.add(fn);
  }

  offEvent(method, fn) {
    const set = this.eventListeners.get(method);
    if (!set) return;
    set.delete(fn);
    if (!set.size) this.eventListeners.delete(method);
  }

  _onFrame(text) {
    let message;
    try { message = JSON.parse(text); } catch { return; }
    if (!message) return;
    if (!Number.isInteger(message.id)) {
      if (typeof message.method === "string") {
        const listeners = this.eventListeners.get(message.method);
        if (listeners) {
          for (const fn of Array.from(listeners)) {
            try { fn(message.params || {}, message.sessionId); } catch {}
          }
        }
      }
      return;
    }
    const pending = this.pending.get(message.id);
    if (!pending) return;
    this.pending.delete(message.id);
    clearTimeout(pending.timer);
    if (message.error) {
      pending.reject(new BridgeError(`${this.label} DevTools ${pending.method} failed: ${message.error.message || JSON.stringify(message.error)}`));
    } else {
      pending.resolve(message.result || {});
    }
  }

  command(method, params = {}, sessionId, timeoutMs = CDP_TIMEOUT_MS) {
    if (this.closed || !this.writer || this.writer.destroyed) {
      return Promise.reject(new BridgeError(`${this.label} DevTools pipe is not open`));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new BridgeError(`${this.label} DevTools ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      try {
        const payload = JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + "\0";
        this.writer.write(payload, "utf8");
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new BridgeError(`Could not send ${this.label} DevTools ${method}: ${error.message}`));
      }
    });
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new BridgeError(`${this.label} DevTools pipe closed`));
    }
    this.pending.clear();
    this.eventListeners.clear();
    try { this.writer.destroy(); } catch {}
    try { this.reader.destroy(); } catch {}
  }
}

const pool = new Map();
const pendingPool = new Map();

function touchPoolEntry(key) {
  const entry = pool.get(key);
  if (!entry) return;
  if (entry.idleTimer) {
    clearTimeout(entry.idleTimer);
    entry.idleTimer = null;
  }
  if (entry.visible) return;
  if (POOL_IDLE_TIMEOUT_MS > 0) {
    entry.idleTimer = setTimeout(() => {
      closePoolEntry(key).catch(() => {});
    }, POOL_IDLE_TIMEOUT_MS);
    if (typeof entry.idleTimer.unref === "function") entry.idleTimer.unref();
  }
}

function killProcessTree(proc) {
  if (!proc) return;
  if (process.platform === "win32" && proc.pid) {
    try {
      const killer = spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
        stdio: "ignore",
        windowsHide: true,
      });
      killer.unref();
    } catch {}
  }
  try {
    if (proc.exitCode === null && !proc.killed) proc.kill("SIGKILL");
  } catch {}
}

async function closePoolEntry(key) {
  const entry = pool.get(key);
  if (!entry) return false;
  if (entry.idleTimer) {
    clearTimeout(entry.idleTimer);
    entry.idleTimer = null;
  }
  pool.delete(key);
  const proc = entry.proc;
  let exited = !proc || proc.exitCode !== null || proc.killed;
  const waitExit = exited
    ? Promise.resolve()
    : new Promise((resolve) => {
        proc.once("exit", () => { exited = true; resolve(); });
        setTimeout(resolve, 1500);
      });
  try {
    if (entry.cdp && !entry.cdp.closed) {
      await Promise.race([
        entry.cdp.command("Browser.close", {}, undefined, 1200).catch(() => {}),
        waitExit,
      ]);
      await waitExit;
      entry.cdp.close();
    }
  } catch {}
  if (!exited && proc && proc.exitCode === null) {
    killProcessTree(proc);
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
  if (entry.lockPath) {
    releaseKeptLock(entry.lockPath);
  }
  if (entry.ephemeralDir && entry.profileDir) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        fs.rmSync(entry.profileDir, { recursive: true, force: true });
        break;
      } catch {
        if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, 120));
      }
    }
  }
  return true;
}

async function spawnPipeBrowser(profile, browser, visible, deadlineMs) {
  const exe = findBrowserExe(browser);
  const profileDir = profileDirFor(profile, browser);
  fs.mkdirSync(profileDir, { recursive: true });
  const lockPath = profile === "kept" ? acquireKeptLock(profileDir, browser) : null;

  const launchArgs = [
    "--remote-debugging-pipe",
    `--user-data-dir=${profileDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-search-engine-choice-screen",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--mute-audio",
    ...(visible ? [] : ["--headless=new"]),
    "about:blank",
  ];
  const proc = spawn(exe, launchArgs, {
    stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"],
    windowsHide: !visible,
    detached: false,
  });

  const cdp = new PipeCdpConnection(proc, `${browser}-${profile}`);
  const key = `${profile}:${browser}`;
  proc.once("exit", () => {
    const cur = pool.get(key);
    if (cur && cur.proc === proc) pool.delete(key);
    if (lockPath) releaseKeptLock(lockPath);
  });

  try {
    const bootTimeout = deadlineMs ? Math.min(8000, remainingBudget(deadlineMs, "browser startup")) : 8000;
    await cdp.command("Target.getTargets", {}, undefined, bootTimeout);
  } catch (err) {
    cdp.close();
    if (proc.exitCode === null) {
      killProcessTree(proc);
    }
    if (lockPath) releaseKeptLock(lockPath);
    if (err && err.timeoutCode === "TOTAL_TIMEOUT") throw err;
    const isStartupTimeout = String(err && err.message || "").includes("timed out") && deadlineMs && Date.now() >= deadlineMs;
    if (isStartupTimeout) {
      const te = new BridgeError(`Total timeout exceeded during browser startup`);
      te.timeoutCode = "TOTAL_TIMEOUT";
      throw te;
    }
    throw new BridgeError(`${browser} (${profile}) pipe startup failed: ${err.message}. Close any standalone window sharing ${profileDir} and retry.`);
  }
  return {
    cdp,
    proc,
    profileDir,
    lockPath,
    ephemeralDir: profile === "auto",
    profile,
    browser,
    visible: Boolean(visible),
    idleTimer: null,
  };
}

async function getCdp(profile = "auto", browser = "edge", visible = false, deadlineMs) {
  const key = `${profile}:${browser}`;
  const existing = pool.get(key);
  if (existing && existing.cdp && !existing.cdp.closed) {
    if (visible && !existing.visible) {
      await closePoolEntry(key);
    } else {
      touchPoolEntry(key);
      return existing.cdp;
    }
  }
  if (pendingPool.has(key)) return pendingPool.get(key);

  const connectPromise = (async () => {
    const entry = await spawnPipeBrowser(profile, browser, visible, deadlineMs);
    pool.set(key, entry);
    touchPoolEntry(key);
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
  let scopeRoot = document;
  let textRoot = document.body;
  if (args.selector) {
    const found = document.querySelector(args.selector);
    if (!found) {
      return { ok: false, error: `inspect_tab selector matched no elements: ${args.selector}` };
    }
    scopeRoot = found;
    textRoot = found;
  }
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
  const typeFilter = args.control_type ? String(args.control_type).toLocaleLowerCase() : "";
  const textFilter = args.text_contains ? String(args.text_contains).toLocaleLowerCase() : "";
  const maxCand = args.max_candidates || 30;
  const candidates = [];
  const candidateSelector = "button,a[href],input,textarea,select,[role=button],[role=link],[role=checkbox],[role=radio]";
  const all = [];
  if (scopeRoot !== document && typeof scopeRoot.matches === "function" && scopeRoot.matches(candidateSelector)) {
    all.push(scopeRoot);
  }
  all.push(...scopeRoot.querySelectorAll(candidateSelector));
  for (const el of all) {
    if (!visible(el)) continue;
    const role = el.getAttribute("role");
    const type = el.getAttribute("type");
    if (typeFilter) {
      const tag = el.localName.toLocaleLowerCase();
      const roleLc = role ? role.toLocaleLowerCase() : "";
      const typeLc = type ? type.toLocaleLowerCase() : "";
      const matchesType = tag === typeFilter
        || roleLc === typeFilter
        || typeLc === typeFilter
        || (typeFilter === "link" && tag === "a");
      if (!matchesType) continue;
    }
    const label = (el.getAttribute("aria-label") || el.labels && Array.from(el.labels).map((x) => x.innerText).join(" ") || el.innerText || el.getAttribute("placeholder") || "").trim().slice(0, 120);
    if (textFilter && !label.toLocaleLowerCase().includes(textFilter)) continue;
    const item = { tag: el.localName, locator: locator(el) };
    if (role) item.role = role;
    if (type) item.type = type;
    if (label) item.label = label;
    if (el.disabled || el.getAttribute("aria-disabled") === "true") item.enabled = false;
    candidates.push(item);
    if (candidates.length >= maxCand) break;
  }
  return {
    ok: true,
    url: location.href.slice(0, 2048),
    title: document.title.slice(0, 300),
    text: args.text_limit > 0 ? ((textRoot && textRoot.innerText) || "").slice(0, args.text_limit) : "",
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
    text: (document.body && document.body.innerText || "").slice(0, args.text_limit || 1200),
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
    "op", "action", "type", "selector", "value", "as", "attribute", "check", "equals",
    "ms", "timeout_ms", "min_count", "files", "hover_selectors", "rule_selectors", "item_selector",
    "limit", "text_limit",
  ]);
  return raw.map((a, i) => {
    if (!a || typeof a !== "object" || Array.isArray(a)) throw new BridgeError(`actions[${i}] must be an object`);
    const unknown = Object.keys(a).filter((key) => !accepted.has(key));
    if (unknown.length) throw new BridgeError(`actions[${i}] has unsupported fields: ${unknown.join(", ")}`);
    const op = a.op || a.action || a.type;
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

const ACTIONS_SCRIPT = async function (payload) {
  const actions = Array.isArray(payload) ? payload : (payload && Array.isArray(payload.actions) ? payload.actions : []);
  const expectedUrlNorm = !Array.isArray(payload) && payload && payload.expected_url_norm ? String(payload.expected_url_norm) : "";
  const normFull = (raw) => {
    try {
      const parsed = new URL(raw);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      const pathname = parsed.pathname.replace(/\/+$/, "") || "/";
      const params = Array.from(parsed.searchParams.entries()).sort((a, b) => (
        a[0] === b[0] ? (a[1] < b[1] ? -1 : (a[1] > b[1] ? 1 : 0)) : (a[0] < b[0] ? -1 : 1)
      ));
      const query = params.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
      return query ? `${parsed.origin}${pathname}?${query}` : `${parsed.origin}${pathname}`;
    } catch {
      return "";
    }
  };
  const readOnly = new Set(["read", "read_list", "read_hover_menu", "assert", "wait", "wait_for"]);
  const results = [];
  const count = (selector, i) => {
    try { return document.querySelectorAll(selector).length; }
    catch (error) { throw new Error(`actions[${i}] invalid CSS selector: ${error.message}`); }
  };
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
    if (expectedUrlNorm && !readOnly.has(String(a.op || ""))) {
      const currentNorm = normFull(location.href);
      if (currentNorm !== expectedUrlNorm) {
        return {
          ok: false,
          reason: "page_url_changed",
          failed_index: i,
          expected_url_norm: expectedUrlNorm,
          actual_url: location.href,
          error: `Page URL changed before write action (expected ${expectedUrlNorm}, got ${currentNorm || location.href})`,
          steps: results,
        };
      }
    }
    try {
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
          matched = count(a.selector, i);
          if (matched >= a.min_count) break;
          if (Date.now() >= deadline) throw new Error(`actions[${i}] wait_for timed out after ${a.timeout_ms}ms (found ${matched}, expected >= ${a.min_count})`);
          await new Promise((resolve) => setTimeout(resolve, 40));
        }
        results.push({ op: a.op, selector: a.selector, count: matched });
        continue;
      }
      if (a.op === "read_list") {
        let nodeList;
        try { nodeList = document.querySelectorAll(a.item_selector); }
        catch (error) { throw new Error(`actions[${i}] invalid CSS selector: ${error.message}`); }
        const nodes = [];
        const take = Math.min(nodeList.length, a.limit);
        for (let k = 0; k < take; k++) nodes.push(nodeList[k]);
        const items = nodes.map((n) => {
          if (n instanceof HTMLInputElement && n.type.toLowerCase() === "password") {
            return { text: "[password value hidden]" };
          }
          const link = n.matches && n.matches("a[href]") ? n : n.querySelector("a[href]");
          const row = { text: (n.innerText || n.textContent || "").replace(/\s+/g, " ").trim().slice(0, a.text_limit) };
          if (link && link.href) row.href = String(link.href).slice(0, 1024);
          const title = link ? (link.innerText || link.getAttribute("title") || "").replace(/\s+/g, " ").trim().slice(0, 160) : "";
          if (title && !row.text.includes(title)) row.title = title;
          return row;
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
        results.push({ op: a.op, count: count(a.selector, i) });
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
    } catch (err) {
      return { ok: false, failed_index: i, error: String(err && err.message || err), steps: results };
    }
  }
  return { ok: true, steps: results };
};

const BATCHABLE_READ_OPS = new Set(["read", "read_list", "read_hover_menu", "assert"]);

async function runActionsOnSession(c, sessionId, actions, opts = {}) {
  const deadlineMs = opts.deadlineMs ?? (Date.now() + 35000);
  const reqCtx = opts.reqCtx || null;
  const guardState = opts.guardState || null;
  const expectedUrlNorm = opts.expectedUrlNorm || "";
  const steps = [];

  for (let i = 0; i < actions.length; i++) {
    if (guardState && guardState.interruptedByDialog) {
      return {
        ok: false,
        reason: "action_interrupted_by_dialog",
        error: "Interactive browser dialog (confirm/prompt/beforeunload) was auto-dismissed; stopped before next action",
        failed_index: i,
        stopped_before_index: i,
        steps,
        needs_readback: true,
      };
    }
    if (reqCtx && reqCtx.cancelled) {
      return {
        ok: false,
        reason: "cancelled",
        error: reqCtx.reason || "Cancelled by client",
        failed_index: i,
        stopped_before_index: i,
        steps,
        needs_readback: hasWriteBefore(actions, i),
      };
    }
    if (Date.now() >= deadlineMs) {
      return {
        ok: false,
        reason: "timeout",
        error: `Total timeout exceeded before actions[${i}]`,
        failed_index: i,
        stopped_before_index: i,
        steps,
        needs_readback: hasWriteBefore(actions, i),
      };
    }

    const action = actions[i];
    if (BATCHABLE_READ_OPS.has(action.op)) {
      let j = i + 1;
      while (j < actions.length && BATCHABLE_READ_OPS.has(actions[j].op)) j += 1;
      const slice = actions.slice(i, j);
      let batchTimeout;
      try {
        batchTimeout = Math.min(CDP_TIMEOUT_MS, remainingBudget(deadlineMs, `actions[${i}..${j - 1}]`));
      } catch (err) {
        return {
          ok: false,
          reason: "timeout",
          error: err.message,
          failed_index: i,
          stopped_before_index: i,
          steps,
          needs_readback: hasWriteBefore(actions, i),
        };
      }
      let res;
      try {
        res = await evaluate(c, sessionId, ACTIONS_SCRIPT, { actions: slice }, batchTimeout);
      } catch (err) {
        return {
          ok: false,
          reason: Date.now() >= deadlineMs ? "timeout" : "batch_interrupted",
          failed_index: i,
          error: err && err.message ? err.message : String(err),
          steps,
          needs_readback: hasWriteBefore(actions, i),
        };
      }
      if (res && Array.isArray(res.steps)) steps.push(...res.steps);
      if (res && res.ok === false) {
        const failedIdx = i + (Number.isInteger(res.failed_index) ? res.failed_index : 0);
        return {
          ok: false,
          reason: "batch_interrupted",
          failed_index: failedIdx,
          error: res.error || "read action failed",
          steps,
          needs_readback: hasWriteBefore(actions, failedIdx),
        };
      }
      i = j - 1;
      continue;
    }

    if (action.op === "wait") {
      await abortableSleep(action.ms, reqCtx, deadlineMs);
      if (guardState && guardState.interruptedByDialog) {
        return {
          ok: false,
          reason: "action_interrupted_by_dialog",
          error: "Interactive browser dialog interrupted wait",
          failed_index: i,
          stopped_before_index: i,
          steps,
          needs_readback: true,
        };
      }
      if (reqCtx && reqCtx.cancelled) {
        return {
          ok: false,
          reason: "cancelled",
          error: reqCtx.reason || "Cancelled by client",
          failed_index: i,
          stopped_before_index: i,
          steps,
          needs_readback: hasWriteBefore(actions, i),
        };
      }
      if (Date.now() >= deadlineMs && action.ms > 0) {
        return {
          ok: false,
          reason: "timeout",
          error: `Total timeout exceeded during wait at actions[${i}]`,
          failed_index: i,
          stopped_before_index: i,
          steps,
          needs_readback: hasWriteBefore(actions, i),
        };
      }
      steps.push({ op: "wait", ms: action.ms });
      continue;
    }

    if (action.op === "wait_for") {
      const waitDeadline = Math.min(Date.now() + action.timeout_ms, deadlineMs);
      let matched = 0;
      while (true) {
        if (guardState && guardState.interruptedByDialog) {
          return {
            ok: false,
            reason: "action_interrupted_by_dialog",
            error: "Interactive browser dialog interrupted wait_for",
            failed_index: i,
            stopped_before_index: i,
            steps,
            needs_readback: true,
          };
        }
        if (reqCtx && reqCtx.cancelled) {
          return {
            ok: false,
            reason: "cancelled",
            error: reqCtx.reason || "Cancelled by client",
            failed_index: i,
            stopped_before_index: i,
            steps,
            needs_readback: hasWriteBefore(actions, i),
          };
        }
        const rem = waitDeadline - Date.now();
        if (rem <= 0) {
          const isTotal = Date.now() >= deadlineMs;
          return {
            ok: false,
            reason: isTotal ? "timeout" : "batch_interrupted",
            error: isTotal
              ? `Total timeout exceeded waiting for ${action.selector}`
              : `actions[${i}] wait_for timed out after ${action.timeout_ms}ms (found ${matched}, expected >= ${action.min_count})`,
            failed_index: i,
            stopped_before_index: i,
            steps,
            needs_readback: hasWriteBefore(actions, i),
          };
        }
        try {
          const r = await evaluate(
            c,
            sessionId,
            ACTIONS_SCRIPT,
            { actions: [{ op: "read", selector: action.selector, as: "count" }] },
            Math.min(2000, rem)
          );
          if (r && r.ok === false) {
            return {
              ok: false,
              reason: "batch_interrupted",
              error: r.error || "wait_for query failed",
              failed_index: i,
              steps,
              needs_readback: hasWriteBefore(actions, i),
            };
          }
          matched = (r && r.steps && r.steps[0] && r.steps[0].count) || 0;
          if (matched >= action.min_count) break;
        } catch (err) {
          if (Date.now() >= deadlineMs) {
            return {
              ok: false,
              reason: "timeout",
              error: `Total timeout exceeded waiting for ${action.selector}`,
              failed_index: i,
              stopped_before_index: i,
              steps,
              needs_readback: hasWriteBefore(actions, i),
            };
          }
          return {
            ok: false,
            reason: "batch_interrupted",
            error: err && err.message ? err.message : String(err),
            failed_index: i,
            steps,
            needs_readback: hasWriteBefore(actions, i),
          };
        }
        await abortableSleep(40, reqCtx, waitDeadline);
      }
      steps.push({ op: "wait_for", selector: action.selector, count: matched });
      continue;
    }

    if (action.op === "set_files") {
      try {
        if (expectedUrlNorm) {
          const curHref = await evaluate(c, sessionId, () => location.href, {}, Math.min(2000, remainingBudget(deadlineMs, "url check")));
          if (normUrlFull(curHref) !== expectedUrlNorm) {
            return {
              ok: false,
              reason: "page_url_changed",
              error: `Page URL changed before set_files (expected ${expectedUrlNorm}, got ${curHref})`,
              actual_url: String(curHref || ""),
              failed_index: i,
              stopped_before_index: i,
              steps,
              needs_readback: hasWriteBefore(actions, i),
            };
          }
        }
        const rem = remainingBudget(deadlineMs, `actions[${i}] set_files`);
        await c.command("DOM.enable", {}, sessionId, rem);
        const doc = await c.command("DOM.getDocument", { depth: 0 }, sessionId, remainingBudget(deadlineMs, "DOM.getDocument"));
        const q = await c.command("DOM.querySelector", { nodeId: doc.root.nodeId, selector: action.selector }, sessionId, remainingBudget(deadlineMs, "DOM.querySelector"));
        if (!q || !q.nodeId) throw new BridgeError(`set_files selector matched no elements: ${action.selector}`);
        const desc = await c.command("DOM.describeNode", { nodeId: q.nodeId }, sessionId, remainingBudget(deadlineMs, "DOM.describeNode"));
        const attrs = desc && desc.node && desc.node.attributes || [];
        const nodeName = (desc && desc.node && desc.node.nodeName || "").toUpperCase();
        let inputType = "";
        for (let k = 0; k + 1 < attrs.length; k += 2) {
          if (attrs[k].toLowerCase() === "type") inputType = String(attrs[k + 1] || "").toLowerCase();
        }
        if (nodeName !== "INPUT" || inputType !== "file") {
          throw new BridgeError(`set_files target is not <input type="file">: ${action.selector}`);
        }
        await c.command("DOM.setFileInputFiles", { nodeId: q.nodeId, files: action.files }, sessionId, remainingBudget(deadlineMs, "DOM.setFileInputFiles"));
        steps.push({ op: "set_files", selector: action.selector, file_count: action.files.length });
        continue;
      } catch (err) {
        return {
          ok: false,
          reason: err && err.timeoutCode === "TOTAL_TIMEOUT" ? "timeout" : "batch_interrupted",
          failed_index: i,
          error: err && err.message ? err.message : String(err),
          steps,
          needs_readback: true,
        };
      }
    }

    let stepTimeout;
    try {
      stepTimeout = Math.min(CDP_TIMEOUT_MS, remainingBudget(deadlineMs, `actions[${i}]`));
    } catch (err) {
      return {
        ok: false,
        reason: "timeout",
        error: err.message,
        failed_index: i,
        stopped_before_index: i,
        steps,
        needs_readback: hasWriteBefore(actions, i),
      };
    }

    let res;
    try {
      res = await evaluate(c, sessionId, ACTIONS_SCRIPT, { actions: [action], expected_url_norm: expectedUrlNorm }, stepTimeout);
    } catch (err) {
      const timedOut = Date.now() >= deadlineMs || String(err && err.message || "").includes("timed out");
      return {
        ok: false,
        reason: timedOut && Date.now() >= deadlineMs ? "timeout" : "batch_interrupted",
        failed_index: i,
        error: err && err.message ? err.message : String(err),
        steps,
        needs_readback: hasWriteBefore(actions, i + 1),
      };
    }
    if (res && res.ok === false) {
      if (res.reason === "page_url_changed") {
        return {
          ok: false,
          reason: "page_url_changed",
          failed_index: i,
          stopped_before_index: i,
          actual_url: res.actual_url || "",
          error: res.error || "Page URL changed before write action",
          steps,
          needs_readback: hasWriteBefore(actions, i),
        };
      }
      return {
        ok: false,
        reason: "batch_interrupted",
        failed_index: i,
        error: res.error || "action failed",
        steps,
        needs_readback: hasWriteBefore(actions, i + 1),
      };
    }
    if (res && Array.isArray(res.steps)) {
      steps.push(...res.steps);
    }
  }

  if (guardState && guardState.interruptedByDialog) {
    return {
      ok: false,
      reason: "action_interrupted_by_dialog",
      error: "Interactive browser dialog (confirm/prompt/beforeunload) was auto-dismissed; business action may not have completed",
      stopped_before_index: actions.length,
      steps,
      needs_readback: true,
    };
  }
  return { ok: true, steps };
}

async function withDialogGuard(c, sessionId, fn) {
  const state = { dialogs: [], interruptedByDialog: false };
  const onDialog = (params, evSessionId) => {
    if (evSessionId && evSessionId !== sessionId) return;
    const dtype = String(params && params.type || "dialog");
    if (dtype !== "alert") state.interruptedByDialog = true;
    state.dialogs.push({
      type: dtype,
      message: String(params && params.message || "").slice(0, 200),
    });
    c.command("Page.handleJavaScriptDialog", { accept: false }, sessionId).catch(() => {});
  };
  c.onEvent("Page.javascriptDialogOpening", onDialog);
  await c.command("Page.enable", {}, sessionId).catch(() => {});
  try {
    const value = await fn(state);
    return { value, dialogs: state.dialogs, interruptedByDialog: state.interruptedByDialog };
  } finally {
    c.offEvent("Page.javascriptDialogOpening", onDialog);
  }
}

async function waitForPageReady(c, sessionId, waitSelector, timeoutMs, deadlineMs, reqCtx) {
  const hardDeadline = deadlineMs ? Math.min(Date.now() + timeoutMs, deadlineMs) : (Date.now() + timeoutMs);
  let lastState = null;
  while (true) {
    checkCancelled(reqCtx);
    const rem = hardDeadline - Date.now();
    if (rem <= 0) {
      if (deadlineMs && Date.now() >= deadlineMs) {
        const te = new BridgeError("Total timeout exceeded waiting for page load");
        te.timeoutCode = "TOTAL_TIMEOUT";
        throw te;
      }
      break;
    }
    try {
      const check = await evaluate(c, sessionId, WAIT_READY_SCRIPT, { wait_selector: waitSelector || "" }, Math.min(2500, rem));
      lastState = check;
      if (check && check.error) throw new BridgeError(`Invalid wait_selector: ${check.error}`);
      if (check && check.ok) return check;
    } catch (err) {
      if (err instanceof BridgeError && err.message.startsWith("Invalid wait_selector")) throw err;
      if (reqCtx && reqCtx.cancelled) checkCancelled(reqCtx);
      if (deadlineMs && Date.now() >= deadlineMs) {
        const te = new BridgeError("Total timeout exceeded waiting for page load");
        te.timeoutCode = "TOTAL_TIMEOUT";
        throw te;
      }
    }
    await abortableSleep(20, reqCtx, hardDeadline);
  }
  const urlHint = lastState && lastState.url ? ` (last url: ${lastState.url})` : "";
  throw new BridgeError(
    waitSelector
      ? `Timed out after ${timeoutMs}ms waiting for selector ${waitSelector}${urlHint}`
      : `Timed out after ${timeoutMs}ms waiting for page load${urlHint}`
  );
}

async function callTool(name, args, reqCtx = null) {
  if (!args || typeof args !== "object" || Array.isArray(args)) throw new BridgeError("tool arguments must be an object");

  if (name === "daily_browser_run") {
    const totalTimeoutMs = args.total_timeout_ms === undefined
      ? 60000
      : intArg(args.total_timeout_ms, "total_timeout_ms", 60000, 500, 120000);
    const overallDeadline = (reqCtx && typeof reqCtx.deadlineMs === "number")
      ? reqCtx.deadlineMs
      : (Date.now() + totalTimeoutMs);
    const res = await runDailyBrowser(args, reqCtx, Math.max(1, overallDeadline - Date.now()));
    if (res && (res.reason === "cancelled" || res.reason === "timeout")) return res;
    if (reqCtx && reqCtx.cancelled) {
      return { ok: false, available: false, reason: "cancelled", error: reqCtx.reason || "Cancelled by client", needs_readback: false };
    }
    if (res && res.ok === false && args.fallback_to_kept === true && res.needs_readback !== true && res.available === false) {
      const keptBrowser = args.browser || "chrome";
      if (Date.now() >= overallDeadline) {
        return {
          ok: false,
          fallback_used: "kept",
          daily_reason: res.reason || res.error || "daily_unavailable",
          reason: "timeout",
          error: "Total timeout exceeded before falling back to kept",
          needs_readback: false,
        };
      }
      return await enqueueScope(`kept:${keptBrowser}`, async () => {
        if (reqCtx && reqCtx.cancelled) {
          return {
            ok: false,
            fallback_used: "kept",
            reason: "cancelled",
            error: reqCtx.reason || "Cancelled by client",
            needs_readback: false,
          };
        }
        if (Date.now() >= overallDeadline) {
          return {
            ok: false,
            fallback_used: "kept",
            daily_reason: res.reason || res.error || "daily_unavailable",
            reason: "timeout",
            error: "Total timeout exceeded waiting for kept browser queue",
            needs_readback: false,
          };
        }
        const keptArgs = {
          url: args.url,
          _expected_url: args.url,
          _deadline_ms: overallDeadline,
          browser: keptBrowser,
          profile: "kept",
          actions: args.actions || [],
          ...(args.read_summary !== undefined ? { read_summary: args.read_summary } : {}),
        };
        const fallbackRes = await callTool("open_and_run", keptArgs, reqCtx);
        if (fallbackRes && typeof fallbackRes === "object") {
          fallbackRes.fallback_used = "kept";
          fallbackRes.daily_reason = res.reason || res.error || "daily_unavailable";
        }
        return fallbackRes;
      }, {
        reqCtx,
        deadlineMs: overallDeadline,
        onAbort: (kind) => ({
          ok: false,
          fallback_used: "kept",
          ...(kind === "timeout" ? { daily_reason: res.reason || res.error || "daily_unavailable" } : {}),
          reason: kind === "cancelled" ? "cancelled" : "timeout",
          error: kind === "cancelled"
            ? ((reqCtx && reqCtx.reason) || "Cancelled by client")
            : "Total timeout exceeded waiting for kept browser queue",
          needs_readback: false,
        }),
      });
    }
    return res;
  }

  if (name === "stop_auto_browser") {
    const target = args.browser === undefined ? "all" : args.browser;
    const prof = args.profile === undefined ? "all" : args.profile;
    if (!["edge", "chrome", "all"].includes(target)) throw new BridgeError("browser must be edge, chrome, or all");
    if (!["auto", "kept", "all"].includes(prof)) throw new BridgeError("profile must be auto, kept, or all");
    const browsers = target === "all" ? ["edge", "chrome"] : [target];
    const profiles = prof === "all" ? ["auto", "kept"] : [prof];
    const stopped = [];
    for (const p of profiles) {
      for (const b of browsers) {
        const k = `${p}:${b}`;
        if (await closePoolEntry(k)) stopped.push(k);
      }
    }
    return { ok: true, stopped };
  }

  if (name === "open_and_run") {
    const t0 = performance.now();
    const { profile, browser } = resolveScope(args);
    const visible = boolArg(args.visible, "visible", false);
    const defaultWaitSelector = args.wait_selector === undefined ? "" : validString(args.wait_selector, "wait_selector", 1024);
    const waitTimeoutMs = intArg(args.wait_timeout_ms, "wait_timeout_ms", 12000, 500, 30000);
    const totalTimeoutMs = intArg(args.total_timeout_ms, "total_timeout_ms", 35000, 1000, 120000);
    const deadlineMs = typeof args._deadline_ms === "number"
      ? args._deadline_ms
      : ((reqCtx && typeof reqCtx.deadlineMs === "number") ? reqCtx.deadlineMs : (Date.now() + totalTimeoutMs));
    const defaultActions = args.actions === undefined ? [] : validateActions(args.actions, true);
    const hasDefaultReadAction = defaultActions.some((a) => ["read", "read_list", "read_hover_menu"].includes(a.op));
    const defaultReadSummary = args.read_summary === undefined ? undefined : boolArg(args.read_summary, "read_summary", !hasDefaultReadAction);
    const closeAfter = boolArg(args.close_after, "close_after", !visible);
    const expectedFallbackUrl = typeof args._expected_url === "string" ? args._expected_url : "";
    const explicitExpectedUrl = args.expected_url === undefined ? "" : validateHttpUrl(args.expected_url);
    const poolKey = `${profile}:${browser}`;

    let isMulti = false;
    let pageSpecs = [];
    if (args.pages !== undefined || args.urls !== undefined) {
      if (args.pages !== undefined && args.urls !== undefined) {
        throw new BridgeError("Specify either pages or urls, not both");
      }
      isMulti = true;
      const rawList = args.pages !== undefined ? args.pages : args.urls;
      if (!Array.isArray(rawList) || rawList.length < 1 || rawList.length > 20) {
        throw new BridgeError("pages/urls must be a non-empty array of at most 20 items");
      }
      pageSpecs = rawList.map((item, idx) => {
        if (typeof item === "string") {
          const rs = defaultReadSummary === undefined ? !hasDefaultReadAction : defaultReadSummary;
          return { url: validateHttpUrl(item), expectedUrl: "", waitSelector: defaultWaitSelector, actions: defaultActions, readSummary: rs };
        }
        if (!item || typeof item !== "object" || Array.isArray(item)) {
          throw new BridgeError(`pages[${idx}] must be an object`);
        }
        const pActions = item.actions === undefined ? defaultActions : validateActions(item.actions, true);
        const pHasRead = pActions.some((a) => ["read", "read_list", "read_hover_menu"].includes(a.op));
        const pReadSummary = item.read_summary === undefined
          ? (defaultReadSummary === undefined ? !pHasRead : defaultReadSummary)
          : boolArg(item.read_summary, `pages[${idx}].read_summary`, !pHasRead);
        const pWait = item.wait_selector === undefined ? defaultWaitSelector : validString(item.wait_selector, `pages[${idx}].wait_selector`, 1024);
        const pExp = item.expected_url === undefined ? "" : validateHttpUrl(item.expected_url);
        return { url: validateHttpUrl(item.url), expectedUrl: pExp, waitSelector: pWait, actions: pActions, readSummary: pReadSummary };
      });
    } else {
      const rs = defaultReadSummary === undefined ? !hasDefaultReadAction : defaultReadSummary;
      pageSpecs = [{ url: validateHttpUrl(args.url), expectedUrl: explicitExpectedUrl, waitSelector: defaultWaitSelector, actions: defaultActions, readSummary: rs }];
    }

    const compactStepsFn = (list) => (list || []).map((s) => (
      ["fill", "click", "select", "check", "uncheck", "press_enter", "wait"].includes(s.op) ? { op: s.op } : s
    ));

    let c;
    try {
      checkCancelled(reqCtx);
      remainingBudget(deadlineMs, "browser startup");
      c = await getCdp(profile, browser, visible, deadlineMs);
    } catch (err) {
      const reason = (err && err.cancelCode === "CANCELLED") ? "cancelled"
        : ((err && err.timeoutCode === "TOTAL_TIMEOUT") ? "timeout" : null);
      if (reason) {
        return {
          ok: false,
          reason,
          error: err.message,
          needs_readback: false,
          completed_steps: [],
          profile,
          browser,
          elapsed_ms: Math.round((performance.now() - t0) * 100) / 100,
        };
      }
      throw err;
    }

    const runSingleSpec = async (spec, keepOpen, openedTargetIds) => {
      const created = await c.command("Target.createTarget", { url: spec.url, background: !visible }, undefined, remainingBudget(deadlineMs, "Target.createTarget"));
      if (!created || !created.targetId) throw new BridgeError("Target.createTarget returned no targetId");
      const targetId = created.targetId;
      if (!keepOpen && Array.isArray(openedTargetIds)) openedTargetIds.push(targetId);
      const attached = await c.command("Target.attachToTarget", { targetId, flatten: true }, undefined, remainingBudget(deadlineMs, "Target.attachToTarget"));
      if (!attached || !attached.sessionId) {
        if (!keepOpen) await c.command("Target.closeTarget", { targetId }).catch(() => {});
        throw new BridgeError("Browser did not return a session for created target");
      }
      const sessionId = attached.sessionId;
      let readyInfo = null;
      let stepsResult = { ok: true, steps: [] };
      let summary = null;
      let dialogs = [];
      let interruptedByDialog = false;
      let fallbackUrlMismatch = false;
      let outerErr = null;
      const writeExpectUrl = expectedFallbackUrl || spec.expectedUrl || "";
      const expectedUrlNorm = writeExpectUrl ? normUrlFull(writeExpectUrl) : "";
      try {
        const guarded = await withDialogGuard(c, sessionId, async (guardState) => {
          const readyBudget = Math.min(waitTimeoutMs, remainingBudget(deadlineMs, "page ready"));
          readyInfo = await waitForPageReady(c, sessionId, spec.waitSelector, readyBudget, deadlineMs, reqCtx);
          if (
            !isMulti
            && expectedFallbackUrl
            && spec.actions.some((a) => isWriteOp(a.op))
            && normUrlFull(readyInfo && readyInfo.url) !== normUrlFull(expectedFallbackUrl)
          ) {
            fallbackUrlMismatch = true;
            return;
          }
          if (spec.actions.length) {
            stepsResult = await runActionsOnSession(c, sessionId, spec.actions, {
              deadlineMs,
              reqCtx,
              guardState,
              expectedUrlNorm,
            });
            if (!stepsResult.ok) return;
          }
          if (spec.readSummary) {
            checkCancelled(reqCtx);
            const sumRem = deadlineMs - Date.now();
            if (sumRem > 100) {
              for (let attempt = 0; attempt < 5; attempt++) {
                checkCancelled(reqCtx);
                const r = deadlineMs - Date.now();
                if (r <= 0) break;
                try {
                  const s = await evaluate(c, sessionId, SUMMARY_SCRIPT, { text_limit: 1200 }, Math.min(3000, r));
                  if (s && (s.readyState !== "loading" || attempt === 4)) {
                    summary = s;
                    break;
                  }
                } catch {}
                await abortableSleep(30, reqCtx, deadlineMs);
              }
            }
          }
        });
        dialogs = guarded.dialogs;
        interruptedByDialog = guarded.interruptedByDialog;
      } catch (err) {
        outerErr = err;
      } finally {
        await c.command("Target.detachFromTarget", { sessionId }).catch(() => {});
        if (!keepOpen) await c.command("Target.closeTarget", { targetId }).catch(() => {});
      }

      if (outerErr) {
        if (outerErr.cancelCode === "CANCELLED" || outerErr.timeoutCode === "TOTAL_TIMEOUT") {
          return {
            ok: false,
            reason: outerErr.cancelCode === "CANCELLED" ? "cancelled" : "timeout",
            error: outerErr.message,
            needs_readback: Boolean(stepsResult.steps && stepsResult.steps.length && spec.actions.some((a) => isWriteOp(a.op))),
            completed_steps: compactStepsFn(stepsResult.steps),
            url: (readyInfo && readyInfo.url) || spec.url,
            title: (readyInfo && readyInfo.title) || "",
            ...(dialogs.length ? { dismissed_dialogs: dialogs } : {}),
            ...(keepOpen ? { target_id: targetId, closed: false } : {}),
          };
        }
        throw outerErr;
      }

      const compactSteps = compactStepsFn(stepsResult.steps);
      const pageMeta = {
        ...(keepOpen ? { target_id: targetId, closed: false } : {}),
        ...(dialogs.length ? { dismissed_dialogs: dialogs } : {}),
        url: (summary && summary.url) || (readyInfo && readyInfo.url) || spec.url,
        title: (summary && summary.title) || (readyInfo && readyInfo.title) || "",
      };
      if (fallbackUrlMismatch) {
        return {
          ok: false,
          reason: "fallback_url_mismatch",
          expected_url: expectedFallbackUrl,
          actual_url: (readyInfo && readyInfo.url) || "",
          needs_login_or_verify: true,
          completed_steps: [],
          ...pageMeta,
        };
      }
      if (stepsResult.ok === false) {
        return {
          ok: false,
          reason: stepsResult.reason || "batch_interrupted",
          error: stepsResult.error,
          failed_index: stepsResult.failed_index,
          ...(stepsResult.actual_url ? { actual_url: stepsResult.actual_url } : {}),
          ...(stepsResult.stopped_before_index !== undefined
            ? { stopped_before_index: stepsResult.stopped_before_index }
            : {}),
          resume_from: stepsResult.stopped_before_index !== undefined ? stepsResult.stopped_before_index : stepsResult.failed_index,
          needs_readback: Boolean(stepsResult.needs_readback),
          completed_steps: compactSteps,
          ...pageMeta,
        };
      }
      if (interruptedByDialog) {
        return {
          ok: false,
          reason: "action_interrupted_by_dialog",
          error: "Interactive browser dialog (confirm/prompt/beforeunload) was auto-dismissed; business action may not have completed",
          needs_readback: true,
          completed_steps: compactSteps,
          ...pageMeta,
        };
      }
      return {
        ok: true,
        ...pageMeta,
        ...(compactSteps.length ? { steps: compactSteps } : {}),
        ...(summary ? { summary_text: summary.text } : {}),
      };
    };

    const pageResults = [];
    const openedTargetIds = [];
    const isWriteMulti = isMulti && pageSpecs.some((sp) => sp.actions.some((a) => isWriteOp(a.op)));
    let stoppedBeforePage;
    let resumeFromPage;
    let abortErr = null;
    let browserClosed = false;
    try {
      for (let pIdx = 0; pIdx < pageSpecs.length; pIdx++) {
        checkCancelled(reqCtx);
        remainingBudget(deadlineMs, `pages[${pIdx}]`);
        const keepThisTab = !closeAfter && !isMulti;
        const pageRes = await runSingleSpec(pageSpecs[pIdx], keepThisTab, openedTargetIds);
        pageResults.push(pageRes);
        if (!pageRes.ok && (pageRes.reason === "cancelled" || pageRes.reason === "timeout" || isWriteMulti)) {
          if (pIdx + 1 < pageSpecs.length) stoppedBeforePage = pIdx + 1;
          resumeFromPage = pIdx;
          break;
        }
      }
    } catch (err) {
      if (err && (err.cancelCode === "CANCELLED" || err.timeoutCode === "TOTAL_TIMEOUT")) {
        abortErr = err;
      } else {
        throw err;
      }
    } finally {
      const poolEntry = pool.get(poolKey);
      if (!visible && closeAfter && poolEntry && !poolEntry.visible) {
        const targetsRes = await c.command("Target.getTargets", {}, undefined, 1500).catch(() => null);
        const closedSet = new Set(openedTargetIds);
        const hasOtherPages = (targetsRes?.targetInfos || []).some(
          (t) => t.type === "page" && !closedSet.has(t.targetId) && String(t.url || "") !== "about:blank"
        );
        if (!hasOtherPages) {
          browserClosed = await closePoolEntry(poolKey);
        } else {
          touchPoolEntry(poolKey);
        }
      } else {
        touchPoolEntry(poolKey);
      }
    }

    const elapsed_ms = Math.round((performance.now() - t0) * 100) / 100;
    const closedFlag = browserClosed;

    if (!isMulti) {
      const single = pageResults[0] || {
        ok: false,
        reason: (abortErr && abortErr.cancelCode === "CANCELLED") ? "cancelled" : "timeout",
        error: (abortErr && abortErr.message) || "Aborted",
        needs_readback: false,
        completed_steps: [],
        url: pageSpecs[0].url,
        title: "",
      };
      return {
        ...single,
        profile,
        browser,
        ...(visible ? { visible: true } : {}),
        ...(closedFlag ? { browser_closed: true } : {}),
        elapsed_ms,
      };
    }

    const failedPage = pageResults.find((p) => !p.ok) || null;
    const topOk = !abortErr && !failedPage;
    return {
      ok: topOk,
      profile,
      browser,
      ...(visible ? { visible: true } : {}),
      ...(closedFlag ? { browser_closed: true } : {}),
      elapsed_ms,
      count: pageResults.length,
      pages: pageResults,
      ...(stoppedBeforePage !== undefined ? { stopped_before_page: stoppedBeforePage } : {}),
      ...(resumeFromPage !== undefined ? { resume_from_page: resumeFromPage } : {}),
      ...(failedPage && isWriteMulti ? { needs_readback: true } : {}),
      ...(abortErr ? {
        reason: abortErr.cancelCode === "CANCELLED" ? "cancelled" : "timeout",
        error: abortErr.message,
      } : (failedPage ? { reason: failedPage.reason, error: failedPage.error } : {})),
    };
  }

  if (name === "open_tab") {
    const safeUrl = validateHttpUrl(args.url);
    const { profile, browser } = resolveScope(args);
    const visible = boolArg(args.visible, "visible", false);
    const background = args.background === undefined ? !visible : boolArg(args.background, "background", !visible);
    const c = await getCdp(profile, browser, visible);
    const res = await c.command("Target.createTarget", { url: safeUrl, background });
    touchPoolEntry(`${profile}:${browser}`);
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
    touchPoolEntry(`${profile}:${browser}`);
    return { ok: true, profile, browser, target_id: target.targetId };
  }

  const c = await getCdp(profile, browser, false);
  const poolKey = `${profile}:${browser}`;
  if (name === "list_tabs") {
    const search = args.search === undefined ? "" : validString(args.search, "search", 256, true).toLocaleLowerCase();
    const url = args.url_contains === undefined ? "" : validString(args.url_contains, "url_contains", 1024, true).toLocaleLowerCase();
    const title = args.title_contains === undefined ? "" : validString(args.title_contains, "title_contains", 256, true).toLocaleLowerCase();
    const limit = intArg(args.limit, "limit", 50, 1, MAX_TABS);
    const result = await c.command("Target.getTargets");
    touchPoolEntry(poolKey);
    const tabs = (result.targetInfos || []).filter((target) => target.type === "page")
      .map((target) => ({ target_id: target.targetId, url: String(target.url || "").slice(0, 2048), title: String(target.title || "").slice(0, 300) }))
      .filter((target) => (!search || `${target.url} ${target.title}`.toLocaleLowerCase().includes(search)) &&
        (!url || target.url.toLocaleLowerCase().includes(url)) && (!title || target.title.toLocaleLowerCase().includes(title)))
      .slice(0, limit);
    return { ok: true, profile, browser, tabs };
  }
  if (name === "inspect_tab") {
    const textLimit = intArg(args.text_limit, "text_limit", 2500, 0, MAX_TEXT);
    const selector = args.selector === undefined ? "" : validString(args.selector, "selector", 1024);
    const textContains = args.text_contains === undefined ? "" : validString(args.text_contains, "text_contains", 256);
    const controlType = args.control_type === undefined ? "" : validString(args.control_type, "control_type", 64);
    const maxCandidates = intArg(args.max_candidates, "max_candidates", MAX_CANDIDATES, 1, 50);
    const { sessionId } = await attach(c, args.target_id);
    try {
      return await evaluate(c, sessionId, INSPECT_SCRIPT, {
        text_limit: textLimit,
        max_candidates: maxCandidates,
        selector,
        text_contains: textContains,
        control_type: controlType,
      });
    } finally {
      await c.command("Target.detachFromTarget", { sessionId }).catch(() => {});
      touchPoolEntry(poolKey);
    }
  }
  if (name === "run_actions") {
    const actions = validateActions(args.actions, false);
    const totalTimeoutMs = args.total_timeout_ms === undefined
      ? 35000
      : intArg(args.total_timeout_ms, "total_timeout_ms", 35000, 1000, 120000);
    const expectedUrlNorm = args.expected_url === undefined ? "" : normUrlFull(validateHttpUrl(args.expected_url));
    const deadlineMs = (reqCtx && typeof reqCtx.deadlineMs === "number")
      ? reqCtx.deadlineMs
      : (Date.now() + totalTimeoutMs);
    const { sessionId } = await attach(c, args.target_id);
    try {
      const { value: stepsResult, dialogs, interruptedByDialog } = await withDialogGuard(
        c,
        sessionId,
        (guardState) => runActionsOnSession(c, sessionId, actions, { deadlineMs, reqCtx, guardState, expectedUrlNorm })
      );
      if (stepsResult.ok === false) {
        return {
          ok: false,
          reason: stepsResult.reason || "batch_interrupted",
          error: stepsResult.error,
          failed_index: stepsResult.failed_index,
          ...(stepsResult.actual_url ? { actual_url: stepsResult.actual_url } : {}),
          ...(stepsResult.stopped_before_index !== undefined ? { stopped_before_index: stepsResult.stopped_before_index } : {}),
          resume_from: stepsResult.stopped_before_index !== undefined ? stepsResult.stopped_before_index : stepsResult.failed_index,
          needs_readback: Boolean(stepsResult.needs_readback),
          completed_steps: stepsResult.steps || [],
          ...(dialogs.length ? { dismissed_dialogs: dialogs } : {}),
        };
      }
      if (interruptedByDialog) {
        return {
          ok: false,
          reason: "action_interrupted_by_dialog",
          error: "Interactive browser dialog (confirm/prompt/beforeunload) was auto-dismissed; business action may not have completed",
          needs_readback: true,
          dismissed_dialogs: dialogs,
          completed_steps: stepsResult.steps || [],
        };
      }
      return { ok: true, steps: stepsResult.steps || [], ...(dialogs.length ? { dismissed_dialogs: dialogs } : {}) };
    } finally {
      await c.command("Target.detachFromTarget", { sessionId }).catch(() => {});
      touchPoolEntry(poolKey);
    }
  }
  throw new BridgeError(`unknown tool: ${name}`);
}

function toolResult(value, isError = false) {
  const text = JSON.stringify(value);
  return { content: [{ type: "text", text }], structuredContent: value, isError };
}

function dailyBrowserPython() {
  if (process.env.AUTO_COMPUTER_PYTHON) return process.env.AUTO_COMPUTER_PYTHON;
  try {
    const runtimePath = path.join(DEFAULT_HOME, "runtime.json");
    const runtime = JSON.parse(fs.readFileSync(runtimePath, "utf8"));
    if (typeof runtime.python === "string" && runtime.python.trim()) return runtime.python;
  } catch {}
  const fallback = path.join(DEFAULT_HOME, ".venv", "Scripts", "python.exe");
  if (fs.existsSync(fallback)) return fallback;
  throw new BridgeError("Daily browser automation needs AUTO_COMPUTER_PYTHON or runtime.json python");
}

function resolveDailyHelper() {
  const envHelper = process.env.AUTO_COMPUTER_DAILY_HELPER;
  const candidates = [
    ...(envHelper ? [envHelper] : []),
    path.join(__dirname, "..", "skill", "auto-computer", "scripts", "browser_task.py"),
    path.join(__dirname, "..", "skills", "auto-computer", "scripts", "browser_task.py"),
    path.join(DEFAULT_HOME, "skills", "auto-computer", "scripts", "browser_task.py"),
  ];
  return candidates.find((p) => fs.existsSync(p)) || null;
}

function bestEffortDetachDaily(helperPath, browser, profile) {
  const sessionCandidates = [
    ...(helperPath ? [path.join(path.dirname(helperPath), "browser_session.py")] : []),
    path.join(__dirname, "..", "skill", "auto-computer", "scripts", "browser_session.py"),
    path.join(DEFAULT_HOME, "skills", "auto-computer", "scripts", "browser_session.py"),
  ];
  const sessionScript = sessionCandidates.find((p) => fs.existsSync(p));
  if (!sessionScript) return Promise.resolve({ attempted: false, ok: false });
  return new Promise((resolve) => {
    let python;
    try { python = dailyBrowserPython(); } catch { resolve({ attempted: false, ok: false }); return; }
    const child = spawn(
      python,
      ["-X", "utf8", sessionScript, "detach", "--browser", browser, "--profile", profile],
      { stdio: ["ignore", "ignore", "ignore"], windowsHide: true }
    );
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      clearTimeout(t);
      resolve({ attempted: true, ok });
    };
    const t = setTimeout(() => { killProcessTree(child); finish(false); }, 2500);
    child.once("error", () => finish(false));
    child.once("exit", (code) => finish(code === 0));
  });
}

function runDailyBrowser(args, reqCtx = null, timeoutMs = 60000) {
  const helper = resolveDailyHelper();
  if (!helper) throw new BridgeError("Daily browser helper browser_task.py not found");
  const hasWrite = Array.isArray(args.actions) && args.actions.some((a) => isWriteOp(a && (a.op || a.action || a.type)));
  const browserName = args.browser || "chrome";
  const profileName = args.profile || "Default";
  if (reqCtx && reqCtx.cancelled) {
    return Promise.resolve({
      ok: false,
      available: false,
      reason: "cancelled",
      error: reqCtx.reason || "Cancelled by client",
      needs_readback: false,
    });
  }
  return new Promise((resolve, reject) => {
    const python = dailyBrowserPython();
    const child = spawn(python, ["-X", "utf8", helper], { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let cancelling = false;
    let timedOut = false;
    let onCancel = null;
    const finish = (fn, val) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (onCancel && reqCtx && reqCtx.waiters) reqCtx.waiters.delete(onCancel);
      fn(val);
    };
    onCancel = () => {
      if (settled || cancelling || timedOut) return;
      cancelling = true;
      killProcessTree(child);
      bestEffortDetachDaily(helper, browserName, profileName).then((detach_cleanup) => {
        finish(resolve, {
          ok: false,
          reason: "cancelled",
          error: (reqCtx && reqCtx.reason) || "Cancelled by client",
          needs_readback: hasWrite,
          detach_cleanup,
        });
      });
    };
    if (reqCtx && reqCtx.waiters) reqCtx.waiters.add(onCancel);
    const timer = setTimeout(() => {
      if (settled || cancelling || timedOut) return;
      timedOut = true;
      killProcessTree(child);
      bestEffortDetachDaily(helper, browserName, profileName).then((detach_cleanup) => {
        finish(resolve, {
          ok: false,
          reason: "timeout",
          error: `Daily browser helper timed out after ${timeoutMs}ms`,
          needs_readback: hasWrite,
          detach_cleanup,
        });
      });
    }, timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      if (stderr.length < 1000) stderr = (stderr + chunk).slice(0, 1000);
    });
    child.once("error", (error) => {
      if (cancelling || timedOut) return;
      finish(reject, new BridgeError(`Daily browser helper could not start: ${error.message}`));
    });
    child.stdin.once("error", () => {
      if (!settled && !cancelling && !timedOut) {
        finish(reject, new BridgeError("Daily browser helper closed its input"));
      }
    });
    child.once("close", (code) => {
      if (settled || cancelling || timedOut) return;
      if (reqCtx && reqCtx.cancelled) return;
      try {
        finish(resolve, JSON.parse(stdout.trim()));
      } catch {
        const detail = stderr.trim() || `exit=${code}`;
        finish(reject, new BridgeError(`Daily browser helper returned invalid JSON (${detail})`));
      }
    });
    child.stdin.end(JSON.stringify(args));
  });
}

async function handle(req, reqCtx = null) {
  const method = req.method;
  const id = req.id;
  if (method === "notifications/initialized" || (typeof method === "string" && method.startsWith("notifications/"))) return null;
  if (method === "initialize") {
    const version = req.params && req.params.protocolVersion || "2024-11-05";
    return { jsonrpc: "2.0", id, result: { protocolVersion: version, capabilities: { tools: { listChanged: false } }, serverInfo: { name: "antigravity-edge-bridge", version: "0.3.0" } } };
  }
  if (method === "ping") return { jsonrpc: "2.0", id, result: {} };
  if (method === "tools/list") return { jsonrpc: "2.0", id, result: { tools: TOOLS } };
  if (method === "tools/call") {
    try {
      const params = req.params || {};
      const result = await callTool(params.name, params.arguments || {}, reqCtx);
      return { jsonrpc: "2.0", id, result: toolResult(result, result && result.ok === false) };
    } catch (error) {
      if (error && error.structured) {
        return { jsonrpc: "2.0", id, result: toolResult(error.structured, true) };
      }
      return { jsonrpc: "2.0", id, result: toolResult({ ok: false, error: error.message || String(error) }, true) };
    }
  }
  if (id === undefined || id === null) return null;
  return { jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } };
}

function scopeKeyForRequest(request) {
  if (!request || request.method !== "tools/call") return null;
  const params = request.params || {};
  const name = params.name;
  const args = params.arguments && typeof params.arguments === "object" ? params.arguments : {};
  if (name === "daily_browser_run") {
    return `daily:${args.browser || "chrome"}:${args.profile || "Default"}`;
  }
  if (name === "stop_auto_browser") {
    const prof = args.profile === undefined ? "all" : args.profile;
    const br = args.browser === undefined ? "all" : args.browser;
    if (prof !== "all" && br !== "all") return `${prof}:${br}`;
    return "__all_scopes__";
  }
  const prof = args.profile === "kept" ? "kept" : "auto";
  const br = args.browser === "chrome" ? "chrome" : "edge";
  return `${prof}:${br}`;
}

const scopeQueues = new Map();
function queueAbortError(kind, reqCtx) {
  if (kind === "cancelled") {
    const err = new BridgeError((reqCtx && reqCtx.reason) || "Cancelled by client");
    err.cancelCode = "CANCELLED";
    return err;
  }
  const err = new BridgeError("Total timeout exceeded while waiting in browser queue");
  err.timeoutCode = "TOTAL_TIMEOUT";
  return err;
}

function enqueueScope(key, fn, opts = {}) {
  const reqCtx = opts && opts.reqCtx ? opts.reqCtx : null;
  const deadlineMs = opts && typeof opts.deadlineMs === "number" ? opts.deadlineMs : null;
  const onAbort = opts && typeof opts.onAbort === "function" ? opts.onAbort : null;

  if (!key) {
    if (reqCtx && reqCtx.cancelled) {
      return onAbort ? Promise.resolve(onAbort("cancelled")) : Promise.reject(queueAbortError("cancelled", reqCtx));
    }
    if (deadlineMs !== null && Date.now() >= deadlineMs) {
      return onAbort ? Promise.resolve(onAbort("timeout")) : Promise.reject(queueAbortError("timeout", reqCtx));
    }
    return Promise.resolve().then(fn);
  }

  const isAllScopes = key === "__all_scopes__";
  const prev = isAllScopes
    ? Promise.allSettled(Array.from(scopeQueues.values()))
    : (scopeQueues.get(key) || Promise.resolve());

  return new Promise((resolve, reject) => {
    let state = "waiting";
    let timer = null;
    let cancelWaiter = null;

    const cleanupWait = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (cancelWaiter && reqCtx && reqCtx.waiters) {
        reqCtx.waiters.delete(cancelWaiter);
        cancelWaiter = null;
      }
    };

    const triggerAbort = (kind) => {
      if (state !== "waiting") return;
      state = "aborted";
      cleanupWait();
      if (onAbort) {
        try {
          resolve(onAbort(kind));
        } catch (err) {
          reject(err);
        }
      } else {
        reject(queueAbortError(kind, reqCtx));
      }
    };

    if (reqCtx && reqCtx.cancelled) {
      triggerAbort("cancelled");
      return;
    }
    if (deadlineMs !== null && Date.now() >= deadlineMs) {
      triggerAbort("timeout");
      return;
    }

    if (reqCtx && reqCtx.waiters) {
      cancelWaiter = () => {
        if (reqCtx.cancelled) triggerAbort("cancelled");
      };
      reqCtx.waiters.add(cancelWaiter);
    }

    if (deadlineMs !== null) {
      const waitMs = Math.max(1, deadlineMs - Date.now());
      timer = setTimeout(() => {
        triggerAbort("timeout");
      }, waitMs);
    }

    const runSlot = () => {
      cleanupWait();
      if (state !== "waiting") return;
      if (reqCtx && reqCtx.cancelled) {
        triggerAbort("cancelled");
        return;
      }
      if (deadlineMs !== null && Date.now() >= deadlineMs) {
        triggerAbort("timeout");
        return;
      }
      state = "running";
      return Promise.resolve().then(fn).then(resolve, reject);
    };

    const slotPromise = prev.then(runSlot, runSlot);
    if (!isAllScopes) {
      const tracked = slotPromise.finally(() => {
        if (scopeQueues.get(key) === tracked) scopeQueues.delete(key);
      });
      scopeQueues.set(key, tracked);
    }
  });
}

function requestQueueDeadline(request, receivedAt) {
  if (!request || request.method !== "tools/call") return null;
  const params = request.params || {};
  const name = params.name;
  const args = params.arguments && typeof params.arguments === "object" && !Array.isArray(params.arguments) ? params.arguments : {};
  const raw = args.total_timeout_ms;
  if (name === "daily_browser_run") {
    const ms = raw === undefined ? 60000 : (Number.isInteger(raw) && raw >= 500 && raw <= 120000 ? raw : null);
    return ms === null ? null : receivedAt + ms;
  }
  if (name === "open_and_run" || name === "run_actions") {
    const ms = raw === undefined ? 35000 : (Number.isInteger(raw) && raw >= 1000 && raw <= 120000 ? raw : null);
    return ms === null ? null : receivedAt + ms;
  }
  return null;
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity, terminal: false });
input.on("line", (line) => {
  if (!line.trim()) return;
  let request;
  try {
    request = JSON.parse(line);
    if (!request || request.jsonrpc !== "2.0" || typeof request.method !== "string") {
      throw new Error("request must be a JSON-RPC 2.0 object");
    }
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32700, message: error.message || String(error) } })}\n`);
    return;
  }
  if (request.method === "notifications/cancelled") {
    const reqId = request.params && request.params.requestId;
    if (reqId !== undefined && reqId !== null) {
      const ctx = activeRequests.get(String(reqId));
      if (ctx) {
        ctx.cancelled = true;
        ctx.reason = String((request.params && request.params.reason) || "Cancelled by client");
        for (const wake of Array.from(ctx.waiters)) {
          try { wake(); } catch {}
        }
      }
    }
    return;
  }
  const reqKey = request.id !== undefined && request.id !== null ? String(request.id) : null;
  const receivedAt = Date.now();
  const queueDeadlineMs = requestQueueDeadline(request, receivedAt);
  const reqCtx = reqKey ? { cancelled: false, reason: "", waiters: new Set(), deadlineMs: queueDeadlineMs } : null;
  if (reqKey && reqCtx) activeRequests.set(reqKey, reqCtx);
  const scopeKey = scopeKeyForRequest(request);
  enqueueScope(scopeKey, async () => {
    let response;
    try {
      response = await handle(request, reqCtx);
    } catch (error) {
      response = { jsonrpc: "2.0", id: request.id ?? null, error: { code: -32603, message: error.message || String(error) } };
    } finally {
      if (reqKey) activeRequests.delete(reqKey);
    }
    if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
  }, {
    reqCtx,
    deadlineMs: queueDeadlineMs,
    onAbort: (kind) => {
      if (reqKey) activeRequests.delete(reqKey);
      if (request.id === undefined || request.id === null) return;
      const errMsg = kind === "cancelled"
        ? ((reqCtx && reqCtx.reason) || "Cancelled by client")
        : "Total timeout exceeded while waiting in browser queue";
      const result = {
        ok: false,
        reason: kind === "cancelled" ? "cancelled" : "timeout",
        error: errMsg,
        needs_readback: false,
      };
      const response = { jsonrpc: "2.0", id: request.id, result: toolResult(result, true) };
      process.stdout.write(`${JSON.stringify(response)}\n`);
    },
  });
});

async function shutdownManaged() {
  for (const key of Array.from(pool.keys())) {
    await closePoolEntry(key);
  }
}

input.on("close", () => {
  Promise.allSettled(Array.from(scopeQueues.values())).finally(() => shutdownManaged());
});

process.on("exit", () => {
  for (const entry of pool.values()) {
    if (entry && entry.lockPath) releaseKeptLock(entry.lockPath);
  }
});
