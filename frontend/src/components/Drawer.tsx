import { useEffect,useRef,type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import styles from './Drawer.module.css';
export function Drawer({open,onClose,title,children}:{open:boolean;onClose:()=>void;title:string;children:ReactNode}){
 const dialog=useRef<HTMLDivElement>(null),close=useRef(onClose);close.current=onClose;
 useEffect(()=>{if(!open)return;const previous=document.activeElement as HTMLElement|null;
 const node=dialog.current;node?.querySelector<HTMLElement>('button')?.focus();
 const keyboard=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();close.current();}if(e.key==='Tab'&&node){const targets=[...node.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input,textarea,select,[tabindex="0"]')].filter(el=>el.getClientRects().length||process.env.NODE_ENV==='test');const first=targets[0],last=targets.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}};
 document.addEventListener('keydown',keyboard);return()=>{document.removeEventListener('keydown',keyboard);if(previous?.isConnected)previous.focus();};},[open]);
 if(!open)return null;
 return createPortal(<div className={styles.layer}><div className={styles.scrim} onClick={onClose}/><div ref={dialog} className={styles.panel} role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-button" aria-label="关闭面板" onClick={onClose}><X size={20}/></button></header><div className={styles.content}>{children}</div></div></div>,document.body);
}
