"use client";

import * as React from "react";
import AppShell from "@/components/AppShell";
import { BreadcrumbTrail } from "@/components/Breadcrumbs";
import { PrefsScreen } from "./prefsScreen";

export default function NotificationSettingsPage() {
  return (
    <AppShell active="/settings">
      <BreadcrumbTrail items={[{ label: "Settings", href: "/settings" }, { label: "Notifications" }]} />
      <h1>Notification settings</h1>
      <div style={{ maxWidth: 760 }}><PrefsScreen /></div>
    </AppShell>
  );
}
