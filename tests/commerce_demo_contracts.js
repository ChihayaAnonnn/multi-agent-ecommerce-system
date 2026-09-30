// 独立 JS 契约测试；模拟 DOM 与 fetch，不依赖真实浏览器或网络。
const nodes=new Map();
class Node {
    constructor(){this.hidden=false;this.disabled=false;this.children=[];this.dataset={};this.value='';this.isConnected=true;this.textContent='';}
    append(...xs){this.children.push(...xs);} replaceChildren(...xs){this.children=xs;} querySelectorAll(){return [];} reportValidity(){return true;}
}
globalThis.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},createElement:()=>new Node(),createTextNode:s=>s};
globalThis.crypto={randomUUID:()=>String(Math.random())};
globalThis.IntersectionObserver=class{observe(){} unobserve(){} disconnect(){}};
let requests=[], refreshResolve;
globalThis.fetch=async(path,opts)=>{requests.push([path,opts]);if(path.includes('catalog'))return {ok:true,json:async()=>({items:[]})};if(path.includes('refresh'))return await new Promise(resolve=>refreshResolve=()=>resolve({ok:true,json:async()=>({access_token:'new',csrf_token:'new-csrf'})}));return {status:opts.headers.Authorization==='Bearer new'?200:401,ok:opts.headers.Authorization==='Bearer new',json:async()=>({})};};
load('demo/commerce.js');
function assert(ok,msg){if(!ok)throw Error(msg);}
async function ticks(){for(let i=0;i<15;i++)await Promise.resolve();}
async function main(){
    accessToken='old';csrfToken='csrf';
    const calls=[api('/one'),api('/two'),api('/three')];await ticks();
    assert(requests.filter(x=>x[0].includes('refresh')).length===1,'concurrent 401 must share one refresh');
    refreshResolve();await Promise.all(calls);assert(accessToken==='new','refresh credentials');
    accessToken='old';const stale=api('/stale').catch(e=>e);await ticks();epoch++;accessToken='other-login';csrfToken='other-csrf';refreshResolve();await stale;
    assert(accessToken==='other-login'&&csrfToken==='other-csrf','stale refresh must not overwrite another login');
    pendingAction={action_id:'old'};$('action-panel').hidden=false;
    renderTurn({status:'clarify',intent:'confirmation',action_result:'declined',errors:[],question:'已取消'});
    assert(!pendingAction&&$('action-panel').hidden,'chat cancellation clears action card');
    selectedSku={sku_id:'unbuyable'};$('propose').disabled=false;$('selection').textContent='old sku';
    renderTurn({status:'shortlist',items:[],errors:[],incomplete:false});
    assert($('propose').disabled&&!$('selection').textContent.includes('old sku'),'unbuyable selection clears enabled stale summary');
    let kinds=[];event=async(kind,payload)=>kinds.push([kind,payload]);refreshCart=async()=>{};
    api=async()=>({status:'cart_updated'});pendingAction={action_id:'a',kind:'add'};confirmation=null;
    await confirmAction(true);assert(kinds.some(x=>x[0]==='cart_updated'),'button commit emits cart_updated');
    kinds=[];api=async()=>({status:'declined'});pendingAction={action_id:'b',kind:'add'};confirmation=null;
    await confirmAction(false);assert(kinds.some(x=>x[0]==='confirm'&&x[1].status==='declined')&&!kinds.some(x=>x[0]==='cart_updated'),'decline event distinguishes cancellation');
    print('5 commerce demo contracts passed');
}
main().catch(e=>{print(String(e));quit(1);});
