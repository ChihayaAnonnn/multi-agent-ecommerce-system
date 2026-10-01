import { test,expect,vi,beforeEach } from 'vitest';
import { ApiClient } from './client';
const auth={access_token:'access',csrf_token:'csrf',user_id:'u'};
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status});
beforeEach(()=>vi.unstubAllGlobals());
test('network failure does not create a new guest',async()=>{
 let requests:string[]=[];
 vi.stubGlobal('fetch',async(p:string)=>{requests.push(p);throw new TypeError('offline');});
 await expect(new ApiClient().bootstrap()).rejects.toThrow('offline');
 expect(requests).toEqual(['/api/v1/auth/csrf']);
});
test('concurrent expired requests share one refresh and receive new token',async()=>{
 let refresh=0;
 vi.stubGlobal('fetch',async(p:string,init:RequestInit)=>{
  if(p.endsWith('/csrf'))return response({csrf_token:'csrf'});
  if(p.endsWith('/refresh')){refresh++;await new Promise(r=>setTimeout(r,5));return response({...auth,access_token:refresh===1?'old':'new'});}
  return response({ok:true},new Headers(init.headers).get('Authorization')==='Bearer new'?200:401);
 });
 const client=new ApiClient();await client.bootstrap();
 const result=await Promise.all([client.request('/a'),client.request('/b')]);
 expect(result).toEqual([{ok:true},{ok:true}]);expect(refresh).toBe(2);
 expect(localStorage.getItem('access_token')).toBeNull();
});
