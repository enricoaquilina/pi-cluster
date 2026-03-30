"""Team roster endpoint."""

from fastapi import APIRouter

from ..trading_helpers import TEAM_ROSTER

router = APIRouter()


@router.get("/api/team")
def get_team():
    return TEAM_ROSTER
