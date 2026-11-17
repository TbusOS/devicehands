---
title: 能力 · app
type: capability-spec
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [capability, app, apk, install, start, stop]
---

# Capability · app

Android app（apk）管理：安装 / 卸载 / 启动 / 停止 / 列表 / 清数据。

---

## CLI

```bash
dhands app install <apk> [options]
dhands app uninstall <package> [options]
dhands app start <package>[/.Activity]
dhands app stop <package>
dhands app list [--filter PATTERN] [--system] [--disabled]
dhands app info <package>
dhands app clear-data <package>        # 清 app 数据（ask 级）
alb app grant <package> <permission>   # 授权
alb app revoke <package> <permission>
```

选项：
- `--user U` 指定用户（多用户场景）
- `--keep-data` uninstall 时保留数据
- `--replace` install 时替换已存在
- `--downgrade` 允许降级
- `--abi <abi>` 指定 ABI
- `--grant-runtime` install 后立即授予运行时权限

---

## MCP tools

```python
@mcp.tool()
async def dhands_app_install(apk_path: str,
                          replace: bool = True,
                          grant_runtime: bool = False) -> dict:
    """
    Install an APK on the device.

    When to use:
      - Deploying test app
      - Updating existing app

    LLM notes:
      - apk_path is LOCAL path on your server, not on device.
      - replace=True (default) overwrites; False fails if exists.
      - If install fails with INSTALL_FAILED_OLDER_SDK, target API too high for device.
    """

@mcp.tool()
async def dhands_app_uninstall(package: str, keep_data: bool = False) -> dict:
    """Uninstall package. Permission level: ask (since user data may be lost)."""

@mcp.tool()
async def dhands_app_start(component: str) -> dict:
    """
    Start an app / activity.

    Args:
      component: package name (e.g. "com.example") starts default launcher activity,
                 OR "package/.Activity" starts specific activity.
    """

@mcp.tool()
async def dhands_app_stop(package: str) -> dict:
    """Force-stop app. Kills all processes. Permission: allow."""

@mcp.tool()
async def dhands_app_list(filter: str | None = None,
                       include_system: bool = False) -> dict:
    """List installed packages. Returns structured list.
       filter is a substring match on package name."""

@mcp.tool()
async def dhands_app_info(package: str) -> dict:
    """Detailed info: version, install time, permissions, components, size."""

@mcp.tool()
async def dhands_app_clear_data(package: str) -> dict:
    """Clear app data. DESTRUCTIVE. Permission: ask."""
```

---

## 业务函数

```python
# src/devicehands/capabilities/app.py

async def install(transport, apk: Path,
                  replace: bool = True,
                  grant_runtime: bool = False) -> Result[InstallResult]:
    if not apk.exists():
        return fail(code="FILE_NOT_FOUND", ...)

    # adb 有原生 install，ssh 需要 push + pm install
    if isinstance(transport, AdbTransport):
        flags = []
        if replace: flags.append("-r")
        if grant_runtime: flags.append("-g")
        r = await transport.shell(f"adb install {' '.join(flags)} {apk}", timeout=120)
        # ^ 伪代码，实际通过 transport.install(apk, ...)
    elif isinstance(transport, SshTransport):
        remote = f"/data/local/tmp/{apk.name}"
        await transport.push(apk, remote)
        r = await transport.shell(f"pm install -r {remote}", timeout=120)
        await transport.shell(f"rm {remote}")
    else:
        return fail(code="TRANSPORT_NOT_SUPPORTED", ...)

    # parse "Success" / INSTALL_FAILED_*
    ...

async def uninstall(transport, package: str,
                    keep_data: bool = False) -> Result[None]:
    perm = await transport.check_permissions("app.uninstall", {"package": package})
    if perm.behavior == "deny": return fail(...)
    if perm.behavior == "ask": ...

    cmd = f"pm uninstall {'-k ' if keep_data else ''}{package}"
    r = await transport.shell(cmd)
    ...

async def start(transport, component: str) -> Result[None]:
    if "/" in component:
        cmd = f"am start -n {component}"
    else:
        cmd = f"monkey -p {component} -c android.intent.category.LAUNCHER 1"
    return await transport.shell(cmd)

async def stop(transport, package: str) -> Result[None]:
    return await transport.shell(f"am force-stop {package}")

async def list_apps(transport, filter: str | None = None,
                    include_system: bool = False) -> Result[list[AppInfo]]:
    flags = []
    if not include_system:
        flags.append("-3")   # 只 3rd-party
    r = await transport.shell(f"pm list packages {' '.join(flags)}")
    # parse "package:com.xxx" lines
    ...

async def info(transport, package: str) -> Result[AppDetails]:
    r = await transport.shell(f"dumpsys package {package}")
    # parse versionCode / versionName / requested perms / services / activities ...
    ...

async def clear_data(transport, package: str) -> Result[None]:
    perm = await transport.check_permissions("app.clear_data", {"package": package})
    if perm.behavior == "deny": return fail(...)
    r = await transport.shell(f"pm clear {package}")
    ...
```

---

## 权限规则

| 操作 | 默认 |
|-----|------|
| install | allow |
| start / stop / list / info | allow |
| uninstall | **ask**（用户数据可能丢） |
| clear_data | **ask**（数据会丢） |
| grant / revoke | ask |
| 对系统级包（`com.android.*`）uninstall | **deny** |

---

## 典型用例

### 测试流：装 + 启 + 跑 + 清
```bash
dhands app install my-test.apk --grant-runtime
dhands app start com.example.test
# ... 跑测试 ...
dhands app stop com.example.test
dhands app clear-data com.example.test  # 清状态准备下次
dhands app uninstall com.example.test --keep-data=false
```

### 查看某 app 版本
```bash
dhands app info com.example.app
# returns: versionCode/Name, installed/update time, permissions, ...
```

### 列出某前缀的所有包
```bash
dhands app list --filter com.example
```

---

## 错误场景

| 错误码 | 场景 | suggestion |
|-------|------|-----------|
| `APP_NOT_INSTALLED` | 对不存在的包操作 | dhands app list 确认 |
| `PACKAGE_NAME_INVALID` | 包名格式错 | 应为 com.x.y |
| `FILE_NOT_FOUND` | apk 不存在 | 检查路径 |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | 装老版本 | 用 --downgrade 或先 uninstall |
| `INSTALL_FAILED_OLDER_SDK` | apk targetSdk 高于设备 | 找对应版本 |
| `INSTALL_FAILED_INSUFFICIENT_STORAGE` | 板子盘满 | 清空间 |

---

## 关联文件

- `src/devicehands/capabilities/app.py`
- `src/devicehands/cli/app_cli.py`
- `src/devicehands/mcp/tools/app.py`
- `tests/capabilities/test_app.py`
