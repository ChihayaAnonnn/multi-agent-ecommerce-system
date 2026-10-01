import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  use: {
    baseURL: process.env.COMMERCE_BASE_URL || "https://localhost:18444",
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 960 },
    launchOptions: {
      executablePath:
        process.env.CHROME_PATH ||
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    },
  },
  reporter: [["list"]],
  outputDir: "test-results",
});
