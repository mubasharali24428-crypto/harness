"use client";

import * as React from "react";
import AppShell from "@/components/AppShell";
import { InboxScreen } from "./inboxScreen";

export default function NotificationsPage() {
  return (
    <AppShell active="/notifications">
      <h1>Notifications</h1>
      <InboxScreen />
    </AppShell>
  );
}
