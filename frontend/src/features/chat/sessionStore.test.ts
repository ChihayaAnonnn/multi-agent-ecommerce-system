import { test,expect,vi } from 'vitest';
import { ShoppingSessionStore } from './sessionStore';
const ok=(data:unknown)=>Promise.resolve(data);
test('switching sessions ignores delayed turn response',async()=>{
 let finish!:(v:unknown)=>void;
 const client={bootstrap:()=>ok({user_id:'u'}),request:vi.fn((p:string)=>p.endsWith('/turns')?new Promise(r=>{finish=r;}):ok({session_id:'s',version:1}))};
 const store=new ShoppingSessionStore(client as any);await store.start();
 const send=store.sendMessage('hello');await vi.waitFor(()=>expect(finish).toBeDefined());
 store.newSession();finish({status:'shortlist',items:[{product_id:'old',sku_id:'s'}]});await send;
 expect(store.getSnapshot().messages).toEqual([]);expect(store.getSnapshot().candidates).toBeNull();
});
test('retry keeps exact original turn and idempotency body after network failure',async()=>{
 const bodies:unknown[]=[];let failed=true;
 const client={bootstrap:()=>ok({user_id:'u'}),request:vi.fn((p:string,init?:RequestInit)=>{
 if(p.endsWith('/turns')){bodies.push(JSON.parse(init!.body as string));if(failed){failed=false;return Promise.reject(new Error('offline'));}return ok({status:'clarify',question:'用途？'});}
 return ok({session_id:'s',version:1});})};
 const store=new ShoppingSessionStore(client as any);await store.start();await store.sendMessage('买一个');
 const reply=store.getSnapshot().messages.at(-1)!;expect(reply.retry).toBeDefined();
 await store.retryTurn(reply.id);expect(bodies[0]).toEqual(bodies[1]);
});
test('clarification retains candidates and histories stay user scoped',async()=>{
 localStorage.clear();let n=0;
 const client={bootstrap:()=>ok({user_id:'A'}),request:vi.fn((p:string)=>p.endsWith('/turns')?ok(++n===1?{status:'shortlist',items:[{product_id:'p',sku_id:'s'}]}:{status:'clarify',question:'继续？'}):ok({session_id:'session',version:1}))};
 const store=new ShoppingSessionStore(client as any);await store.start();await store.sendMessage('选商品');await store.sendMessage('继续');
 expect(store.getSnapshot().candidates?.[0].product_id).toBe('p');
 expect(localStorage.getItem('commerce.chat.A')).toContain('选商品');
 expect(localStorage.getItem('commerce.chat.B')).toBeNull();
});
