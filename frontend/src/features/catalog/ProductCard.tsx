import { useState } from "react";
import { ArrowUpRight, ImageOff } from "lucide-react";
import type { Product } from "../../api/contracts";
import { money, stockLabel } from "../../api/format";
import styles from "./ProductCard.module.css";
export function ProductCard({
  product: p,
  onOpen,
}: {
  product: Product;
  onOpen: (p: Product) => void;
}) {
  const [failed, setFailed] = useState(false),
    [loaded, setLoaded] = useState(false);
  return (
    <button
      className={styles.card}
      onClick={() => onOpen(p)}
      aria-label={`查看 ${p.name}`}
    >
      <div className={styles.imageArea}>
        {p.image_url && !failed ? (
          <img
            src={p.image_url}
            alt={p.name}
            loading="lazy"
            referrerPolicy="no-referrer"
            className={loaded ? styles.loaded : ""}
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
          />
        ) : (
          <div className={styles.fallback}>
            <ImageOff size={22} strokeWidth={1} />
            <span>暂无图片</span>
          </div>
        )}
        <span className={styles.open}>
          <ArrowUpRight size={18} strokeWidth={1.3} />
        </span>
      </div>
      <div className={styles.info}>
        <span className={styles.category}>{p.brand || p.category.name}</span>
        <h3 title={p.name}>{p.name}</h3>
        <div className={styles.bottom}>
          <span className={styles.price}>{money(p.price?.amount_minor)}</span>
          {!p.purchasable && (
            <span className={styles.stock}>{stockLabel(p.reason)}</span>
          )}
        </div>
      </div>
    </button>
  );
}
