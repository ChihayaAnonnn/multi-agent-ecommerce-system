import { useEffect,useRef,type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence,motion,useReducedMotion,useIsPresent } from 'motion/react';
import { X } from 'lucide-react';
import { panelMotion } from '../app/motion';
import styles from './Drawer.module.css';
function Layer({children,reduced}:{children:ReactNode;reduced:boolean}){const present=useIsPresent();return <motion.div className={styles.layer} initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}} transition={{duration:reduced?.08:.22}} aria-hidden={!present||undefined} inert={!present} style={{pointerEvents:present?'auto':'none'}}>{children}</motion.div>;}
export function Drawer({open,onClose,title,children}:{open:boolean;onClose:()=>void;title:string;children:ReactNode}){
 const dialog=useRef<HTMLDivElement>(null),close=useRef(onClose);close.current=onClose;const reduced=!!useReducedMotion();
 useEffect(()=>{if(!open)return;const previous=document.activeElement as HTMLElement|null;
 const node=dialog.current;node?.querySelector<HTMLElement>('button')?.focus();
 const keyboard=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();close.current();}if(e.key==='Tab'&&node){const targets=[...node.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input,textarea,select,[tabindex="0"]')].filter(el=>getComputedStyle(el).display!=='none'&&getComputedStyle(el).visibility!=='hidden');const first=targets[0],last=targets.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}};
 document.addEventListener('keydown',keyboard);return()=>{document.removeEventListener('keydown',keyboard);if(previous?.isConnected)previous.focus();};},[open]);
 return createPortal(<AnimatePresence>{open&&<Layer key="drawer" reduced={reduced}><div className={styles.scrim} onClick={onClose}/><motion.div ref={dialog} className={styles.panel} {...panelMotion(reduced)} role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-button" aria-label="关闭面板" onClick={onClose}><X size={20}/></button></header><div className={styles.content}>{children}</div></motion.div></Layer>}</AnimatePresence>,document.body);
}
