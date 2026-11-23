import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Separate from vite.config.ts so the tests do not run the service-worker build plugin.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    // Primer React imports its own CSS from its JavaScript: run it through Vite instead of Node.
    server: { deps: { inline: [/@primer\/react/] } },
  },
});
