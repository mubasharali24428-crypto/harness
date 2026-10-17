import { defineConfig } from "@playwright/test";
export default defineConfig({ testDir: "./test/smoke", testMatch: /.*\.e2e\.ts/, timeout: 60_000, workers: 1, reporter: "list" });
