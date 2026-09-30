"""显式条件覆盖、模型推断不能成为硬条件。"""
from commerce_orchestrator.schemas import ShoppingIntent, IntentUpdate
from commerce_orchestrator.intent import parse_intent


def test_explicit_budget_overrides_previous_constraint():
    current = ShoppingIntent(budget_max_minor=200000)
    update = parse_intent('改为1500元', current)
    result = current.apply(update)
    assert result.budget_max_minor == 150000
    assert result.sources['budget_max_minor'] == 'explicit'
    result = result.apply(IntentUpdate(budget_max_minor=999999, sources={'budget_max_minor':'inferred'}))
    assert result.budget_max_minor == 150000
