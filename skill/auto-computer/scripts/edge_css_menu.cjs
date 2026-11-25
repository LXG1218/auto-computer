#!/usr/bin/env node
'use strict';

// Temporarily map explicitly named :hover rules to marked elements in an
// already attached Playwright CLI session. This never dispatches input events.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { parseArgs } = require('node:util');

function validateRequest(request) {
  if (!request || typeof request.url !== 'string' || !Array.isArray(request.hoverSelectors) ||
      !Array.isArray(request.ruleSelectors) || typeof request.itemSelector !== 'string' ||
      !request.hoverSelectors.length || !request.ruleSelectors.length || !request.itemSelector) {
    throw new Error('Request requires url, non-empty hoverSelectors, non-empty ruleSelectors, and itemSelector');
  }
  for (const key of ['hoverSelectors', 'ruleSelectors']) {
    if (request[key].some(value => typeof value !== 'string' || !value.trim()) ||
        new Set(request[key]).size !== request[key].length) {
      throw new Error(`${key} must contain unique non-empty strings`);
    }
  }
}

function buildHoverCore(request) {
  validateRequest(request);
  // Use a base64 JSON literal so Windows command quoting cannot alter the request.
  const encoded = Buffer.from(JSON.stringify(request), 'utf8').toString('base64');
  return `async (page) => {
  const req = JSON.parse(Buffer.from('${encoded}', 'base64').toString('utf8'));
  const matches = page.context().pages().filter(candidate => candidate.url() === req.url);
  if (matches.length !== 1) return { ok: false, error: matches.length ? 'Target URL is ambiguous in attached session' : 'Target URL not found in attached session', matchedPages: matches.length };
  const target = matches[0];
  return await target.evaluate((req) => {
    const result = { ok: false, url: location.href, visibility: document.visibilityState, items: [], changedRules: 0 };
    if (location.href !== req.url) return { ...result, error: 'URL mismatch' };
    let triggers;
    let itemNodes;
    try {
      triggers = req.hoverSelectors.map(selector => {
        const nodes = document.querySelectorAll(selector);
        if (nodes.length !== 1) throw new Error('Trigger selector must match exactly one element: ' + selector);
        return nodes[0];
      });
      itemNodes = [...document.querySelectorAll(req.itemSelector)];
    } catch (error) { return { ...result, error: String(error.message || error) }; }
    if (!itemNodes.length) return { ...result, error: 'itemSelector matched no elements' };

    const marker = 'data-ac-hover-' + Math.random().toString(36).slice(2);
    const rules = [];
    const inaccessible = [];
    const walk = list => {
      for (const rule of list) {
        if (typeof rule.selectorText === 'string' && req.ruleSelectors.includes(rule.selectorText)) rules.push(rule);
        if (rule.cssRules) {
          try { walk(rule.cssRules); } catch { /* inaccessible grouped rule */ }
        }
      }
    };
    for (const sheet of [...document.styleSheets, ...document.adoptedStyleSheets]) {
      try { walk(sheet.cssRules); } catch { inaccessible.push(sheet.href || '<inline stylesheet>'); }
    }
    const found = new Set(rules.map(rule => rule.selectorText));
    const missing = req.ruleSelectors.filter(selector => !found.has(selector));
    if (missing.length) return { ...result, error: 'CSS rules unavailable or selectorText mismatch', missingRules: missing, inaccessibleStylesheets: inaccessible };

    const rewrite = selector => {
      let out = '', quote = '', count = 0;
      for (let i = 0; i < selector.length;) {
        const ch = selector[i];
        if (quote) {
          out += ch;
          if (ch === '\\\\') { if (i + 1 < selector.length) out += selector[++i]; }
          else if (ch === quote) quote = '';
          i++; continue;
        }
        if (ch === '"' || ch === "'") { quote = ch; out += ch; i++; continue; }
        if (ch === '\\\\') throw new Error('Escaped selector is unsupported: ' + selector);
        if (selector.slice(i, i + 6).toLowerCase() === ':hover' && !/[\\\\\\w\\u0080-\\uFFFF-]/.test(selector[i + 6] || '')) {
          out += ':is(:hover,[' + marker + '])'; count++; i += 6; continue;
        }
        out += ch; i++;
      }
      if (quote || count === 0) throw new Error('Unsupported selector or no literal :hover token: ' + selector);
      return out;
    };
    const originals = rules.map(rule => ({ rule, selector: rule.selectorText }));
    const updates = [];
    try {
      for (const original of originals) updates.push({ ...original, rewritten: rewrite(original.selector) });
    } catch (error) {
      return { ...result, error: String(error.message || error) };
    }
    const visible = node => {
      const style = getComputedStyle(node);
      return node.getClientRects().length > 0 && style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse';
    };
    const initialVisibility = itemNodes.map(visible);
    result.visibleBefore = initialVisibility.filter(Boolean).length;
    const marked = [];
    try {
      for (const { rule, selector, rewritten } of updates) {
        rule.selectorText = rewritten;
        if (rule.selectorText === selector) throw new Error('Browser rejected the temporary CSS selector: ' + selector);
        result.changedRules++;
      }
      for (const trigger of triggers) {
        if (!trigger.isConnected || trigger.hasAttribute(marker)) throw new Error('Trigger is disconnected or duplicated');
        trigger.setAttribute(marker, ''); marked.push(trigger);
      }
      result.items = itemNodes.filter((node, index) => !initialVisibility[index] && visible(node))
        .map(node => (node.innerText || node.textContent || '').trim().slice(0, 240)).filter(Boolean).slice(0, 100);
      result.ok = result.items.length > 0;
      if (!result.ok) result.error = 'No newly visible labels under requested triggers';
    } catch (error) {
      result.error = String(error.message || error);
    } finally {
      for (const node of marked) node.removeAttribute(marker);
      for (const entry of originals) {
        try { entry.rule.selectorText = entry.selector; } catch (error) { result.restoreError = String(error.message || error); }
      }
      result.restored = originals.every(entry => entry.rule.selectorText === entry.selector) && marked.every(node => !node.hasAttribute(marker));
      const finalVisibility = itemNodes.map(visible);
      result.visibleAfter = finalVisibility.filter(Boolean).length;
      if (!result.restored || finalVisibility.some((shown, index) => shown !== initialVisibility[index])) {
        result.ok = false;
        result.restoreError ||= 'Temporary CSS, marker or menu visibility restoration failed';
      }
    }
    return result;
  }, req);
}`;
}

function buildHoverSnippet(request, postSnippetCode = null) {
  const core = buildHoverCore(request);
  if (!postSnippetCode || !postSnippetCode.trim()) {
    return core;
  }
  const postTrimmed = postSnippetCode.trim();
  return `async (page) => {
  const hover = await (${core})(page);
  const post = await (${postTrimmed})(page);
  return { hover, post };
}`;
}

function fail(message) {
  console.error(message);
  process.exit(2);
}

function main() {
  const { values } = parseArgs({
    options: {
      cli: { type: 'string' },
      session: { type: 'string' },
      request: { type: 'string' },
      'request-file': { type: 'string' },
      'emit-snippet': { type: 'boolean', default: false },
      'post-snippet': { type: 'string' },
    },
    strict: true,
  });

  if (Boolean(values.request) === Boolean(values['request-file'])) {
    fail('Usage: node edge_css_menu.cjs (--emit-snippet | --cli <playwright-cli.js> --session <session>) (--request <JSON> | --request-file <JSON path>) [--post-snippet <js file>]');
  }
  if (!values['emit-snippet'] && (!values.cli || !values.session)) {
    fail('Running via Playwright CLI requires --cli and --session (or pass --emit-snippet)');
  }

  let request;
  try {
    const json = values['request-file'] ? fs.readFileSync(values['request-file'], 'utf8') : values.request;
    request = JSON.parse(json.replace(/^\uFEFF/, ''));
  } catch (error) {
    fail('Cannot read request JSON: ' + error.message);
  }

  let postCode = null;
  if (values['post-snippet']) {
    try {
      postCode = fs.readFileSync(values['post-snippet'], 'utf8').replace(/^\uFEFF/, '');
    } catch (error) {
      fail('Cannot read --post-snippet file: ' + error.message);
    }
  }

  let snippet;
  try {
    snippet = buildHoverSnippet(request, postCode);
  } catch (error) {
    fail(error.message);
  }

  if (values['emit-snippet']) {
    process.stdout.write(snippet);
    return;
  }

  const temp = path.join(os.tmpdir(), `ac-hover-${process.pid}-${Date.now()}.js`);
  try {
    fs.writeFileSync(temp, snippet, { encoding: 'utf8', flag: 'wx' });
    const cli = path.resolve(values.cli);
    const run = spawnSync(process.execPath, [cli, `-s=${values.session}`, 'run-code', `--filename=${temp}`, '--json'], {
      encoding: 'utf8', windowsHide: true, timeout: 30000, maxBuffer: 2 * 1024 * 1024,
    });
    if (run.error) throw run.error;
    process.stdout.write(run.stdout || '');
    if (run.stderr) process.stderr.write(run.stderr);
    process.exitCode = run.status ?? 1;
  } catch (error) {
    console.error(`Playwright CLI failed: ${error.message}`);
    process.exitCode = 2;
  } finally {
    try { fs.unlinkSync(temp); } catch {}
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  buildHoverSnippet,
  validateRequest,
};
