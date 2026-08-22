"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type ProjectDto } from "@/lib/api";
import { mockPrincipal } from "@/lib/mockApi";

const AREAS = [
  { href: "/", label: "Command Center" },
  { href: "/meeting", label: "Meeting" },
  { href: "/working", label: "Working" },
  { href: "/review", label: "Review" },
];

export default function AppShell({
  children,
  active,
}: {
  children: React.ReactNode;
  active: string;
}) {
  const [projects, setProjects] = useState<ProjectDto[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (!api.getToken()) return;
    api
      .listProjects()
      .then((items) => {
        setProjects(items);
        const saved = localStorage.getItem("harness.projectId");
        const valid = items.find((i) => i.id === saved) ?? items[0];
        if (valid) {
          localStorage.setItem("harness.projectId", valid.id);
          setSelected(valid.id);
        }
      })
      .catch(() => {});
  }, []);

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
              localStorage.setItem("harness.projectId", e.target.value);
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
          <div className="role-badge">{mockPrincipal.role}</div>
        </div>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
