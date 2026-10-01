import { test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProductCard } from "./ProductCard";
const product = {
  id: "p",
  name: "Very long product name",
  brand: null,
  image_url: "https://example.test/image.jpg",
  category: { id: "c", name: "家居" },
  sku_id: "s",
  price: { currency: "CNY", amount_minor: 1299 },
  availability: { status: "unknown", available_quantity: null },
  purchasable: false,
  reason: "stock_unknown",
  simulated: true,
};
test("image failure shows stable fallback and unknown is not zero", () => {
  render(<ProductCard product={product as any} onOpen={vi.fn()} />);
  fireEvent.error(screen.getByRole("img"));
  expect(screen.getByText("暂无图片")).toBeVisible();
  expect(screen.getByText("库存待确认")).toBeVisible();
  expect(screen.getByText("¥12.99")).toBeVisible();
});
