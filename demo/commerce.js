'use strict';
// 身份凭据只在内存中保存；浏览器会话记录按服务端签发的 user_id 分区。
const $ = id => document.getElementById(id);
let accessToken = null, csrfToken = null, sessionId = null, epoch = 0;
let refreshOperation = null, historyKey = null, chats = [], activeChat = null;
let pendingAction = null, confirmation = null, currentItems = [], cartView = null;
let ready = false, busy = false;
const labels = {
    dependency_unavailable: '暂时无法读取商品信息，请稍后重试。',
    checkout_unavailable: '暂时无法准备结账，请重试。',
    reconfirmation_required: '价格或可售条件发生变化，请核对新的摘要后再次确认。',
    cart_version_conflict: '购物车已发生变化，请重新提出修改。',
    session_expired: '这段对话已过期，请新建对话继续。',
    turn_processing: '这条消息仍在处理中，请稍后重试。',
    not_found: '无法打开这段对话，请新建对话。',
    action_expired: '确认摘要已过期，请重新提出购物操作。',
    model_degraded: '暂时只能提供已核验的商品信息。',
    stock_unknown: '暂时无法确认库存。', out_of_stock: '当前缺货。',
    insufficient_stock: '当前库存不足。', price_missing: '暂时无法确认价格。',
    not_seen: '请先在当前对话中查看商品。',
    empty_cart: '购物车为空。', action_already_resolved: '这项操作已处理，请查看购物车。',
};
const money = value => value == null ? '价格暂未提供' : new Intl.NumberFormat('zh-CN', {style:'currency', currency:'CNY'}).format(value / 100);
const textValue = value => value === true ? '是' : value === false ? '否' : String(value ?? '未提供');
function element(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
}
function notify(text = '', error = false) {
    $('notice').textContent = text;
    $('notice').className = 'notice' + (error ? ' error' : '');
}
async function api(path, body, opts = {}) {
    const startEpoch = epoch, tokenUsed = accessToken, headers = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (opts.auth !== false && accessToken) headers.Authorization = 'Bearer ' + accessToken;
    if (opts.csrf && csrfToken) headers['X-CSRF-Token'] = csrfToken;
    const response = await fetch(path, {
        method: body === undefined ? 'GET' : 'POST', headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'same-origin', cache: 'no-store',
    });
    const data = await response.json().catch(() => ({}));
    if (opts.auth !== false && startEpoch !== epoch) throw Error('会话状态已变化，请重新操作。');
    if (response.status === 401 && opts.auth !== false && !opts.retried && csrfToken) {
        if (accessToken === tokenUsed) await refreshCredentials(startEpoch);
        if (startEpoch !== epoch) throw Error('会话状态已变化，请重新操作。');
        return api(path, body, {...opts, retried:true});
    }
    if (!response.ok) {
        const code = data.detail?.code || data.error_code;
        const error = Error(labels[code] || (typeof data.detail === 'string' ? data.detail : '请求未完成，请稍后重试。'));
        error.code = code; error.status = response.status;
        throw error;
    }
    return data;
}
async function refreshCredentials(startEpoch) {
    if (!refreshOperation || refreshOperation.epoch !== startEpoch) {
        const operation = {epoch:startEpoch, promise:null};
        operation.promise = (async () => {
            try {
                const refreshed = await api('/api/v1/auth/refresh', {}, {auth:false, csrf:true});
                if (startEpoch !== epoch) throw Error('会话状态已变化，请重新操作。');
                accessToken = refreshed.access_token; csrfToken = refreshed.csrf_token;
            } finally {
                if (refreshOperation === operation) refreshOperation = null;
            }
        })();
        refreshOperation = operation;
    }
    return refreshOperation.promise;
}
async function bootstrapIdentity() {
    let auth;
    try {
        const csrf = await api('/api/v1/auth/csrf', undefined, {auth:false});
        csrfToken = csrf.csrf_token;
        auth = await api('/api/v1/auth/refresh', {}, {auth:false, csrf:true});
    } catch (error) {
        // 只有身份不存在或已失效时创建访客，网络/依赖故障不能换成新身份。
        if (error.status !== 401) throw error;
        auth = await api('/demo/commerce/guest', {}, {auth:false});
    }
    epoch++;
    accessToken = auth.access_token; csrfToken = auth.csrf_token;
    historyKey = 'commerce.chat.' + auth.user_id;
}
function readHistory() {
    try {
        const stored = JSON.parse(localStorage.getItem(historyKey) || '[]');
        if (!Array.isArray(stored)) return [];
        return stored.filter(chat => typeof chat.id === 'string' && typeof chat.title === 'string' && Array.isArray(chat.messages))
            .slice(0, 50).map(chat => ({...chat, messages:chat.messages.filter(m => m && ['user','assistant'].includes(m.role)).slice(-100)}));
    } catch (_) { return []; }
}
function persist() {
    if (!historyKey) return;
    try { localStorage.setItem(historyKey, JSON.stringify(chats.slice(0, 50))); }
    catch (_) { notify('浏览器无法保存更多会话记录；当前对话仍可继续。', true); }
}
function setBusy(value) {
    busy = value;
    const unavailable = !ready || busy || Boolean(activeChat?.expired);
    $('message').disabled = unavailable;
    $('send').disabled = unavailable || !$('message').value.trim();
    $('new-session').disabled = !ready || busy;
    $('status').textContent = !ready ? '正在准备对话…' : busy ? '正在处理…' : activeChat?.expired ? '会话已过期，请新建对话' : '';
    for (const button of document.querySelectorAll('.retry-button')) button.disabled = busy;
    renderHistory();
}
function renderHistory() {
    const root = $('history'); root.replaceChildren();
    if (!chats.length) root.append(element('p', '对话会保存在这里', 'history-empty'));
    for (const chat of chats) {
        const button = element('button', chat.title, 'history-entry' + (chat === activeChat ? ' active' : '') + (chat.expired ? ' expired' : ''));
        button.title = chat.title; button.disabled = busy || !ready;
        button.setAttribute('aria-current', chat === activeChat ? 'true' : 'false');
        button.onclick = () => void perform(() => openChat(chat));
        root.append(button);
    }
}
function resetContext() {
    pendingAction = null; confirmation = null; currentItems = []; cartView = null;
}
function updateContext(response) {
    if (response.status === 'shortlist') currentItems = response.items || [];
    if (response.status === 'confirm_action') { pendingAction = response.proposed_action; confirmation = null; }
    if (response.cart) cartView = response.cart;
    if (response.status === 'cart_updated' || response.status === 'handoff' || response.action_result === 'declined') {
        pendingAction = null; confirmation = null;
    }
}
function restoreContext(messages) {
    resetContext();
    for (const message of messages) if (message.response) updateContext(message.response);
}
async function openChat(chat) {
    if (busy) return;
    setBusy(true);
    try {
        try { await api(`/api/v1/commerce/sessions/${encodeURIComponent(chat.id)}`); }
        catch (error) {
            if (error.code !== 'session_expired') throw error;
            chat.expired = true;
        }
        activeChat = chat; sessionId = chat.id;
        restoreContext(chat.messages);
        $('message').value = ''; resizeComposer(); notify();
        renderMessages(); persist();
    } finally { setBusy(false); }
}
function newChat() {
    if (busy) return;
    activeChat = null; sessionId = null; resetContext();
    $('message').value = ''; resizeComposer(); notify();
    renderMessages(); setBusy(false); $('message').focus();
}
function turnInput(message) {
    const input = {message};
    if (pendingAction && ['确认','确认加购','确认修改','确认移除','确认结账','取消'].includes(message)) {
        const approved = message !== '取消';
        if (!confirmation || confirmation.action_id !== pendingAction.action_id || confirmation.approved !== approved) {
            confirmation = {action_id:pendingAction.action_id, approved, idempotency_key:crypto.randomUUID()};
        }
        input.respond_to_action_id = pendingAction.action_id;
        input.idempotency_key = confirmation.idempotency_key;
        return input;
    }
    // 仅将用户明确指向的“第几款”转换为已展示 ID；不提取或追加隐藏预算/品类。
    const numbers = {一:1, 二:2, 三:3};
    const references = [...message.matchAll(/第([123一二三])(?:款|个)/g)]
        .map(match => currentItems[(numbers[match[1]] || Number(match[1])) - 1]).filter(Boolean);
    if (/比较|对比/.test(message) && references.length >= 2) {
        input.product_ids = [...new Set(references.map(item => item.product_id))];
    } else if (references.length === 1 && /选择|选|加入|加购/.test(message)) {
        input.selected_product_id = references[0].product_id;
        input.selected_sku_id = references[0].sku_id;
        const quantity = message.match(/(?:数量|买|加购|加入)\s*(\d+)\s*(?:件|个|副)/);
        if (quantity) input.quantity = Number(quantity[1]);
        if (/加入|加购/.test(message)) {
            input.message = '加入购物车：' + message;
            input.quantity = input.quantity ?? 1;
        }
    }
    return input;
}
async function submitMessage() {
    const message = $('message').value.trim();
    if (!ready || busy || activeChat?.expired || !message) return;
    setBusy(true); notify();
    try {
        if (!sessionId) {
            const session = await api('/api/v1/commerce/sessions', {});
            sessionId = session.session_id;
            activeChat = {id:sessionId, title:message.slice(0, 28), messages:[]};
            chats.unshift(activeChat); chats = chats.slice(0, 50);
        }
        const body = {turn_id:crypto.randomUUID(), ...turnInput(message)};
        const userMessage = {role:'user', text:message};
        const reply = {role:'assistant', pending:true};
        activeChat.messages.push(userMessage, reply);
        activeChat.messages = activeChat.messages.slice(-100);
        $('message').value = ''; resizeComposer(); renderMessages();
        await deliverTurn(reply, body);
    } finally { setBusy(false); if (!activeChat?.expired) $('message').focus(); }
}
async function deliverTurn(reply, body) {
    const path = `/api/v1/commerce/sessions/${encodeURIComponent(sessionId)}/turns`;
    // 网络故障后保留完整原请求；不能用新 turn_id 重复提交购物操作。
    reply.retry = {path, body, sent:false}; reply.pending = true; delete reply.error;
    persist(); renderMessages();
    try {
        await prepareTurnRequest(reply.retry);
        const response = await api(path, body);
        reply.response = response; reply.pending = false;
        if (response.status === 'unavailable' || (response.errors || []).some(error => error.code === 'turn_processing')) {
            reply.error = response.question || '暂时无法处理这条消息，请稍后重试。';
        } else { delete reply.retry; }
        updateContext(response); persist(); renderMessages();
        void recordResult(response);
    } catch (error) {
        reply.pending = false; reply.error = error.message;
        persist(); renderMessages(); throw error;
    }
}
async function prepareTurnRequest(retry) {
    if (retry.sent) return;
    const body = retry.body;
    if (/加入|加购|改成|移除|删除|结账|去付款/.test(body.message) && !body.respond_to_action_id) {
        const cart = await api(`/api/v1/commerce/sessions/${encodeURIComponent(sessionId)}/cart`);
        body.expected_cart_version = cart.version;
    }
    retry.sent = true;
    persist();
}
async function retryTurn(reply) {
    if (busy || !reply.retry) return;
    const {path, body} = reply.retry;
    if (path !== `/api/v1/commerce/sessions/${encodeURIComponent(sessionId)}/turns`) return;
    setBusy(true); notify();
    try {
        // 重试使用已冻结的原请求，不重新读取购物车或改写请求内容。
        reply.pending = true; renderMessages();
        await prepareTurnRequest(reply.retry);
        const response = await api(path, body);
        reply.response = response; reply.pending = false;
        if (response.status !== 'unavailable') { delete reply.retry; delete reply.error; }
        updateContext(response); persist(); renderMessages(); void recordResult(response);
    } catch (error) {
        reply.pending = false; reply.error = error.message; persist(); renderMessages(); throw error;
    } finally { setBusy(false); }
}
async function recordResult(response) {
    const kind = response.status === 'cart_updated' ? 'cart_updated' : response.intent === 'confirmation' ? 'confirm' : null;
    if (!kind || !response.request_id) return;
    try {
        await api('/api/v1/commerce/events', {event_id:crypto.randomUUID(), session_id:sessionId,
            request_id:response.request_id, kind, payload:{status:response.action_result || response.status}});
    } catch (_) { /* 反馈故障不改变已完成的购物结果。 */ }
}
function optionText(options) { return (options || []).map(option => option.display_value).join(' / ') || '默认规格'; }
function responseText(response) {
    if (response.question) return response.question;
    if (response.action_result === 'declined') return '已取消，购物车未修改。';
    const texts = {
        shortlist:(response.items || []).length ? '根据你的需求，找到了这些商品。' : '没有找到符合条件的可售商品，可以调整需求后再试。',
        compare:'下面是这些商品的对比。', confirm_action:'请核对这项购物操作。',
        cart_updated:'购物车已更新。', handoff:'购物准备已完成。这是模拟交接，不会创建订单或付款。',
    };
    return texts[response.status] || (response.errors || []).map(error => labels[error.code] || '暂时无法完成这项操作。').join(' ') || '请继续描述你的需求。';
}
function appendCart(root, cart) {
    for (const item of cart.items || []) {
        root.append(element('p', `${item.product_name} · ${optionText(item.sku_options)} · ${money(item.quote?.price?.amount_minor)} × ${item.quantity}`));
    }
    root.append(element('p', '商品小计：' + money(cart.current_subtotal)));
}
function renderResponse(root, response) {
    root.append(element('div', responseText(response), 'assistant-text'));
    if (response.status === 'shortlist') {
        (response.items || []).forEach((item, index) => {
            const row = element('div', undefined, 'product-result');
            row.append(element('h3', `${index + 1}. ${item.name}`), element('p', `${money(item.price?.amount_minor)} · ${optionText(item.sku?.selected_values)}`));
            if (item.reason_text) row.append(element('p', item.reason_text, 'detail-muted'));
            root.append(row);
        });
        if ((response.items || []).length) root.append(element('p', '可以说“比较第1款和第2款”，或“加入第1款”。', 'detail-muted'));
        if (response.incomplete) root.append(element('p', '还有其他可能匹配的商品，可以进一步描述需求。', 'detail-muted'));
    }
    if (response.comparison) {
        const wrap = element('div', undefined, 'comparison-wrap'), table = element('table', undefined, 'comparison');
        const head = element('tr'); head.append(element('th', '对比项'));
        for (const product of response.comparison.products) head.append(element('th', product.name));
        table.append(head);
        for (const dim of response.comparison.dimensions) {
            const row = element('tr'); row.append(element('th', dim.name + (dim.unit ? `（${dim.unit}）` : '')));
            for (const product of response.comparison.products) row.append(element('td', textValue(product.values[dim.code])));
            table.append(row);
        }
        wrap.append(table); root.append(wrap);
    }
    if (response.proposed_action) {
        const action = response.proposed_action, detail = element('div', undefined, 'reply-detail');
        if (action.items) {
            for (const item of action.items) detail.append(element('p', `${item.name} · ${optionText(item.sku_options)} · ${money(item.unit_amount_minor)} × ${item.quantity}`));
            detail.append(element('p', '商品小计：' + money(action.current_subtotal)));
        } else if (action.summary) {
            const s = action.summary;
            detail.append(element('p', `${s.name} · ${optionText(s.sku_options)}`));
            detail.append(element('p', action.kind === 'remove' ? '将从购物车移除这项商品。' : `${money(s.unit_amount_minor)} × ${s.final_quantity}`));
        }
        const phrase = {add:'确认加购',set_quantity:'确认修改',remove:'确认移除',checkout_handoff:'确认结账'}[action.kind] || '确认结账';
        detail.append(element('p', `输入“${phrase}”继续，或输入“取消”。`, 'detail-muted')); root.append(detail);
    }
    if (response.cart) appendCart(root, response.cart);
    if (response.handoff?.status === 'handoff' && response.handoff.destination?.ref) {
        const url = new URL(response.handoff.destination.ref, window.location.origin);
        if (url.protocol === 'https:') {
            const link = element('a', '查看交接结果'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener'; root.append(link);
        }
    }
    for (const error of response.errors || []) if (labels[error.code]) root.append(element('p', labels[error.code], 'detail-muted'));
}
function renderMessages() {
    const root = $('messages'); root.replaceChildren();
    const messages = activeChat?.messages || [];
    $('conversation').classList.toggle('empty', messages.length === 0);
    for (const message of messages) {
        const row = element('article', undefined, 'chat-message ' + message.role);
        if (message.role === 'user') row.append(element('div', message.text || '', 'user-text'));
        else {
            row.append(element('span', '购物助手', 'assistant-name'));
            if (message.pending) row.append(element('span', '正在理解你的需求…', 'pending-indicator'));
            else {
                if (message.response) renderResponse(row, message.response);
                if (message.error) row.append(element('div', message.error, 'assistant-text'));
                if (message.retry && !activeChat.expired) {
                    const retry = element('button', '重试这条消息', 'retry-button'); retry.disabled = busy;
                    retry.onclick = () => void perform(() => retryTurn(message)); row.append(retry);
                }
            }
        }
        root.append(row);
    }
    requestAnimationFrame(() => root.scrollTo({top:root.scrollHeight, behavior:'instant'}));
    renderHistory();
}
function resizeComposer() {
    $('message').style.height = 'auto';
    $('message').style.height = Math.min($('message').scrollHeight, 180) + 'px';
}
async function perform(fn) {
    try { await fn(); } catch (error) { notify(error.message, true); }
}
async function start() {
    setBusy(true);
    try {
        await bootstrapIdentity(); chats = readHistory(); ready = true;
        // 页面关闭期间中断的消息保留重试入口，不永远显示等待动画。
        for (const chat of chats) for (const message of chat.messages) if (message.pending) {
            message.pending = false; message.error = '这条消息尚未收到结果，可以重试。';
        }
        setBusy(false);
        if (chats.length) await openChat(chats[0]); else newChat();
    } catch (error) {
        notify(error.message, true);
        const retry = element('button', '重新连接', 'retry-button'); retry.onclick = () => void start(); $('notice').append(retry);
    } finally { setBusy(false); }
}
$('shop-form').onsubmit = event => { event.preventDefault(); void perform(submitMessage); };
$('message').oninput = () => { resizeComposer(); setBusy(busy); };
$('message').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void perform(submitMessage); }
};
$('new-session').onclick = newChat;
$('toggle-sidebar').onclick = () => {
    const collapsed = $('toggle-sidebar').getAttribute('aria-expanded') === 'true';
    $('shell').classList.toggle('sidebar-collapsed', collapsed); $('sidebar').inert = collapsed;
    $('toggle-sidebar').setAttribute('aria-expanded', String(!collapsed));
    $('toggle-sidebar').setAttribute('aria-label', collapsed ? '展开会话记录' : '收起会话记录');
    $('toggle-sidebar').title = collapsed ? '展开会话记录' : '收起会话记录';
};
document.addEventListener('DOMContentLoaded', () => void start());
