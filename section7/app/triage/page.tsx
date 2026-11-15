"use client";

// U5: the phone inbox. Pending plan approvals only, Approve and Deny only.
import AppShell from "@/components/AppShell";
import { PhoneTriage } from "@/components/PhoneTriage";

export default function TriagePage() {
  return (
    <AppShell active="/triage">
      <PhoneTriage />
    </AppShell>
  );
}
