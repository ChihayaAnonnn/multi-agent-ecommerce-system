import type { ReactNode } from "react";
import {
  AnimatePresence,
  motion,
  useReducedMotion,
  useIsPresent,
} from "motion/react";
function Frame({ children }: { children: ReactNode }) {
  const present = useIsPresent(),
    reduced = useReducedMotion();
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reduced ? 0.08 : 0.2 }}
      style={
        present
          ? { position: "relative" }
          : { position: "absolute", inset: 0, pointerEvents: "none" }
      }
      aria-hidden={!present || undefined}
      inert={!present}
    >
      {children}
    </motion.div>
  );
}
export function AnimatedPresence({
  viewKey,
  children,
}: {
  viewKey: string;
  children: ReactNode;
}) {
  return (
    <div style={{ position: "relative" }}>
      <AnimatePresence initial={false} mode="sync">
        <Frame key={viewKey}>{children}</Frame>
      </AnimatePresence>
    </div>
  );
}
