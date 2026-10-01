import { test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChatPanel } from "./ChatPanel";
test("composition enter does not send but ordinary enter sends once", () => {
  const sendMessage = vi.fn();
  const session = {
    ready: true,
    busy: false,
    expired: false,
    messages: [],
    pendingAction: null,
    error: "",
    sendMessage,
    newSession: vi.fn(),
    retryTurn: vi.fn(),
    reconnect: vi.fn(),
  };
  render(
    <ChatPanel
      session={session as any}
      draftReference={null}
      onClearReference={() => {}}
      onCollapse={() => {}}
    />,
  );
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "耳机" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(sendMessage).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter", isComposing: false });
  expect(sendMessage).toHaveBeenCalledWith("耳机", []);
});
