# Shopping Agent Web Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为站内购物 Agent 增加可选的公开网页搜索和阅读能力，使解释可引用外部资料，同时保持商品交易事实边界。

**Architecture:** 通过主计划的 ToolRegistry 注册两个只读工具；独立的异步公开网络客户端控制解析、连接及重定向。所有外部结果是带出处的数据，实际报价与交易继续使用站内领域服务。

**Tech Stack:** Python、httpx（搜索 API）、aiohttp（受控公开页面读取）、Pydantic、现有 ToolRegistry/EvidenceRef、pytest 固定网络夹具。

**Spec:** [设计 §6、8、13](../specs/2026-10-01-shopping-agent-design.md)；依赖[主计划](./2026-10-01-shopping-agent.md)的 Task 1、8、10–14、17。此计划是 A 阶段可选扩展，默认关闭，不阻塞站内闭环验收。

状态：待计划审阅；首个适配器选择 Brave Web Search，未配置凭证时保持关闭，不宣称真实网页搜索已经可用。

## Global Constraints

- 仅公开 HTTP(S) 只读检索和正文读取，不运行浏览器、不使用登录 Cookie、不进行任意网络写入。
- 每 Run `web_search` 最多 3 次、`read_web_page` 最多 4 次，且计入全局 12 次工具预算，重试也计数。
- 未配置时从模型工具清单中移除，capabilities 明确说明不可用；不能伪造搜索结果或默默替用户改变购物条件。
- 外部网页价格/库存不能成为本地 SKU 事实，历史或模拟数据说明沿用主计划；跨商家比价属于阶段 B。
- 每次 DNS 和重定向都执行公网检查，拒绝回环、私网、链路本地、云元数据及非 HTTP 协议；限制响应体、时间和内容类型。
- 来源记录 URL、标题、访问时间和支持结论的片段，不保存完整用户历史、凭证或模型私有推理。
- 离线增加至少 10 个固定场景；真实网页变化不进入普通 CI。

## Review Focus

1. DNS 检查后连接地址变化：真正连接的 IP 仍必须通过检查，不能只做字符串和预先解析验证（W1）。
2. 公网 URL 重定向到私网、IPv4 映射 IPv6、带用户信息 URL：每跳重新验证并拒绝绕过（W1）。
3. 压缩超大正文、超时和非文本内容：解压后字节数、总耗时及类型都有界，不拖死 worker（W1）。
4. 页面“忽略指令/已授权购买”及无支持片段的结论：内容无工具权限，来源必须与结论对应（W2、W3）。
5. 搜索未配置、来源矛盾、只有摘要未读正文：明确能力/证据范围，不冒充已查全网或真实现价（W2、W3）。

### Task W1: 可验证的公开网络读取边界

**Files:** Create `services/web_research/__init__.py`、`services/web_research/types.py`、`services/web_research/public_http.py`、`tests/test_public_web_http.py`；Modify `requirements.txt`、`config/settings.py`、`.env.example`。

**Interfaces:** `PublicWebClient.async read(url: str) -> WebPage`；`WebPage(url, title, text, fetched_at, truncated)`；`PublicResolver` 实现 aiohttp 的 `AbstractResolver.resolve(host, port, family)`，仅返回已批准的公网地址记录。类型定义放 `types.py`，实现同任务完成；测试注入 resolver 后端及连接探针，不只断言独立 URL 校验函数。

选择 aiohttp 的理由是公开页面需要在实际建连时控制地址解析，使用其 connector/resolver 边界比依赖 httpx 私有连接实现更容易维护；搜索 API 继续用现有 httpx。代价是多一个 HTTP 客户端的依赖和生命周期。使用受测的稳定 3.x 版本并记录实际版本；API 能力参考 [aiohttp Client Reference](https://docs.aiohttp.org/en/stable/client_reference.html?highlight=timeout)，是否防住地址变化仍由本项目传输测试证明。

- [ ] 写 `test_redirect_to_private_host_rejected`、`test_dns_rebinding_cannot_change_connected_address`、`test_ipv4_mapped_loopback_rejected`；捕获实际连接探针并断言 `assert connected_ips <= approved_public_ips`，非法目标抛明确错误。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_public_web_http.py -q` 确认新读取边界缺失导致失败。
- [ ] 使用 `TCPConnector(resolver=PublicResolver(...), use_dns_cache=False, force_close=True, limit=4)`；每跳 URL/端口及实际 resolver 返回地址都校验，IP 字面量也校验。默认仅端口 80/443，拒绝 userinfo；保留原 Host/TLS 主机名验证，`allow_redirects=False`、`trust_env=False`、`DummyCookieJar`，不以预先 DNS 查询代替连接控制。
- [ ] 设置最多 3 次跳转、全过程 10 秒、传输及解压后均最多 2 MiB、正文最多 12000 字符，仅接收 HTML/纯文本。关闭自动解压，gzip 用设置输出上限的增量解压，其他未支持编码明确拒绝；流式读到上限即停。标题和正文提取移除 script/style，仅允许来源片段进入工具结果。
- [ ] 写 `test_decompressed_body_is_bounded`、`test_total_redirect_time_is_bounded`、`test_binary_and_cookie_headers_are_rejected`，验证上限和敏感头不发送；运行整个测试文件通过，提交 `feat: bound public web reads for shopping research`。

### Task W2: 搜索提供方适配与工具注册

**Files:** Create `services/web_research/search.py`、`services/web_research/tools.py`、`tests/test_web_research_tools.py`；Modify `services/shopping_agent_runtime.py`、`config/settings.py`、`.env.example`。

**Interfaces:** `SearchProvider.async search(query: str, limit: int = 5) -> list[WebSearchHit]`，`WebSearchHit(title, url, snippet, fetched_at)`；`register_web_tools(registry, search: SearchProvider, reader: PublicWebClient) -> None`。搜索 provider 配置必须对应具体的受测适配器，不把“任意 JSON endpoint”作为通用协议。

首选 `BraveSearchProvider`：本阶段只需要 URL、标题与摘要，然后由受控 reader 获取正文，适合其独立 Web Search 接口。[Brave 官方 API](https://api-dashboard.search.brave.com/api-reference/web/search/get)定义了鉴权、查询和结果结构。备选 [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)还提供答案/正文等选项，本阶段已有自己的答复和页面读取边界，先不增加第二个适配器。这里没有验证两者的中文召回优劣或实际费用；真实冒烟报告必须记录可用性、配额和查询表现，不能将选型当作质量结论。

- [ ] 写 `test_unconfigured_search_omits_both_tools`，断言 `assert 'web_search' not in tool_names`；`test_web_calls_consume_run_budget` 验证第4次 search、第5次 read 在网络前拒绝，实际调用 `assert search.call_count == 3`、`assert reader.call_count == 4`，且计入主预算。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_web_research_tools.py -q` 确认新工具契约缺失导致失败。
- [ ] 实现 Brave GET `https://api.search.brave.com/res/v1/web/search`，使用 `X-Subscription-Token`，参数 count=5、result_filter=web、text_decorations=false；将 `web.results` 的 title/url/description 映射为 WebSearchHit，fetched_at 使用本次服务端时钟。配置 `shopping_agent_web_provider=brave` 与独立的 `shopping_agent_web_api_key`，不自动注册或购买账户。
- [ ] 注册工具参数与结果白名单；search query 最多 500 字符且不超过 75 个空白分词，最多 5 个结果；中文直接查询，不硬编码英语过滤。只传用户需求中必要检索词，禁止完整身份和会话上传。搜索 API 只用可信端点，禁跟随跨域重定向携带 Key；结果 URL 交给 W1 公网读取边界。
- [ ] 写 `test_search_snippet_is_not_full_page_evidence`、`test_page_instruction_cannot_change_requirements_or_cart`、`test_external_offer_cannot_become_local_sku`；返回 EvidenceRef 保留出处和时间，非法结果、配额/超时错误显式映射为 unavailable。
- [ ] 运行该文件及主计划的 `tests/test_agent_tools.py tests/test_shopping_agent_graph.py` 通过；提交 `feat: register optional public research tools`。

### Task W3: 网页夹具、引用验收与启用说明

**Files:** Create `tests/fixtures/shopping_agent/web/tasks.json`、`tests/fixtures/shopping_agent/web/pages.json`、`tests/test_web_research_evaluation.py`；Modify `services/agent_evaluation.py`、`scripts/evaluate_shopping_agent.py`、`docs/shopping-agent.md`、`docs/shopping-agent-evaluation.md`。

**Interfaces:** 复用 `EvaluationTask/EvaluationResult`，CLI 新增 `--suite web`；搜索/网页观察使用固定 fixture，任务断言包含引用与禁止网络/交易动作。

- [ ] 写 `test_web_suite_covers_ten_distinct_boundaries`，断言 `assert len({t.id for t in tasks}) >= 10`；`test_unsupported_citation_fails` 断言 `assert result.success is False`；`test_unknown_shipping_not_reported_as_zero` 验证网页缺失值不变成确定交易事实。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_web_research_evaluation.py -q` 确认缺少网页场景评价导致失败。
- [ ] 加入正常官方资料、仅摘要、死链、矛盾来源、提示注入、私网重定向、DNS 地址变化、压缩超限、非文本、无价格页面；每个案例写明引用或错误预期。
- [ ] 执行 `.venv/bin/python scripts/evaluate_shopping_agent.py --mode fixture --suite web --repeat 1`；所有网络边界/引用/禁止写入断言通过，再用配置的提供方和公开页面做少量真实搜索→读取→引用冒烟，记录访问时间和来源范围。
- [ ] 更新启用/关闭、配额、错误、来源说明；未配置提供方或真实冒烟未通过时继续关闭 web capability。公开网页能力只称为研究工具，不能宣称跨商家比价或长期监控已经交付。
- [ ] 运行全部 web 测试及 `git diff --check`，完成附属功能审阅后提交 `test: verify grounded public shopping research`。
