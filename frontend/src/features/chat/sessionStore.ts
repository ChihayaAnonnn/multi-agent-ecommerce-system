import { apiClient,ApiError,post } from '../../api/client';
import type { ApiClient } from '../../api/client';
import type { Candidate,Cart,History,Message,Proposal,TurnBody,TurnResponse } from '../../api/contracts';
import { readHistory,writeHistory } from './historyStore';
export type Reference={product_id:string;sku_id?:string;name?:string};
type State={ready:boolean;busy:boolean;error:string;userId:string|null;sessionId:string|null;messages:Message[];histories:History[];candidates:Candidate[]|null;pendingAction:Proposal|null;cart:Cart|null;expired:boolean};
export class ShoppingSessionStore {
 private client:ApiClient;private listeners=new Set<()=>void>();private epoch=0;private startPromise:Promise<void>|null=null;
 private state:State={ready:false,busy:false,error:'',userId:null,sessionId:null,messages:[],histories:[],candidates:null,pendingAction:null,cart:null,expired:false};
 constructor(client:ApiClient=apiClient){this.client=client;}
 getSnapshot=()=>this.state;
 subscribe=(fn:()=>void)=>{this.listeners.add(fn);return()=>{this.listeners.delete(fn);};};
 private update(values:Partial<State>){this.state={...this.state,...values};this.listeners.forEach(fn=>fn());}
 private persist(){
  if(!this.state.userId)return;
  const h=this.state.histories.find(h=>h.id===this.state.sessionId);
  if(h){h.messages=this.state.messages.slice(-100);h.expired=this.state.expired;}
  if(!writeHistory(this.state.userId,this.state.histories))this.update({error:'浏览器无法保存记录，当前对话仍可继续。'});
 }
 start():Promise<void>{
  if(!this.startPromise)this.startPromise=(async()=>{
   this.update({busy:true,error:''});
   try{const auth=await this.client.bootstrap();const histories=readHistory(auth.user_id);this.update({ready:true,userId:auth.user_id,histories,busy:false});if(histories.length)await this.selectHistory(histories[0].id);}
   catch(e){this.update({error:(e as Error).message,busy:false});this.startPromise=null;}
  })();return this.startPromise;
 }
 newSession=()=>{this.epoch++;this.update({sessionId:null,messages:[],candidates:null,pendingAction:null,cart:null,expired:false,error:'',busy:false});};
 async selectHistory(id:string){
  const history=this.state.histories.find(h=>h.id===id);if(!history)return;
  const epoch=++this.epoch;this.update({busy:true,error:''});
  try{await this.client.request(`/api/v1/commerce/sessions/${encodeURIComponent(id)}`);history.expired=false;}
  catch(e){if(epoch!==this.epoch)return;if((e as ApiError).code==='session_expired')history.expired=true;else {this.update({busy:false,error:(e as Error).message});return;}}
  if(epoch!==this.epoch)return;
  let candidates:Candidate[]|null=null,pendingAction:Proposal|null=null,cart:Cart|null=null;
  for(const m of history.messages){const r=m.response;if(!r)continue;if(r.status==='shortlist')candidates=r.items||[];if(r.proposed_action)pendingAction=r.proposed_action;if(r.cart)cart=r.cart;if(r.status==='cart_updated'||r.status==='handoff'||r.action_result==='declined')pendingAction=null;}
  this.update({sessionId:id,messages:history.messages,candidates,pendingAction,cart,expired:!!history.expired,busy:false});this.persist();
 }
 private body(text:string,references:Reference[]):TurnBody{
  const body:TurnBody={turn_id:crypto.randomUUID(),message:text};
  const action=this.state.pendingAction;
  if(action && ['确认','确认加购','确认修改','确认移除','确认结账','取消'].includes(text)){body.respond_to_action_id=action.action_id;body.idempotency_key=crypto.randomUUID();return body;}
  const nums:Record<string,number>={'一':1,'二':2,'三':3};
  const inferred=[...text.matchAll(/第([123一二三])(?:款|个)/g)].map(m=>this.state.candidates?.[(nums[m[1]]||Number(m[1]))-1]).filter(Boolean) as Candidate[];
  const refs=references.length?references:inferred;
  if(/比较|对比/.test(text) && refs.length>=2)body.product_ids=[...new Set(refs.map(r=>r.product_id))].slice(0,3);
  else if(refs.length===1){body.selected_product_id=refs[0].product_id;body.selected_sku_id=refs[0].sku_id;if(/加入|加购/.test(text)){body.message='加入购物车：'+text;body.quantity=1;}}
  return body;
 }
 async sendMessage(text:string,references:Reference[]=[]){
  text=text.trim();if(!this.state.ready||this.state.busy||this.state.expired||!text)return;
  const epoch=this.epoch;this.update({busy:true,error:''});
  try{
   if(!this.state.sessionId){const session=await this.client.request<{session_id:string}>('/api/v1/commerce/sessions',post({}));if(epoch!==this.epoch)return;
    const h={id:session.session_id,title:text.slice(0,28),messages:[]};this.update({sessionId:session.session_id,histories:[h,...this.state.histories].slice(0,50)});}
   const body=this.body(text,references);const path=`/api/v1/commerce/sessions/${encodeURIComponent(this.state.sessionId!)}/turns`;
   const reply:Message={id:crypto.randomUUID(),role:'assistant',pending:true,retry:{path,body,sent:false}};
   this.update({messages:[...this.state.messages,{id:crypto.randomUUID(),role:'user' as const,text},reply].slice(-100)});this.persist();await this.deliver(reply,epoch);
  }catch(e){if(epoch===this.epoch)this.update({error:(e as Error).message});}
  finally{if(epoch===this.epoch)this.update({busy:false});}
 }
 private async deliver(reply:Message,epoch:number){
  const retry=reply.retry!;reply.pending=true;reply.error=undefined;this.update({messages:[...this.state.messages]});
  try{
   if(!retry.sent){if(/加入|加购|改成|移除|删除|结账|去付款/.test(retry.body.message)&&!retry.body.respond_to_action_id){const cart=await this.client.request<Cart>(`/api/v1/commerce/sessions/${encodeURIComponent(this.state.sessionId!)}/cart`);if(epoch!==this.epoch)return;retry.body.expected_cart_version=cart.version;}retry.sent=true;this.persist();}
   const response=await this.client.request<TurnResponse>(retry.path,post(retry.body));if(epoch!==this.epoch)return;
   reply.response=response;reply.pending=false;
   if(response.status==='unavailable'||response.errors?.some(e=>e.code==='turn_processing'))reply.error=response.question||'暂时无法处理，请重试。';else delete reply.retry;
   const next:Partial<State>={messages:[...this.state.messages]};
   if(response.status==='shortlist')next.candidates=response.items||[];
   if(response.proposed_action)next.pendingAction=response.proposed_action;
   if(response.cart)next.cart=response.cart;
   if(response.status==='cart_updated'||response.status==='handoff'||response.action_result==='declined')next.pendingAction=null;
   this.update(next);this.persist();
  }catch(e){if(epoch!==this.epoch)return;reply.pending=false;reply.error=(e as Error).message;this.update({messages:[...this.state.messages]});this.persist();}
 }
 async retryTurn(id:string){const reply=this.state.messages.find(m=>m.id===id);if(!reply?.retry||this.state.busy||this.state.expired)return;const epoch=this.epoch;this.update({busy:true});try{await this.deliver(reply,epoch);}finally{if(epoch===this.epoch)this.update({busy:false});}}
 async loadCart(){if(!this.state.sessionId)return;const epoch=this.epoch;try{const cart=await this.client.request<Cart>(`/api/v1/commerce/sessions/${encodeURIComponent(this.state.sessionId)}/cart`);if(epoch===this.epoch)this.update({cart});}catch(e){if(epoch===this.epoch)this.update({error:(e as Error).message});}}
 confirmAction=(approved:boolean)=>this.sendMessage(approved?'确认':'取消');
}
export const shoppingStore=new ShoppingSessionStore();
