// Node-env component driver (no DOM available in this repo): expands hook-free function components into a
// tree of host elements so a test can find a control by role/label/text and call its handler with a fake
// event. Components that use hooks (they throw outside a render) stay as opaque elements; those are covered
// by renderToStaticMarkup plus the pure helpers they call. This is NOT a browser: focus, layout and real key
// events are not exercised.
import * as React from "react";

export interface Node { type: string; props: Record<string, any>; children: Node[]; text?: string }

const textNode = (text: string): Node => ({ type: "#text", props: {}, children: [], text });

export function expand(el: unknown, path = ""): Node[] {
  if (el === null || el === undefined || typeof el === "boolean") return [];
  if (typeof el === "string" || typeof el === "number") return [textNode(String(el))];
  if (Array.isArray(el)) return el.flatMap((c, i) => expand(c, `${path}[${i}]`));
  if (!React.isValidElement(el)) return [];
  const { type } = el;
  const props = el.props as Record<string, any>;
  const here = `${path}/${typeof type === "function" ? (type as { name?: string }).name || "C" : String(type)}${el.key ? `:${el.key}` : ""}`;
  if (typeof type === "function") {
    try {
      runtime.path = here;
      runtime.counters.delete(here);
      return expand((type as (p: unknown) => unknown)(props), here);
    } catch {
      return [{ type: `opaque:${(type as { name?: string }).name ?? "component"}`, props, children: [] }];
    }
  }
  if (typeof type === "symbol") return expand(props.children, here); // Fragment
  return [{ type: String(type), props, children: expand(props.children, here) }];
}

// ---- optional hook runtime -------------------------------------------------------------------
// A test file that needs hooked components (useState / useRef / useId ...) mocks "react" with `fakeHooks`:
//   vi.mock("react", async (orig) => ({ ...(await orig<typeof import("react")>()), ...(await import("<path>/elementTree")).fakeHooks }));
// State lives in `runtime.state`, keyed by the component's position in the tree, so it survives
// `expand(...)` being called again after an event handler ran (that is this driver's "re-render").
export const runtime = { path: "", counters: new Map<string, number>(), state: new Map<string, unknown>() };
const slot = (kind: string) => { const n = (runtime.counters.get(runtime.path) ?? 0) + 1; runtime.counters.set(runtime.path, n); return `${runtime.path}#${kind}${n}`; };
export const fakeHooks = {
  useState<T>(init: T | (() => T)) {
    const key = slot("s");
    if (!runtime.state.has(key)) runtime.state.set(key, typeof init === "function" ? (init as () => T)() : init);
    return [runtime.state.get(key) as T, (v: T | ((p: T) => T)) => runtime.state.set(key, typeof v === "function" ? (v as (p: T) => T)(runtime.state.get(key) as T) : v)] as const;
  },
  useRef<T>(init: T) {
    const key = slot("r");
    if (!runtime.state.has(key)) runtime.state.set(key, { current: init });
    return runtime.state.get(key) as { current: T };
  },
  useId: () => `id${runtime.path.replace(/[^A-Za-z0-9]/g, "")}`,
  useEffect: () => undefined,
  useLayoutEffect: () => undefined,
  useMemo: <T>(fn: () => T) => fn(),
  useCallback: <T>(fn: T) => fn,
  useContext: <T>(ctx: { _currentValue?: T }) => ctx._currentValue as T,
};
export const resetRuntime = () => { runtime.state.clear(); runtime.counters.clear(); runtime.path = ""; };

export const textOf = (n: Node): string => (n.type === "#text" ? (n.text ?? "") : n.children.map(textOf).join(""));

export function findAll(nodes: Node[], pred: (n: Node) => boolean): Node[] {
  const out: Node[] = [];
  const walk = (n: Node) => { if (pred(n)) out.push(n); n.children.forEach(walk); };
  nodes.forEach(walk);
  return out;
}

/** A <button> whose visible text (or aria-label) matches. */
export const button = (nodes: Node[], label: RegExp | string) => {
  const hit = findAll(nodes, (n) => n.type === "button" && (matches(label, textOf(n)) || matches(label, n.props["aria-label"] ?? "")));
  if (hit.length === 0) throw new Error(`no button matching ${String(label)}`);
  return hit[0];
};
export const hasButton = (nodes: Node[], label: RegExp | string) =>
  findAll(nodes, (n) => n.type === "button" && (matches(label, textOf(n)) || matches(label, n.props["aria-label"] ?? ""))).length > 0;
const matches = (l: RegExp | string, s: string) => (typeof l === "string" ? s.includes(l) : l.test(s));

/** Fire a handler prop with a fake event; returns what preventDefault / focus saw. */
export function press(n: Node, prop: "onClick" | "onKeyDown" | "onSubmit" | "onChange" | "onScroll" = "onClick", ev: Record<string, unknown> = {}) {
  const calls = { prevented: false };
  const fake = { preventDefault: () => { calls.prevented = true; }, currentTarget: n, target: n, ...ev };
  n.props[prop]?.(fake);
  return calls;
}
export const disabled = (n: Node) => !!n.props.disabled;
