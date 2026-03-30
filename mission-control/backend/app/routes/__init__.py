"""Collect all route modules into a single router."""

from fastapi import APIRouter

from .health import router as health_router
from .tasks import router as tasks_router
from .nodes import router as nodes_router
from .services import router as services_router
from .dispatch import router as dispatch_router
from .stats import router as stats_router
from .team import router as team_router
from .memories import router as memories_router
from .budget import router as budget_router
from .trading import router as trading_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(tasks_router)
api_router.include_router(nodes_router)
api_router.include_router(services_router)
api_router.include_router(dispatch_router)
api_router.include_router(stats_router)
api_router.include_router(team_router)
api_router.include_router(memories_router)
api_router.include_router(budget_router)
api_router.include_router(trading_router)
