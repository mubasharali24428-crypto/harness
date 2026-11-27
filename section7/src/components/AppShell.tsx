"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import { clearSession, getToken, getProjectId, getRunId, loadSession } from "@/lib/auth";
import { formatRelativeTime, formatTimestamp } from "@/lib/format";
import { needsYouAction, needsYouHref } from "@/lib/triage";
import { stateLabel } from "../../app/runs/runsApi";
import { useRunList } from "@/components/workspace/useRunList";
import { runRowTitle } from "@/components/workspace/runRows";
// M14-F1b (raw-03 P2): skip-link behavior + the shared main-content landmark id.
import { MAIN_CONTENT_ID, useSkipTarget } from "@/lib/focus";
// Part 47: unread badge as a plain link (no sound, no popup); renders nothing without a session.
import NotificationBell from "../../app/notifications/NotificationBell";
import { ThemeToggle } from "@/components/ThemeToggle";
import CommandPalette from "@/components/CommandPalette";
import { KeyTour } from "@/components/KeyTour";
import { MENU, SETTINGS_PAGES } from "@/lib/commands";

// R2: the sidebar is the run list. Everything that used to be a nav
// destination lives in this footer menu and in the command palette.
// Operability slice 5: the menu's labels come from the same list as the palette (lib/commands).

const GROUPS = [
  { key: "needsYou", label: "Needs you", empty: "Nothing is waiting on a person." },
  { key: "working", label: "Working", empty: "No agent is working right now." },
  { key: "done", label: "Done", empty: "No finished runs yet." },
] as const;

// False for the first page of a visit, so a fresh load keeps the browser's own focus order.
let arrivedByNavigation = false;

export default function AppShell({
  children,
  active,
  runId = null,
}: {
  children: React.ReactNode;
  active: string;
  /** The run on screen, so its sidebar row reads as current. */
  runId?: string | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  // After an in-app page change focus is left on <body>; move it to the main region so
  // keyboard and screen-reader users start in the new page, not at the top of the sidebar.
  useEffect(() => {
    // The document was loaded at another address: this page was reached without a reload.
    const loadedAt = (performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined)?.name;
    const clientSide = arrivedByNavigation || (loadedAt !== undefined && loadedAt !== window.location.href);
    if (clientSide && document.activeElement === document.body) {
      document.getElementById(MAIN_CONTENT_ID)?.focus({ preventScroll: true });
    }
    arrivedByNavigation = true;
  }, []);
  // M14-F1b: skip-link behavior — href fragment + explicit focus move.
  const { skipHref, onSkipActivate } = useSkipTarget(MAIN_CONTENT_ID);
  const list = useRunList(runId);
  const { projects, selectedProjectId: selected } = list;
  const [role, setRole] = useState<string>("viewer");
  const [workingHref, setWorkingHref] = useState<string | null>(null);
  // M14-F2b (raw-03 P2): ≤900px the sidebar becomes an off-canvas drawer.
  // This boolean is the ONLY new JS in the shell — everything else about the
  // responsive behavior is pure CSS media queries in app/globals.css.
  const [sidebarOpen, setSidebarOpen] = useState(false);
  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    if (loadSession()?.principal.role === "guest") {
      // P-17: a guest is an outside reader; the workspace routes refuse them, the portal does not.
      router.replace("/portal");
      return;
    }
    setRole(loadSession()?.principal.role ?? "viewer");
    // Goal 2: Working jumps to the latest project/run the browser remembers.
    const pid = getProjectId();
    const rid = getRunId();
    if (pid && rid) setWorkingHref(`/projects/${pid}/runs/${rid}`);
  }, [router]);

  // M14-F2b: lock page scroll while the drawer is open (≤900px only — the
  // class is inert on desktop where the media query never applies it).
  useEffect(() => {
    if (!sidebarOpen) return;
    document.body.classList.add("has-open-sidebar");
    return () => document.body.classList.remove("has-open-sidebar");
  }, [sidebarOpen]);

  return (
    <div className="shell">
      {/* M14-F1b (raw-03 P2): skip link is the FIRST element of the shell so
          it is the very first Tab stop on every page. Hidden until focused
          (see .skip-link in globals.css); lands focus on the main region. */}
      <a className="skip-link" href={skipHref} onClick={onSkipActivate}>
        Skip to content
      </a>
      {/* M14-F2b: mobile top bar. Hidden entirely on desktop (display:none in
          globals.css); ≤900px it carries the hamburger toggle. aria-expanded
          is the machine-readable source of truth for drawer state; the label
          flips with it and the icon-only glyph gets .sr-only text so the
          accessible name stays stable for assistive tech. */}
      <div className="topbar">
        <button
          type="button"
          className="sidebar-toggle"
          aria-controls="app-sidebar"
          aria-expanded={sidebarOpen}
          onClick={() => setSidebarOpen((v) => !v)}
        >
          <span aria-hidden="true">☰</span>
          <span className="sr-only">{sidebarOpen ? "Close navigation" : "Open navigation"}</span>
        </button>
        <div className="brand">HARNESS</div>
      </div>
      {/* M14-F2b: scrim sits between page and drawer; clicking it closes.
          Rendered only while open, so desktop markup is unchanged. */}
      {sidebarOpen && (
        <div
          className="scrim"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
      {/* M14-F2b: `open` class slides the drawer in (transform/visibility in
          globals.css). */}
      <aside id="app-sidebar" className={`sidebar${sidebarOpen ? " open" : ""}`}>
        <div className="brand">HARNESS</div>
        <div className="switcher">
          <div className="label" id="project-select-label">
            PROJECT
          </div>
          <select
            aria-labelledby="project-select-label"
            value={selected ?? ""}
            onChange={(e) => list.selectProject(e.target.value)}
          >
            {projects.length === 0 && <option value="">(no projects yet)</option>}
            {projects.map((prj) => (
              <option key={prj.id} value={prj.id}>
                {prj.name}
              </option>
            ))}
          </select>
        </div>
        <Link href="/" className={`nav-item${active === "/" ? " active" : ""}`}>
          New run
        </Link>
        <div className="ws-groups" role="navigation" aria-label="Run list">
          {list.error ? (
            <p className="ws-group-empty" role="alert">
              The run list didn’t load: {list.error}{" "}
              <button type="button" className="ws-link-btn" onClick={list.retry}>
                Try again
              </button>
            </p>
          ) : !list.loaded ? (
            <p className="ws-group-empty" role="status">
              Loading runs…
            </p>
          ) : (
            GROUPS.map((g) => {
              const items = list.groups[g.key];
              return (
                <section key={g.key} className="ws-group" aria-labelledby={`ws-group-${g.key}`}>
                  <h2 id={`ws-group-${g.key}`} className="ws-group-label">
                    {g.label}
                    <span className="ws-group-count" aria-hidden="true">
                      {items.length}
                    </span>
                  </h2>
                  {items.length === 0 ? (
                    <p className="ws-group-empty">{g.empty}</p>
                  ) : (
                    <ul className="ws-rows">
                      {items.map((run) => {
                        const when = run.startedAt ?? run.createdAt;
                        const goal = list.goals[run.id] ?? null;
                        return (
                          <li key={run.id}>
                            <Link
                              href={needsYouHref(run)}
                              className={`ws-row${run.id === runId ? " active" : ""}`}
                              aria-current={run.id === runId ? "page" : undefined}
                              title={goal ?? undefined}
                            >
                              <span className="ws-row-title">{runRowTitle(goal)}</span>
                              <span className="ws-row-meta">
                                <span className={`ws-dot ${run.status}`} aria-hidden="true" />
                                {needsYouAction(run) ?? stateLabel(run.status)}
                                <span aria-hidden="true">·</span>
                                <time dateTime={when} title={formatTimestamp(when)}>
                                  {formatRelativeTime(when)}
                                </time>
                              </span>
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              );
            })
          )}
        </div>
        <div className="footer">
          <NotificationBell />
          <CommandPalette
            role={role}
            workingHref={workingHref}
            projects={projects}
            selectedProject={selected}
            onSelectProject={list.selectProject}
            onSignOut={() => {
              clearSession();
              router.push("/login");
            }}
          />
          <KeyTour />
          <details className="ws-menu">
            <summary>Menu</summary>
            <nav aria-label="More">
              {MENU.map((a) => (
                <Link key={a.href} href={a.href} className={`nav-item${active === a.href ? " active" : ""}`}>
                  {a.label}
                </Link>
              ))}
            </nav>
          </details>
          <div className="ws-foot-row">
            <div className="role-badge">{role}</div>
            <ThemeToggle compact />
          </div>
          <button
            className="btn"
            style={{ width: "100%" }}
            onClick={() => {
              clearSession();
              router.push("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      {/* M14-F1b: the skip-link target. tabindex="-1" lets the region take
          programmatic focus (skip link, route-change focus moves) without
          putting it in the Tab order. */}
      <main id={MAIN_CONTENT_ID} tabIndex={-1} className="content">
        {active === "/settings" && (
          <nav className="settings-nav" aria-label="Settings sections">
            {[{ id: "settings", label: "Overview", href: "/settings" }, ...SETTINGS_PAGES].map((p) => (
              <Link key={p.id} href={p.href} aria-current={pathname === p.href ? "page" : undefined}>
                {p.label}
              </Link>
            ))}
          </nav>
        )}
        {children}
      </main>

    </div>
  );
}
