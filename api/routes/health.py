"""Health endpoint."""

from fastapi import APIRouter

from config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "model": get_settings().llm_model}
