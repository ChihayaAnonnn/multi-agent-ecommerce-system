import { test, expect } from "./fixtures";
test("rapid drawers close without overlay and keyboard focus returns", async ({
  page,
}) => {
  await page.goto("/demo/commerce");
  await expect(page.getByRole("textbox")).toBeEnabled();
  const trigger = page.getByRole("button", { name: "会话记录" });
  await trigger.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.getByRole("button", { name: "关闭面板" }).click();
  await trigger.click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("textbox")).toBeEnabled();
});
test("reduced motion keeps panel usable without displacement", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/demo/commerce");
  await expect(page.getByRole("textbox")).toBeEnabled();
  await page.getByRole("button", { name: "会话记录" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.waitForTimeout(150);
  const transform = await page
    .getByRole("dialog")
    .evaluate((el) => getComputedStyle(el).transform);
  expect(["none", "matrix(1, 0, 0, 1, 0, 0)"]).toContain(transform);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
