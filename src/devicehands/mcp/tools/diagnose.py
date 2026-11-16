"""MCP tools: dhands_bugreport, dhands_anr_pull, dhands_tombstone_pull, dhands_devinfo."""

from __future__ import annotations

from typing import Any

from devicehands.capabilities.diagnose import (
    anr_pull,
    bugreport,
    devinfo,
    tombstone_pull,
)
from devicehands.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def dhands_bugreport(device: str | None = None) -> dict[str, Any]:
        """Trigger adb bugreport and save the zip to workspace.

        When to use:
            - Full system snapshot for hard-to-reproduce bugs
            - Escalating to a vendor (they typically ask for this)

        LLM notes:
            - Takes 60-180 seconds. Synchronous.
            - Returns only the zip path (NOT the contents).
            - Extract and use dhands_log_search to analyze.
        """
        t = build_transport(device_serial=device)
        r = await bugreport(t, device=device)
        return r.to_dict()

    @mcp.tool()
    async def dhands_anr_pull(
        clear_after: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Pull /data/anr/*.txt into workspace.

        When to use:
            - Immediately after an ANR is observed
            - Periodically polling a long-running test session
        """
        t = build_transport(device_serial=device)
        r = await anr_pull(t, clear_after=clear_after, device=device)
        return r.to_dict()

    @mcp.tool()
    async def dhands_tombstone_pull(
        limit: int = 10,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Pull native crash tombstones.

        Args:
            limit: pull only the most recent N tombstones
        """
        t = build_transport(device_serial=device)
        r = await tombstone_pull(t, limit=limit, device=device)
        return r.to_dict()

    @mcp.tool()
    async def dhands_devinfo(device: str | None = None) -> dict[str, Any]:
        """Composite device info (brand / model / build / kernel / battery / storage).

        Fast — returns structured data directly, no artifact.

        Good first call to contextualise a debugging session.
        """
        t = build_transport(device_serial=device)
        r = await devinfo(t)
        return r.to_dict()
