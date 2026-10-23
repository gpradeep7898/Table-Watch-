"""FastAPI application factory and local server entry point.

Security defaults: binds to 127.0.0.1, restrictive CORS and WebSocket
origins, Host header checking against DNS rebinding, no telemetry.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router, ws_router
from .api.ws import Hub
from .config.layout import data_dir, load_layout, project_root
from .services.runtime import Runtime
from .services.settings import SettingsService
from .simulation.scenarios import load_scenarios
from .storage.db import Store
from .vision.sources import redact

log = logging.getLogger("tablewatch")

DEFAULT_ORIGINS = [
    "http://127.0.0.1:5173", "http://localhost:5173",
    "http://127.0.0.1:8000", "http://localhost:8000",
    "http://127.0.0.1:4173", "http://localhost:4173",
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def setup_logging() -> None:
    logging.basicConfig(level=os.environ.get("TABLEWATCH_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(RedactingFilter())


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Never logs values."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def detector_factory():
    from .vision.detector import YoloxOnnxDetector

    model = os.environ.get("TABLEWATCH_MODEL", "yolox_nano")
    return YoloxOnnxDetector(model=model)


def create_app(db_path: str | Path | None = None, run_loop: bool = True, autoplay: bool | None = None) -> FastAPI:
    load_dotenv(project_root() / ".env")
    store = Store(db_path or os.environ.get("TABLEWATCH_DB") or data_dir() / "tablewatch.db")
    layout = load_layout()
    scenarios = load_scenarios({t.id for t in layout.tables})
    settings = SettingsService(store)
    runtime = Runtime(store, layout, scenarios, settings, detector_factory=detector_factory)
    retention = settings.privacy().get("event_retention_days")
    if retention:
        store.purge_older_than(float(retention))
    if autoplay is None and os.environ.get("TABLEWATCH_AUTOPLAY") is not None:
        autoplay = os.environ["TABLEWATCH_AUTOPLAY"] not in ("0", "false", "no")
    runtime.start_simulation(autoplay=autoplay)

    hub = Hub()
    origins = [o for o in os.environ.get("TABLEWATCH_ALLOWED_ORIGINS", "").split(",") if o] or DEFAULT_ORIGINS
    bind_host = os.environ.get("TABLEWATCH_HOST", "127.0.0.1")

    async def flush(snapshot: bool = False) -> None:
        msgs, runtime.outbox = runtime.outbox, []
        if snapshot or msgs:
            msgs.append(runtime.snapshot())
        await hub.broadcast(msgs)

    async def loop() -> None:
        last = time.monotonic()
        last_snapshot = 0.0
        last_frame = 0.0
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            dt, last = min(0.5, now - last), now
            moved = runtime.tick(dt)
            if not hub.clients:
                runtime.outbox.clear()
                continue
            msgs, runtime.outbox = runtime.outbox, []
            if msgs or now - last_snapshot > 0.5:
                msgs.append(runtime.snapshot())
                last_snapshot = now
            if moved or now - last_frame > 1.0:
                frame = runtime.frame_payload()
                if frame:
                    msgs.append(frame)
                last_frame = now
            await hub.broadcast(msgs)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(loop()) if run_loop else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            runtime.stop_cv()
            runtime._end_current_sim("stopped")
            store.close()

    app = FastAPI(title="TableWatch AI", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.runtime = runtime
    app.state.hub = hub
    app.state.flush = flush
    app.state.history_job = None
    app.state.allowed_origins = set(origins)
    app.state.bind_host = bind_host
    hosts = ["127.0.0.1", "localhost", "testserver"] + [h for h in os.environ.get("TABLEWATCH_ALLOWED_HOSTS", "").split(",") if h]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type"], allow_credentials=False)

    @app.exception_handler(Exception)
    async def unhandled(request, exc):  # visible, redacted, never a stack trace to the client
        log.exception("unhandled error")
        return JSONResponse({"detail": {"code": "server_error", "message": redact(f"{type(exc).__name__}: {exc}")}}, 500)

    app.include_router(router)
    app.include_router(ws_router)

    dist = project_root() / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Any:
            target = (dist / path).resolve()
            if path and dist in target.parents and target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


def main() -> None:
    import uvicorn

    setup_logging()
    host = os.environ.get("TABLEWATCH_HOST", "127.0.0.1")
    port = int(os.environ.get("TABLEWATCH_PORT", "8000"))
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        log.warning("Binding to %s exposes the control API beyond this computer. There is no authentication. "
                    "Only do this on a trusted network.", host)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
