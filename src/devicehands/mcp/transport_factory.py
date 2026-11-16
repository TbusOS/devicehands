"""Deprecated shim — the transport factory moved to :mod:`devicehands.transport.factory`.

Kept so existing ``devicehands.mcp.transport_factory`` imports keep working;
new code should import from ``devicehands.transport.factory`` directly.
"""

from __future__ import annotations

from devicehands.transport.factory import active_settings, build_transport

__all__ = ["active_settings", "build_transport"]
