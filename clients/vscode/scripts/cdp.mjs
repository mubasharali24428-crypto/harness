/**
 * A small Chrome-DevTools-Protocol driver for a VS Code window (W24).
 *
 * VS Code is an Electron app: started with `--remote-debugging-port`, its workbench is a page that
 * accepts real input events and can be read back. This sends key events *into that window* (never
 * to the operating system), so it cannot type into anything else on the machine, and it needs no
 * dependency beyond Node's built-in `WebSocket` and `fetch`.
 */

import { writeFileSync } from "node:fs";

const MODIFIERS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };

/** Keys that are not printable characters: name -> [key, code, windowsVirtualKeyCode]. */
const NAMED = {
  Enter: ["Enter", "Enter", 13],
  Escape: ["Escape", "Escape", 27],
  ArrowDown: ["ArrowDown", "ArrowDown", 40],
  ArrowUp: ["ArrowUp", "ArrowUp", 38],
  Tab: ["Tab", "Tab", 9],
  F1: ["F1", "F1", 112],
};

export async function connect(port) {
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((t) => t.type === "page" && t.url.includes("workbench"));
  if (!page) throw new Error(`no VS Code workbench on port ${port}`);
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let nextId = 0;
  const waiting = new Map();
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    const slot = message.id && waiting.get(message.id);
    if (!slot) return;
    waiting.delete(message.id);
    if (message.error) slot.reject(new Error(message.error.message));
    else slot.resolve(message.result);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    waiting.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });

  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(`page error: ${JSON.stringify(result.exceptionDetails).slice(0, 300)}`);
    return result.result.value;
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  /** One key press, e.g. press("Enter"), press("y", { alt: true }), press("F1"). */
  async function press(name, mods = {}) {
    const modifiers = Object.entries(mods).reduce((sum, [k, on]) => sum + (on ? MODIFIERS[k] : 0), 0);
    let key, code, keyCode, text;
    if (NAMED[name]) {
      [key, code, keyCode] = NAMED[name];
      text = name === "Enter" ? "\r" : undefined;
    } else if (name.length === 1) {
      key = name;
      code = /[a-z]/i.test(name) ? `Key${name.toUpperCase()}` : name;
      keyCode = name.toUpperCase().charCodeAt(0);
      text = modifiers & (MODIFIERS.alt | MODIFIERS.ctrl | MODIFIERS.meta) ? undefined : name;
    } else {
      throw new Error(`unknown key ${name}`);
    }
    const base = { modifiers, key, code, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode };
    await send("Input.dispatchKeyEvent", { ...base, type: text ? "keyDown" : "rawKeyDown", ...(text ? { text } : {}) });
    await send("Input.dispatchKeyEvent", { ...base, type: "keyUp" });
    await sleep(60);
  }

  /** Text into whatever has focus (an input box, the editor). */
  async function type(text) {
    await send("Input.insertText", { text });
    await sleep(60);
  }

  async function screenshot(path) {
    const shot = await send("Page.captureScreenshot", { format: "png" });
    const bytes = Buffer.from(shot.data, "base64");
    writeFileSync(path, bytes);
    return bytes.length;
  }

  /** Everything a person could read in the window that this driver decides on. */
  const ui = () => evaluate(`(() => {
    const text = (el) => (el?.innerText ?? '').trim();
    const shown = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).display !== 'none';
    const quick = document.querySelector('.quick-input-widget');
    const dialog = document.querySelector('.monaco-dialog-box');
    return {
      quickInput: shown(quick) ? {
        title: text(quick.querySelector('.quick-input-title')),
        value: quick.querySelector('.quick-input-box input')?.value ?? '',
        placeholder: quick.querySelector('.quick-input-box input')?.placeholder ?? '',
        message: text(quick.querySelector('.quick-input-message')),
        items: [...quick.querySelectorAll('.quick-input-list .monaco-list-row')].map((row) => text(row).replace(/\\s+/g, ' ')),
      } : null,
      dialog: shown(dialog) ? {
        message: text(dialog.querySelector('.dialog-message-text')),
        detail: text(dialog.querySelector('.dialog-message-detail')),
        buttons: [...dialog.querySelectorAll('.dialog-buttons .monaco-button')].map(text),
      } : null,
      toasts: [...document.querySelectorAll('.notifications-toasts .notification-toast')].filter(shown).map((el) => ({
        message: text(el.querySelector('.notification-list-item-message')),
        severity: el.querySelector('.codicon-error') ? 'error' : el.querySelector('.codicon-warning') ? 'warning' : 'info',
        buttons: [...el.querySelectorAll('.notification-list-item-buttons-container .monaco-button')].map(text),
      })),
      status: [...document.querySelectorAll('.statusbar-item')].map(text).filter(Boolean),
      tabs: [...document.querySelectorAll('.tabs-container .tab')].map((el) => el.getAttribute('aria-label') || text(el)),
    };
  })()`);

  /** Poll the window until `test(state)` is truthy; the error carries the last state. */
  async function waitFor(label, test, { timeoutMs = 30000, everyMs = 250 } = {}) {
    const started = Date.now();
    let last;
    while (Date.now() - started < timeoutMs) {
      last = await ui();
      const found = test(last);
      if (found) return { state: last, found };
      await sleep(everyMs);
    }
    throw new Error(`timed out after ${timeoutMs} ms waiting for ${label}; last state: ${JSON.stringify(last).slice(0, 700)}`);
  }

  /** Click a button by its label inside `scope` (a CSS selector): the fallback when a key does not take. */
  async function clickButton(scope, label) {
    const clicked = await evaluate(`(() => {
      const wanted = ${JSON.stringify(label)};
      const button = [...document.querySelectorAll(${JSON.stringify(`${scope} .monaco-button`)})]
        .find((el) => (el.innerText ?? '').trim() === wanted);
      if (!button) return false;
      button.click();
      return true;
    })()`);
    if (!clicked) throw new Error(`no "${label}" button inside ${scope}`);
  }
  const clickDialogButton = (label) => clickButton(".monaco-dialog-box .dialog-buttons", label);
  const clickToastButton = (label) => clickButton(".notifications-toasts .notification-toast", label);

  /** Command palette: run a command by its title. */
  async function runCommand(title) {
    await press("F1");
    await waitFor("the command palette", (s) => s.quickInput, { timeoutMs: 8000 });
    await type(title); // F1 opens the palette with ">" already in the box
    await sleep(400);
    await press("Enter");
  }

  return {
    send, evaluate, press, type, screenshot, ui, waitFor, runCommand, sleep,
    clickDialogButton, clickToastButton, close: () => ws.close(),
  };
}
