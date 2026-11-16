"""Default TCP ports, in one place.

Every port here can be changed with its environment variable; these are
only the values used when nothing is set.

Why these numbers: DeviceHands must run next to the program it grew out of
(which keeps using 8765 / 5037 / 9001 / 8731) on the same computer, and its
adb forwarder must not take adb's own port. A forwarder on 5037 collided with
any adb server already running on the hub machine; on its own port it does
not, and DeviceHands clients find it by asking the hub (infra.adb_endpoint).

    8775  hub web UI + HTTP/WebSocket API        DHANDS_API_PORT
    8776  hub adb forwarder (127.0.0.1)           DHANDS_ADB_FORWARD_PORT
    8777  hub serial forwarder (127.0.0.1)        DHANDS_SERIAL_FORWARD_PORT
    8741  bench program's local status page      --status-port / status_port

The bench program is a standalone script and keeps its own copy of 8741
(bench-py/dhands_bench.py, DEFAULT_STATUS_PORT).
"""

from __future__ import annotations

DEFAULT_API_PORT = 8775
DEFAULT_ADB_FORWARD_PORT = 8776
DEFAULT_SERIAL_FORWARD_PORT = 8777
DEFAULT_BENCH_STATUS_PORT = 8741

# Not ours: adb's own default server port. An adb client with no
# ADB_SERVER_SOCKET connects here. Kept as a name so code that means "adb's
# default" does not read like code that means "our forwarder".
ADB_SERVER_DEFAULT_PORT = 5037
