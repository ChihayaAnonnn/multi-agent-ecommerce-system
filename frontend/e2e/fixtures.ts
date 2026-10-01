import { test as base, expect } from "@playwright/test";
import fs from "node:fs";
import type { BrowserContext } from "@playwright/test";
let saved: Awaited<ReturnType<BrowserContext["storageState"]>> | undefined;
const file = ".auth/storefront.json";
if (fs.existsSync(file)) saved = JSON.parse(fs.readFileSync(file, "utf8"));
export const test = base.extend({
  context: async ({ browser }, use) => {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true,
      viewport: { width: 1440, height: 960 },
      storageState: saved,
    });
    try {
      await use(context);
    } finally {
      saved = await context.storageState();
      saved.origins = [];
      fs.mkdirSync(".auth", { recursive: true });
      fs.writeFileSync(file, JSON.stringify(saved), { mode: 0o600 });
      await context.close();
    }
  },
});
export { expect };
