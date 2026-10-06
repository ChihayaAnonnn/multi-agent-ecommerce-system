"""Health endpoint."""

from fastapi import APIRouter, Request

from ecom.api.deps import get_app_settings

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    # 读应用实例的配置（与 lifespan 构造 Runtime 是同一份），不回退全局配置。
    return {"status": "healthy", "model": get_app_settings(request).llm_model}
