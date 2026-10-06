# 阶段一 714 节点身份证据（Task14–16 审查第 5 项）

本目录持久化第一阶段 714 节点的原始执行证据与逐项映射说明，供
`tests/unit/test_test_layout_contract.py::test_phase_one_nodes_survive_with_recorded_evidence`
与 CI 的 `compare-ci --baseline tests/fixtures/layering/phase-one` 使用。

## 证据来源与验真

- `manifest.json` / `results.xml`：第一阶段收尾提交 `8779cf5` 的执行记录，
  原始副本当时位于 `.artifacts/layering/commit-final/`（被 `.gitignore` 忽略），
  因此在此持久化。与原始副本逐字节相同：
  - manifest.json sha256 `6591823b3b9975f67d2c52142e308a075f8a6b5ace66c3f9fec3893c9ea6ef8e`
  - results.xml sha256 `f5d62824ec316bf0fe659a3d10b96497aa289b7eb90fc6c2d559196bd248f6bb`

- 验真方法（2026-10-07 复核）：`git worktree add --detach <tmp> 8779cf5` 后在该树运行
  `pytest tests/ --collect-only -q`，收集到的 714 个 node id 与 manifest 集合完全相等；
  `results.xml` 中的 714 条 `node_id` 与 manifest 相等，且全部为 passed。
- 本目录刻意不含 `routes/openapi/metadata/errors` 合同：合同由 `tests/fixtures/layering/`
  的 226 冻结基线负责；把当前合同复制进来会冒充原始证据（metadata 的模块名已随包根变化）。

## 映射补齐（80 条，写入 path-map.json）

### 函数改名（5 条）

- `tests/test_layering.py::test_stage_import_contracts` → `tests/unit/test_layering.py::test_packaged_import_contracts`
- `tests/test_layering.py::test_stage_wrapper_rejects_missing_configuration` → `tests/unit/test_layering.py::test_packaged_wrapper_rejects_missing_configuration`
- `tests/test_layering.py::test_stage_wrapper_rejects_missing_cli` → `tests/unit/test_layering.py::test_packaged_wrapper_rejects_missing_cli`
- `tests/test_layering_rules.py::test_original_repository_passes_ast_and_has_current_generated_contracts` → `tests/unit/test_layering_rules.py::test_packaged_repository_passes_ast_and_has_current_generated_contracts`
- `tests/test_shared_primitives.py::test_new_id_remains_uuid4_and_same_alias` → `tests/unit/test_shared_primitives.py::test_new_id_remains_uuid4`

### 语句随模块层次变化重写（3 条，语义相同的相对导入用例）

- `tests/test_layering_rules.py::test_rejects_import_bypasses[api.catalog-from . import runtime\nfrom .runtime import gateway-gateway]`
  → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[api.routes.catalog-from .. import deps\nfrom ..deps import gateway-gateway]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[api.catalog-from . import runtime\nruntime.gateway-checkout.gateway]`
  → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[api.routes.catalog-from .. import deps\ndeps.gateway-infra.checkout_gateway]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[commerce_orchestrator.service-from ..api import catalog-relative]`
  → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[application.shopping.orchestrator-from ....api import routes-relative]`

### 参数中的模块名按迁移映射重写（72 条）

规则与文件级映射同源：`commerce_orchestrator.* → ecom.application.shopping.*`、
`checkout.gateway → ecom.infra.checkout_gateway`、`shopping_session → ecom.modules.sessions`、
`models.commerce → ecom.application.shopping.contracts`、`config → ecom.config`、
`catalog/service → ecom.modules.*`（grouped 时期为 `modules.*`）等。逐条：

- `tests/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[config-False]` → `tests/unit/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[ecom.config-False]`
- `tests/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[config-True]` → `tests/unit/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[ecom.config-True]`
- `tests/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[cart-False]` → `tests/unit/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[ecom.modules.cart-False]`
- `tests/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[cart-True]` → `tests/unit/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[ecom.modules.cart-True]`
- `tests/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[catalog.service-False]` → `tests/unit/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[ecom.modules.catalog.service-False]`
- `tests/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[catalog.service-True]` → `tests/unit/test_kernel_contracts.py::test_kernel_stage_rejects_dependencies_outside_schema_whitelist[ecom.modules.catalog.service-True]`
- `tests/test_kernel_contracts.py::test_base_schema_stage_rejects_reverse_kernel_dependency[catalog]` → `tests/unit/test_kernel_contracts.py::test_base_schema_stage_rejects_reverse_kernel_dependency[ecom.modules.catalog]`
- `tests/test_kernel_contracts.py::test_base_schema_stage_rejects_reverse_kernel_dependency[pricing]` → `tests/unit/test_kernel_contracts.py::test_base_schema_stage_rejects_reverse_kernel_dependency[ecom.modules.pricing]`
- `tests/test_kernel_contracts.py::test_base_schema_stage_rejects_reverse_kernel_dependency[inventory]` → `tests/unit/test_kernel_contracts.py::test_base_schema_stage_rejects_reverse_kernel_dependency[ecom.modules.inventory]`
- `tests/test_layering.py::test_composition_contract_rejects_concrete_dependencies[checkout.service-checkout.gateway]` → `tests/unit/test_layering.py::test_composition_contract_rejects_concrete_dependencies[ecom.modules.checkout.service-ecom.infra.checkout_gateway]`
- `tests/test_layering.py::test_composition_contract_rejects_concrete_dependencies[commerce_orchestrator.ports-commerce_orchestrator.llm_adapter]` → `tests/unit/test_layering.py::test_composition_contract_rejects_concrete_dependencies[ecom.application.shopping.ports-ecom.infra.llm]`
- `tests/test_layering.py::test_composition_contract_rejects_concrete_dependencies[commerce_orchestrator.guards-commerce_orchestrator.llm_adapter]` → `tests/unit/test_layering.py::test_composition_contract_rejects_concrete_dependencies[ecom.application.shopping.guards-ecom.infra.llm]`
- `tests/test_layering.py::test_composition_contract_rejects_concrete_dependencies[commerce_orchestrator.service-commerce_orchestrator.graph]` → `tests/unit/test_layering.py::test_composition_contract_rejects_concrete_dependencies[ecom.application.shopping.orchestrator-ecom.application.shopping.graph]`
- `tests/test_layering.py::test_composition_contract_rejects_concrete_dependencies[commerce_orchestrator.service-commerce_orchestrator.llm_adapter]` → `tests/unit/test_layering.py::test_composition_contract_rejects_concrete_dependencies[ecom.application.shopping.orchestrator-ecom.infra.llm]`
- `tests/test_layering.py::test_shared_contract_rejects_business_dependencies[config-False]` → `tests/unit/test_layering.py::test_shared_contract_rejects_business_dependencies[ecom.config-False]`
- `tests/test_layering.py::test_shared_contract_rejects_business_dependencies[config-True]` → `tests/unit/test_layering.py::test_shared_contract_rejects_business_dependencies[ecom.config-True]`
- `tests/test_layering.py::test_shared_contract_rejects_business_dependencies[cart-False]` → `tests/unit/test_layering.py::test_shared_contract_rejects_business_dependencies[ecom.modules.cart-False]`
- `tests/test_layering.py::test_shared_contract_rejects_business_dependencies[cart-True]` → `tests/unit/test_layering.py::test_shared_contract_rejects_business_dependencies[ecom.modules.cart-True]`
- `tests/test_layering.py::test_sessions_contract_rejects_application_dependencies[commerce_orchestrator-False]` → `tests/unit/test_layering.py::test_sessions_contract_rejects_application_dependencies[ecom.application-False]`
- `tests/test_layering.py::test_sessions_contract_rejects_application_dependencies[commerce_orchestrator-True]` → `tests/unit/test_layering.py::test_sessions_contract_rejects_application_dependencies[ecom.application-True]`
- `tests/test_layering.py::test_sessions_contract_rejects_application_dependencies[models.commerce-False]` → `tests/unit/test_layering.py::test_sessions_contract_rejects_application_dependencies[ecom.application.shopping.contracts-False]`
- `tests/test_layering.py::test_sessions_contract_rejects_application_dependencies[models.commerce-True]` → `tests/unit/test_layering.py::test_sessions_contract_rejects_application_dependencies[ecom.application.shopping.contracts-True]`
- `tests/test_layering.py::test_business_facts_contract_rejects_application_dependencies[cart-False]` → `tests/unit/test_layering.py::test_business_facts_contract_rejects_application_dependencies[ecom.modules.cart-False]`
- `tests/test_layering.py::test_business_facts_contract_rejects_application_dependencies[cart-True]` → `tests/unit/test_layering.py::test_business_facts_contract_rejects_application_dependencies[ecom.modules.cart-True]`
- `tests/test_layering.py::test_business_facts_contract_rejects_application_dependencies[storefront-False]` → `tests/unit/test_layering.py::test_business_facts_contract_rejects_application_dependencies[ecom.application.storefront-False]`
- `tests/test_layering.py::test_business_facts_contract_rejects_application_dependencies[storefront-True]` → `tests/unit/test_layering.py::test_business_facts_contract_rejects_application_dependencies[ecom.application.storefront-True]`
- `tests/test_layering.py::test_business_facts_contract_rejects_application_dependencies[sale_facts-False]` → `tests/unit/test_layering.py::test_business_facts_contract_rejects_application_dependencies[ecom.modules.sale_facts-False]`
- `tests/test_layering.py::test_business_facts_contract_rejects_application_dependencies[sale_facts-True]` → `tests/unit/test_layering.py::test_business_facts_contract_rejects_application_dependencies[ecom.modules.sale_facts-True]`
- `tests/test_layering.py::test_checkout_contract_rejects_session_or_application[False-shopping_session]` → `tests/unit/test_layering.py::test_checkout_contract_rejects_session_or_application[False-ecom.modules.sessions]`
- `tests/test_layering.py::test_checkout_contract_rejects_session_or_application[False-commerce_orchestrator]` → `tests/unit/test_layering.py::test_checkout_contract_rejects_session_or_application[False-ecom.application]`
- `tests/test_layering.py::test_checkout_contract_rejects_session_or_application[True-shopping_session]` → `tests/unit/test_layering.py::test_checkout_contract_rejects_session_or_application[True-ecom.modules.sessions]`
- `tests/test_layering.py::test_checkout_contract_rejects_session_or_application[True-commerce_orchestrator]` → `tests/unit/test_layering.py::test_checkout_contract_rejects_session_or_application[True-ecom.application]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-from catalog.service import Product-Product]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-from modules.catalog.service import Product-Product]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-from catalog.service import _private-_private]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-from modules.catalog.service import _private-_private]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-import catalog.service as service\nservice.Product-Product]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-import modules.catalog.service as service\nservice.Product-Product]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-from catalog import Product-catalog]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-from modules.catalog import Product-modules.catalog]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-from catalog.service import *-*]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-from modules.catalog.service import *-*]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[kernel.quotes-import catalog.service-catalog.service]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[kernel.quotes-import modules.catalog.service-modules.catalog.service]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[commerce_orchestrator.service-import api.catalog-api.catalog]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[application.shopping.orchestrator-import api.routes.catalog-api.routes.catalog]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[api.catalog-import checkout.gateway-checkout.gateway]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[api.routes.catalog-import infra.checkout_gateway-infra.checkout_gateway]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-from catalog import models-catalog.models]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-from modules.catalog import models-modules.catalog.models]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-def run():\n    import catalog.models-catalog.models]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-def run():\n    import modules.catalog.models-modules.catalog.models]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import catalog.models-catalog.models]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import modules.catalog.models-modules.catalog.models]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-__import__("catalog.models")-dynamic]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-__import__("modules.catalog.models")-dynamic]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-import importlib as loader\nloader.import_module("catalog.models")-dynamic]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-import importlib as loader\nloader.import_module("modules.catalog.models")-dynamic]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-from importlib import import_module as load\nload("catalog.models")-dynamic]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-from importlib import import_module as load\nload("modules.catalog.models")-dynamic]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-loader = __import__\nloader("catalog.models")-dynamic]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-loader = __import__\nloader("modules.catalog.models")-dynamic]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-import fastapi-fastapi]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-import fastapi-fastapi]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-import agents-agents]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-import legacy-legacy]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[api.catalog-import agents-agents]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[api.routes.catalog-import legacy-legacy]`
- `tests/test_layering_rules.py::test_rejects_import_bypasses[inventory.service-import catalog.service as service\ngetattr(service, "_private")-dynamic]` → `tests/unit/test_layering_rules.py::test_rejects_import_bypasses[modules.inventory.service-import modules.catalog.service as service\ngetattr(service, "_private")-dynamic]`
- `tests/test_layering_rules.py::test_public_all_cannot_reexport_orm[from catalog.models import Product\n]` → `tests/unit/test_layering_rules.py::test_public_all_cannot_reexport_orm[from modules.catalog.models import Product\n]`
- `tests/test_layering_rules.py::test_public_all_cannot_reexport_orm[from catalog.models import Product as PublicProduct\n]` → `tests/unit/test_layering_rules.py::test_public_all_cannot_reexport_orm[from modules.catalog.models import Product as PublicProduct\n]`
- `tests/test_layering_rules.py::test_public_all_cannot_reexport_orm[import catalog.models as models\nPublicProduct = models.Product\n]` → `tests/unit/test_layering_rules.py::test_public_all_cannot_reexport_orm[import modules.catalog.models as models\nPublicProduct = models.Product\n]`
- `tests/test_layering_rules.py::test_annotated_public_alias_cannot_reexport_orm[from catalog.models import Product\nPublicProduct: type = Product\n]` → `tests/unit/test_layering_rules.py::test_annotated_public_alias_cannot_reexport_orm[from modules.catalog.models import Product\nPublicProduct: type = Product\n]`
- `tests/test_layering_rules.py::test_annotated_public_alias_cannot_reexport_orm[import catalog.models as models\nmodel_alias: object = models\nPublicProduct: type = model_alias.Product\n]` → `tests/unit/test_layering_rules.py::test_annotated_public_alias_cannot_reexport_orm[import modules.catalog.models as models\nmodel_alias: object = models\nPublicProduct: type = model_alias.Product\n]`
- `tests/test_layering_rules.py::test_module_alias_preserves_private_and_reflection_boundaries[alias._private()\n-private symbol catalog.service._private-alias = service\n]` → `tests/unit/test_layering_rules.py::test_module_alias_preserves_private_and_reflection_boundaries[alias._private()\n-private symbol modules.catalog.service._private-alias = service\n]`
- `tests/test_layering_rules.py::test_module_alias_preserves_private_and_reflection_boundaries[alias._private()\n-private symbol catalog.service._private-alias: object = service\n]` → `tests/unit/test_layering_rules.py::test_module_alias_preserves_private_and_reflection_boundaries[alias._private()\n-private symbol modules.catalog.service._private-alias: object = service\n]`
- `tests/test_layering_rules.py::test_module_alias_preserves_private_and_reflection_boundaries[alias._private()\n-private symbol catalog.service._private-first: object = service\nalias = first\n]` → `tests/unit/test_layering_rules.py::test_module_alias_preserves_private_and_reflection_boundaries[alias._private()\n-private symbol modules.catalog.service._private-first: object = service\nalias = first\n]`
- `tests/test_layering_rules.py::test_later_assignment_cannot_erase_earlier_module_access[service._private()\n-private symbol catalog.service._private-]` → `tests/unit/test_layering_rules.py::test_later_assignment_cannot_erase_earlier_module_access[service._private()\n-private symbol modules.catalog.service._private-]`
- `tests/test_layering_rules.py::test_later_assignment_cannot_erase_earlier_module_access[service._private()\n-private symbol catalog.service._private-: type]` → `tests/unit/test_layering_rules.py::test_later_assignment_cannot_erase_earlier_module_access[service._private()\n-private symbol modules.catalog.service._private-: type]`
- `tests/test_layering_rules.py::test_function_alias_history_survives_later_assignment[alias._private()-private symbol catalog.service._private-]` → `tests/unit/test_layering_rules.py::test_function_alias_history_survives_later_assignment[alias._private()-private symbol modules.catalog.service._private-]`
- `tests/test_layering_rules.py::test_function_alias_history_survives_later_assignment[alias._private()-private symbol catalog.service._private-: type]` → `tests/unit/test_layering_rules.py::test_function_alias_history_survives_later_assignment[alias._private()-private symbol modules.catalog.service._private-: type]`
- `tests/test_layering_rules.py::test_dynamic_aliases_cannot_hide_imports[import importlib\ngetattr(importlib, "import_module")("catalog.models")\n]` → `tests/unit/test_layering_rules.py::test_dynamic_aliases_cannot_hide_imports[import importlib\ngetattr(importlib, "import_module")("modules.catalog.models")\n]`
- `tests/test_layering_rules.py::test_dynamic_aliases_cannot_hide_imports[from builtins import __import__ as load\nload("catalog.models")\n]` → `tests/unit/test_layering_rules.py::test_dynamic_aliases_cannot_hide_imports[from builtins import __import__ as load\nload("modules.catalog.models")\n]`
- `tests/test_layering_rules.py::test_new_unregistered_modules_fail_checker_and_generator[catalog.unregistered]` → `tests/unit/test_layering_rules.py::test_new_unregistered_modules_fail_checker_and_generator[modules.catalog.unregistered]`
- `tests/test_layering_rules.py::test_precise_adapter_exception_does_not_allow_other_consumers[api.catalog]` → `tests/unit/test_layering_rules.py::test_precise_adapter_exception_does_not_allow_other_consumers[api.routes.catalog]`
- `tests/test_layering_rules.py::test_precise_adapter_exception_does_not_allow_other_consumers[inventory.service]` → `tests/unit/test_layering_rules.py::test_precise_adapter_exception_does_not_allow_other_consumers[modules.inventory.service]`
- `tests/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[False-pricing]` → `tests/unit/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[False-ecom.modules.pricing]`
- `tests/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[False-inventory]` → `tests/unit/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[False-ecom.modules.inventory]`
- `tests/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[True-pricing]` → `tests/unit/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[True-ecom.modules.pricing]`
- `tests/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[True-inventory]` → `tests/unit/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[True-ecom.modules.inventory]`

## 删除的陈旧条目（4 条）

原 path-map 用**迁包后**的参数 ID 登记精确键，永远匹配不到第一阶段的原参数 ID，
并因此在 `test_catalog_sku_port.py` 处触发 ambiguous；这些键没有任何基线节点会命中，
改为按原参数 ID 登记正确的后继（见上一节）。被删除的键：

- `tests/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[False-ecom.modules.inventory]`
- `tests/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[False-ecom.modules.pricing]`
- `tests/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[True-ecom.modules.inventory]`
- `tests/test_catalog_sku_port.py::test_stage_enforces_catalog_model_ownership[True-ecom.modules.pricing]`

## 逐条例外（2 条，见 exceptions.json）

- `tests/test_layering_rules.py::test_absent_or_unknown_layout_fails_closed[grouped]`：参数集收敛：布局断言由 grouped/packaged/unknown 三个非法值改为 unknown 与空串两个
- `tests/test_layering_rules.py::test_absent_or_unknown_layout_fails_closed[packaged]`：参数集收敛：布局断言由 grouped/packaged/unknown 三个非法值改为 unknown 与空串两个

这两条是**布局断言变化**而非删除：第一阶段该用例参数集为 `grouped/packaged/unknown`，
当前为 `unknown` 与空串（其他非法布局值），三个用例收敛为两个，无法单射映射。
门禁要求「未映射集合 == 例外集合」，因此例外一旦恢复映射就会失败。

## 复现

```sh
python -m pytest tests/unit/test_test_layout_contract.py -q
python -m tests.support.layering_reports compare-ci \
  --baseline tests/fixtures/layering/phase-one --no-contracts \
  --path-map tests/fixtures/layering/path-map.json \
  --unit .artifacts/layering/unit --mysql .artifacts/layering/mysql \
  --redis .artifacts/layering/redis
```
