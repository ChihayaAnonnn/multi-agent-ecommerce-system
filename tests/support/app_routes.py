"""展开 FastAPI 应用的嵌套路由，得到与冻结契约同粒度的路由序列。"""

from __future__ import annotations

from typing import Any, Iterator


def effective_routes(routes: Any) -> Iterator[Any]:
    """展开演示路由等动态容器；`app.routes` 里的容器没有 `path` 属性。"""
    for route in routes:
        if hasattr(route, "effective_candidates"):
            yield from effective_routes(route.effective_candidates())
        else:
            yield route


def route_paths(routes: Any) -> set[str]:
    return {route.path for route in effective_routes(routes)}
