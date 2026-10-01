export const money = (value: number | null | undefined) =>
  value == null
    ? "价格待确认"
    : new Intl.NumberFormat("zh-CN", {
        style: "currency",
        currency: "CNY",
      }).format(value / 100);
export const stockLabel = (reason: string) =>
  ({
    stock_unknown: "库存待确认",
    out_of_stock: "暂时缺货",
    price_missing: "价格待确认",
    sku_missing: "暂无可售规格",
    sku_inactive: "规格已停用",
    product_not_active: "商品已下架",
  })[reason] || "";

export const skuLabel = (
  options: { display_value: string }[] | undefined,
  skuId: string,
) =>
  options?.length
    ? options.map((v) => v.display_value).join(" · ")
    : `规格 ${skuId}`;
