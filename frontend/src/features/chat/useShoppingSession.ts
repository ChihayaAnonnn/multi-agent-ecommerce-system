import { useEffect, useSyncExternalStore } from "react";
import { shoppingStore } from "./sessionStore";
export function useShoppingSession() {
  const state = useSyncExternalStore(
    shoppingStore.subscribe,
    shoppingStore.getSnapshot,
  );
  useEffect(() => {
    void shoppingStore.start();
  }, []);
  return {
    ...state,
    newSession: shoppingStore.newSession,
    selectHistory: shoppingStore.selectHistory.bind(shoppingStore),
    sendMessage: shoppingStore.sendMessage.bind(shoppingStore),
    retryTurn: shoppingStore.retryTurn.bind(shoppingStore),
    confirmAction: shoppingStore.confirmAction,
    loadCart: shoppingStore.loadCart.bind(shoppingStore),
    reconnect: () => shoppingStore.start(),
  };
}
