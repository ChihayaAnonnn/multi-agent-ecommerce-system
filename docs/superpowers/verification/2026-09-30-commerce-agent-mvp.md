# Commerce Agent MVP 实施与验收记录

2026-09-30，实施分支 `feat/commerce-agent-mvp`，隔离工作区 `.worktrees/commerce-agent-mvp`。交付范围是内部演示版购物准备闭环：真实目录与 SKU、现价与库存、持久会话及购物车、显式确认、重新核验、模拟宿主交接。不创建订单、付款或库存预留。

## 验证证据

- 完整 Python 测试：`157 passed in 128.95s`、零跳过；使用专用 MySQL `commerce_mvp_test_20260930` 和隔离 Redis，必须零跳过。
- 迁移往返：`0004→0005→0006→0007→0008→0004→head`，真实 MySQL 测试通过。
- JavaScript 契约检查：5 项通过。并发 401 只刷新一次；旧登录 epoch 的响应不能覆盖新凭证；聊天取消清除动作卡；不可售规格清除旧摘要并禁用加购；按钮确认与取消事件区分结果。
- 独立审查发现的聊天租约漏洞已复现失败并修复：事务先锁会话验证租约，再锁车与动作；过期或被接管的旧执行者不能写车、消费交接提案或调用网关。按钮入口继续使用同一命令、不要求轮次。
- 用户试用发现页面密码长度与后端不一致。回归测试复现 `12 != 15`，统一为后端的 15–128 字符后通过；失败账号未被注册，不修改用户凭证。
- 真实 HTTPS socket 验证：`__Host-` Cookie 的 Secure/HttpOnly/Path 与无 Domain 属性、缺 CSRF 返回403、正确 CSRF 刷新200、Origin/CORS 允许与拒绝、跨用户404；从可信 SKU 经确认写车到明确标为 DEMO 的持久交接成功。客户端信任本机临时 CA，没有关闭 TLS 验证。
- `git diff --check` 通过。

## 人工页面验收

Computer Use 返回浏览器列表为空，原生控制服务启动失败；直接连接 Chrome 仍返回 `Browser is not available: chrome`。未完成人工视觉验收，也未将 API 测试称为浏览器验收。用户明确要求保留服务、自行操作。

当前本机验收服务：`https://localhost:18443/demo/commerce`。使用自签名证书、专用演示库 `commerce_mvp_demo_20260930` 与独立 Redis DB；演示实例注入无 live LLM 的确定性降级和 DemoHostCheckoutGateway。旧推荐在此验收实例中为测试替身，其真实契约由完整测试验证。该实例不代表生产部署。

页面令牌只放内存，注册成功后需另行登录。人工待确认：

- [ ] 需求、候选、比较、规格选择、确认卡、购物车、模拟交接七种状态及屏幕布局。
- [ ] 可见候选曝光上报，空结果、缺货、价格变化、模型降级、写入失败与同键重试的交互。

## 上线前提

真实宿主的可信服务身份与结账契约（核价、库存、过期、幂等回读）、正式账号归属验证及恢复、正式限购值仍待确认。生产环境不挂载演示页；未配置可信真实宿主时交接返回503、不消费动作、不返回模拟付款地址。

## 执行判断与成本

1. 复用已有批量价格、库存接口，不重复建设；判断错误的成本是查询次数回归，由 SQL 测试检查。
2. 采用专用测试 schema，避免原共享 `ecommerce_test` 的版本/表不一致影响验收；原共享库未重置，原因未确定。成本是增加一个一次性测试库。
3. 将动作卡残留、规格摘要残留、事件入口口径三个审查 Minor 升为需修复的问题，因为它们影响明确的用户流程；成本是小范围页面修改与五项 JS 契约检查。
4. 按用户明确指示将人工页面验收交由用户完成；成本是视觉缺陷在用户验收前仍可能存在。

独立审查未发现 Critical；两个 Important 与上述三个页面问题均已处理，没有延后的小问题。

## 重跑

在已配置独立 MySQL/Redis 的实施工作区运行：

```sh
.venv/bin/python -m pytest tests/ -q -rs
/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc tests/commerce_demo_contracts.js
git diff --check
```

JavaScriptCore 命令是当前 macOS 的本地验证命令；页面运行本身不依赖 Node 或 JavaScriptCore CLI。演示服务的临时启动脚本与密钥位于忽略目录及 `/private/tmp`，不提交任何密钥、密码或令牌。

## 本地集成

2026-09-30 按用户选择，将实施分支快进合并至 `python`（实现提交 `dfa41ad`）。主工作区完整回归 `157 passed in 122.09s`，MySQL/Redis 零跳过，JavaScript 契约5项通过。完整提交范围检查清理一处文件尾多余空行后通过。

演示服务迁到主工作区，健康检查、页面与 JavaScript 在真实 HTTPS 上通过；地址仍为 `https://localhost:18443/demo/commerce`，账号与购物车数据保留。实施 worktree 与已合并的特性分支已清理。原目录计划未提交修改保留原位；设计原修改已包含在合并版本中且逐字一致；原始 MVP 计划和本地验收配置快照保存在忽略目录 `.superpowers/merge-backups/2026-09-30-commerce-agent-mvp/`，原文档同时保留在命名 stash `pre-commerce-merge original plan and reviewed design`，不覆盖已更新的实施计划。没有推送远端。
