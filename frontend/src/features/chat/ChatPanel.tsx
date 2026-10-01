import { motion,useReducedMotion } from 'motion/react';
import { useState,useRef,useEffect } from 'react';
import { ArrowUp,Plus,PanelRightClose,X,ArrowDown,MessageCircle } from 'lucide-react';
import type { useShoppingSession } from './useShoppingSession';
import type { Reference } from './sessionStore';
import { ActionSummary,responseText } from './ActionSummary';
import styles from './ChatPanel.module.css';
type Props={session:ReturnType<typeof useShoppingSession>;draftReference:Reference|null;onClearReference:()=>void;onCollapse:()=>void};
export function ChatPanel({session:s,draftReference,onClearReference,onCollapse}:Props){
 const reduced=useReducedMotion();const [text,setText]=useState(''),[unread,setUnread]=useState(false);const list=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null),atBottom=useRef(true),lastSession=useRef(s.sessionId);
 useEffect(()=>{if(lastSession.current!==s.sessionId){setText('');onClearReference();lastSession.current=s.sessionId;atBottom.current=true;}if(atBottom.current){list.current?.scrollTo({top:list.current.scrollHeight,behavior:'instant'});setUnread(false);}else setUnread(true);},[s.messages,s.sessionId]);
 useEffect(()=>{if(draftReference)input.current?.focus();},[draftReference]);
 const unavailable=!s.ready||s.busy||s.expired;
 const send=()=>{if(unavailable||!text.trim())return;void s.sendMessage(text.trim(),draftReference?[draftReference]:[]);setText('');onClearReference();};
 return <aside className={styles.panel} aria-label="购物助手">
  <header className={styles.header}><div><span className={styles.indicator}/><strong>购物助手</strong></div><div><button className="icon-button" aria-label="新建对话" title="新建对话" onClick={()=>s.newSession()}><Plus size={18}/></button><button className="icon-button" aria-label="收起购物助手" onClick={onCollapse}><PanelRightClose size={18}/></button></div></header>
  <div ref={list} className={styles.messages} role="log" aria-label="对话内容" aria-live="polite" onScroll={()=>{const node=list.current!;atBottom.current=node.scrollHeight-node.scrollTop-node.clientHeight<64;if(atBottom.current)setUnread(false);}}>
   {!s.messages.length&&<div className={styles.welcome}><div className={styles.welcomeIcon}><MessageCircle size={25} strokeWidth={1.3}/></div><h2>从你的日常开始。</h2><p>告诉我你想找什么，<br/>我们一起慢慢挑选。</p><div className={styles.example}>比如，“想给书桌添一盏灯，<br/>希望光线柔和一些。”</div></div>}
   {s.messages.map(m=><motion.article initial={{opacity:0}} animate={{opacity:1}} transition={{duration:reduced?.08:.14}} className={`${styles.message} ${m.role==='user'?styles.user:styles.assistant}`} key={m.id}>{m.role==='user'?<div>{m.text}</div>:<><span className={styles.assistantLabel}>购物助手</span>{m.pending?<p className={styles.pending}>正在理解你的需求…</p>:<>{m.response&&<><p>{responseText(m.response)}</p><ActionSummary response={m.response}/></>}{m.error&&<p className={styles.error}>{m.error}</p>}{m.retry&&!s.expired&&<button className={styles.retry} disabled={s.busy} onClick={()=>void s.retryTurn(m.id)}>重试这条消息</button>}</>}</>}</motion.article>)}
  </div>
  {unread&&<button className={styles.unread} onClick={()=>{atBottom.current=true;list.current?.scrollTo({top:list.current.scrollHeight,behavior:'instant'});setUnread(false);}}><ArrowDown size={14}/>有新消息</button>}
  <div className={styles.composerZone}>
   {s.error&&<div className={styles.error} role="status">{s.error}{!s.ready&&<button onClick={()=>void s.reconnect()}>重新连接</button>}</div>}
   {draftReference&&<div className={styles.reference}><span>{draftReference.name||'已选择的商品'}</span><button className="icon-button" aria-label="移除商品引用" onClick={onClearReference}><X size={14}/></button></div>}
   <form className={styles.composer} onSubmit={e=>{e.preventDefault();send();}}><textarea ref={input} aria-label="输入购物需求" placeholder="说说你想找什么…" rows={2} maxLength={2000} value={text} disabled={unavailable} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send();}}}/><div className={styles.composerBottom}><span>{!s.ready?'正在准备对话…':s.expired?'会话已过期，请新建对话':s.busy?'正在处理…':'Enter 发送'}</span><button className={styles.send} disabled={unavailable||!text.trim()} aria-label="发送消息" type="submit"><ArrowUp size={19}/></button></div></form>
   <p className={styles.note}>购物操作会先与你确认</p>
  </div>
 </aside>;
}
