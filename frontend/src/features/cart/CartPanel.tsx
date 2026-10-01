import type { Cart } from "../../api/contracts";
import { money, stockLabel } from "../../api/format";
import { ShoppingBag } from "lucide-react";
export function CartPanel({
  cart,
  sessionId,
  onChat,
}: {
  cart: Cart | null;
  sessionId: string | null;
  onChat: () => void;
}) {
  if (!cart?.items.length)
    return (
      <div className="cart-empty">
        <ShoppingBag size={30} strokeWidth={1} />
        <h2>留给喜欢的东西。</h2>
        <p>
          {sessionId ? "购物袋还是空的。" : "和助手聊聊，找到适合你的商品。"}
        </p>
        <button className="primary-button" onClick={onChat}>
          继续挑选
        </button>
      </div>
    );
  return (
    <div className="cart-panel">
      {cart.items.map((item) => (
        <div className="cart-row" key={item.sku_id}>
          <p>{item.product_name}</p>
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
