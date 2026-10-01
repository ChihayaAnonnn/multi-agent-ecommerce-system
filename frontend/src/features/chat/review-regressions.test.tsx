import { beforeEach, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ShoppingSessionStore } from "./sessionStore";
import { ActionSummary } from "./ActionSummary";
import { CartPanel } from "../cart/CartPanel";
beforeEach(() => localStorage.clear());
const cart = {
  version: 2,
  current_subtotal: 300,
  items: [
    {
      sku_id: "blue-xl",
      product_name: "衬衫",
      quantity: 3,
      sku_options: [{ display_value: "蓝色" }, { display_value: "XL" }],
    },
  ],
};
test("confirmation interrupted by session switch remains retryable with exact body", async () => {
  let finish!: (v: unknown) => void;
  const bodies: unknown[] = [];
  const client = {
    bootstrap: async () => ({ user_id: "switch" }),
    request: vi.fn(async (p: string, init?: RequestInit) => {
      if (!p.endsWith("/turns")) return { session_id: "s", version: 1 };
      bodies.push(JSON.parse(init!.body as string));
      if (bodies.length === 1)
        return {
          status: "confirm_action",
          proposed_action: { action_id: "a", kind: "add" },
        };
      if (bodies.length === 2)
        return new Promise((r) => {
          finish = r;
        });
      return { status: "cart_updated", cart };
    }),
  };
  const store = new ShoppingSessionStore(client as any);
  await store.start();
  await store.sendMessage("加购");
  const confirm = store.confirmAction(true);
  await vi.waitFor(() => expect(finish).toBeDefined());
  store.newSession();
  finish({ status: "cart_updated", cart });
  await confirm;
  await store.selectHistory("s");
  const reply = store.getSnapshot().messages.at(-1)!;
  expect(reply.pending).toBe(false);
  expect(reply.error).toBeTruthy();
  expect(reply.retry).toBeDefined();
  await store.retryTurn(reply.id);
  expect(bodies[2]).toEqual(bodies[1]);
  expect(store.getSnapshot().cart).toEqual(cart);
});
test.each([
  ["加购 3 件", 3],
  ["加入购物车，数量 2 个", 2],
  ["加入第1款 4 件", 4],
  ["加入购物车", 1],
  ["加购 0 件", 0],
  ["加购 -2 件", -2],
  ["加购 1.5 件", 1.5],
])("explicit quantity preserved: %s", async (text, quantity) => {
  let body: any;
  const client = {
    bootstrap: async () => ({ user_id: "quantity" }),
    request: vi.fn(async (p: string, init?: RequestInit) => {
      if (p.endsWith("/turns")) {
        body = JSON.parse(init!.body as string);
        return { status: "clarify" };
      }
      return { session_id: "s", version: 1 };
    }),
  };
  const store = new ShoppingSessionStore(client as any);
  await store.start();
  await store.sendMessage(text, [{ product_id: "p", sku_id: "sku" }]);
  expect(body.quantity).toBe(quantity);
});
test("new conversation reads user cart even without a chat session and exposes failures", async () => {
  let fail = false;
  const client = {
    bootstrap: async () => ({ user_id: "cart" }),
    request: vi.fn(async (p: string) => {
      if (p.endsWith("/cart")) {
        if (fail) throw new Error("offline");
        return cart;
      }
      return { session_id: "read-session" };
    }),
  };
  const store = new ShoppingSessionStore(client as any);
  await store.start();
  store.newSession();
  await store.loadCart();
  expect(store.getSnapshot().cart).toEqual(cart);
  expect(store.getSnapshot().sessionId).toBeNull();
  store.newSession();
  expect(store.getSnapshot().cart).toEqual(cart);
  fail = true;
  await store.loadCart();
  expect(store.getSnapshot().cartError).toBe("offline");
});
test("confirmation shows operation and chosen SKU", () => {
  render(
    <ActionSummary
      response={{
        status: "confirm_action",
        proposed_action: {
          action_id: "a",
          kind: "remove",
          summary: {
            name: "衬衫",
            sku_id: "blue-xl",
            sku_options: cart.items[0].sku_options,
            unit_amount_minor: 100,
            final_quantity: 0,
          },
          items: [
            {
              name: "衬衫",
              sku_id: "red-m",
              sku_options: [{ display_value: "红色" }],
              unit_amount_minor: 100,
              quantity: 1,
            },
          ],
        },
      }}
    />,
  );
  expect(screen.getByText(/移除商品/)).toBeInTheDocument();
  expect(screen.getByText("蓝色 · XL")).toBeInTheDocument();
  expect(screen.getByText("红色")).toBeInTheDocument();
});
test("cart shows specifications and distinct loading, error and empty states", () => {
  const props = { cart, sessionId: null, onChat: vi.fn() };
  const view = render(
    <CartPanel {...props} loading={true} error="" onRetry={vi.fn()} />,
  );
  expect(screen.getByText(/正在读取购物袋/)).toBeInTheDocument();
  view.rerender(
    <CartPanel {...props} loading={false} error="offline" onRetry={vi.fn()} />,
  );
  expect(screen.getByText("offline")).toBeInTheDocument();
  expect(screen.getByText("蓝色 · XL")).toBeInTheDocument();
  view.rerender(
    <CartPanel
      {...props}
      cart={null}
      loading={false}
      error="offline"
      onRetry={vi.fn()}
    />,
  );
  expect(screen.queryByText("购物袋还是空的。")).not.toBeInTheDocument();
});
test("checkout preview without kind shows checkout operation and SKU fallback", () => {
  render(
    <ActionSummary
      response={{
        status: "confirm_action",
        proposed_action: {
          action_id: "checkout",
          items: [
            {
              name: "衬衫",
              sku_id: "variant-42",
              unit_amount_minor: 100,
              quantity: 1,
            },
          ],
        },
      }}
    />,
  );
  expect(screen.getByText(/结账交接/)).toBeInTheDocument();
  expect(screen.getByText("规格 variant-42")).toBeInTheDocument();
});
