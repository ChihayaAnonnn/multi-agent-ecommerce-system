import { test, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useProductFeed } from "./useProductFeed";
test("retains old cards until new candidates are ready and restores catalog", async () => {
  let finish!: (r: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (p: string) =>
      p.includes("resolve")
        ? new Promise<Response>((r) => {
            finish = r;
          })
        : new Response(
            JSON.stringify({ items: [{ id: "catalog" }], next_cursor: null }),
          ),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result, rerender } = renderHook(({ ids }) => useProductFeed(ids), {
    initialProps: { ids: null as string[] | null },
    wrapper,
  });
  await waitFor(() => expect(result.current.items[0]?.id).toBe("catalog"));
  rerender({ ids: ["candidate"] });
  expect(result.current.items[0]?.id).toBe("catalog");
  await waitFor(() => expect(finish).toBeDefined());
  finish(
    new Response(
      JSON.stringify({ items: [{ id: "candidate" }], missing_product_ids: [] }),
    ),
  );
  await waitFor(() => expect(result.current.items[0]?.id).toBe("candidate"));
  rerender({ ids: null });
  await waitFor(() => expect(result.current.items[0]?.id).toBe("catalog"));
});
