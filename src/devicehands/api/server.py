"""FastAPI server entry point (`dhands-hub` command).

M2 beta — ships `GET /health` and `POST /chat` (non-streaming).

Streaming (`GET /chat/ws`), OpenAPI fine-tuning, and auth come in M3.

Usage:
    dhands-hub                       # start the server, defaults: 0.0.0.0:8775
    DHANDS_API_PORT=9000 dhands-hub  # listen on another port
    dhands-hub --help | --version    # print and exit, server not started
    uvicorn devicehands.api.server:app    # bring your own ASGI runner
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from devicehands import __version__
from devicehands.api.adb_route import router as adb_router
from devicehands.api.agent_route import router as agent_router
from devicehands.api.app_route import router as app_route_router
from devicehands.api.assistant_route import router as assistant_router
from devicehands.api.audit_route import router as audit_router
from devicehands.api.chat_route import router as chat_router
from devicehands.api.devices_route import router as devices_router
from devicehands.api.diag_route import router as diag_router
from devicehands.api.doctor_route import router as doctor_router
from devicehands.api.files_route import router as files_router
from devicehands.api.board_config_route import router as board_config_router
from devicehands.api.bench_route import router as bench_router
from devicehands.api.flash_route import router as flash_router
from devicehands.api.info_route import router as info_router
from devicehands.api.log_search_route import router as log_search_router
from devicehands.api.logcat_stream_route import router as logcat_stream_router
from devicehands.api.meta_route import router as meta_router
from devicehands.api.metrics_route import router as metrics_router
from devicehands.api.metrics_summary_route import router as metrics_summary_router
from devicehands.api.playground_route import router as playground_router
from devicehands.api.power_route import router as power_router
from devicehands.api.screenshots_route import router as screenshots_router
from devicehands.api.serial_route import router as serial_router
from devicehands.api.sessions_route import router as sessions_router
from devicehands.api.terminal_route import router as terminal_router
from devicehands.api.timeline_route import router as timeline_router
from devicehands.api.tools_route import router as tools_router
from devicehands.api.uart_route import router as uart_router
from devicehands.api.uart_stream_route import router as uart_stream_router
from devicehands.api.ui_static import mount_ui
from devicehands.infra.env_loader import load_env_files
from devicehands.infra.ports import DEFAULT_API_PORT

# Load .env.local / .env (checkout root, then the per-user config dir; see
# infra.env_loader) so DHANDS_* values reach FastAPI request handlers.
load_env_files()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """App startup / shutdown (ADR-049 migrates the old @app.on_event
    shutdown hooks here — single ordered teardown, no deprecation).

    Startup: attach the MetricStore read model to the event bus so it
    projects every `tps_sample` for the dashboard's O(window) metric
    queries. attach() is idempotent, so a per-test create_app() that
    re-enters the lifespan against a shared bus can't double-register.
    Also install the access-log redactor (ADR-055) and dhands's own logger
    (ADR-057) — startup is the only hook that runs after uvicorn's dictConfig
    on every launch path.
    """
    from devicehands.api.access_log import install_access_log_redaction
    from devicehands.api.log_setup import install_alb_logging
    from devicehands.infra.event_bus import get_bus
    from devicehands.infra.metric_store import get_metric_store

    install_access_log_redaction()
    # ADR-057: without this, dhands's own INFO lines are dropped before they are
    # formatted — the hub log carries uvicorn requests and dhands warnings only.
    install_alb_logging()
    # ADR-065: before the first WebSocket is served (uvicorn runs lifespan
    # startup before it accepts connections), and after dhands logging so the
    # one line saying whether it went in reaches the hub log.
    from devicehands.api.ws_drain_fix import install_ws_drain_fix

    install_ws_drain_fix()
    get_metric_store().attach(get_bus())
    # Before any request is served: a /devices that lands before the bench
    # agent connects must not leave this process on the wrong adb server.
    from devicehands.infra.adb_endpoint import mark_hub_process

    mark_hub_process(True)
    warm = _start_warm_up(app)
    try:
        yield
    finally:
        mark_hub_process(False)
        if warm is not None and not warm.done():
            warm.cancel()
        from devicehands.capabilities.metrics import shutdown_all_streamers

        await shutdown_all_streamers()
        # DEBT-019: backends keep a long-lived httpx.AsyncClient open for
        # connection reuse; close on shutdown so we don't leave half-open
        # TCP sockets in tests / dev reload.
        from devicehands.agent.backends import close_probe_cache

        await close_probe_cache()
        get_metric_store().detach()
        # ADR-051: the adb + serial forwarders are process-level singletons
        # attached on first agent connect; close their OS listeners on shutdown.
        from devicehands.remote.forwarder import shutdown_forwarders

        await shutdown_forwarders()


_log = logging.getLogger("devicehands.api.warmup")


def _start_warm_up(app: FastAPI) -> asyncio.Task[None] | None:
    """Warm the chat model in the background — only for a hub started by
    main() (app.state.warm_model), never for an app a test builds.

    Only when DHANDS_OLLAMA_MODEL names the model: warming some default model
    nobody chats with would push the real one out of the model server's
    memory. DHANDS_WARMUP=0 turns it off.

    One warm-up per reply language a chat page sends (WARM_UP_LANGS), one
    after the other, each logged when it is done."""
    if not getattr(app.state, "warm_model", False):
        return None
    if os.environ.get("DHANDS_WARMUP", "1").strip() == "0" or not os.environ.get("DHANDS_OLLAMA_MODEL"):
        return None

    async def run() -> None:
        from devicehands.api.chat_route import WARM_UP_LANGS, warm_up, warm_up_request

        for lang in WARM_UP_LANGS:
            try:
                r = await warm_up(warm_up_request(lang))
            except Exception as e:  # a failed warm-up must not touch the hub
                _log.warning("model warm-up (%s) failed: %s: %s", lang, type(e).__name__, e)
                return
            if not r.get("ok"):
                # the next language would only wait on the same broken backend
                _log.warning("model warm-up (%s) failed: %s", lang, r.get("error"))
                return
            _log.info(
                "model warm-up (%s): %s ready in %.1f s (%s tools)",
                lang,
                r.get("model"),
                r.get("seconds", 0.0),
                r.get("tools"),
            )

    return asyncio.create_task(run(), name="dhands-model-warm-up")


def create_app() -> FastAPI:
    """Build the FastAPI app (kept as a factory so tests can get a fresh instance)."""
    app = FastAPI(
        title="devicehands API",
        version=__version__,
        description=(
            "Local HTTP API for dhands: health, chat agent, capabilities. "
            "Pairs with the MCP server (`dhands-mcp`) and CLI (`dhands`) — same "
            "underlying capability layer."
        ),
        lifespan=_lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"ok": "true", "version": __version__, "api": "devicehands"}

    app.include_router(adb_router)
    app.include_router(agent_router)
    app.include_router(assistant_router)
    app.include_router(app_route_router)
    app.include_router(audit_router)
    app.include_router(chat_router)
    app.include_router(devices_router)
    app.include_router(diag_router)
    app.include_router(doctor_router)
    app.include_router(files_router)
    app.include_router(board_config_router)
    app.include_router(flash_router)
    app.include_router(bench_router)
    app.include_router(info_router)
    app.include_router(log_search_router)
    app.include_router(metrics_router)
    app.include_router(metrics_summary_router)
    app.include_router(playground_router)
    app.include_router(power_router)
    app.include_router(screenshots_router)
    app.include_router(sessions_router)
    app.include_router(serial_router)
    app.include_router(terminal_router)
    app.include_router(timeline_router)
    app.include_router(tools_router)
    app.include_router(uart_router)
    app.include_router(uart_stream_router)
    app.include_router(logcat_stream_router)
    app.include_router(meta_router)

    # Best-effort mount of the React UI at /app. Missing bundle =
    # fresh clone before `cd web && npm run build`; silently skip so
    # the API keeps working.
    mount_ui(app)

    return app


# Module-level app for `uvicorn devicehands.api.server:app`
app = create_app()


_HELP_EPILOG = """\
Running dhands-hub with no arguments starts the HTTP + WebSocket server
in the foreground (web UI, REST API, bench agent endpoint). Press Ctrl-C
to stop it. Settings come from environment variables (also read from
.env.local / .env: in the source checkout when run from one, then in the
per-user config dir; never in the current directory):

  DHANDS_API_HOST      address to listen on (default 0.0.0.0, all interfaces)
  DHANDS_API_PORT      port to listen on (default {port})
  DHANDS_AGENT_TOKEN   token a bench agent must send (default: not checked)
  DHANDS_WORKSPACE     data directory for logs, sessions, bench state
  DHANDS_CONFIG        path of config.toml
  DHANDS_WS_DEFLATE    1 = compress WebSocket frames (default off)
  DHANDS_LOG_LEVEL     log level name or number (default INFO)

See docs/configuration.md for the full list.
"""


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dhands-hub",
        description="DeviceHands hub: serve the web UI, REST API and the bench agent endpoint.",
        epilog=_HELP_EPILOG.format(port=DEFAULT_API_PORT),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"dhands-hub {__version__}",
    )
    return parser


def _serve(host: str, port: int, deflate: bool) -> None:
    """Start uvicorn in the foreground. Split out so tests can replace it."""
    try:
        import uvicorn
    except ImportError:
        print(
            "[dhands-hub] uvicorn not installed. Run: uv sync  (or: pip install uvicorn[standard])",
            file=sys.stderr,
        )
        sys.exit(1)
    uvicorn.run(
        "devicehands.api.server:app",
        host=host,
        port=port,
        reload=False,
        ws_per_message_deflate=deflate,
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point referenced by pyproject.toml `[project.scripts]`.

    Arguments are parsed before anything else: `--help` / `--version`
    print and exit 0, an unknown argument exits 2. Neither starts the
    server. With no arguments the server starts as before.
    """
    _build_arg_parser().parse_args(sys.argv[1:] if argv is None else argv)

    host = os.environ.get("DHANDS_API_HOST", "0.0.0.0")
    port = int(os.environ.get("DHANDS_API_PORT", str(DEFAULT_API_PORT)))
    if host == "0.0.0.0":
        # Security audit 2026-05-02 finding MID 2: dhands-hub binds all
        # interfaces by default + has no auth. Anyone with network
        # reachability can call POST /devices/{s}/files/push, write
        # to UART, etc. Default kept (loud-deprecating later via
        # ADR-034 seed) but a startup banner now flags the risk.
        print(
            "[dhands-hub] WARNING: bound 0.0.0.0 with no auth — file push,"
            " adb shell, UART write endpoints reachable from the LAN."
            " For local-only dev set: DHANDS_API_HOST=127.0.0.1",
            file=sys.stderr,
        )
    # WebSocket compression is off unless DHANDS_WS_DEFLATE=1 (ADR-062): the hub
    # compresses every outgoing frame inside its own event loop, and on the
    # bench that — not the network — was the limit. A 2.2 GB image took 108 s
    # compressed, 19 s plain, over the same link.
    deflate = os.environ.get("DHANDS_WS_DEFLATE", "").strip() == "1"
    # uvicorn imports "devicehands.api.server:app" — this module, already loaded —
    # so the flag reaches the app it serves. Tests build their own apps
    # with create_app() and never warm a model.
    app.state.warm_model = True
    _serve(host, port, deflate)


if __name__ == "__main__":
    main()
