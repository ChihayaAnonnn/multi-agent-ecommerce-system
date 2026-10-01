# 桌面商品展厅与购物助手

页面采用 React + TypeScript + Vite，商品区与购物助手并排。购物需求通过一个聊天框输入；顶部可打开会话记录和购物袋，商品卡片打开详情，详情可把真实商品引用带入聊天。

## 安装与构建

需要 Node.js 22.12 或以上、现有 Python 环境以及已配置的 MySQL/Redis。依赖版本由 `frontend/package-lock.json` 固定。

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

构建输出到忽略的 `frontend/dist/`。显式启用非生产演示配置后，FastAPI 在 `/demo/commerce` 提供 React 页面；详情路径 `/demo/commerce/products/<product_id>` 支持刷新。构建尚不存在时回退到原生聊天演示页。运行中的进程需要加载本分支的后端路由；仅复制前端构建不能提供新 API。

实施已合并回主目录的 `python` 分支；在项目根目录运行以下命令。本机临时 Node 位于 `/tmp/commerce-node/bin`，尚未加入全局 PATH；该临时目录在系统清理后可能消失。当前机器执行上述命令可以加前缀：

```bash
PATH=/tmp/commerce-node/bin:$PATH npm --prefix frontend run build
```

## 本机预览与开发

主目录运行的本次验收预览：<https://localhost:18444/demo/commerce>。既有 18443 服务保留。预览使用演示数据库 `commerce_mvp_demo_20260930`，无重新导入或重置商品；浏览器验收产生的访客会话/购物袋是独立演示账号数据。

当前进程由主目录忽略的 `.superpowers/storefront_preview.py` 装配，用于本机验收，使用既有本地 TLS 和身份演示密钥，日志在 `.superpowers/preview.log`。它不是生产启动方式。重复启动前先检查端口，不要删除数据库、清空 Redis 或覆盖用户密钥。

正式本地装配沿用 `main.py` 和 `config/settings.py`：设置 `ECOM_COMMERCE_DEMO_ENABLED=true`、`ECOM_IDENTITY_DEMO_MODE=true`、非生产 `ECOM_IDENTITY_ENVIRONMENT`，以及所选数据库、Redis、身份签名密钥和可信来源。HTTPS 的示例启动如下，密钥文件必须自行准备：

```bash
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 18444 \
  --ssl-certfile /private/tmp/commerce-demo-tls/cert.pem \
  --ssl-keyfile /private/tmp/commerce-demo-tls/key.pem
```

开发使用 Vite，默认 API 目标为既有 18443，可覆盖为本次预览 18444：

```bash
COMMERCE_API_TARGET=https://localhost:18444 npm --prefix frontend run dev
```

Vite 读取 `/private/tmp/commerce-demo-tls/cert.pem` 和 `key.pem` 提供 HTTPS。开发前必须准备并信任本地证书；没有证书时不要使用 HTTP 验收认证流程，Secure Cookie 依赖 HTTPS。开发代理覆盖 `/api` 与 `/demo/commerce/guest`，把 Origin 转换为配置的可信后端来源。后端来源校验保持开启。生产构建由 FastAPI 同源提供，不需要运行 Vite 或常驻 Node。

## 数据与接口

商品展示使用以下公共只读接口，原目录契约不变：

- `GET /api/v1/storefront/products?limit=24&cursor=...` 返回 `items`、`next_cursor`，最大 100 件。
- `POST /api/v1/storefront/products/resolve`，body 为 `{"product_ids":["商品UUID"]}`，最多 100 个输入 ID，返回按输入去重顺序排列的商品与 `missing_product_ids`。
- 卡片包含类目、首图、展示 SKU、CNY 整数分报价、库存状态、可购买原因与模拟标记。展示 SKU 为按 `(sku_code,id)` 排序的第一个 active SKU，价格不宣称最低价。

详情引用还会使用鉴权接口：`POST /api/v1/commerce/sessions/<session_id>/product-context`，body 为 `{"product_id":"商品UUID","sku_id":"规格UUID"}`。服务端核验会话归属、在售商品和 SKU 归属，读取当前报价后记录展示来源。拒绝自报价格/库存及其他额外字段，不能替其他账号记录来源，也不执行加购。随后购物提案与确认仍重新检查价格、库存、约束、版本和权限。

10 万件商品按页读取，不能一次下载完整目录。图片为外部 HTTPS 链接，懒加载且固定比例；失效显示占位，不虚构商品图片。未知库存不是零库存，缺价不补随机价格。页面提示「演示商品，价格与库存为模拟数据」。商品原始文本主要为英文。

## 交互与动画

颜色、排版和留白采用已审阅的设计文档。详情与会话面板约 260ms；推荐结果就绪后约 200ms 淡入；商品悬停轻微放大；消息只淡入，不伪装逐字流式输出。快速连续开关可以中断动画，退出面板立即变为不可交互，关闭恢复触发元素的焦点。

系统 `prefers-reduced-motion` 开启时取消位移、缩放和错开，淡入最多 80ms。聊天仅在阅读位置接近底部时自动跟随，否则提示有新消息。输入法 Enter 确认候选不会发送。

会话历史按 `user_id` 存在浏览器，每用户最多 50 段、每段最近 100 条。刷新令牌在 HttpOnly Cookie，访问令牌和 CSRF 值仅在内存。切换会话忽略旧请求，失败重试保留原 turn ID、请求体与确认幂等键。该设计不提供跨设备历史同步。

## 验证命令

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run build
COMMERCE_BASE_URL=https://localhost:18444 npm --prefix frontend run test:e2e
.venv/bin/python -m pytest tests/ -q
```

Playwright 默认使用本机 Chrome 可执行文件，可用 `CHROME_PATH` 覆盖。测试使用独立浏览器 context，复用测试自己的认证 Cookie，避免每个用例重新注册触发限流。Cookie 快照位于忽略的 `frontend/.auth/`，不得提交或公开；对真实演示实例运行购物测试会写入这个测试账号的购物袋，不修改商品、价格或库存。

MySQL/Redis 回归必须配置隔离测试库与独立 Redis，不能指向演示数据库；仓库夹具会迁移和清理测试资源。本次使用仅清理本次拥有的隔离资源的运行器，不清理共享服务。

## 能力边界

本次预览未注入真实 LLM。已验收商品展示与明确商品引用的购物流程，不代表自由文本选品已完整实现。没有新增实际支付、订单履约、翻译、向量检索、图片镜像、移动端或 SEO。

设计：`docs/superpowers/specs/2026-10-01-commerce-storefront-design.md`；实施计划：`docs/superpowers/plans/2026-10-01-commerce-storefront.md`。

## 本次验收记录（2026-10-01）

- 隔离 MySQL/Redis 完整后端测试：190 项通过。
- 前端单元/组件测试：25 项通过，类型检查与生产构建通过。
- 真实 Chrome：6 项端到端测试通过，包含真实目录分页、详情刷新/引用、会话恢复、断网、动画、图片失效、加购提案、确认响应丢失后的幂等重试、模拟结账及新建对话后的购物袋连续性。
- 1280×800、1440×960、1920×1080 截图及详情截图位于主目录忽略的 `.superpowers/`；抽查可见图片成功加载，无页面 JavaScript 错误。
- 未验证真实 LLM 自由文本选品；未执行真实付款。
