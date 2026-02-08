"""Minimal FastAPI integration for Orchid HTTP observability helpers."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from orchid_commons import (
    create_fastapi_correlation_dependency,
    create_fastapi_observability_middleware,
)

app = FastAPI()
app.middleware("http")(create_fastapi_observability_middleware())

correlation_dependency = create_fastapi_correlation_dependency()


@app.get("/health")
async def health(_: object = Depends(correlation_dependency)) -> dict[str, bool]:
    return {"ok": True}
