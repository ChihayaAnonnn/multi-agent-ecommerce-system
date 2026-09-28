# 电商 Agent 参考仓库索引

整理日期：2026-09-28。本目录位于 `multi-agent-ecommerce-system/` 项目内，集中存放改造项目时要查阅的参考代码。下列仓库各自保留独立的 Git 历史，并被本目录的 `.gitignore` 排除，不会作为主项目源码提交；`README.md` 和 `.gitignore` 可以单独提交。它们均以 `--depth 1 --single-branch` 克隆，包含当前分支的完整工作树，但不包含完整提交历史。

## 仓库与适用范围

| 本地仓库 | 上游 | 主要用途 | 技术与边界 |
| --- | --- | --- | --- |
| [e-commerce-agents](./e-commerce-agents/) | [GitHub](https://github.com/nitin27may/e-commerce-agents) | 参考售前、订单、物流、退货换购的完整业务链路，以及意图路由、多 Agent 协作和业务工具 | Python + FastAPI + Microsoft Agent Framework + A2A；PostgreSQL/pgvector，与当前项目技术栈不同。MIT。 |
| [commerce-agents](./commerce-agents/) | [GitHub](https://github.com/anthropics/commerce-agents) | 参考购物助手的数据契约、商品与政策校验、工具边界、会话记忆 | Anthropic 官方参考实现，使用 Claude；结账交给宿主应用，不会真实下单或扣款，也不是完整退货系统。Apache-2.0。 |
| [ecommerce-ai-agent](./ecommerce-ai-agent/) | [GitHub](https://github.com/m-peker/ecommerce-ai-agent) | 参考 LangGraph 意图分流、订单/物流查询、退货资格判断与发起退货 | Python + FastAPI + LangGraph；SQLite 种子数据，主要覆盖客服与售后，没有购物车和结账。MIT。 |
| [HelpPilot](./HelpPilot/) | [GitHub](https://github.com/poysa213/HelpPilot) | 参考退款前人工审批、可恢复会话、政策 RAG 和售后评估 | Python + LangGraph + SQLite checkpoint；小型客服示例，没有登录、支付或完整交易系统。MIT。 |
| [tau2-bench](./tau2-bench/) | [GitHub](https://github.com/sierra-research/tau2-bench) | 参考零售任务、工具、规则与端到端评测方式 | 评测环境，不是电商应用；当前仓库已更新为 τ³-bench。MIT。 |

## 按业务环节查代码

| 要做的业务 | 优先阅读 | 借鉴点 |
| --- | --- | --- |
| 理解购物意图，跨轮保留预算、品类、用途 | [e-commerce-agents 意图路由](./e-commerce-agents/agents/python/orchestrator/modes/tool_router.py)、[上下文提供器](./e-commerce-agents/agents/python/shared/context_providers.py)、[commerce-agents 会话类型](./commerce-agents/shopping-agent/core/shopping_agent/types.py) | 把当前意图、短期会话约束与长期用户特征分开；不要只在提示词中保存状态。 |
| 商品发现、比较、推荐解释 | [e-commerce-agents 商品发现](./e-commerce-agents/agents/python/product_discovery/)、[售前流程](./e-commerce-agents/agents/python/workflows/pre_purchase.py)、[commerce-agents 搜索技能](./commerce-agents/shopping-agent/skills/search-discovery/SKILL.md) | 自然语言提取条件后检索真实商品；商品 ID、价格和属性来自业务数据。 |
| 库存、价格、优惠校验 | [e-commerce-agents 库存工具](./e-commerce-agents/agents/python/shared/tools/inventory_tools.py)、[价格工具](./e-commerce-agents/agents/python/shared/tools/pricing_tools.py)、[commerce-agents 执行门禁](./commerce-agents/shopping-agent/core/shopping_agent/gates.py) | 将实时库存、报价和硬性过滤放在业务工具中，而不是让 LLM 决定。 |
| 购物车、订单创建与确认 | [e-commerce-agents 购物车工具](./e-commerce-agents/agents/python/shared/tools/cart_tools.py)、[订单管理](./e-commerce-agents/agents/python/order_management/)、[commerce-agents 后端契约](./commerce-agents/shopping-agent/core/shopping_agent/backend.py) | 参考用户身份、确认、业务状态与工具接口；当前项目初版可用模拟支付。`commerce-agents` 的 checkout 是交接流程，不是实际支付实现。 |
| 查订单、物流、预计送达 | [ecommerce-ai-agent 订单工具](./ecommerce-ai-agent/src/services/order_service.py)、[物流工具](./ecommerce-ai-agent/src/services/shipping_service.py)、[e-commerce-agents 履约服务](./e-commerce-agents/agents/python/inventory_fulfillment/) | 查询真实订单及状态事件，再组织答复；不要从聊天记录推断物流状态。 |
| 退货、换购、退款 | [e-commerce-agents 退换工作流](./e-commerce-agents/agents/python/workflows/return_replace.py)、[退货工具](./e-commerce-agents/agents/python/shared/tools/return_tools.py)、[ecommerce-ai-agent 退货服务](./ecommerce-ai-agent/src/services/returns_service.py)、[HelpPilot 工具](./HelpPilot/helppilot/tools.py) | 将资格规则、申请状态和退款金额放在确定性代码中；查看幂等、权限与并发处理。 |
| 人工审批、知识库回答、跨轮恢复 | [HelpPilot 流程图](./HelpPilot/helppilot/graph.py)、[政策检索](./HelpPilot/helppilot/rag.py)、[commerce-agents 客服技能](./commerce-agents/shopping-agent/skills/customer-care/SKILL.md) | 敏感动作暂停等待审批；回答政策时附可核查依据；恢复时从持久化状态继续。 |
| 端到端场景与回归评测 | [tau2-bench 零售规则](./tau2-bench/data/tau2/domains/retail/policy.md)、[零售工具](./tau2-bench/src/tau2/domains/retail/tools.py)、[零售任务](./tau2-bench/data/tau2/domains/retail/tasks.json)、[HelpPilot 评估数据](./HelpPilot/helppilot/eval_dataset.py) | 按“用户目标 + 允许操作 + 最终业务状态”评价，而不只看回答是否流畅。 |

## 对当前项目的阅读顺序

1. 先看 [e-commerce-agents 的售前、查单、退货换购流程](./e-commerce-agents/docs/agent-flows.md)，确定一条可以演示的完整用户旅程。
2. 再看 [commerce-agents 的数据契约](./commerce-agents/shopping-agent/core/shopping_agent/types.py) 和 [工具门禁](./commerce-agents/shopping-agent/core/shopping_agent/gates.py)，设计当前项目自己的 `Product/SKU`、购物车、订单与售后单模型。
3. 看 [ecommerce-ai-agent 的 LangGraph](./ecommerce-ai-agent/src/agent/graph.py) 和 [工具](./ecommerce-ai-agent/src/agent/tools.py)，把订单、物流、售后接入对话。
4. 看 [HelpPilot 的审批和恢复](./HelpPilot/helppilot/graph.py)，为下单、取消与退款等写操作增加确认或人工审批。
5. 用 [tau2-bench 零售任务](./tau2-bench/data/tau2/domains/retail/tasks.json) 的思路构造本项目的评测样例。

建议从“选耳机 → 比较 → 加入购物车 → 模拟下单 → 查询物流 → 申请退货”这一条纵向场景开始。参考业务规则和接口设计时，以当前项目的 FastAPI、MySQL、Redis、Milvus 为基础逐步实现；这些仓库的数据库、模型供应商和编排框架不必整套照搬。

## 克隆版本与更新

| 仓库 | 克隆时的提交 |
| --- | --- |
| e-commerce-agents | `7d3b12018beb` |
| commerce-agents | `fd4d59224ab9` |
| ecommerce-ai-agent | `3d38d5ab7fa0` |
| HelpPilot | `3767824fb90b` |
| tau2-bench | `b7ea9074c1cb` |

在某个仓库中执行 `git pull --ff-only` 可更新其当前分支。若需要完整历史，在对应仓库执行 `git fetch --unshallow`。这些参考仓库尚未安装依赖或运行服务；这里的标注基于已下载的源码和仓库说明。
