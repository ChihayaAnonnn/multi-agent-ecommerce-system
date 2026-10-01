import { test, expect } from "./fixtures";
test("real catalog, pagination, detail reference, collapse and reload", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/demo/commerce");
  await expect(page.getByRole("textbox")).toBeEnabled();
  const cards = page.getByRole("button", { name: /^查看 / });
  await expect(cards).toHaveCount(24);
  await page.getByRole("button", { name: "继续浏览" }).click();
  await expect(cards).toHaveCount(48);
  await cards.first().click();
  const dialog = page.getByRole("dialog", { name: "商品详情" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { level: 2 }).last()).toBeVisible();
  await expect(page).toHaveURL(/products\//);
  await page.reload();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "向助手询问这件商品" }).click();
  await expect(
    page.getByRole("button", { name: "移除商品引用" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "收起购物助手" }).click();
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await page.getByRole("button", { name: "购物助手", exact: true }).click();
  await expect(page.getByRole("textbox")).toBeVisible();
  expect(errors).toEqual([]);
});
test("external image failure keeps stable placeholder", async ({ page }) => {
  await page.route("https://m.media-amazon.com/**", (route) => route.abort());
  await page.goto("/demo/commerce");
  await expect(page.getByRole("button", { name: /^查看 / })).toHaveCount(24);
  await expect(page.getByText("暂无图片").first()).toBeVisible();
});
