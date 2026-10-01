import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { AnimatedPresence } from "../components/AnimatedPresence";
import { panelMotion } from "./motion";
import { useState } from "react";
import { useMatch, useNavigate } from "react-router-dom";
import {
  ShoppingBag,
  History,
  MessageCircle,
  ArrowLeft,
  Leaf,
} from "lucide-react";
import { useShoppingSession } from "../features/chat/useShoppingSession";
import { ChatPanel } from "../features/chat/ChatPanel";
import { HistoryPanel } from "../features/chat/HistoryPanel";
import type { Reference } from "../features/chat/sessionStore";
import { useProductFeed } from "../features/catalog/useProductFeed";
import { ProductGrid } from "../features/catalog/ProductGrid";
import { ProductDetail } from "../features/catalog/ProductDetail";
import { CartPanel } from "../features/cart/CartPanel";
import { Drawer } from "../components/Drawer";
import styles from "./App.module.css";
export default function App() {
  const reduced = !!useReducedMotion();
  const session = useShoppingSession();
  const navigate = useNavigate();
  const detail = useMatch("/demo/commerce/products/:productId");
  const [panel, setPanel] = useState<"history" | "cart" | null>(null),
    [chatOpen, setChatOpen] = useState(true),
    [reference, setReference] = useState<Reference | null>(null),
    [browse, setBrowse] = useState(true);
  const candidateKey =
    session.candidates?.map((p) => p.product_id).join(",") || "";
  const [lastCandidates, setLastCandidates] = useState(candidateKey),
    [lastSession, setLastSession] = useState(session.sessionId);
  if (candidateKey !== lastCandidates) {
    setLastCandidates(candidateKey);
    setBrowse(session.candidates === null);
  }
  if (lastSession !== session.sessionId) {
    setLastSession(session.sessionId);
    setBrowse(session.candidates === null);
    setReference(null);
  }
  const candidateIds = session.candidates?.map((p) => p.product_id) || null;
  const feed = useProductFeed(browse ? null : candidateIds);
  const closePanel = () => {
    setPanel(null);
    if (detail) navigate("/demo/commerce");
  };
  const openPanel = (next: "history" | "cart") => {
    if (detail) navigate("/demo/commerce");
    setPanel(next);
    if (next === "cart") void session.loadCart();
  };
  const quoteReference = (r: Reference) => {
    closePanel();
    setChatOpen(true);
    setReference(r);
  };
  const cartCount =
    session.cart?.items.reduce((n, i) => n + i.quantity, 0) || 0;
  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <a
          href="/demo/commerce"
          className={styles.brand}
          onClick={(e) => {
            e.preventDefault();
            navigate("/demo/commerce");
            setBrowse(true);
          }}
        >
          <span className={styles.mark}>
            <Leaf size={17} strokeWidth={1.6} />
          </span>
          购物助手
        </a>
        <span className={styles.tagline}>为日常，选得更好。</span>
        <nav>
          <button onClick={() => openPanel("history")}>
            <History size={17} strokeWidth={1.5} />
            <span>会话记录</span>
          </button>
          <button onClick={() => openPanel("cart")}>
            <ShoppingBag size={17} strokeWidth={1.5} />
            <span>购物袋</span>
            {cartCount > 0 && <small>{cartCount}</small>}
          </button>
        </nav>
      </header>
      <div
        className={`${styles.workspace} ${chatOpen ? "" : styles.collapsed}`}
      >
        <main className={styles.showroom}>
          <div className={styles.intro}>
            <div>
              <p className={styles.eyebrow}>
                {browse ? "日常好物" : "这次的挑选"}
              </p>
              <h1>{browse ? "为日常挑选" : "为你挑选"}</h1>
              <p className={styles.subtitle}>
                {browse
                  ? "从一件合适的物品开始。"
                  : "看看这些商品，和助手继续聊聊。"}
              </p>
            </div>
            <div className={styles.introSide}>
              {!browse && (
                <button className={styles.back} onClick={() => setBrowse(true)}>
                  <ArrowLeft size={14} />
                  浏览全部商品
                </button>
              )}
              <span>
                {feed.isFetching && feed.items.length
                  ? "正在更新…"
                  : `${feed.items.length ? `${feed.items.length} 件已展示` : ""}`}
              </span>
            </div>
          </div>
          {feed.error && (
            <div className="error-state" role="status">
              <span>暂时无法读取商品，请重试。</span>
              <button onClick={() => void feed.retry()}>重试</button>
            </div>
          )}
          {feed.missingIds.length > 0 && (
            <p className="muted">部分候选商品暂不可展示，其他商品仍可查看。</p>
          )}
          {!feed.isLoading && !feed.items.length && !feed.error && (
            <div className={styles.empty}>
              <h2>还没有合适的商品。</h2>
              <p>和助手聊聊，试试调整你的需求。</p>
            </div>
          )}
          <AnimatedPresence viewKey={feed.viewKey}>
            <ProductGrid
              items={feed.items}
              loading={feed.isLoading}
              onOpen={(p) => {
                setPanel(null);
                navigate(`/demo/commerce/products/${encodeURIComponent(p.id)}`);
              }}
            />
          </AnimatedPresence>
          {feed.hasMore && (
            <div className={styles.more}>
              <button
                disabled={feed.isLoadingMore}
                onClick={() => void feed.loadMore()}
              >
                {feed.isLoadingMore ? "正在加载…" : "继续浏览"}
              </button>
            </div>
          )}
          <footer className={styles.footer}>
            演示商品，价格与库存为模拟数据
          </footer>
        </main>
        <AnimatePresence initial={false}>
          {chatOpen && (
            <motion.div
              key="chat"
              className={styles.chat}
              {...panelMotion(reduced)}
            >
              <ChatPanel
                session={session}
                draftReference={reference}
                onClearReference={() => setReference(null)}
                onCollapse={() => setChatOpen(false)}
              />
            </motion.div>
          )}
        </AnimatePresence>
        {!chatOpen && (
          <button className={styles.openChat} onClick={() => setChatOpen(true)}>
            <MessageCircle size={18} />
            购物助手
          </button>
        )}
      </div>
      <Drawer
        open={Boolean(detail || panel)}
        onClose={closePanel}
        title={
          detail ? "商品详情" : panel === "history" ? "会话记录" : "购物袋"
        }
      >
        {detail ? (
          <ProductDetail
            id={detail.params.productId!}
            onReference={quoteReference}
          />
        ) : panel === "history" ? (
          <HistoryPanel
            histories={session.histories}
            current={session.sessionId}
            onSelect={(id) => {
              closePanel();
              void session.selectHistory(id);
              setChatOpen(true);
            }}
            onNew={() => {
              closePanel();
              session.newSession();
              setChatOpen(true);
            }}
          />
        ) : (
          <CartPanel
            cart={session.cart}
            sessionId={session.sessionId}
            onChat={() => {
              closePanel();
              setChatOpen(true);
            }}
          />
        )}
      </Drawer>
    </div>
  );
}
