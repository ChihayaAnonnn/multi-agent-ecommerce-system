export const ease = [0.22, 1, 0.36, 1] as const;
export function panelMotion(reduced: boolean) {
  return {
    initial: { opacity: 0, x: reduced ? 0 : 32 },
    animate: { opacity: 1, x: 0 },
    exit: { opacity: 0, x: reduced ? 0 : 20 },
    transition: { duration: reduced ? 0.08 : 0.26, ease },
  };
}
