import type { Metadata } from "next";
import { THEME_BOOT_SCRIPT } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Engineering Harness",
  description: "Team command center for human + agent software building",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning /* the theme script sets data-theme before React loads */>
      <head>
        {/* Applies the stored/OS theme before first paint (no flash). */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
