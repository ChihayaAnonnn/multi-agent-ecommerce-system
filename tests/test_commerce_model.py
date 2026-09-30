"""模型输出只可重排已有事实，不能扩展候选或硬条件。"""
import asyncio
import pytest
from commerce_orchestrator.model import SafeCandidateReranker,SafeIntentModel
from commerce_orchestrator.schemas import ShoppingIntent,IntentUpdate


@pytest.mark.parametrize('output',[['fake','s1'],['s1','s1'],['s1','s2','s3','fake'],{'sku_id':'s1','price':1}])
def test_model_cannot_invent_or_expand_candidate_ids(output):
    class Model:
        async def rank(self,*args):
            return output
    async def scenario():
        ids=['s1','s2','s3']
        result=await SafeCandidateReranker(Model()).rank(ids,{'s1':{'price':100}},['通勤'])
        assert result==ids
    asyncio.run(scenario())


def test_model_cannot_change_explicit_hard_constraints():
    class Model:
        async def parse(self,*args):
            return IntentUpdate(budget_max_minor=999999,category_id='invented',soft_preferences=['便携'])
    async def scenario():
        current=ShoppingIntent(budget_max_minor=150000)
        update=await SafeIntentModel(Model()).parse('帮我推荐',current)
        applied=current.apply(update)
        assert applied.budget_max_minor==150000 and applied.category_id is None
        assert applied.soft_preferences==['便携']
    asyncio.run(scenario())


def test_model_timeout_and_missing_key_fall_back():
    class Slow:
        async def rank(self,*args):
            await asyncio.Event().wait()
    assert asyncio.run(SafeCandidateReranker(Slow(),timeout_seconds=.01).rank(['s1'],{},[]))==['s1']
    assert asyncio.run(SafeCandidateReranker().rank(['s1'],{},[]))==['s1']
