"""MCP tools: dhands_push, dhands_pull, dhands_rsync."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from devicehands.capabilities.filesync import pull as cap_pull
from devicehands.capabilities.filesync import push as cap_push
from devicehands.capabilities.filesync import rsync_sync as cap_rsync
from devicehands.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def dhands_push(
        local: str,
        remote: str,
        verify: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Push a local file (or directory) to the device.

        LLM notes:
            - `local` is a path on YOUR server, not inside the device.
            - Push to /data/local/tmp/ for safe temporary landing.
            - Pushing to /system/ /vendor/ /product/ is ASK-level (needs
              explicit confirmation).
            - verify=True adds md5 check (slower).

        Args:
            local: path on host (must exist)
            remote: destination path on device
            verify: md5-verify after push
            device: optional device serial
        """
        t = build_transport(device_serial=device)
        r = await cap_push(t, Path(local), remote, verify=verify)
        return r.to_dict()

    @mcp.tool()
    async def dhands_pull(
        remote: str,
        local: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Pull a remote file/dir to local.

        If `local` is None, lands in workspace/devices/<serial>/pulls/
        with a timestamped basename.
        """
        t = build_transport(device_serial=device)
        r = await cap_pull(
            t,
            remote,
            Path(local) if local else None,
            device=device,
        )
        return r.to_dict()

    @mcp.tool()
    async def dhands_rsync(
        local_dir: str,
        remote_dir: str,
        delete: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Incremental directory sync. Requires ssh transport (method C).

        When to use:
            - Deploying large directories with few changes (SDK output,
              compiled ROM system/ tree)
            - Any time dhands_push would re-transfer unchanged files

        Not implemented in M1-W2; scheduled for M1-W3.
        """
        t = build_transport(device_serial=device)
        r = await cap_rsync(t, Path(local_dir), remote_dir, delete=delete)
        return r.to_dict()
