// 独立契约测试：页面只通过聊天输入，凭据不持久化，会话恢复须由服务端验证。
const nodes = new Map(), saved = new Map();
class Node {
    constructor(){this.hidden=false;this.disabled=false;this.children=[];this.dataset={};this.value='';this.isConnected=true;this.textContent='';this.style={};this.scrollHeight=100;this.classList={add(){},remove(){},toggle(){}};}
    append(...xs){this.children.push(...xs);} replaceChildren(...xs){this.children=xs;}
    setAttribute(){} focus(){} scrollIntoView(){} scrollTo(){} addEventListener(){}
}
globalThis.document={body:new Node(),getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},createElement:()=>new Node(),createTextNode:s=>s,querySelectorAll:selector=>{const all=[];const walk=node=>{if(node?.className==='retry-button')all.push(node);for(const child of node?.children||[])walk(child);};for(const node of nodes.values())walk(node);return all;},addEventListener(){}};
globalThis.localStorage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value)};
globalThis.crypto={randomUUID:()=>String(Math.random())};
globalThis.requestAnimationFrame=fn=>fn();
globalThis.IntersectionObserver=class{observe(){} unobserve(){} disconnect(){}};
let requests=[], refreshResolve;
globalThis.fetch=async(path,opts)=>{requests.push([path,opts]);if(path.includes('refresh'))return await new Promise(resolve=>refreshResolve=()=>resolve({ok:true,json:async()=>({access_token:'new',csrf_token:'new-csrf',user_id:'visitor'})}));return {status:opts.headers.Authorization==='Bearer new'?200:401,ok:opts.headers.Authorization==='Bearer new',json:async()=>({})};};
load('demo/commerce.js');
function assert(ok,msg){if(!ok)throw Error(msg);}
async function ticks(){for(let i=0;i<30;i++)await Promise.resolve();}
async function main(){
    accessToken='old';csrfToken='csrf';
    const calls=[api('/one'),api('/two'),api('/three')];await ticks();
    assert(requests.filter(x=>x[0].includes('refresh')).length===1,'concurrent 401 share one refresh');
    refreshResolve();await Promise.all(calls);assert(accessToken==='new','refresh credentials');
    accessToken='old';const stale=api('/stale').catch(e=>e);await ticks();epoch++;accessToken='other-login';csrfToken='other-csrf';refreshResolve();await stale;
    assert(accessToken==='other-login'&&csrfToken==='other-csrf','stale refresh cannot overwrite identity');
    ready=true;historyKey='commerce.chat.visitor';chats=[{id:'s1',title:'耳机',messages:[]}];activeChat=chats[0];sessionId='s1';
    accessToken='SECRET_TOKEN';csrfToken='SECRET_CSRF';
    let submitted=[];api=async(path,body)=>{
        if(path.endsWith('/turns')){submitted.push(body);return {turn_id:body.turn_id,status:'clarify',question:'请补充用途',errors:[]};}
        return {version:1,items:[],checkout_eligible:false};
    };
    $('message').value='想买耳机，预算500元';await submitMessage();
    assert(submitted.length===1&&submitted[0].message==='想买耳机，预算500元'&&!submitted[0].constraints,'only natural message and transport ID');
    assert(activeChat.messages.length===2&&activeChat.messages[0].text==='想买耳机，预算500元','both sides recorded');
    assert(!saved.get(historyKey).includes('SECRET_'),'never persist credentials');
    pendingAction={action_id:'a',kind:'add'};
    const confirmed=turnInput('确认加购');assert(confirmed.respond_to_action_id==='a'&&confirmed.idempotency_key,'chat confirmation binds current action');
    assert(turnInput('确认加购').idempotency_key===confirmed.idempotency_key,'confirmation retry reuses idempotency key');
    restoreContext([{role:'assistant',response:{status:'confirm_action',proposed_action:{action_id:'a'}}},{role:'assistant',response:{status:'clarify',action_result:'declined'}}]);
    assert(pendingAction===null,'restored cancellation clears pending action');
    currentItems=[{product_id:'p1',sku_id:'k1'},{product_id:'p2',sku_id:'k2'}];
    assert(turnInput('加入第2款').selected_sku_id==='k2','explicit reference maps only displayed SKU');
    assert(turnInput('加入第2款').quantity===1 && turnInput('加入第2款').message.includes('加入购物车'),'explicit add produces a proposal for one unit');
    restoreContext([{role:'assistant',response:{status:'shortlist',items:[]}}]);
    assert(!turnInput('加入第2款').selected_sku_id,'empty shortlist drops stale SKU reference');
    const before=activeChat;api=async()=>{throw Object.assign(Error('不可访问'),{status:404});};
    await openChat({id:'other',title:'其他',messages:[]}).catch(()=>{});
    assert(activeChat===before&&sessionId==='s1','rejected session never becomes active');
    api=async()=>{throw Object.assign(Error('网络故障'),{status:503});};
    await bootstrapIdentity().catch(()=>{});
    assert(accessToken==='SECRET_TOKEN','dependency failure does not silently create a new identity');
    let turnCalls=0, retryIds=[];api=async(path,body)=>{
        if(path.endsWith('/turns')){retryIds.push(body.turn_id);turnCalls++;if(turnCalls===1)throw Error('connection lost');return {status:'clarify',question:'ok',errors:[]};}
        return {};
    };
    $('message').value='查询耳机';await submitMessage().catch(()=>{});
    const failed=activeChat.messages[activeChat.messages.length-1];assert(failed.retry,'failed request retains original input');
    const retryButton=$('messages').children[$('messages').children.length-1].children.find(node=>node.className==='retry-button');
    assert(retryButton&&!retryButton.disabled,'retry button reenabled after failure');
    const original=failed.retry.body.turn_id;await retryTurn(failed);
    assert(activeChat.messages.filter(m=>m.role==='user'&&m.text==='查询耳机').length===1,'retry does not duplicate user message');
    assert(failed.response&&failed.retry===undefined,'retry replaces failed reply');
    assert(retryIds.length===2&&retryIds[0]===original&&retryIds[1]===original,'retry submits original turn ID');
    print('10 desktop chat contracts passed');
}
main().catch(e=>{print(String(e));quit(1);});
