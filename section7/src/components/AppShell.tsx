"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { ProjectDto } from "@/lib/api";
import { listProjects } from "@/lib/api";
import { clearSession, getToken, getProjectId, setProjectId, loadSession } from "@/lib/auth";

const AREAS = [
  { href: "/", label: "Command Center" },
  { href: "/meeting", label: "Meeting" },
  { href: "/review", label: "Review" },
];

export default function AppShell({
  children,
  active,
}: {
  children: React.ReactNode;
  active: string;
}) {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectDto[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [role, setRole] = useState<string>("viewer");

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    setRole(loadSession()?.principal.role ?? "viewer");
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

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">HARNESS</div>
        <div className="switcher">
          <div className="label">TEAM</div>
          <select defaultValue="solo">
            <option value="solo">My team</option>
          </select>
          <div className="label">PROJECT</div>
          <select
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
        <nav>
          {AREAS.map((a) => (
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
          <div className="role-badge">{role}</div>
          <button
            className="btn"
            style={{ marginTop: 8, width: "100%" }}
            onClick={() => {
              clearSession();
              router.push("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
