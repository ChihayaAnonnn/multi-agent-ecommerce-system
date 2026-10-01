import { test, expect } from "./fixtures";
test("chat persists, network retry and history restore", async ({ page }) => {
  await page.goto("/demo/commerce");
  const input = page.getByRole("textbox");
  await expect(input).toBeEnabled();
  await input.fill("想买通勤耳机，预算500元");
  await input.press("Enter");
  await expect(input).toBeEnabled();
  await expect(page.getByRole("log")).toContainText("想买通勤耳机");
  await page.reload();
  await expect(input).toBeEnabled();
  await expect(page.getByRole("log")).toContainText("想买通勤耳机");
  await page.route("**/api/v1/commerce/sessions/*/turns", (route) =>
    route.abort(),
  );
  await input.fill("查询购物车");
  await input.press("Enter");
  await expect(
    page.getByRole("button", { name: "重试这条消息" }),
  ).toBeVisible();
  await page.unroute("**/api/v1/commerce/sessions/*/turns");
  await page.getByRole("button", { name: "重试这条消息" }).click();
  await expect(page.getByRole("button", { name: "重试这条消息" })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(page.getByRole("log")).not.toContainText("想买通勤耳机");
  await page.getByRole("button", { name: "会话记录" }).click();
  await page
    .getByRole("navigation", { name: "历史对话" })
    .getByRole("button")
    .first()
    .click();
  await expect(page.getByRole("log")).toContainText("想买通勤耳机");
  const stored = await page.evaluate(() => Object.values(localStorage));
  expect(stored.join("")).not.toMatch(/access_token|csrf_token/);
});
test("browsed product proposal, lost confirmation response, idempotent retry and handoff", async ({
  page,
}) => {
  await page.goto("/demo/commerce");
  const input = page.getByRole("textbox");
  await expect(input).toBeEnabled();
  let beforeQuantity = 0;
  let selectedSku = "";
  let finalCart: any;
  page.on("response", async (r) => {
    if (r.url().endsWith("/cart") && r.request().method() === "GET") {
      const cart = await r.json();
      beforeQuantity =
        cart.items.find((i: any) => i.sku_id === selectedSku)?.quantity || 0;
    }
    if (r.url().endsWith("/turns")) {
      const data = await r.json();
      if (data.cart) finalCart = data.cart;
    }
  });
  const catalog = await page.request.get(
    "/api/v1/storefront/products?limit=24",
  );
  const first = (await catalog.json()).items[0];
  selectedSku = first.sku_id;
  await page
    .getByRole("button", { name: /^查看 / })
    .first()
    .click();
  await page.getByRole("button", { name: "向助手询问这件商品" }).click();
  await input.fill("加入购物车");
  await input.press("Enter");
  await expect(page.getByRole("log")).toContainText("待你确认");
  const before = beforeQuantity;
  let lost = false;
  let originalBody: string | undefined;
  await page.route("**/api/v1/commerce/sessions/*/turns", async (route) => {
    const body = route.request().postDataJSON();
    if (body.respond_to_action_id && !lost) {
      lost = true;
      originalBody = route.request().postData()!;
      await route.fetch();
      await route.abort();
    } else {
      if (body.respond_to_action_id)
        expect(route.request().postData()).toBe(originalBody);
      await route.continue();
    }
  });
  await input.fill("确认");
  await input.press("Enter");
  await expect(
    page.getByRole("button", { name: "重试这条消息" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "重试这条消息" }).click();
  await expect(page.getByRole("log")).toContainText("购物袋已更新");
  await expect
    .poll(
      () =>
        finalCart?.items.find((i: any) => i.sku_id === selectedSku)?.quantity,
    )
    .toBe(before + 1);
  await page.unroute("**/api/v1/commerce/sessions/*/turns");
  await page
    .getByRole("button", { name: "购物袋", exact: false })
    .first()
    .click();
  await expect(page.getByRole("dialog", { name: "购物袋" })).toContainText(
    first.name,
  );
  await page.keyboard.press("Escape");
  await input.fill("结账");
  await input.press("Enter");
  await expect(page.getByRole("log")).toContainText("小计 ¥");
  await input.fill("确认结账");
  await input.press("Enter");
  await expect(page.getByRole("log")).toContainText("这是模拟交接");
  await expect(page.getByRole("link", { name: "查看交接结果" })).toBeVisible();
});
