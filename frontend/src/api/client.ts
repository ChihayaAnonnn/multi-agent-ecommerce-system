import type { Identity } from './contracts';
export class ApiError extends Error {
 status:number;code?:string;
 constructor(status:number,data:any){super(typeof data?.detail==='string'?data.detail:labels[data?.detail?.code] || '请求未完成，请稍后重试。');this.status=status;this.code=data?.detail?.code;}
}
const labels:Record<string,string>={session_expired:'会话已过期，请新建对话。',dependency_unavailable:'服务暂不可用，请稍后重试。',cart_version_conflict:'购物袋已变化，请重新确认。',action_expired:'确认已过期，请重新提出操作。',turn_processing:'这条消息仍在处理，可以稍后重试。',rate_limited:'操作较频繁，请稍后再试。'};
export class ApiClient {
 private token='';private csrf='';private refreshPromise:Promise<Identity>|null=null;private bootPromise:Promise<Identity>|null=null;
 private async raw<T>(path:string,init:RequestInit={},auth=true):Promise<T>{
  const headers=new Headers(init.headers);if(init.body)headers.set('Content-Type','application/json');
  if(auth && this.token)headers.set('Authorization',`Bearer ${this.token}`);
  if(path.endsWith('/refresh') && this.csrf)headers.set('X-CSRF-Token',this.csrf);
  const r=await fetch(path,{...init,headers,credentials:'same-origin',cache:'no-store'});
  const data=await r.json().catch(()=>({}));if(!r.ok)throw new ApiError(r.status,data);return data as T;
 }
 private accept(auth:Identity){this.token=auth.access_token;this.csrf=auth.csrf_token;return auth;}
 private refresh():Promise<Identity>{
  if(!this.refreshPromise)this.refreshPromise=this.raw<Identity>('/api/v1/auth/refresh',{method:'POST',body:'{}'},false).then(a=>this.accept(a)).finally(()=>{this.refreshPromise=null;});
  return this.refreshPromise;
 }
 bootstrap():Promise<Identity>{
  if(!this.bootPromise)this.bootPromise=(async()=>{
   try {const csrf=await this.raw<{csrf_token:string}>('/api/v1/auth/csrf',{},false);this.csrf=csrf.csrf_token;return await this.refresh();}
   catch(e){if(!(e instanceof ApiError) || e.status!==401)throw e;return this.accept(await this.raw<Identity>('/demo/commerce/guest',{method:'POST',body:'{}'},false));}
  })().catch(e=>{this.bootPromise=null;throw e;});return this.bootPromise;
 }
 async request<T>(path:string,init:RequestInit={}):Promise<T>{
  const used=this.token;
  try{return await this.raw<T>(path,init);}
  catch(e){if(!(e instanceof ApiError)||e.status!==401||!this.csrf)throw e;if(this.token===used)await this.refresh();return this.raw<T>(path,init);}
 }
}
export const apiClient=new ApiClient();
export function apiRequest<T>(path:string,init?:RequestInit):Promise<T>{return apiClient.request<T>(path,init);}
export const post=(body:unknown):RequestInit=>({method:'POST',body:JSON.stringify(body)});
