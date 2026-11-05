import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Tests run against the core's source, so they need no build first.
export default defineConfig({
  test: {
    // Offline mode's files in a folder of each test's own, never the real ~/.bindable.
    setupFiles: [fileURLToPath(new URL("../gate/test/setup.ts", import.meta.url))],
  },
  resolve: {
    alias: {
      "@bindable-ai/gate": fileURLToPath(new URL("../gate/src/index.ts", import.meta.url)),
    },
  },
});
