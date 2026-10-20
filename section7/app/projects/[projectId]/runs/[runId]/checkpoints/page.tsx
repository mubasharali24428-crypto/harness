"use client";

// M12-G9 (first slice): linkable home for a run's checkpoint snapshots.
// Client page because the session token lives in localStorage (same model as
// the rest of the working area).

import { useEffect, useState, use } from "react";
import { useRouter } from "next/navigation";
import { loadSession } from "@/lib/auth";
import { CheckpointsPanel } from "@/components/CheckpointsPanel";

export default function CheckpointsPage({
  params,
}: {
  params: Promise<{ projectId: string; runId: string }>;
}) {
  const router = useRouter();
  const { projectId, runId } = use(params);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      router.push("/");
      return;
    }
    setToken(session.token);
  }, [router]);

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 18, marginBottom: 16 }}>
        Checkpoints{" "}
        <span style={{ color: "var(--muted)", fontWeight: 400 }}>· run {runId}</span>
      </h1>
      {!token ? null : <CheckpointsPanel projectId={projectId} token={token} />}
    </main>
  );
}
