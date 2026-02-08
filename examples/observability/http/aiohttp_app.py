"""Minimal aiohttp integration for Orchid HTTP observability helpers."""

from __future__ import annotations

from aiohttp import web

from orchid_commons import create_aiohttp_observability_middleware


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def build_app() -> web.Application:
    app = web.Application(middlewares=[create_aiohttp_observability_middleware()])
    app.router.add_get("/health", health)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=8080)
