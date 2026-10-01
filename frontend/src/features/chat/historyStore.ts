import type { History } from "../../api/contracts";
export function readHistory(userId: string): History[] {
  try {
    const raw = JSON.parse(
      localStorage.getItem(`commerce.chat.${userId}`) || "[]",
    );
    if (!Array.isArray(raw)) return [];
    return raw
      .filter(
        (h) =>
          typeof h?.id === "string" &&
          typeof h?.title === "string" &&
          Array.isArray(h?.messages),
      )
      .slice(0, 50)
      .map((h) => ({
        ...h,
        messages: h.messages
          .filter((m: any) => m && ["user", "assistant"].includes(m.role))
          .slice(-100)
          .map((m: any) => ({
            ...m,
            id: m.id || crypto.randomUUID(),
            pending: false,
            ...(m.pending ? { error: "这条消息尚未收到结果，可以重试。" } : {}),
          })),
      }));
  } catch {
    return [];
  }
}
export function writeHistory(userId: string, history: History[]): boolean {
  try {
    localStorage.setItem(
      `commerce.chat.${userId}`,
      JSON.stringify(history.slice(0, 50)),
    );
    return true;
  } catch {
    return false;
  }
}
