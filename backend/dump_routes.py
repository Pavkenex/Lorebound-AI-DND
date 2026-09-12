"""Dump all app routes, recursing into FastAPI included-router wrappers."""
import sys
sys.path.insert(0, ".")
from app.main import app  # noqa: E402


def walk(router, prefix="", depth=0):
    rows = []
    for r in getattr(router, "routes", []):
        orig = getattr(r, "original_router", None)
        if orig is not None:
            ctx = getattr(r, "include_context", None)
            p = getattr(ctx, "prefix", "") if ctx else ""
            rows += walk(orig, prefix + p, depth + 1)
        else:
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None)
            if path:
                rows.append((sorted(methods) if methods else ["-"], prefix + path))
    return rows


seen = set()
for methods, path in walk(app):
    key = (tuple(methods), path)
    if key in seen:
        continue
    seen.add(key)
    print(",".join(methods), path)
