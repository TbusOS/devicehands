# Changelog

> 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。
> 版本编号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### 敏感词检查:真实的词不再放在公开仓库里(2026-11-16)
- `scripts/check_sensitive_words.sh` 只保留通用规则;真实要拦的词(公司名、内部用户名、内网地址、机器名等)从私有词表读取:`$DHANDS_SENSITIVE_WORDS_FILE`,默认 `~/.config/devicehands/sensitive-words.txt`,一行一个扩展正则。没有私有词表时只跑通用规则,并在 stderr 说明。
- 新参数:`--require-private`(缺私有词表就失败,退出码 2)、`--file PATH...`(检查指定文件,提交说明的检查用它)。
- `CLAUDE.md`、`CONTRIBUTING.md`、安全审查规则只写类别,不写具体的值;pre-commit 的提交说明检查改成调用同一个脚本。
- 合并进来的历史在第一次推送前改写过一次,见 `docs/contributing/history-rewrites.md` 第 4 次记录。

### 改名为 DeviceHands,能和旧的 alb 装在同一台电脑上(P1,2026-11-16)
设计依据:`docs/devicehands-design.md` 5.10、5.11。新旧两套的名字、端口、目录全部不同,新程序不读旧程序的配置和数据。

| 项 | 旧 | 新 |
|---|---|---|
| Python 包 / 模块 | `android-llm-bridge` / `alb` | `devicehands` / `devicehands` |
| 命令 | `alb`、`alb-mcp`、`alb-api` | `devicehands` 和 `dhands`(同一个入口)、`dhands-mcp`、`dhands-hub` |
| 环境变量前缀 | `ALB_` | `DHANDS_`(旧变量不再生效) |
| MCP 服务名 / 工具前缀 | `alb` / `alb_` | `devicehands` / `dhands_` |
| 网页和接口端口 | 8765 | 8775 |
| adb 隧道(hub 本机转发口) | 5037 | 8776(不再占 adb 自己的 5037) |
| 串口隧道(hub 本机转发口) | 9001 | 8777 |
| 台架状态页 | 8731 | 8741 |
| 全局配置 | `~/.config/alb/config.toml` | `platformdirs` 的配置目录下 `config.toml`(Linux:`~/.config/devicehands/config.toml`) |
| 工作目录默认值 | 当前目录的 `workspace/`,否则 `~/.alb-workspace` | config.toml 的 `[workspace] root`,否则数据目录下 `workspace/`(Linux:`~/.local/share/devicehands/workspace`);不再看当前目录 |
| 下载的 adb / fastboot | `~/.local/share/alb` | 数据目录下 `tools/` |
| 设备子目录名 | 序列号原样(无线 adb 带 `:`) | `:` 换成 `+`;`COM5`、`NUL` 这类 Windows 保留名前后加 `+` |
| 台架程序目录 / 主文件 | `clients/windows-agent/alb_agent.py` | `bench-py/dhands_bench.py` |
| 台架启动脚本 / 配置 | `run-agent.bat` / `agent.conf` | `run-bench.bat` / `bench.conf` |
| 台架状态目录 / 日志 | `.alb-agent/` / `logs/agent.log` | `.dhands-bench/` / `logs/bench.log` |
| 台架计划任务名 | `alb-agent` | `dhands-bench` |
| 台架环境变量 | `ALB_AGENT_NO_PAUSE`、`ALB_AGENT_LAUNCHER` | `DHANDS_BENCH_NO_PAUSE`、`DHANDS_BENCH_LAUNCHER` |
| hub `/health` 的 `api` 字段 | `"alb"` | `"devicehands"` |

- `dhands-hub` 现在认 `--help` 和 `--version`(打印后退出,不启动服务);不认识的参数报错退出(退出码 2),也不启动服务。不带参数时和以前一样。
- 主程序在没有 `fcntl` / `termios` 的系统(Windows)上能导入;那里网页终端返回 `TERMINAL_UNSUPPORTED_OS`。
- config.toml 的 `[workspace] root` 以前不生效,现在生效(`DHANDS_WORKSPACE` 仍优先)。
- 不变:协议里的名字(HTTP 头 `x-alb-token` / `x-alb-csecret`、`/agent/connect`、帧字段),留到协议 v2。
- 升级注意:
  - 旧数据不迁移。要继续用旧工作目录,设 `DHANDS_WORKSPACE` 指向它。
  - 已经部署的旧版台架程序不能用 `dhands bench agent-update` 升到新版(旧版要求更新包里有 `alb_agent.py`)。第一次要把 `bench-py/` 手工拷到台架主机,`agent.conf` 改名为 `bench.conf`,再运行一次 `setup-remote-start.bat` 注册 `dhands-bench` 任务;或在 hub 上设 `DHANDS_BENCH_AGENT_TASK` 指向旧任务名。
  - 不经过 dhands 的 `adb` 命令默认连 5037,看不到台架上的设备;用 `dhands devices`,或 `adb -P 8776`。

### 往板子推文件不再要求确认(2026-11-16)
- `POST /devices/{serial}/files/push` 和 `/devices/{serial}/files/push/stream` 推到 `/system`、`/vendor`、`/data`、`/dev` 等路径时直接执行,不再返回 `requires_confirm` / 以 `sensitive_path` 断开。
- 原因:`docs/permissions.md` 定的规则是板子上的操作不拦截、不先问;这一处是 2026-10-23 去掉板子侧拦截时漏改的,项目所有者 2026-11-16 确认。
- 请求里的 `force` 照常接受,不起作用。相对路径、空字节、回车换行、带 `..` 的路径仍然拒绝。

### 许可证改为 Apache 2.0(2026-10-10)
- `LICENSE` 换成 Apache License 2.0 全文,新增 `NOTICE`;`pyproject.toml`、`alb.__license__`、生成的 `SKILL.md`、README 和网页页脚同步改。
- 原因:Apache 2.0 带明确的专利授权,设备厂商的法务评审更容易通过;和 AOSP、Gemma 4 用同一个许可证。
- 2026-10-10 之前发布的版本(到 `9e86e11` 为止)仍是 MIT,已经拿到的副本按 MIT 使用。

### M1 · 核心完成（W1-W3）
- ✅ 4 个传输 beta：AdbTransport（A/B）、SshTransport（C）、SerialTransport（G）、HybridTransport（智能路由）
- ✅ 6 个能力 beta：shell / logging（含 UART）/ filesync（含 rsync）/ diagnose / power / app
- ✅ 权限系统：黑名单 + 多层策略 + 每个 transport 自定义 check_permissions
- ✅ CLI：21 个顶层命令 + 7 个子命令组
- ✅ MCP 服务器：21 个工具全注册，stdio 运行
- ✅ 一键安装 / 卸载脚本（完全用户态，不碰系统）
- ✅ `alb setup {adb,wifi,ssh,serial}` 引导式配置
- ✅ SKILL.md 自动生成（`alb skills generate`）
- 🚧 剩余：更多集成测试（M1 核心能力已全部完成）

### M2 · Agent 首批落地 · 后端 step 1（2026-04-16）
- ✅ `src/alb/agent/backends/ollama.py` OllamaBackend（POST `/api/chat`，tool_calls 支持，5 种错误映射：unreachable/timeout/http-error，health 查 `/api/tags`）
- ✅ `src/alb/agent/backends/__init__.py` `get_backend(name, **kwargs)` lazy 工厂
- ✅ `src/alb/agent/loop.py` AgentLoop.run() 完整实现（ReAct-lite 循环、max_turns 熔断、tool 分发、artifacts 提取、BackendError → Result 翻译）
- ✅ `src/alb/agent/session.py` ChatSession JSONL 落盘 + load 解析 + `clear()`
- ✅ `src/alb/agent/backend.py` Message/ToolCall 增 `from_dict`（支持 session load）
- ✅ `pyproject.toml` GitHub URL 从 skyzhangbinghua → TbusOS
- ✅ registry ollama 状态 planned → beta
- ✅ 31 单测（16 Ollama HTTP mock / 7 Session JSONL / 8 AgentLoop）
- 🚧 剩余 step 2：`alb chat` CLI + FastAPI `/chat` POST（下一轮）

### M1 · prompt_builder（2026-04-16）
- ✅ `src/alb/infra/prompt_builder.py` 系统提示词组装器
  - `PromptBlock` / `Prompt` / `PromptBuilder`（链式 fluent 接口）
  - 静态在前、动态在后的不变式（builder 强制检查，违反即 `PromptOrderError`）
  - 三种输出：`as_text()` / `as_anthropic()`（cache_control 自动放在最后一个静态块）/ `as_openai()`
  - `default_agent_prompt(device, transport, workspace, tool_count, extra_static, extra_dynamic)` 一行生成 agent 默认提示词
  - 常量 `DEFAULT_ROLE` / `DEFAULT_SAFETY_RULES` / `DEFAULT_TOOL_NORMS`
- ✅ `src/alb/agent/loop.py` 接线 prompt_builder（示例 docstring + 不再重复定义 DEFAULT_SYSTEM_PROMPT）
- ✅ 25 单测：顺序不变式 / 三种输出格式 / cache_boundary 位置 / default 各字段组合

### M1 · HybridTransport beta（2026-04-16）
- ✅ `src/alb/transport/hybrid.py` 智能路由：shell→primary；logcat→adb→ssh；uart→serial；dmesg/kmsg→adb→ssh→serial；push/pull→ssh→adb；forward→adb→ssh；reboot recovery/bootloader→adb only
- ✅ `pick_for(op, hint)` 公开接口 + 未覆盖能力走 `TRANSPORT_NOT_SUPPORTED` 拒绝
- ✅ 聚合 supports_boot_log / supports_recovery + 聚合 health 快照
- ✅ registry hybrid 状态 planned → beta
- ✅ 30 单测，覆盖路由决策 + fallback + 拒绝路径 + 委托 check_permissions + health 聚合

### M1 · Agent 层架构预留（2026-04-16）
- ✅ `src/alb/agent/` 骨架：`LLMBackend` ABC + `AgentLoop` + `ChatSession` + `Message`/`ToolCall`/`ToolSpec`/`ChatResponse` 数据原语
- ✅ `BackendSpec` + `BACKENDS` registry（ollama / openai-compat / llama-cpp / anthropic，全部 planned）
- ✅ ADR-016 · 可插拔 LLM Backend + 本地小模型支持
- ✅ `docs/agent.md` 完整设计文档（模块 / 选型 / 接入 / 路线图 / 风险）
- ✅ `docs/project-plan.md` M2/M3/M4+ 拆入 agent 落地 + Web 三层特性（Tier 1/2/3）
- ✅ `docs/architecture.md` 分层图加入 L1.5 Agent 层

### M1 · 开源中立清理（品牌示例字样统一）（2026-04-16）
- ✅ 移除历史遗留的品牌示例字样，统一为 com.example（docs/capabilities/*, tests/capabilities/test_app.py, docs/methods/01-ssh-tunnel-adb.md）
- ✅ `git-filter-repo` 精确改写历史，清理旧 commit diff 里的品牌示例残留（commit hash 已全部重生）

---

## [0.0.1] · 2026-04-15

### Added
- M0 · 初始仓库骨架
- 完整技术方案文档（`docs/` 下 15 篇）
  - 总览 / 架构 / 设计决策 / LLM 集成
  - 权限系统 / 错误码 / tool 编写指南
  - 项目计划（M0-M3 里程碑）
  - 方案对比 + 4 方案详细（A/B/C/G）+ 3 方案占位（D/E/F）
  - 6 业务能力文档（shell/logging/filesync/diagnose/power/app）
  - 贡献指南
- LLM 集成配置模板（Claude Code / Cursor / Codex）
- `llm/CLAUDE.md` + `llm/AGENTS.md`
- Python 骨架（`pyproject.toml` + `src/alb/` 各模块 `__init__.py`）
  - `infra/`: result / errors / permissions / registry / workspace
  - `transport/`: base.py (ABC)
  - `capabilities/` `cli/` `mcp/` `api/` 占位
- 冒烟测试（验证包可 import、registry 非空、权限黑名单生效）
- MIT License
