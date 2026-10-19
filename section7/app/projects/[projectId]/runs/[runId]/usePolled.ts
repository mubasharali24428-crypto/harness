"use client";

// Tiny shared hooks for the part 41/42/45 panels.
import { useCallback, useEffect, useRef, useState } from "react";
import { friendlyError } from "./controlApi";

/**
 * Loads on mount, whenever `tick` changes (the shared panel bumps it on every new collab event) and every
 * `everyMs`. A failed poll keeps the last good data and stays silent: the shared panel already reports
 * connectivity, and these side panels must not add a second alert.
 */
export function usePolled<T>(load: () => Promise<T>, tick: number, everyMs = 8000) {
  const [data, setData] = useState<T | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const reload = useCallback(async () => {
    try { setData(await loadRef.current()); } catch { /* keep last data */ }
  }, []);
  useEffect(() => { void reload(); }, [reload, tick]);
  useEffect(() => {
    const t = setInterval(() => void reload(), everyMs);
    return () => clearInterval(t);
  }, [reload, everyMs]);
  return [data, reload] as const;
}

/** Runs a user action: one at a time, plain-language error (role=alert in the view), optional spoken result. */
export function useAction(after: () => Promise<void> | void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const run = useCallback(async (fn: () => Promise<unknown>, say?: (r: unknown) => string | undefined) => {
    setBusy(true);
    try {
      const r = await fn();
      setError(null);
      setSaid(say?.(r) ?? "");
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
      await after();
    }
  }, [after]);
  return { busy, error, said, setSaid, run };
}
