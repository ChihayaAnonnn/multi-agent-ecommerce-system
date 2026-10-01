import type { TurnResponse } from "../../api/contracts";
import { money, skuLabel } from "../../api/format";
import styles from "./ChatPanel.module.css";
export function responseText(r: TurnResponse) {
  return (
    r.question ||
    (r.action_result === "declined"
      ? "已取消，购物袋未修改。"
      : {
          shortlist: "为你挑选了这些商品，可以在左侧查看。",
          compare: "这些商品的对比如下。",
          confirm_action: "请核对这项购物操作。",
          cart_updated: "购物袋已更新。",
          handoff: "购物准备已完成。这是模拟交接，不会创建订单或付款。",
        }[r.status] || "请继续描述你的需求。")
  );
}
export function ActionSummary({ response: r }: { response: TurnResponse }) {
  const action = r.proposed_action;
  const href = r.handoff?.destination?.ref;
  let safeHref: string | undefined;
  try {
    const u = new URL(href || "", window.location.origin);
    if (href && u.protocol === "https:") safeHref = u.href;
  } catch {
    /* 不展示无效交接地址。 */
  }
  return (
    <>
      {r.items?.map((item, i) => (
        <p className={styles.result} key={`${item.product_id}-${item.sku_id}`}>
          <span>{i + 1}</span>
          {item.name || "商品"}
          {item.explanation && <small>{item.explanation}</small>}
        </p>
      ))}
      {action && (
        <div className={styles.action}>
          <span className={styles.actionLabel}>
            待你确认 ·{" "}
            {(
              {
                add: "加入购物袋",
                set_quantity: "修改数量",
                remove: "移除商品",
                checkout_handoff: "结账交接",
              } as Record<string, string>
            )[action.kind || (action.items ? "checkout_handoff" : "")] ||
              action.kind}
          </span>
          {action.summary && (
            <>
              <p>{action.summary.name}</p>
              <small>
                {skuLabel(action.summary.sku_options, action.summary.sku_id)}
              </small>
              <p>
                {money(action.summary.unit_amount_minor)} ×{" "}
                {action.summary.final_quantity}
              </p>
            </>
          )}
          {action.items?.map((item, i) => (
            <p key={i}>
              {item.name}
              <br />
              <small>{skuLabel(item.sku_options, item.sku_id)}</small>
              <br />
              {money(item.unit_amount_minor)} × {item.quantity}
            </p>
          ))}
          {action.current_subtotal != null && (
            <p>小计 {money(action.current_subtotal)}</p>
          )}
          <small>输入“确认”继续，或输入“取消”。</small>
        </div>
      )}
      {r.comparison && (
        <div className={styles.comparison}>
          <table>
            <thead>
              <tr>
                <th>属性</th>
                {r.comparison.products.map((p, i) => (
                  <th key={i}>{p.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {r.comparison.dimensions.map((d) => (
                <tr key={d.code}>
                  <th>{d.name}</th>
                  {r.comparison!.products.map((p, i) => (
                    <td key={i}>{String(p.values[d.code] ?? "未提供")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {safeHref && (
        <a href={safeHref} target="_blank" rel="noopener noreferrer">
          查看交接结果
        </a>
      )}
    </>
  );
}
