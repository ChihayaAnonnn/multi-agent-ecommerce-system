'use strict';
// 访问令牌、CSRF 与会话仅在当前页面内存中保存。
const $ = id => document.getElementById(id);
let accessToken = null, csrfToken = null, sessionId = null, epoch = 0;
let cartView = null, selectedSku = null, pendingAction = null, confirmation = null;
let refreshOperation = null;
let requestId = crypto.randomUUID(), lastRetry = null;
const catalogDetails = new Map(), comparedProducts = new Set();
const labels = {dependency_unavailable:'可信事实暂不可用，请稍后重试。',checkout_unavailable:'宿主交接暂不可用，可使用同一请求重试。',event_unavailable:'反馈采集暂不可用。',reconfirmation_required:'价格、规格或可售条件发生变化，请核对新摘要并再次确认。',cart_version_conflict:'购物车已被另一端修改，请刷新后重新生成提案。',session_expired:'购物会话已过期，请新建会话继续使用原购物车。',turn_processing:'当前轮次仍在处理中，请稍后使用原请求重试。',stock_unknown:'库存未知，暂不可购买。',out_of_stock:'当前缺货。',insufficient_stock:'当前可售量不足。',price_missing:'缺少可信现价。',constraints_mismatch:'所选规格不符合当前条件。',not_seen:'请选择当前会话已展示的商品与规格。',invalid_quantity:'数量须为正整数，且不能超过演示保护值。',action_expired:'提案已过期，请重新生成。',action_already_resolved:'该动作已处理或失效，请刷新购物车。',idempotency_conflict:'请求内容发生变化，请重新生成动作。',empty_cart:'购物车为空。',not_found:'无法访问这份资源。',model_degraded:'模型已降级：使用确定性结果和中性说明。'};
const money = amount => amount === null || amount === undefined ? '未提供' : new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'}).format(amount / 100);
const textValue = value => value === true ? '是' : value === false ? '否' : String(value ?? '未提供');
function element(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
function notify(text, error=false) { $('notice').textContent=text; $('notice').className='message'+(error?' error':''); }
function showRetry(fn) { lastRetry=fn; $('retry').hidden=!fn; }
function resetWorkspace() {
    sessionId=null; cartView=null; selectedSku=null; pendingAction=null; confirmation=null;
    comparedProducts.clear(); $('candidates').replaceChildren(element('p','请表达新的购物需求。','muted'));
    for(const id of ['comparison-panel','sku-panel','action-panel','handoff-panel']) $(id).hidden=true;
    $('cart').replaceChildren(element('p','购物车为空。','muted')); $('compare').disabled=true; $('propose').disabled=true; $('checkout').disabled=true; showRetry(null);
}
async function api(path, body, opts={}) {
    const startEpoch=epoch, tokenUsed=accessToken, headers={};
    if(body!==undefined)headers['Content-Type']='application/json';
    if(opts.auth!==false && accessToken)headers.Authorization='Bearer '+accessToken;
    if(opts.csrf && csrfToken)headers['X-CSRF-Token']=csrfToken;
    const response=await fetch(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),credentials:'same-origin',cache:'no-store'});
    const data=await response.json().catch(()=>({}));
    if(opts.auth!==false && startEpoch!==epoch)throw new Error('登录状态已变化，请重新操作。');
    if(response.status===401 && opts.auth!==false && !opts.retried && csrfToken) {
        if(accessToken===tokenUsed)await refreshCredentials(startEpoch);
        if(startEpoch!==epoch)throw new Error('登录状态已变化，请重新操作。');
        return api(path,body,{...opts,retried:true});
    }
    if(!response.ok) {
        const code=data.detail?.code || data.error_code;
        const err=new Error(labels[code] || (typeof data.detail==='string' ? data.detail : Array.isArray(data.detail)?data.detail.map(e=>e.msg).join('；'):'请求未完成，请核对输入或稍后重试。'));
        err.code=code; err.status=response.status; throw err;
    }
    return data;
}
async function refreshCredentials(startEpoch) {
    if(!refreshOperation || refreshOperation.epoch!==startEpoch) {
        const operation={epoch:startEpoch,promise:null};
        operation.promise=(async()=>{
            try {
                const refreshed=await api('/api/v1/auth/refresh',{}, {auth:false,csrf:true});
                if(startEpoch!==epoch)throw new Error('登录状态已变化，请重新操作。');
                accessToken=refreshed.access_token;csrfToken=refreshed.csrf_token;
            } finally { if(refreshOperation===operation)refreshOperation=null; }
        })();
        refreshOperation=operation;
    }
    return refreshOperation.promise;
}
async function run(fn,button) {
    if(button)button.disabled=true;
    try { await fn(); } catch(error) { notify(error.message,true); } finally { if(button && button.isConnected) {
        button.disabled=button===$('propose')?!selectedSku?.validated:button===$('checkout')?!cartView?.checkout_eligible:button===$('compare')?comparedProducts.size<2 || comparedProducts.size>3:false;
    } }
}
async function event(kind,payload={}) {
    if(!sessionId || !accessToken)return;
    const body={event_id:crypto.randomUUID(),session_id:sessionId,request_id:requestId,kind,payload};
    try { await api('/api/v1/commerce/events',body); $('event-note').textContent=''; }
    catch (_) { $('event-note').textContent='反馈采集暂不可用，已提交的购物结果仍保留。'; }
}
const exposures=new IntersectionObserver(entries=>{
    for(const entry of entries)if(entry.isIntersecting && entry.intersectionRatio>=.5) {
        const card=entry.target;
        void event('impression',{product_id:card.dataset.productId,sku_id:card.dataset.skuId,position:Number(card.dataset.position)});
        exposures.unobserve(card);
    }
},{threshold:.5});
async function newSession() {
    resetWorkspace(); const session=await api('/api/v1/commerce/sessions',{}); sessionId=session.session_id;
    await refreshCart(); notify('购物会话已就绪，请填写需求与明确条件。');
}
async function refreshCart() {
    if(!sessionId)return;
    cartView=await api(`/api/v1/commerce/sessions/${sessionId}/cart`);renderCart(cartView);
}
function renderCart(cart) {
    const root=$('cart');root.replaceChildren();
    if(!cart.items.length)root.append(element('p','购物车为空。','muted'));
    for(const line of cart.items) {
        const row=element('div',undefined,'line');row.append(element('strong',line.product_name));
        row.append(element('p',line.sku_options.map(v=>v.display_value).join(' / ') || '默认规格','muted small'));
        row.append(element('p',`${money(line.quote.price?.amount_minor)} × ${line.quantity}`));
        if(line.price_changed)row.append(element('span','价格已变化','badge warning'));
        if(line.reason!=='purchasable')row.append(element('p',labels[line.reason] || '当前不可购买。','small'));
        const controls=element('div',undefined,'row'), qty=element('input');qty.type='number';qty.min='1';qty.max='10';qty.value=line.quantity;qty.className='qty';
        const change=element('button','改量','secondary'), remove=element('button','移除','danger');
        change.onclick=()=>run(()=>sendTurn({message:'改成指定数量',selected_sku_id:line.sku_id,quantity:Number(qty.value),expected_cart_version:cart.version}),change);
        remove.onclick=()=>run(()=>sendTurn({message:'移除',selected_sku_id:line.sku_id,expected_cart_version:cart.version}),remove);
        controls.append(qty,change,remove);row.append(controls);root.append(row);
    }
    root.append(element('div','商品小计：'+money(cart.current_subtotal),'total'));
    $('checkout').disabled=!cart.checkout_eligible;
}
async function sendTurn(input) {
    const body={turn_id:crypto.randomUUID(),...input}, path=`/api/v1/commerce/sessions/${sessionId}/turns`;
    const attempt=async()=>{ const response=await api(path,body); requestId=response.request_id;renderTurn(response);showRetry(response.status==='unavailable'?attempt:null); };
    showRetry(attempt);await attempt();
}
function renderTurn(response) {
    const degraded=response.errors.some(e=>e.code==='model_degraded');
    if(response.intent==='confirmation' && pendingAction && (response.action_result==='declined' || response.status==='cart_updated'))void event('confirm',{action_id:pendingAction.action_id,status:response.action_result || response.status});
    if(response.action_result==='declined') { pendingAction=null;confirmation=null;$('action-panel').hidden=true; }
    if(response.status==='shortlist') {
        renderCandidates(response.items);$('scan-note').textContent=response.incomplete?'已达到受控扫描上限，结果未穷尽全部匹配。':'';
        notify(response.items.length?'以下候选符合已确认条件。'+(degraded?' '+labels.model_degraded:''):'没有符合条件且当前可售的候选，可调整条件后重新搜索。');
        if(selectedSku) {
            const item=response.items.find(i=>i.sku_id===selectedSku.sku_id);
            selectedSku.validated=Boolean(item);$('propose').disabled=!item;
            if(!item)$('selection').textContent='所选规格当前不符合可售条件，请重新选择。';
            if(item){ $('selection').textContent=`已选：${item.name} · ${item.sku.selected_values.map(v=>v.display_value).join(' / ') || '默认规格'} · ${money(item.price.amount_minor)}`; $('propose').disabled=false; }
        }
    } else if(response.status==='compare') renderComparison(response.comparison);
    else if(response.status==='confirm_action') { renderAction(response.proposed_action);notify('请核对新操作摘要，再明确确认。'); }
    else if(response.status==='cart_updated') { renderCart(response.cart);pendingAction=null;$('action-panel').hidden=true;notify('购物车更新已提交。');void event('cart_updated',{status:'cart_updated'}); }
    else if(response.status==='handoff') renderHandoff(response.handoff);
    else { notify(response.question || response.errors.map(e=>labels[e.code] || '暂时无法处理。').join(' ') || '请补充明确条件。',response.status==='unavailable'); }
    if(response.errors.some(e=>e.code==='reconfirmation_required'))notify(labels.reconfirmation_required,true);
}
function renderCandidates(items) {
    exposures.disconnect();const root=$('candidates');root.replaceChildren();comparedProducts.clear();$('compare').disabled=true;
    if(!items.length)root.append(element('p','当前条件下没有可售结果。','muted'));
    items.forEach((item,index)=>{
        const card=element('article',undefined,'card');card.dataset.productId=item.product_id;card.dataset.skuId=item.sku_id;card.dataset.position=index;
        card.append(element('h3',item.name),element('div',money(item.price.amount_minor),'price'));
        card.append(element('p',item.sku.selected_values.map(v=>v.display_value).join(' / ') || '默认规格','muted small'));
        card.append(element('p',`当前可售量：${item.availability.available_quantity}`,'small'),element('p',item.reason_text || '现行事实已核验。','muted small'));
        const checkLabel=element('label'),check=element('input');check.type='checkbox';
        check.onchange=()=>{check.checked?comparedProducts.add(item.product_id):comparedProducts.delete(item.product_id);$('compare').disabled=comparedProducts.size<2 || comparedProducts.size>3;};
        checkLabel.append(check,document.createTextNode(' 加入比较'));
        const choose=element('button','查看并选择规格','secondary');choose.onclick=()=>run(()=>loadSkuOptions(item.product_id),choose);
        card.append(checkLabel,choose);root.append(card);exposures.observe(card);
    });
}
async function loadSkuOptions(productId) {
    const detail=await api(`/api/v1/catalog/products/${productId}`,undefined,{auth:false});
    $('sku-panel').hidden=false;$('sku-options').replaceChildren();selectedSku=null;$('propose').disabled=true;
    $('selection').textContent='请选择一个实际存在的规格组合；选择后会重新核验价格和库存。';
    for(const sku of detail.skus.filter(s=>s.status==='active')) {
        const button=element('button',sku.selected_values.map(v=>v.display_value).join(' / ') || '默认规格','secondary');
        button.onclick=()=>run(async()=>{ selectedSku={product_id:productId,sku_id:sku.id,validated:false};$('propose').disabled=true;$('selection').textContent='正在核验所选规格…';await sendTurn({selected_product_id:productId,selected_sku_id:sku.id});void event('select_sku',{product_id:productId,sku_id:sku.id}); },button);
        $('sku-options').append(button);
    }
}
function renderComparison(comparison) {
    $('comparison-panel').hidden=false;const table=element('table'),head=element('tr');head.append(element('th','维度'));
    for(const product of comparison.products)head.append(element('th',product.name));table.append(head);
    for(const dim of comparison.dimensions) {
        const row=element('tr');row.append(element('th',dim.name+(dim.unit?'（'+dim.unit+'）':'')));
        for(const product of comparison.products)row.append(element('td',textValue(product.values[dim.code])));
        table.append(row);
    }
    $('comparison').replaceChildren(table);notify('仅按同类目的目录属性比较；未提供的值不作推断。');
}
function renderAction(action) {
    pendingAction=action;confirmation=null;$('action-panel').hidden=false;$('approve').hidden=false;$('decline').hidden=false;
    const root=$('action-summary');root.replaceChildren();
    if(action.items) {
        for(const item of action.items)root.append(element('p',`${item.name} · ${item.sku_options.map(v=>v.display_value).join(' / ') || '默认规格'} · ${money(item.unit_amount_minor)} × ${item.quantity}`));
        root.append(element('div','商品小计：'+money(action.current_subtotal),'total'));
        $('approve').textContent='确认模拟交接';
    } else {
        const summary=action.summary;root.append(element('h3',summary.name),element('p',summary.sku_options.map(v=>v.display_value).join(' / ') || '默认规格'));
        root.append(element('p',action.kind==='remove'?'移除此行':`确认后数量：${summary.final_quantity} · 当前单价：${money(summary.unit_amount_minor)}`));
        $('approve').textContent={add:'确认加购',set_quantity:'确认修改',remove:'确认移除'}[action.kind] || '确认';
    }
    root.append(element('p','摘要有效期至 '+new Date(action.expires_at).toLocaleTimeString('zh-CN'),'muted small'));
    void event('propose_action',{action_id:action.action_id,status:'confirm_action'});
}
async function confirmAction(approved) {
    if(!pendingAction)return;
    const action=pendingAction, checkout=Boolean(action.items);
    if(!confirmation || confirmation.action_id!==action.action_id || confirmation.approved!==approved)confirmation={action_id:action.action_id,approved,idempotency_key:crypto.randomUUID()};
    const path=checkout?`/api/v1/commerce/sessions/${sessionId}/checkout-handoff`:`/api/v1/commerce/sessions/${sessionId}/actions/${action.action_id}/confirm`;
    const body=checkout?{...confirmation}:{approved:confirmation.approved,idempotency_key:confirmation.idempotency_key};
    const attempt=async()=>{
        const result=await api(path,body);
        if(result.error_code==='reconfirmation_required') {
            if(result.preview || result.proposed_action)renderAction(result.preview || result.proposed_action);
            else {pendingAction=null;$('action-panel').hidden=true;}
            notify(labels.reconfirmation_required,true);showRetry(null);return;
        }
        if(result.status==='handoff') {renderHandoff(result);showRetry(null);return;}
        if(result.status==='unavailable') {notify(labels[result.error_code] || '交接暂不可用，请使用原请求重试。',true);showRetry(attempt);return;}
        pendingAction=null;$('action-panel').hidden=true;await refreshCart();showRetry(null);
        notify(result.status==='declined'?'已取消，购物车未修改。':'购物车更新已提交。');void event('confirm',{action_id:action.action_id,status:result.status});
        if(result.status==='cart_updated')void event('cart_updated',{action_id:action.action_id,status:'cart_updated'});
    };
    showRetry(attempt);await attempt();
}
function renderHandoff(result) {
    $('handoff-panel').hidden=false;$('action-panel').hidden=true;pendingAction=null;
    const root=$('handoff');root.replaceChildren(element('span',result.destination.demo?'DEMO · 模拟交接':'宿主交接','badge warning'));
    root.append(element('p','购物准备已交接；未创建订单或付款。'));
    const link=element('a','查看模拟交接入口');link.href=result.destination.ref;link.target='_blank';link.rel='noopener';root.append(link);
    root.append(element('p','有效期至 '+new Date(result.expires_at).toLocaleTimeString('zh-CN'),'muted small'));
    notify('模拟交接已持久化，可以查看受控入口。');void event('handoff',{handoff_id:result.handoff_id,status:'handoff',demo:result.destination.demo});
}
async function loadCatalog() {
    try {
        const page=await api('/api/v1/catalog/products?limit=20',undefined,{auth:false});
        const details=await Promise.all(page.items.map(p=>api(`/api/v1/catalog/products/${p.id}`,undefined,{auth:false})));
        for(const detail of details)catalogDetails.set(detail.category.id,detail);
        for(const [id,detail] of catalogDetails){const option=element('option',detail.category.name);option.value=id;$('category').append(option);}
    } catch (_) { notify('目录暂不可用，请先确认服务配置。',true); }
}
$('category').onchange=()=>{
    $('filters').replaceChildren();const detail=catalogDetails.get($('category').value);
    if(!detail)return;
    for(const attr of detail.category_attributes.filter(a=>a.is_filterable && a.value_type==='boolean')) {
        const label=element('label'),input=element('input');input.type='checkbox';input.dataset.code=attr.code;
        label.append(input,document.createTextNode(' 明确要求：'+attr.name));$('filters').append(label);
    }
};
$('login-form').onsubmit=event=>{event.preventDefault();void run(async()=>{
    if(refreshOperation)await refreshOperation.promise.catch(()=>{});
    const auth=await api('/api/v1/auth/login',{email:$('email').value,password:$('password').value},{auth:false});
    epoch++;accessToken=auth.access_token;csrfToken=auth.csrf_token;$('password').value='';$('auth').hidden=true;$('workspace').hidden=false;
    $('account').textContent=$('email').value;await newSession();
},event.submitter);};
$('register').onclick=()=>run(async()=>{if(!$('login-form').reportValidity())return;await api('/api/v1/auth/register',{email:$('email').value,password:$('password').value},{auth:false});notify('演示账号注册成功。请使用同一邮箱和密码点击“登录”。');},$('register'));
$('logout').onclick=()=>run(async()=>{if(refreshOperation)await refreshOperation.promise.catch(()=>{});await api('/api/v1/auth/logout',{}, {auth:false,csrf:true});epoch++;accessToken=csrfToken=null;resetWorkspace();$('auth').hidden=false;$('workspace').hidden=true;notify('已退出登录。');},$('logout'));
$('new-session').onclick=()=>run(newSession,$('new-session'));
$('refresh-cart').onclick=()=>run(refreshCart,$('refresh-cart'));
$('shop-form').onsubmit=event=>{event.preventDefault();void run(async()=>{
    const message=$('message').value.trim();
    if(['确认','确认加购','确认修改','确认移除','确认结账','取消'].includes(message)) {
        const input={message};
        if(pendingAction){
            const approved=message!=='取消';
            if(!confirmation || confirmation.action_id!==pendingAction.action_id || confirmation.approved!==approved)confirmation={action_id:pendingAction.action_id,approved,idempotency_key:crypto.randomUUID()};
            input.respond_to_action_id=pendingAction.action_id;input.idempotency_key=confirmation.idempotency_key;
        }
        await sendTurn(input);return;
    }
    selectedSku=null;$('sku-panel').hidden=true;$('comparison-panel').hidden=true;
    const constraints={category_id:$('category').value || null,query_text:$('query').value.trim() || null,hard_conditions:[...$('filters').querySelectorAll('input:checked')].map(i=>({code:i.dataset.code,value:true,source:'explicit'}))};
    if($('budget').value!=='')constraints.budget_max_minor=Number($('budget').value)*100;
    await sendTurn({message,constraints});
},event.submitter);};
$('compare').onclick=()=>run(async()=>{await sendTurn({message:'比较',product_ids:[...comparedProducts]});void event('compare',{status:'compare'});},$('compare'));
$('propose').onclick=()=>run(async()=>{if(!selectedSku)return;await refreshCart();await sendTurn({message:'加入购物车',selected_sku_id:selectedSku.sku_id,quantity:Number($('quantity').value),expected_cart_version:cartView.version});},$('propose'));
$('approve').onclick=()=>run(()=>confirmAction(true),$('approve'));
$('decline').onclick=()=>run(()=>confirmAction(false),$('decline'));
$('checkout').onclick=()=>run(async()=>{await refreshCart();await sendTurn({message:'结账',expected_cart_version:cartView.version});},$('checkout'));
$('retry').onclick=()=>{const fn=lastRetry;if(fn)void run(fn,$('retry'));};
void loadCatalog();
