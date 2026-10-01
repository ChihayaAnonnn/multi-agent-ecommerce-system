# 桌面商品展厅与购物助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将桌面聊天演示升级为真实商品展厅与聊天助手并排的 React 页面，支持商品详情、会话恢复和优雅交互动画。

**Architecture:** 独立 `frontend/` 通过只读商品展示接口批量读取卡片，通过现有购物 API 完成会话与确认。沿用 FastAPI 同源部署和独立访客身份；不改变领域事实、交易权限和幂等边界。

**Tech Stack:** React、TypeScript、Vite、React Router、TanStack Query、CSS Modules、Motion、Vitest、Testing Library、Playwright、FastAPI、SQLAlchemy async、pytest。

**Spec:** `docs/superpowers/specs/2026-10-01-commerce-storefront-design.md`，用户已审阅通过。

## Global Constraints

- 仅桌面，验收尺寸 1280×800、1440×960、1920×1080；商品与聊天约 70:30，聊天宽约 360–420px。
- 聊天为唯一购物需求输入；无搜索框、预算表单、类目筛选器；会话记录可隐藏。
- 品牌名暂沿用「购物助手」；首屏标题「为日常挑选」，候选标题「为你挑选」，返回按钮「浏览全部商品」。
- 背景 `#FAFAF8`、图片底板 `#F0F1EE`、正文 `#252923`、次要文字 `#747A71`、主色 `#385447`、边线 `#E2E5DE`。
- 字体 `Helvetica Neue`、`PingFang SC`、系统无衬线；正文 14–16px、标题 28–36px；商品图 4:5、contain，名称最多两行。
- 默认分页 24、最大 100；详情与报价读取真实数据；unknown 与 out_of_stock 不合并；无图、缺价不可伪造。
- 模拟事实明确提示「演示商品，价格与库存为模拟数据」；不冒充真实市场价格，不制造推荐理由。
- 动画以 transform/opacity 为主，缓动 `cubic-bezier(0.22, 1, 0.36, 1)`；展开 220–280ms、结果 180–240ms、卡片进入 160–200ms、消息 120–180ms；图片悬停至 1.025。
- 可见新卡片最多错开 4 项、总延迟 ≤100ms；reduced-motion 取消位移/缩放/错开，淡入 ≤80ms；动画可中断，不阻塞焦点或输入。
- 不修改已导入商品，不清理用户数据库/Redis，不纳入 secrets、下载文件和原有无关修改。
- 不扩大本次范围到真实 LLM 接入、翻译、移动端、支付、SSR、语义检索或图片镜像。

## Review Focus

1. 快速切换会话与延迟响应：旧请求不得覆盖新会话（Task 3）。
2. 图片失效与超长英文名称：固定比例，稳定占位，详情可读取完整可用文本（Task 4）。
3. 多 SKU、缺价、库存未知与未上架：确定性 SKU 选择，准确原因，交易另行复核（Task 1）。
4. 抽屉快速连续开关与键盘操作：没有透明遮挡层，关闭恢复焦点，reduced-motion 正常（Task 5）。
5. 历史购物确认后网络超时：原 turn/action 幂等键可重试，不重复写购物车（Task 3、6）。

## 执行准备与文件边界

执行前按 using-git-worktrees 检查隔离工作区，保留当前 dirty 文件。沿用用户此前同意的隔离 worktree 方式；创建新实施区时不得覆盖用户修改。设计与计划已经提交；原生聊天、导入脚本等未提交的依赖需安全复制到隔离区并核对，不用 reset/clean。

后端新模块：`storefront/__init__.py`、`storefront/schemas.py`、`storefront/service.py`、`api/storefront.py`；优先复用现有 `CatalogService.get_products/get_skus`、`PricingService.get_prices`、`InventoryService.get_availability` 与 `CommerceFactsReader.quote_many`，这些批量能力已经存在，无需重复增加。

前端文件按功能划分：`frontend/src/app/` 放路由、主题与提供器；`api/` 放契约和传输；`features/catalog/` 放卡片/详情/分页；`features/chat/` 放会话、历史、确认；`features/cart/` 放购物袋；`components/` 仅放通用抽屉和动画封装。所有依赖由 package-lock 固定；`frontend/dist/` 与 `node_modules/` 被忽略。

### Task 1: 批量商品展示读模型与 API

**Files:** Create 上述 storefront 文件、`tests/test_storefront.py`、`tests/test_storefront_api.py`；Modify `main.py` 注册路由。不改旧目录 API。

**Interfaces:**
- `StorefrontService(catalog: CatalogService, facts: CommerceFactsReader)`。
- `async list_products(*, limit: int = 24, cursor: str | None = None) -> StorefrontPage`。
- `async resolve_products(product_ids: list[str]) -> StorefrontResolved`，按输入去重顺序返回有效 active 商品，另给 `missing_product_ids`。
- `StorefrontProduct`：`id/name/brand/category/image_url/sku_id/price/availability/purchasable/reason/simulated`；category 使用 `CategoryRefView`，price 使用 `PriceView | None`，availability 使用 `AvailabilityView | None`。`simulated` 显式表示价格或 SKU 的模拟标记。
- `StorefrontPage(items, next_cursor)`；`StorefrontResolved(items, missing_product_ids)`；无 active SKU 时 `sku_id=None`、reason=`sku_missing`、不可购买。
- GET `/api/v1/storefront/products`；POST `/api/v1/storefront/products/resolve` 的 body `{product_ids: string[]}`，输入数组长度最大 100，空数组合法，非法游标 422、依赖失败 503。

- [ ] 写失败测试：`test_card_uses_first_active_sku` 断言按 `(sku_code,id)` 选 SKU，不以最低价排序；`test_resolve_preserves_order_and_reports_missing` 断言重复去除、draft/archived 不出现。
- [ ] 写失败测试：`test_missing_facts_remain_explicit` 分别断言无图、缺价、unknown、缺货和 sku_missing；`test_batch_read_count_is_bounded` 断言 1/24/100 件分别仅一次批量详情和一次 quote_many，不逐商品调用。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_storefront.py tests/test_storefront_api.py -q`，确认失败由新接口缺失导致。
- [ ] 实现 DTO、拼装服务、依赖装配与错误映射；分页转调现有 catalog.search；get_products 后选 SKU，再一次 quote_many。对分页后并发下架的商品过滤，保留目录 next_cursor。
- [ ] API 测试断言默认 24、limit=101/超100个ID/非法游标返回422、已知依赖故障503，旧目录响应不变；以真实数据库 SQL 计数补充 N+1 检查，批量读取100件与24件次数一致。
- [ ] 运行上述测试及 `tests/test_commerce_facts.py` 和 `tests/test_commerce_facts_mysql.py`，通过后提交 `feat: add batch storefront product reads`，仅暂存本任务文件。

### Task 2: React 构建与同源静态部署

**Files:** Create `frontend/package.json`、`package-lock.json`、`index.html`、`tsconfig.json`、`tsconfig.node.json`、`vite.config.ts`、`vitest.config.ts`、`src/main.tsx`、`src/app/App.tsx`、`src/app/theme.css`、`src/test/setup.ts`；Modify `.gitignore`、`api/commerce_demo.py`、`tests/test_commerce_demo.py`。

**Interfaces:** `npm run dev/build/typecheck/test`；首页 `/demo/commerce`，详情路由 `/demo/commerce/products/:productId`。构建静态前缀 `/demo/commerce/assets/`。未构建时保留现有原生页面作为回退，构建存在则返回 React 首页。

- [ ] 写失败 Python 路由测试：`test_react_build_mount_is_demo_only` 使用临时构建目录，断言非生产门禁；`test_static_assets_do_not_shadow_api` 断言 assets 正确 MIME、缺失文件404、路径穿越404、guest/handoffs 不被 SPA 捕获。
- [ ] 运行上述测试确认失败；核对本机 Node 与所选依赖官方兼容要求，再创建最小 Vite 项目和固定 lockfile，不使用整套商城模板。
- [ ] 配置开发代理 `/api` 与 `/demo/commerce/guest`，将 Origin/Host 处理与后端可信来源配置一致；验证 Secure Cookie 的开发 HTTPS 路径，不关闭后端来源校验。
- [ ] 实现同源 build 资源挂载、限域 SPA 路由与 CSP；带 hash 资源可以长期缓存，HTML no-store；不改变旧JS/CSS和handoff契约。
- [ ] 创建单一 QueryClient、BrowserRouter 与主题变量；用 `frontend/src/app/App.test.tsx` 断言首页结构可渲染、无预算/搜索输入。
- [ ] 运行 `npm --prefix frontend run build`、`npm --prefix frontend run typecheck`、`npm --prefix frontend test -- --run` 及 Python 路由测试，通过后提交 `feat: add React storefront shell and deployment`。

### Task 3: 会话、认证与交易状态迁移

**Files:** Create `frontend/src/api/{client,contracts}.ts`、`features/chat/{sessionStore,historyStore,useShoppingSession}.ts`、`features/chat/{ChatPanel,HistoryPanel,ActionSummary}.tsx`、对应 `*.test.ts(x)`。以 `demo/commerce.js`、`tests/commerce_demo_contracts.js` 为行为依据。

**Interfaces:** `apiRequest<T>(path: string, init?: RequestInit): Promise<T>`；`useShoppingSession()` 返回当前 session/messages/candidates/pendingAction/loading/error 及 newSession/selectHistory/sendMessage/retryTurn/confirmAction；`sendMessage(text: string, references?: {product_id: string}[])`，商品 ID 经后端再次验证。

- [ ] 写失败测试：`session_changes_ignore_late_responses`、`refresh_is_single_flight`、`history_is_scoped_to_user`、`network_error_does_not_create_new_guest`，断言旧会话无污染、一次刷新、令牌不落 localStorage、仅401创建访客。
- [ ] 写失败测试：`retry_preserves_turn_and_confirmation_keys`，断言请求超时后 ID、body 与幂等键保持一致；`composition_enter_does_not_send` 断言输入法 Enter 不发送；`clarification_keeps_previous_candidates` 断言无新候选时商品保留。
- [ ] 运行指定 Vitest 文件确认失败后，迁移原页面的恢复、重试与确认语义到 hooks；保持服务端归属检查，不把本地聊天记录当交易事实。
- [ ] 实现历史面板、聊天列表、输入框、真实 pendingAction 摘要；不伪造逐字流式输出；接近底部才跟随，否则显示新消息提示。
- [ ] 运行前端会话测试与既有 commerce/auth/demo 回归，通过后提交 `feat: migrate shopping chat and session lifecycle`。

### Task 4: 真实商品展厅、详情与购物袋

**Files:** Create `features/catalog/{api,useProductFeed}.ts`、`features/catalog/{ProductGrid,ProductCard,ProductDetail}.tsx`、各组件 `.module.css` 和测试；Create `features/cart/{CartPanel,useCart}.tsx`（hook 可分为 `.ts`）、测试；Modify `src/app/App.tsx`。

**Interfaces:** `useProductFeed(candidateProductIds: string[] | null)` 使用 Task1 DTO；目录采用 infinite query，候选采用 resolve。商品详情使用现有目录详情，报价使用现有 quote；购物袋使用已有 session cart API。引用通过 Task3 sendMessage 或输入草稿引用，不自动提交。

- [ ] 写失败测试：`feed_retains_old_items_until_ready`、`load_more_keeps_existing_cards`、`candidate_order_matches_response`、`return_to_catalog_restores_feed`。
- [ ] 写失败测试：`image_failure_keeps_layout_and_placeholder`、`long_titles_have_full_detail`、`unknown_stock_is_not_zero`、`card_price_is_not_used_to_authorize_cart`。
- [ ] 运行组件测试确认失败；实现首屏24件、加载更多、候选视图/返回目录；缓存键区分候选与目录，取消过期请求。
- [ ] 实现4:5 contain 图片、两行名称、品牌和CNY格式化；首图懒加载、固定占位、no-referrer；不显示伪造折扣、评分或最低价标签。
- [ ] 实现详情路由/面板、SKU查看与逐SKU报价；详情引用进入聊天草稿。购物袋与确认沿用服务端 proposal，不依据卡片快照交易。
- [ ] 实现指定配色、字体、顶部、两栏与宽度变化；主视觉由图片组成，首页不占用大横幅；统一模拟数据提示。
- [ ] 运行前端测试、类型检查和build，通过后提交 `feat: render product showroom and shopping details`。

### Task 5: 动画、焦点与操作连续性

**Files:** Create `frontend/src/components/{Drawer,AnimatedPresence}.tsx`、`.module.css`、测试；Create `src/app/motion.ts`；Modify Task3/4组件与CSS。

**Interfaces:** `Drawer({open,onClose,title,children,returnFocusRef})` 管理 Escape、焦点约束与恢复；共享 motion 常量与 reduced-motion 分支，不将 reduced-motion 作为测试环境禁用动画的替代。

- [ ] 写失败测试：`drawer_restores_focus_on_close`、`escape_closes_topmost_panel`、`rapid_toggle_leaves_no_blocking_overlay`、`reduced_motion_disables_transform`；浏览器测试检查实际退出完成后 overlay 不截获点击。
- [ ] 运行测试确认失败，再实现 Motion presence 与 CSS transition；所有动画时长与位移遵循设计文件，不为长列表挂全量布局动画。
- [ ] 接入面板220–280ms、结果180–240ms、新卡片160–200ms、消息120–180ms、悬停1.025；新卡片最多4项错开、延迟≤100ms；不闪空、不强制滚动。
- [ ] 验证面板快速连续开关可转向、等待请求时输入框状态清楚、退出后焦点正确；reduced-motion 取消平移缩放与错开，淡入≤80ms。
- [ ] 运行组件测试及 build/typecheck，通过后提交 `feat: add refined storefront motion and accessibility`。

### Task 6: 完整浏览器验收与运行说明

**Files:** Create `frontend/playwright.config.ts`、`frontend/e2e/{storefront,session,motion}.spec.ts`、`docs/commerce-storefront.md`；Modify `docs/demo-chat.md` 说明新旧入口与build回退。

**Interfaces:** `npm --prefix frontend run test:e2e`；真实实例验收使用独立浏览器 profile 和该任务拥有的测试账号/隔离数据库，测试绝不修改100k商品。完整LLM不可用时用明确商品引用验收已有购物链路，并单独报告自由文本未验证。

- [ ] 添加浏览器用例：首次页面真实图片/价格、加载更多、详情路由刷新、历史切换、断网恢复、会话刷新、加购提案/确认/购物袋、模拟handoff。
- [ ] 添加失败条件用例：确认后响应丢失再重试购物车只增加一次；图片404不塌布局；滚动阅读时新消息不强制跳转；抽屉连续开关/键盘/reduced-motion无遮挡。
- [ ] 跑浏览器用例；在1280×800、1440×960、1920×1080分别保存首屏、长名称、详情与长对话截图到忽略的验收目录，人工检查布局和动画，记录控制台错误与网络失败。
- [ ] 运行 `.venv/bin/python -m pytest tests/ -q`、前端test/typecheck/build/e2e、`git diff --check`；数据库测试若需要真实服务，先建立该任务隔离库，不清空既有库，不把历史通过数当本次证据。
- [ ] 编写中文安装、开发HTTPS代理、同源构建、启动、数据状态、LLM能力边界与测试说明，列出实际测试结果及未验证项。
- [ ] 最终审查功能与本设计对照、精确Git diff及用户原修改保留情况；仅提交任务文件 `test: verify storefront workflows and document setup`。按用户选择执行方式完成相应独立审查，不自动合并或部署。

## 自审与执行建议

已核对设计覆盖：技术取舍/同源部署→Task2；批量数据/领域事实→Task1；唯一聊天输入/历史/幂等→Task3；商品展厅/详情/购物袋→Task4；视觉动画/键盘/减少动画→Task5；性能、截图、失败路径和能力边界→Task6。

推荐 Native：六项任务依赖同一 API DTO、会话状态和动画约定，在本会话顺序实施更容易保持一致，结束时进行一次独立审查。实施前需用户审阅本计划并选择执行方式。
