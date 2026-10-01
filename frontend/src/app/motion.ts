export const ease=[.22,1,.36,1] as const;
export function panelMotion(reduced:boolean){return {initial:{opacity:0,x:reduced?0:32},animate:{opacity:1,x:0},exit:{opacity:0,x:reduced?0:20},transition:{duration:reduced?.08:.26,ease}};}
