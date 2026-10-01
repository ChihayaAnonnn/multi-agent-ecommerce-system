import type { Cart } from "../../api/contracts";
import { money, stockLabel, skuLabel } from "../../api/format";
import { ShoppingBag } from "lucide-react";
export function CartPanel({
  cart,
  loading = false,
  error = "",
  onRetry,
  onChat,
}: {
  cart: Cart | null;
  sessionId?: string | null;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  onChat: () => void;
}) {
  if (loading) return <p role="status">正在读取购物袋…</p>;
  const failure = error && (
    <div role="alert">
      <p>{error}</p>
      <button className="primary-button" onClick={onRetry}>
        重试读取
      </button>
    </div>
  );
  if (!cart && error) return <>{failure}</>;
  if (!cart) return <p role="status">购物袋尚未读取。</p>;
  if (!cart.items.length)
    return (
      <div className="cart-empty">
        {failure}
        <ShoppingBag size={30} strokeWidth={1} />
        <h2>留给喜欢的东西。</h2>
        <p>购物袋还是空的。</p>
        <button className="primary-button" onClick={onChat}>
          继续挑选
        </button>
      </div>
    );
  return (
    <div className="cart-panel">
      {failure}
      {cart.items.map((item) => (
        <div className="cart-row" key={item.sku_id}>
          <p>{item.product_name}</p>
          <small>{skuLabel(item.sku_options, item.sku_id)}</small>
          <span>
            {money(item.quote?.price?.amount_minor)} × {item.quantity}
          </span>
          {item.quote && !item.quote.purchasable && (
            <small>{stockLabel(item.quote.reason)}</small>
          )}
        </div>
      ))}
      <div className="cart-total">
        <span>商品小计</span>
        <strong>{money(cart.current_subtotal)}</strong>
      </div>
      <p className="muted">在对话中输入“结账”，助手会准备摘要供你确认。</p>
      <button className="primary-button" onClick={onChat}>
        返回购物助手
      </button>
    </div>
  );
}
