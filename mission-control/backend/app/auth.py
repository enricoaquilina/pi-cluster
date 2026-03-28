"""API key authentication."""

from typing import Optional

from fastapi import Header, HTTPException

from .config import API_KEY


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
