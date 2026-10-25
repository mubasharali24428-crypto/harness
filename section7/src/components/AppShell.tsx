"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import type { ProjectDto } from "@/lib/api";
import { listProjects } from "@/lib/api";
import { clearSession, getToken, getProjectId, getRunId, setProjectId, loadSession } from "@/lib/auth";
// M14-F1b (raw-03 P2): skip-link behavior + the shared main-content landmark id.
import { MAIN_CONTENT_ID, useSkipTarget } from "@/lib/focus";
// Part 47: unread badge as a plain link (no sound, no popup); renders nothing without a session.
import NotificationBell from "../../app/notifications/NotificationBell";
import { ThemeToggle } from "@/components/ThemeToggle";

const AREAS = [
  { href: "/", label: "Command Center" },
  { href: "/onboarding", label: "Get started" }, // M12-E7 (raw-04 M2): first-run checklist reachable from the nav
  { href: "/runs", label: "Runs" },
  { href: "/meeting", label: "Meeting" },
  { href: "/review", label: "Review" },
  { href: "/dashboard/cost", label: "Cost" },
  { href: "/settings", label: "Settings" },
];

export default function AppShell({
  children,
  active,
}: {
  children: React.ReactNode;
  active: string;
}) {
  const router = useRouter();
  // M14-F1b: skip-link behavior — href fragment + explicit focus move.
  const { skipHref, onSkipActivate } = useSkipTarget(MAIN_CONTENT_ID);
  const [projects, setProjects] = useState<ProjectDto[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
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
    setRole(loadSession()?.principal.role ?? "viewer");
    // Goal 2: Working jumps to the latest project/run the browser remembers.
    const pid = getProjectId();
    const rid = getRunId();
    if (pid && rid) setWorkingHref(`/projects/${pid}/runs/${rid}`);
    listProjects(getToken()!)
      .then((items) => {
        setProjects(items);
        const saved = getProjectId();
        const valid = items.find((i) => i.id === saved) ?? items[0];
        if (valid) {
          setProjectId(valid.id);
          setSelected(valid.id);
        }
      })
      .catch(() => {});
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
          {/* M14-F1b: programmatic labels — the visual .label divs are not
              real <label for> pairs, so screen-reader users heard "combobox"
              with no name. */}
          <div className="label" id="team-select-label">
            TEAM
          </div>
          <select defaultValue="solo" aria-labelledby="team-select-label">
            <option value="solo">My team</option>
          </select>
          <div className="label" id="project-select-label">
            PROJECT
          </div>
          <select
            aria-labelledby="project-select-label"
            value={selected ?? ""}
            onChange={(e) => {
              setProjectId(e.target.value);
              setSelected(e.target.value);
            }}
          >
            {projects.length === 0 && <option value="">(no projects yet)</option>}
            {projects.map((prj) => (
              <option key={prj.id} value={prj.id}>
                {prj.name}
              </option>
            ))}
          </select>
        </div>
        <NotificationBell />
        <div className="nav-group-label">Workspace</div>
        <nav>
          {AREAS.slice(0, 1).map((a) => (
            <Link
              key={a.href}
              href={a.href}
              className={`nav-item${active === a.href ? " active" : ""}`}
            >
              {a.label}
            </Link>
          ))}
          {workingHref ? (
            <Link
              href={workingHref}
              className={`nav-item${active.startsWith("/projects") ? " active" : ""}`}
            >
              Working
            </Link>
          ) : (
            // M14-F1b: an inert span with cursor:not-allowed reads as a broken
            // control. A real disabled button is honest — keyboard users skip
            // it natively and it can't be clicked into a dead end.
            <button
              type="button"
              className="nav-item"
              disabled
              title="No run yet — start one from the Command Center"
              style={{ opacity: 0.4, cursor: "not-allowed", textAlign: "left", border: "none", background: "none" }}
            >
              Working
            </button>
          )}
          {AREAS.slice(1).map((a) => (
            <Link
              key={a.href}
              href={a.href}
              className={`nav-item${active === a.href ? " active" : ""}`}
            >
              {a.label}
            </Link>
          ))}
        </nav>
        <div className="footer">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
              marginBottom: 8,
            }}
          >
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
        {children}
      </main>
    </div>
  );
}
