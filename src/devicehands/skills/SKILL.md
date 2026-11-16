---
name: devicehands
description: >
  Unified Android debugging bridge for LLM agents. Abstracts adb / ssh /
  UART over a single interface; exposes structured tools via MCP, CLI,
  and a future Web API.
version: 0.0.1
homepage: https://github.com/TbusOS/devicehands
license: Apache-2.0
---

# devicehands · SKILL.md

> This file is auto-generated from the registry. Do not edit by hand —
> regenerate with: `dhands skills generate`.

## Invocation styles

- **MCP** (recommended): configure `dhands-mcp` in your client. All tools
  appear with `alb_` prefix. See llm/mcp-config-examples/.
- **CLI**: `uv run dhands <group> <command>` inside the repo.
- **HTTP** (M2): FastAPI surface; see docs/architecture.md.

## Global conventions

- Every tool returns `{"ok", "data", "error", "artifacts", "timing_ms"}`.
- On failure read `error.code` (stable enum) and `error.suggestion` (actionable).
- Long outputs land in `workspace/devices/<serial>/<category>/`.
  Read them with `alb_log_search` / `alb_log_tail`.
- Board-side commands are not blocked or confirmed (reboot bootloader,
  setprop persist.*, rm, dd all run as given). Nothing asks first, so do
  not run an irreversible command the user did not ask for.

## Supported transports

| name | methods | status | requires |
|------|---------|--------|----------|
| adb | A,B | beta | adb binary |
| ssh | C,D,F | beta | asyncssh, rsync (for rsync_sync) |
| serial | G | beta | pyserial-asyncio, ser2net (Windows for TCP mode) |
| hybrid | — | planned | — |

## Capabilities (M1 ships 6)

| name | CLI | MCP tools | transports |
|------|-----|-----------|------------|
| shell | `dhands shell` | `alb_shell` | adb, ssh, serial |
| logging | `dhands logcat / dmesg / log search / log tail` + `dhands serial capture` | `alb_logcat`, `alb_dmesg`, `alb_uart_capture`, `alb_log_search`, `alb_log_tail` | adb, ssh, serial |
| filesync | `dhands fs push / pull / rsync` | `alb_push`, `alb_pull`, `alb_rsync` | adb, ssh |
| diagnose | `dhands diag bugreport / devinfo / anr pull / tombstone pull` | `alb_bugreport`, `alb_anr_pull`, `alb_tombstone_pull`, `alb_devinfo` | adb, ssh |
| power | `dhands power reboot / wait-boot / battery / sleep-wake` | `alb_reboot`, `alb_wait_boot`, `alb_battery`, `alb_sleep_wake_test` | adb, ssh, serial |
| app | `dhands app install / uninstall / start / stop / list / info / clear-data` | `alb_app_*` | adb, ssh |

Full per-capability reference: [docs/capabilities/](../../../docs/capabilities/)
Full error catalog: [docs/errors.md](../../../docs/errors.md)
