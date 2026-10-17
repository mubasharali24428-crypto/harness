import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  resolve: { alias: { vscode: path.resolve(__dirname, "test/vscode-mock.ts") } },
  test: { environment: "node", include: ["test/**/*.test.ts"] },
});
