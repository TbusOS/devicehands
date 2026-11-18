# DeviceHands P2「骨架」实施方案

> 状态:已确认。2026-11-17 写成,同日项目所有者定了 10.2 的前四条(都按建议);
> 2026-11-18 进仓库。基于 main `57edd6b1`。
> 上位文档:`docs/devicehands-design.md` v1.0(下文叫「框架设计」)。本文不改它的决定,
> 只把第 9 节 P2 那一行写到能派活的程度。和框架设计不完全一样的地方集中列在 10.3。
> 写法:每个决定按「是什么 → 没有它会怎样 → 为什么这么选 → 什么时候用到 → 机制」。
> 代码事实带 `文件:行`(相对仓库根);我推出来的标「推断」。
> 本文只读代码写成,没有改仓库任何文件。附录 A 列出我实际跑过的验证。

## 0. 先说结论

P2 做六件事,按依赖顺序:

1. **一条调用入口 `invoke()`**。三个接入面都只做「把输入变成一份 JSON 参数」和
   「把统一返回结构呈现出来」两件事,校验、找板子、查设备能力、查权限、记审计全在
   `invoke()` 里做一次。「三处行为一致」不靠三处各自写对,靠三处走同一段代码。
2. **工具登记表**:`@tool` 声明 + pydantic 参数模型。命令行、MCP、网页接口从登记表生成。
3. **设备模型**:板子 = 类型(android / linux …)+ 若干口;口的适配器包住现有传输,
   声明自己提供哪些设备能力;类型负责把口的能力合成板子的能力(重启模式、日志来源
   这类安卓专有的东西放在这里)。
4. **运行时对象**:入口创建,往下传。新写的代码里没有模块级状态,用测试卡住。
5. **试点四个工具**:`exec.run`、`logs.search`(一次性);`logs.dmesg`、`logs.logcat`
   (持续输出)。旧名字继续能用。
6. **旧代码里 8 处按传输名字判断、12 处探测全部改成问设备能力**,用一个扫语法树的
   测试证明,并且证明这个测试自己会报错。

分两个阶段:第一阶段不碰 `src/devicehands/remote/`、`src/devicehands/api/agent_route.py`、
`bench-py/`;第二阶段等 P4 合并后,把台架连接表、两个转发器、烧录服务挂到运行时上。

调用路径(三个接入面汇到一处):

```
  dhands logs dmesg     dhands_logs_dmesg      POST /api/tools/logs.dmesg
        |                      |                        |
   cli_gen.py             mcp_gen.py               api_gen.py        surfaces/
        \______________________|________________________/
                               v
                 invoke(runtime, name, raw_args, caller)             core/invoke.py
                   1 find tool (old names map to new)
                   2 validate args      -> INVALID_ARGUMENT
                   3 resolve board      -> BOARD_NOT_FOUND ...
                   4 kind allowed?      -> TOOL_NOT_APPLICABLE
                   5 capabilities?      -> CAPABILITY_MISSING
                   6 permission, audit  -> PERMISSION_DENIED
                               v
                 tool function (ctx, params) -> Result               tools/
                               v
                 ctx.cap(Logs) / ctx.cap(Exec)                       core/caps.py
                               v
                 kind composes board capabilities from links         core/kinds/
                               v
                 link adapter (AdbLink / SerialLink / SshLink)       core/links/
                               v
                 existing transports, unchanged                      transport/
```

## 1. 包和层

### 1.1 新增四个包

**是什么。**

| 包 | 放什么 | 对应框架设计 |
|---|---|---|
| `devicehands.core` | 运行时、登记表、调用入口、设备能力接口、板子和口、设备类型 | 第 6 节 `core/` |
| `devicehands.tools` | 迁到登记表的工具。P2 只有 `exec.py`、`logs.py`、`board.py` | 第 6 节 `tools/` |
| `devicehands.surfaces` | 三个生成器:`cli_gen.py`、`mcp_gen.py`、`api_gen.py` | 第 6 节 `surfaces/` |
| `devicehands.sdk` | 一个只做转出口的 `__init__.py`:`tool`、`ToolParams`、`ToolContext`、能力接口、`Result`、`ok`、`fail` | 5.7 |

现有的 `capabilities/`(53 个能力函数)在本文里叫「旧工具函数」。它的名字和框架设计里的
「设备能力(capability)」撞了:框架设计说的设备能力是 `exec`、`logs` 这一层,旧包里装的
其实是工具。为了不混,新的设备能力接口放在 `core/caps.py`,不再建叫 capabilities 的东西;
旧包等 P3 把函数全部迁走后删除。

**没有它会怎样。** 新代码只能塞进现有的包:生成器放进 `cli/`、`mcp/`、`api/` 就得写三份
互相不能导入的代码(现在这三个包之间互相导入已经是 5 条白名单违反项,`.importlinter:44-53`);
设备模型放进 `transport/` 就会让传输反过来依赖工具声明。

**为什么这么选。**

- 口的适配器放在 `core/links/`,包住传输,而不是让传输类自己去实现能力接口。
  没选「传输类直接继承能力接口」:那样 `transport/` 要导入 `core/`,方向反了;而且
  P2 就得改三个传输类,风险落在最老、测试最多的代码上。包一层,传输一行不改。
- `sdk` 现在就建。没选「等 P6 再建」:P6 的验收是「示例扩展不改本仓库任何文件就能加一个
  工具」,如果内置工具从第一天起就只经 `sdk` 拿 `tool`、`ToolContext`,这个出口够不够用
  在 P2、P3 就被内置工具验证了,不会到 P6 才发现缺东西要改接口。兼容承诺仍然按框架设计
  5.12 从 1.0 才开始,P2 标试验性。
- `tools` 层放在 `capabilities` 之上。迁移期间新工具要用旧包里和传输无关的辅助函数
  (例如 `capabilities/logging.py:746` 的按行统计 `_LineStats`),允许向下导入;反方向不允许。

**什么时候用到。** 每次加工具(写在 `tools/`)、加连接方式(写在 `core/links/`)、
加设备类型(写在 `core/kinds/`)、加接入面的呈现方式(写在 `surfaces/`)。

**机制。** 文件清单:

```
src/devicehands/
  sdk/__init__.py            re-exports only
  core/
    caps.py                  capability interfaces + their result dataclasses
    registry.py              ToolSpec, @tool, ToolParams, Opt/Arg/Only, Aliases,
                             ToolRegistry (declaration checks live here)
    context.py               ToolContext, Caller, StreamEvent, Chunk, Progress
    invoke.py                invoke(): the only call path
    policy.py                PermissionPolicy, AuditSink + default implementations
    runtime.py               Runtime
    board.py                 BoardDesc, LinkDesc, BoardAddress, Board, BoardTable
    boardfile.py             load board files; derive the default board
    logstats.py              per-line statistics and line parsers for log streams
    links/__init__.py        Link base, adapter table, link_for(), provide()
    links/adb.py  serial.py  ssh.py
    kinds/__init__.py        DeviceKind, builtin_kinds()
    kinds/generic.py  linux.py  android.py
  tools/
    __init__.py              BUILTIN_MODULES = ("...tools.exec", "...tools.logs")
    exec.py                  exec.run
    logs.py                  logs.dmesg, logs.logcat, logs.search
    board.py                 board.list
  surfaces/
    common.py                envelope, error-category table, argv_for()
    cli_gen.py  mcp_gen.py  api_gen.py
tests/
  support/fakes.py           FakeLink, FakeExec, FakeLogs, make_runtime()
  support/sample_tools.py    echo / fail / ticker tools for generator tests
  core/  tools/  surfaces/  contract/
  test_no_transport_name_checks.py
  test_no_new_globals.py
```

### 1.2 更新后的导入方向检查

**是什么。** `.importlinter` 的层次改成下面这样,再加三条「禁止导入」规则。白名单
(`ignore_imports`)一条不加。

```ini
[importlinter:contract:layers]
layers =
    devicehands.cli | devicehands.mcp | devicehands.api | devicehands.skills
    devicehands.surfaces
    devicehands.tools
    devicehands.capabilities | devicehands.agent
    devicehands.sdk
    devicehands.core
    devicehands.transport
    devicehands.remote
    devicehands.infra

[importlinter:contract:tools-reach]
name = Tools reach hardware only through device capabilities
type = forbidden
source_modules = devicehands.tools
forbidden_modules =
    devicehands.transport
    devicehands.remote
    devicehands.surfaces
    devicehands.cli
    devicehands.mcp
    devicehands.api
allow_indirect_imports = true

[importlinter:contract:core-knows-no-transport-class]
name = Only link adapters know concrete transports
type = forbidden
source_modules =
    devicehands.core.caps
    devicehands.core.registry
    devicehands.core.context
    devicehands.core.invoke
    devicehands.core.policy
    devicehands.core.runtime
    devicehands.core.board
    devicehands.core.kinds
forbidden_modules =
    devicehands.transport.adb
    devicehands.transport.ssh
    devicehands.transport.serial
allow_indirect_imports = true

[importlinter:contract:generators-know-only-the-registry]
name = Surface generators do not import tools or transports
type = forbidden
source_modules = devicehands.surfaces
forbidden_modules =
    devicehands.tools
    devicehands.capabilities
    devicehands.transport
    devicehands.remote
allow_indirect_imports = true
```

**没有它会怎样。** 「工具只依赖设备能力」「生成器不认识具体工具」就只是约定。现在
8 处按名字判断正是在没有检查的情况下一处处长出来的。

**为什么这么选。** 层次检查只管上下方向,管不了「工具不许直接碰传输」(工具在传输上面,
向下导入本来是合法的),所以要另加禁止规则。没选「只靠代码评审」:CI 里已经有这一步
(`.github/workflows/ci.yml:61`),加规则不加步骤。

**什么时候用到。** 每次提交,CI 的 Ubuntu 3.11 那一格。

**机制。** 我用草稿目录里的配置对当前代码试过:带可选层(包还不存在时写成
`(devicehands.surfaces)`)的层次规则和禁止规则都能被 import-linter 2.16 接受,当前代码
通过(附录 A 第 4 条)。实施时第一步先建空包,所以正式配置里不需要括号。
`boardfile.py` 和 `links/` 没列进第二条规则的 `source_modules`:`links/` 就是允许认识
传输类的地方;`boardfile.py` 要调 `transport/factory.py` 的构造函数。

## 2. 设备能力接口

### 2.1 两级:口提供的能力,板子的能力

**是什么。** 设备能力是一组抽象基类,每个代表「对板子能做的一类事」。分两级:

- **口的能力**:一条物理连接本来就能做的事。adb 口能执行命令、传文件、转发端口;
  串口能收发字节。由口的适配器声明。
- **板子的能力**:工具真正用的。由**设备类型**把口的能力合成出来。例如「读日志」
  在安卓板上有 logcat、内核日志、串口输出三个来源,在 Linux 板上没有 logcat;
  「重启」在安卓板上有 recovery、bootloader 等目标,在 Linux 板上只有普通重启。

```
  board "demo" (kind = android)
    links:  usb (adb)          console (serial)
              Exec               Console
              Files              Exec (when a shell is at the prompt)
              Forward
              AdbDaemon
                 \                  /
                  AndroidKind.compose(links)
                           v
    board capabilities:
      Exec      -> usb          (first link that has it, in declared order)
      Files     -> usb
      Console   -> console
      Logs      -> sources: logcat (usb), kernel (usb), console (console)
      Reset     -> targets: normal recovery bootloader fastboot sideload
      AdbDaemon -> usb
```

**没有它会怎样。** 只有一级的话有两种坏法。全放在口上:`reboot(mode)` 的安卓模式
还得留在每种口里,就是现在 `transport/base.py:168-172` 的样子,三个传输各写一份对
模式的取舍(`transport/adb.py:299-304`、`ssh.py:269-279`、`serial.py:727-763`)。
全放在板子上:每加一种口都要改设备类型的代码。

**为什么这么选。**

- 口只管「我能传什么」,类型只管「在这种系统上这件事怎么做」。加探针是加一种口,
  加 RTOS 是加一种类型,两边互不牵连。这是需求「安卓、Linux、RTOS、各种 MCU」加
  「先做串口和 USB、探针留位置」同时成立的最小结构。
- 没选框架设计 5.2 里否掉的「一个大传输基类」,理由框架设计已写。
- 没选「用 `typing.Protocol` 做结构匹配」:结构匹配等于换个写法的 `hasattr`,
  少实现一个方法要到调用时才知道。用抽象基类,提供方显式继承、显式登记,缺方法在
  定义类的时候就报错。

**什么时候用到。** 工具声明 `requires=[Logs]`;`invoke()` 在调用前查板子有没有;
工具里 `ctx.cap(Logs)` 取出来用。

### 2.2 接口签名(`core/caps.py`)

```python
class Capability(ABC):
    key: ClassVar[str]      # "exec" "console" "logs" "files" "flash" "reset"
                            # "forward" "memory" "registers" "breakpoints"
                            # kind-specific ones are namespaced: "android.adbd"
    link: str               # which link provides it; composed ones name them all

@dataclass(frozen=True)
class ExecResult:           # same fields as transport.base.ShellResult minus artifacts
    ok: bool; exit_code: int; stdout: str; stderr: str
    duration_ms: int; error_code: str | None = None

class Exec(Capability):
    key = "exec"
    async def run(self, cmd: str, *, timeout: float = 30.0) -> ExecResult: ...
    def stream(self, cmd: str) -> AsyncIterator[bytes]: ...
        # output of a long-running command; aclose() stops it and reaps the process

class Console(Capability):
    key = "console"
    def read(self) -> AsyncIterator[bytes]: ...          # read-only, shareable
    async def send(self, data: bytes) -> ExecResult: ... # write, no wait
    async def open(self) -> ConsoleSession: ...          # read+write on one link
    async def state(self) -> ConsoleState: ...           # shell / uboot / login / panic
    def endpoint(self) -> tuple[str, int] | None: ...
        # the TCP endpoint when the console is reached through a forwarder

class ConsoleSession(ABC):
    async def read(self, max_bytes: int = 4096) -> bytes: ...
    async def write(self, data: bytes) -> None: ...
    async def aclose(self) -> None: ...

@dataclass(frozen=True)
class LogSource:
    name: str                          # "kernel" "logcat" "console" ...
    description: str
    options: type[BaseModel] | None    # e.g. LogcatOptions for "logcat"

class Logs(Capability):
    key = "logs"
    def sources(self) -> tuple[LogSource, ...]: ...
    def open(self, source: str,
             options: BaseModel | None = None) -> AsyncIterator[bytes]: ...

class Files(Capability):
    key = "files"
    async def push(self, local: Path, remote: str) -> TransferResult: ...
    async def pull(self, remote: str, local: Path) -> TransferResult: ...
    def push_events(self, local: Path, remote: str) -> AsyncIterator[TransferEvent]:
        ...  # base class default: call push(), yield one "done" event
    def pull_events(self, remote: str, local: Path) -> AsyncIterator[TransferEvent]:
        ...

class Reset(Capability):
    key = "reset"
    def targets(self) -> tuple[str, ...]: ...   # first one is the default: "normal"
    async def reset(self, target: str = "normal") -> ExecResult: ...

class Forward(Capability):
    key = "forward"
    async def forward(self, local_port: int, remote_port: int) -> ExecResult: ...

class DirSync(Capability):          # rsync today
    key = "dirsync"
    async def sync(self, local_dir: Path, remote_dir: str, *, delete: bool = False,
                   extra_args: Sequence[str] = ()) -> ExecResult: ...

class Discover(Capability):         # "adb devices" today
    key = "discover"
    async def devices(self) -> list[DiscoveredDevice]: ...

# interface only in P2, no provider:
class Flash(Capability):
    key = "flash"
    def methods(self) -> tuple[FlashMethod, ...]: ...   # fastboot / edl / vendor:<name>
    def flash(self, method: str, images: Mapping[str, Path],
              options: Mapping[str, Any]) -> AsyncIterator[FlashEvent]: ...
class Memory(Capability):       # read(addr, size) / write(addr, data)
    key = "memory"
class Registers(Capability):    # read_all() / read(name) / write(name, value)
    key = "registers"
class Breakpoints(Capability):  # set(addr) / clear(id) / list()
    key = "breakpoints"
```

安卓专有的放在 `core/kinds/android.py`,不进 `caps.py`:

```python
class AdbDaemon(Capability):
    key = "android.adbd"
    async def adb(self, args: Sequence[str], *,
                  timeout: float = 30.0) -> ExecResult: ...
        # runs "adb <args>": ["root"], ["unroot"], ["remount"], ["tcpip", "5555"]

ANDROID_RESET_TARGETS = ("normal", "recovery", "bootloader", "fastboot", "sideload")

class LogcatOptions(BaseModel):     # the ONE place that validates these
    filter: str | None = None       # "<TAG>:<LEVEL> ..." tokens
    tags: list[str] | None = None   # shortcut; becomes "<tag>:V ... *:S"
    buffers: list[str] | None = None  # each ^[a-z]{1,16}$, at most 8
    clear: bool = False
    tail: int | None = None
```

说明几个取舍:

- **`Files` 的带进度版本在基类里有默认实现**(调一次 `push`,结束时给一条完成事件)。
  现在 `api/files_route.py:845` 用 `hasattr` 探测 `push_stream`,而基类
  `transport/base.py:106-134` 本来就定义了这两个方法(默认抛 `NotImplementedError`),
  所以探测恒为真,ssh 和串口照样通过检查然后在运行时抛异常。有了默认实现就不需要探测。
- **重启目标的默认名字保留 `normal`**。现有三个接入面的参数值都是 `normal`
  (`capabilities/power.py:66`),改名没有收益。
- **`LogcatOptions` 只有一份**。现在 tags 转 filter 写了两份
  (`capabilities/logging.py:209-211`、`api/logcat_stream_route.py:123-124`),
  buffers 的形状检查三份(`capabilities/logging.py:181-188`、
  `api/logcat_stream_route.py:68`、`transport/adb.py:175-178`)。
- **`Flash`、`Memory`、`Registers`、`Breakpoints` 只定接口**。`Flash` 的提供方在
  `remote/flash.py`,P2 第一阶段不能碰;后三个留给调试探针。`Flash.methods()` 里留了
  `vendor:<名字>`,对应框架设计 5.7 台架侧清单里 `provides = "flash"` 的厂商工具。

### 2.3 谁实现:口的适配器(`core/links/`)

**是什么。** 每种口一个适配器类,包住一个现有的传输对象,列出它提供的能力。

```python
class Link(ABC):
    type: ClassVar[str]                      # "adb" | "serial" | "ssh"
    name: str                                # link name from the board description
    def caps(self) -> Mapping[type[Capability], Capability]: ...
    async def health(self) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...
```

| 适配器 | 包住 | 提供的能力 |
|---|---|---|
| `AdbLink` | `AdbTransport` | `Exec`、`Files`、`Forward`、`AdbDaemon`、`Discover` |
| `SerialLink` | `SerialTransport` | `Console`、`Exec`(控制台停在 shell 提示符时才成功) |
| `SshLink` | `SshTransport` | `Exec`、`Files`、`Forward`、`DirSync` |

**没有它会怎样。** 「哪种连接能做什么」这件事没有唯一的存放处,于是散成现在的 8 处
名字判断。

**为什么这么选。** 适配器内部直接调现有传输的方法(包括 `stream_read`),P2 不重写
命令拼接。这意味着 `transport/adb.py:173-187` 和 `transport/ssh.py:158-171` 那两份
logcat 命令行拼接 P2 还在,合成一份留到 P3(到时改成安卓类型经 `Exec.stream` 发命令)。
没选「P2 就合并」:那要改传输,而传输自己有 14 个测试文件(`tests/transport/`),
P2 的目标是立骨架,不是重写驱动。

**什么时候用到。** 运行时按板子描述建口的时候;旧工具函数经 2.5 的过渡函数查询能力的时候。

**机制。** `HybridTransport` 删掉。生产代码不构造它
(`grep -rn HybridTransport src` 只命中定义、`transport/__init__.py:13,15` 的导出、
`infra/registry.py:96` 的清单),「一块板子多个口、按操作挑口」现在由板子加设备类型承担。
`Transport.supports_boot_log`、`supports_recovery` 两个标志在 `transport/` 之外没有任何
读取点(`grep -rn "supports_boot_log\|supports_recovery" src` 去掉 `transport/` 后 0 行;
反向验证:同一条命令不去掉 `transport/` 是 10 行),一并删。

### 2.4 设备类型(`core/kinds/`)

**是什么。** 一个设备类型 = 一个把口的能力合成板子能力的对象。

```python
class DeviceKind(ABC):
    name: ClassVar[str]
    def compose(self, links: Sequence[Link]) -> dict[type[Capability], Capability]: ...

def builtin_kinds() -> dict[str, DeviceKind]:
    return {"android": AndroidKind(), "linux": LinuxKind(),
            "rtos": GenericKind("rtos"), "mcu": GenericKind("mcu")}
```

| 类型 | 合成规则 |
|---|---|
| `GenericKind` | 每种能力取第一个提供它的口(按板子描述里口的顺序)。有 `Console` 就合成一个只有 `console` 来源的 `Logs` |
| `LinuxKind`(继承上一行) | 有 `Exec` 就给 `Logs` 加 `kernel` 来源;合成 `Reset`,目标只有 `normal` |
| `AndroidKind`(继承上一行) | `Logs` 加 `logcat` 来源(选项是 `LogcatOptions`);`Reset` 的目标换成 `ANDROID_RESET_TARGETS`,有 `AdbDaemon` 时走 `adb reboot`,否则经 `Exec` 发 `reboot <目标>`;adb 报找不到设备时退到下一个有 `Exec` 的口 |

**没有它会怎样。** 安卓的重启模式没有地方放,只能留在传输基类里;「同一个工具在
Linux 板上自动少一个日志来源」做不到,只能在工具里写 `if 安卓`。

**为什么这么选。**

- `rtos`、`mcu` 现在就能写进板子描述,用 `GenericKind`。框架设计说 P3 之前只在
  设备类型和能力接口里留位置、不写实现;`GenericKind` 是继承链的根,本来就要写,
  让这两个名字指向它不多一行实现,却让「只接了串口的 MCU 板子有 `console` 和 `logs`、
  需要 `exec` 的工具对它不出现」这句话在 P2 就能用测试验证。
- 安卓重启「adb 找不到设备就退到串口」现在是工具函数加工厂函数配合做的
  (`capabilities/power.py:118-142`、`transport/factory.py:125-140`,调用点在
  `cli/power_cli.py:30`、`mcp/tools/power.py:51`、`api/timeline_route.py:604`)。
  放进 `AndroidKind` 的 `Reset` 里,三个接入面不用各自记得传这个参数。P2 只把机制
  备好,`power.reboot` 这个工具本身 P3 再迁。
- 类型表由运行时持有(`runtime.kinds`),不是模块级变量,扩展以后可以加类型。

**什么时候用到。** 第一次解析到某块板子时合成一次,结果缓存在板子对象上。

### 2.5 怎么消掉 8 处名字判断和 12 处探测

**是什么。** 旧工具函数 P2 不迁(只迁四个试点),但它们里面的判断 P2 要清掉。给旧代码
一个过渡函数:

```python
# core/links/__init__.py
def link_for(transport: Transport) -> Link: ...
    # looks the transport's class up in the adapter table; unknown class -> TypeError
def provide(transport: Transport, cap: type[C]) -> C | None: ...
    # link_for(transport).caps().get(cap)
```

旧代码从 `if transport.name != "serial": return 不支持` 改成
`console = provide(transport, Console)`、`if console is None: return 不支持`。

**没有它会怎样。** 要么 P2 把 53 个旧工具函数全迁到登记表(那是 P3 的内容,而且
62 个 `build_transport(` 调用点要一起改),要么名字判断留到 P3,P2 的第二条验收不成立。

**为什么这么选。** 「哪个传输类对应哪个适配器」这张表只在 `core/links/__init__.py`
出现一次,按类查,不比较名字字符串。没选「查不到类时再退回去看 `name` 属性」:
那等于把名字判断集中起来而不是消掉,而且测试里手设 `t.name` 的替身会继续骗过它。

逐条改法(行号是 main `57edd6b1`):

| # | 位置 | 现在 | 改成 |
|---|---|---|---|
| 1 | `capabilities/adbd.py:117-122,135` | `name == "adb"`,再翻 hybrid 成员,再取私有 `_run` | `provide(t, AdbDaemon)`,调 `.adb(["root"])` |
| 2 | `capabilities/filesync.py:172,209` | `name != "ssh"` 加 `hasattr rsync` | `provide(t, DirSync)` |
| 3 | `capabilities/logging.py:277` | `name != "serial"` | `provide(t, Console)` |
| 4 | `capabilities/logging.py:402` | `name != "serial"` | `provide(t, Console)`,调 `.send()` |
| 5 | `capabilities/diagnose.py:96` | `name != "adb"` | `provide(t, Exec)` 和 `provide(t, Files)` 都有才继续 |
| 6 | `capabilities/app.py:102,120` | adb、ssh 两个分支 | 合成一个分支:`provide(t, Files)` |
| 7 | `capabilities/breakin.py:364` | `name != "serial"` 加 `hasattr open_session` | `provide(t, Console)`,调 `.open()` |
| 8 | `cli/main.py:191`、`api/devices_route.py:74`、`mcp/tools/devices.py:91` | `hasattr devices` | `provide(t, Discover)` |
| 9 | `api/uart_stream_route.py:136` | `hasattr open_session` | `provide(t, Console)` |
| 10 | `api/logcat_stream_route.py:175` | `hasattr stream_read`(恒为真) | 整个路由改成试点工具的旧名字入口,见 5.5 |
| 11 | `api/files_route.py:845` | `hasattr push_stream`(恒为真) | `provide(t, Files)`,调 `.push_events()` |
| 12 | `capabilities/logging.py:370-371`、`capabilities/timeline.py:776` | `getattr tcp_host / tcp_port` | `provide(t, Console).endpoint()` 返回 `(host, port)` 或 `None` |
| 13 | `cli/serial_cli.py:31` | `assert isinstance(t, SerialTransport)` | `provide(t, Console)` |
| 14 | `cli/setup_cli.py:201,209` | 直接调 `t._run` | `provide(t, AdbDaemon).adb([...])` |
| 15 | `cli/main.py:150` | `primary_transport != "adb"` 时提示「只实现了 adb」 | 删掉这段提示(三种传输都已实现,提示是过期的) |

读代码时发现的两件事,会影响第 5、6 条怎么改:

- 第 5 条的 bugreport 只用了执行命令和拉文件(`capabilities/diagnose.py:106` 的
  `transport.shell("bugreportz")` 和后面的 `transport.pull`),它要的其实是「安卓板子
  加 `Exec` 加 `Files`」,不是 adb。按名字判断把 ssh 连着的安卓板挡在了外面。
- 第 6 条的两个分支做的事一样:都是先 `push` 再 `pm install`
  (`capabilities/app.py:102-140`),只是变量名和失败时的错误码不同。

测试里手设 `t.name = "…"` 的地方要跟着改成 `MagicMock(spec=具体传输类)` 或换用
`tests/support/fakes.py`。我用 `grep -rn -E '\.name\s*=\s*"(adb|ssh|serial|hybrid)"' tests`
数到 22 行、8 个文件;调查报告写 29 处,没给 pattern,两个数的数法不同,以实施时重新数的为准。

## 3. 板子描述和寻址

### 3.1 格式

**是什么。** 一块板子一个 TOML 文件,放在配置目录的 `boards/` 下
(配置目录由 `infra/dirs.py:35` 的 `config_dir()` 给出)。格式沿用框架设计 5.2:

```toml
[board]
id    = "demo-board"      # ^[a-z0-9][a-z0-9_-]{0,31}$
kind  = "android"         # android | linux | rtos | mcu
bench = "local"           # optional, default "local"
note  = ""

[[board.link]]            # order = priority when several links offer one capability
name   = "usb"
type   = "adb"
serial = "0123456789AB"   # optional: omitted = the only adb device

[[board.link]]
name = "console"
type = "serial"
port = "/dev/ttyUSB0"     # or: tcp = "127.0.0.1:8777"
baud = 115200

[[board.link]]
name = "net"
type = "ssh"
host = "192.0.2.10"
user = "root"
```

**没有它会怎样。** 框架设计 4 节已写:只能用 adb 序列号指代设备,没有 adb 的板子
无法表示。现状是 `device_serial` 只有 adb 用(`transport/factory.py:44-61`),
代码里没有板子描述这个东西。

**为什么这么选。**

- 一个文件一块板子:厂商能单独发一个文件,用户复制进目录就生效。没选「所有板子写在
  一个大表里」(现在 `remote/bench_board.py:1-25` 的台架板子表是这种),那种不方便分发。
- 用 pydantic 模型校验,不认识的键直接报错并指出文件和键名。没选「不认识的键忽略」:
  `baud` 拼成 `buad` 时静悄悄用默认波特率,表现是串口乱码,很难想到是配置拼错。
  现有的台架板子表也是这个取向(`remote/bench_board.py:110` 起对未知键报错)。
- 口的类型 P2 实现 `adb`、`serial`、`ssh` 三种。`usb-net`、`fastboot`、`edl`、`probe`
  这几个名字解析器认识但还没有适配器,写了会得到 `BOARD_LINK_UNAVAILABLE`,
  说明「这个类型已预留,哪个阶段实现」。没选「当成未知类型」:那样报错信息会让人
  以为是拼错了。

**什么时候用到。** 运行时创建时读一遍;`dhands board list` 列出来;每次调用工具按
地址找到它。

**机制。** `core/board.py` 里 `BoardDesc`、`LinkDesc` 是 pydantic 模型
(`extra="forbid"`);每种口类型的字段由对应适配器类给出一个子模型(`AdbLinkDesc`、
`SerialLinkDesc`、`SshLinkDesc`),按 `type` 字段区分。校验规则:id 不重复;同一块板子
里口的名字不重复;`serial` 口的 `port` 和 `tcp` 二选一。读文件失败或校验失败抛
`BoardError("BOARD_DESC_INVALID", "<文件>: [board.link.1] ...")`,运行时创建失败,
三个入口各自把它显示出来。

### 3.2 没有描述文件时的默认板子

**是什么。** `boards/` 目录不存在或为空时,运行时从现有配置推出一块叫 `default`
的板子,让现在的用法一行配置都不用改。

| 默认板子的 | 从哪推 | 出处 |
|---|---|---|
| `kind` | 环境变量 `DHANDS_BOARD_KIND`,没有就是 `android` | 新增 |
| 口 `usb`(adb) | 总是有,不指定序列号 | `transport/factory.py:50-61` |
| 口 `console`(serial) | `DHANDS_SERIAL_DEVICE`,否则 `DHANDS_SERIAL_TCP`,否则配置里的默认主机和端口 | `transport/factory.py:106-121` |
| 口 `net`(ssh) | 有 `DHANDS_SSH_HOST` 或 profile 里某个设备带 `ssh_host` 时才有 | `transport/factory.py:64-70` |
| 口的顺序 | `--transport` 参数、`DHANDS_TRANSPORT`、profile 的 `primary_transport` 指的那个排第一 | `transport/factory.py:48` |

**没有它会怎样。** P2 一合并,所有现有用户都要先写板子描述才能用,违反框架设计
第 9 节「每个阶段结束时旧功能照常可用」。

**为什么这么选。** 默认类型定为 `android` 是「推断」:现在的代码把每块板子都当安卓
(logcat、`pm`、`getprop` 的工具无条件可用),默认成 `linux` 会让串口连安卓板的用户
突然少掉一批工具。Linux 板的用户设一个环境变量或写一个三行的描述文件。这一条已定
(10.2 第 4 条),`dhands doctor` 里提示当前用的是默认类型。没选「连上去探测是不是安卓」:探测要板子在线,而板子不在线时
(卡在 bootloader)恰恰是最需要工具列表稳定的时候。

**机制。** `core/boardfile.py` 的 `derive_default_board(settings, env) -> BoardDesc`。
环境变量在运行时创建时读一次,作为参数传进来,函数本身不读 `os.environ`,测试直接
传字典。

### 3.3 寻址

**是什么。** 地址是 `台架/板子/口`,三段都可省。所有接入面的 `device` 参数都接受它。

**没有它会怎样。** 多板只有 adb 能区分;「在串口上执行命令」只能靠另一个工具
(`dhands_uart_shell`,`mcp/tools/logging.py:136-163`)里写死 `override="serial"`。

**为什么这么选。** 地址带上口之后,「经串口执行命令」就是 `exec.run` 加地址
`demo-board/console`,不需要为每种口各写一个工具。旧的 adb 序列号写法继续能用,
因为 62 个调用点和所有用户脚本都在用它。

**什么时候用到。** 每次调用带 `device` 的工具。

**机制。** `BoardTable.resolve(addr) -> Board`,规则写死,有歧义就报错而不是猜
(重启、烧录打到错的板子上代价高,宁可让调用方多写一段):

| 写法 | 解析 |
|---|---|
| 不给(或空白字符串) | 环境变量 `DHANDS_BOARD` → 配置里的 `default_board` → 只有一块板子时就是它 → 都没有报 `BOARD_NOT_SELECTED`,列出已知板子 |
| 三段 `a/b/c` | 台架 a、板子 b、口 c |
| 两段 `a/b` | a 是已知板子 → 板子 a 的口 b;否则台架 a 的板子 b;两种都成立报 `BOARD_ADDRESS_AMBIGUOUS` |
| 一段 `a` | 依次试:板子 id → 默认板子的口名 → 某块板子 adb 口的 `serial` → 当作 adb 序列号,临时生成一块板子(默认板子的副本,`usb` 口的序列号设成 a);前两种同时成立报 `BOARD_ADDRESS_AMBIGUOUS` |

- 每一段先过字符检查。板子 id 和口名用 `^[a-z0-9][a-z0-9_-]{0,31}$`;当作 adb 序列号的
  一段用现有的 `is_safe_device`(`infra/workspace.py:33,75`)。`/` 不在序列号允许的
  字符里,所以用它做分隔不会和 `192.0.2.10:5555` 这种序列号冲突。
- 空白字符串当作没给。这是把 `mcp/executor.py:92` `drop_blank_device` 修过的问题
  (小模型传 `device=""`)挪到所有接入面共用的地方。
- 带口的地址得到的是「只用这一个口合成的板子视图」(`Board.via(口名)`):
  `demo-board/console` 上的 `Exec` 就是串口上的 `Exec`。
- 工作区目录名:临时生成的板子用 adb 序列号(现有产物目录不动);有描述的板子用
  板子 id。同一块实物先后用两种写法会落到两个目录,见 10.2。
- 台架段 P2 只接受 `local`。多台架要等第二阶段把台架连接表挂到运行时上。
- 每块板子带一把 `asyncio.Lock`(框架设计 5.1 的「按板加锁」)。工具声明
  `exclusive=True` 时 `invoke()` 先拿锁。P2 的四个试点都不独占,锁先备着,P3 迁烧录和
  重启时用,并取代 `api/files_route.py:73` 的 `_FILE_OP_LOCKS`。

## 4. 工具声明和登记表

### 4.1 声明的形状

**是什么。** 一个工具 = 一个异步函数 + 一个 pydantic 参数模型 + 一份声明。

```python
# tools/logs.py
class DmesgParams(ToolParams):
    duration: Annotated[
        int | None,
        Field(ge=1, le=3600, description="Seconds to collect; omit to follow "
              "until stopped (command line and WebSocket only)"),
        Opt(error="INVALID_DURATION"),
    ] = 10
    output: Annotated[
        Path | None,
        Field(description="Where to save; default is the workspace"),
        Opt(short="-o"), Only("cli"),
    ] = None

@tool(
    name="logs.dmesg",
    summary="Collect the kernel log into the workspace",
    requires=[Logs],
    kinds=["android", "linux"],
    risk="read",
    stream=Stream(event=Chunk, default_seconds=10, max_seconds=3600),
    errors=["INVALID_DURATION", "INVALID_SOURCE"],
    aliases=Aliases(
        cli=[CliAlias("dmesg", short={"duration": "-d"}, device_short=None)],
        mcp=["dhands_dmesg"],
        http=[HttpAlias("POST", "/api/log/dmesg", device_in="query")],
    ),
)
async def dmesg(ctx: ToolContext, p: DmesgParams) -> Result[DmesgSummary]:
    """When to use: kernel-level issues (driver errors, OOM, panics) ...

    LLM notes: returns a summary; the full log is result.artifacts[0].
    """
    logs = ctx.cap(Logs)
    path = ctx.artifact_path(f"{iso_timestamp()}-dmesg.txt", output=p.output)
    stats = LineStats(parse_dmesg_line)
    with path.open("wb") as f:
        async for chunk in ctx.until_stopped(logs.open("kernel"), seconds=p.duration):
            f.write(chunk); f.flush()
            stats.feed(chunk)
            await ctx.emit(Chunk(chunk))
    return ok(DmesgSummary(lines=stats.lines, errors=stats.errors, ...),
              artifacts=[path])
```

**没有它会怎样。** 现状:同一个 dmesg 在三处各写一遍
(`cli/main.py:285-301`、`mcp/tools/logging.py:69-82`、`api/log_search_route.py:58-82`),
结果是时长越界三处报法不同(命令行和 MCP 走到工具函数里得 `INVALID_DURATION`,
`capabilities/logging.py:489`;网页接口被 `Field(ge=1, le=3600)` 挡成 HTTP 422,
`api/log_search_route.py:59`),`output` 只有命令行有,网页接口的返回丢了 `artifacts`
(`infra/result.py:127-129`)。

**为什么这么选。**

- **参数用 pydantic 模型,不用函数的关键字参数。** 一个模型同时给出:JSON Schema
  (MCP 的输入描述、接口文档、前端类型)、带范围的校验、命令行选项。现在的做法是
  typer 和 FastMCP 各自从函数签名推,所以范围检查只能写在函数体里或者某一个接入面上。
  pydantic 已经是核心依赖(`pyproject.toml:35`),FastAPI 和 FastMCP 本来就用它。
  没选 dataclass:没有范围约束,也出不了 JSON Schema。
- **装饰器只往函数上挂一份 `ToolSpec`,不往任何全局表里登记。** 登记表由运行时创建时
  按 `tools/__init__.py` 里的模块清单导入并收集。没选「装饰器导入时登记到模块级列表」:
  那是一个新的全局状态,测试里造一个只含假工具的登记表会很别扭。
- **长说明写在函数的文档字符串里**,格式沿用现有 MCP 工具的
  「When to use / When NOT to use / LLM notes」分节。`tests/mcp/test_agent_tool_list.py`
  已经在检查这些分节,内嵌模型拿到的精简版也靠它。

**什么时候用到。** 加一个工具时写一次;登记表创建时校验一次;三个生成器各读一次。

**机制。** `core/registry.py`:

```python
Risk = Literal["read", "board", "bench"]
Target = Literal["board", "board-optional", "none"]

@dataclass(frozen=True)
class ToolSpec:
    name: str                          # ^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*){1,2}$
    summary: str                       # one English line
    doc: str                           # the function docstring
    params: type[ToolParams]
    result: type | None                # T in Result[T]: pydantic model or dataclass
    stream: Stream | None
    requires: tuple[type[Capability], ...]
    kinds: frozenset[str] | None       # None = any kind
    risk: Risk
    target: Target                     # default "board" when requires is non-empty
    exclusive: bool                    # take the board lock
    errors: tuple[str, ...]
    aliases: Aliases
    cli: CliHooks | None               # custom renderer / exit code, see 5.3
    fn: Callable[[ToolContext, Any], Awaitable[Result[Any]]]

class ToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, coerce_numbers_to_str=True)

class Opt:   # Annotated[...] metadata: command-line hints and error code of a field
    def __init__(self, *, short: str | None = None, name: str | None = None,
                 error: str | None = None): ...
class Arg:   # positional on the command line
    ...
class Only:  # the field exists only on these surfaces: Only("cli")
    ...

class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec], *, known_errors: Mapping[str, Any],
                 known_kinds: Collection[str]): ...   # runs the checks below
    @classmethod
    def from_modules(cls, modules: Iterable[str], **kw) -> "ToolRegistry": ...
    def get(self, name_or_alias: str) -> tuple[ToolSpec, str | None]: ...
        # second item: the new name when an old one was used
    def all(self) -> list[ToolSpec]: ...
    def applicable(self, board: Board) -> list[tuple[ToolSpec, str | None]]: ...
        # second item: why not (missing capability / kind), None = usable
```

登记表创建时的检查(任何一条不过就抛 `ToolDeclarationError`,进程起不来,而不是等到
调用时才发现):

1. 名字合法、不重复;旧名字不和任何新名字或别的旧名字重复。
2. 参数模型没有嵌套模型字段(命令行表达不了);没有叫 `device`、`confirm`、`json`、
   `follow` 的字段;没有占用保留短选项 `-d -y -f -h`。
3. `errors` 里每个码都在 `infra/errors.py` 的 `ERROR_CODES` 里;`Opt(error=…)` 同样。
4. `kinds` 里每个名字都是已知类型。
5. `stream` 不为空时,参数模型必须有 `duration: int | None` 字段。
6. `result` 能生成 JSON Schema(`pydantic.TypeAdapter(result).json_schema()` 不抛)。

`coerce_numbers_to_str=True` 是把 `mcp/executor.py:111` `coerce_string_args` 修过的问题
(小模型把全数字的序列号写成 JSON 数字)放到根上:任何字符串字段收到数字都转成字符串,
三个接入面一起受益。我验证过(附录 A 第 2 条)。

### 4.2 ToolContext

**是什么。** 工具函数拿到的唯一入口。它不暴露运行时本身。

```python
class ToolContext:
    call_id: str                    # "c-20261117-8f3a12"; also in result and audit
    tool: ToolSpec
    caller: Caller                  # who is calling (5.9); default: local single user
    board: Board | None             # None: target "none", or optional and not given
    settings: ActiveSettings
    log: logging.Logger

    def cap(self, c: type[C]) -> C: ...           # declared in requires: always present
    def opt(self, c: type[C]) -> C | None: ...    # not declared: may be absent
    def artifact_path(self, default_name: str, *, category: str = "logs",
                      output: Path | str | None = None) -> Path: ...
    async def emit(self, event: StreamEvent) -> None: ...   # no receiver: does nothing
    async def progress(self, done: float, total: float | None = None,
                       message: str = "") -> None: ...
    def until_stopped(self, source: AsyncIterator[T], *,
                      seconds: float | None) -> AsyncIterator[T]: ...
    @property
    def stopping(self) -> bool: ...

@dataclass(frozen=True)
class Caller:
    id: str = "local"
    kind: Literal["user", "model", "service"] = "user"
    surface: Literal["cli", "mcp", "http", "agent", "test"] = "cli"
```

**没有它会怎样。** 工具要么自己去找全局对象(现在的做法),要么拿到整个运行时——
那样工具能摸到别的板子、登记表、台架连接,扩展也就能摸到,以后想收紧就是破坏兼容。

**为什么这么选。** 给得窄,以后放宽是加字段;给得宽,以后收紧是删字段。
`cap()` 和 `opt()` 分开:`cap()` 拿不到说明框架的调用前检查有 bug,直接抛;
`opt()` 拿不到是正常情况,工具自己决定降级。没选「工具自己 `if board.has(X)`」:
那会让 `requires` 声明和函数体里实际用的能力慢慢对不上。

**什么时候用到。** 每次调用建一个,调用结束丢掉。

**机制。** `artifact_path` 就是现有 `infra/workspace.py:248` 的 `resolve_capture_path`,
`device` 换成板子的工作区目录名。`until_stopped` 把 `下一块数据`、`叫停`、`时长到` 三件事
放在一起等,先到的赢;叫停或到时后对源迭代器调 `aclose()`,下面的子进程在迭代器的
`finally` 里被回收(`transport/adb.py:198-206` 现在就是这么收尾的)。

### 4.3 调用前检查和 `invoke()`

**是什么。** 三个接入面调工具的唯一函数。

```python
# core/invoke.py
async def invoke(
    rt: Runtime, name: str, raw: Mapping[str, Any], *,
    caller: Caller,
    sink: EventSink | None = None,      # given when the surface can show events live
    stop: asyncio.Event | None = None,  # given when the surface can stop the call
    progress: ProgressFn | None = None,
) -> Envelope: ...
```

`raw` 是一份扁平的 JSON 对象:工具参数加两个保留键 `device`(板子地址)、`confirm`
(对「要先问」的操作表示已确认)。同一份对象可以原样当 MCP 的参数、网页接口的请求体,
命令行生成器把选项拼成它。

统一返回结构(下文叫「返回结构」)在现有 `Result.to_dict()`(`infra/result.py:58-65`)
上加三个键:

```json
{"ok": true, "data": {}, "error": null, "artifacts": ["..."], "timing_ms": 12,
 "call_id": "c-20261117-8f3a12", "tool": "logs.dmesg", "board": "local/default"}
```

**没有它会怎样。** 这就是现状:非法设备号在网页接口是 HTTP 400
(`api/log_search_route.py:74-76`),在命令行是 typer 的参数错误
(`cli/common.py:69-70` 把 `ValueError` 转掉),在 MCP 是异常变成的工具错误。
同一个输入三种结果,而且没有一处是 `Result`。

**为什么这么选。**

- **校验只做一次,在 `invoke()` 里用参数模型做。** 没选「让 typer、FastMCP、FastAPI
  各自校验」:我试过,FastMCP 会在函数之前先按自己的模型校验并把越界变成 `ToolError`,
  还会把不认识的参数静悄悄丢掉(附录 A 第 2 条),三处永远对不齐。
- **`invoke()` 不抛异常**(取消除外)。工具函数里漏出来的异常变成 `INTERNAL_ERROR`,
  记完整堆栈到日志。接入面因此不需要各写一套 `try`。
- **返回结构里带 `board`。** 省略 `device` 时调用方要能看到到底打到了哪块板子。
- **返回结构里带 `call_id`。** 界面 v4 的定案是「数据写 hub 输出编号,AI 不抄数据;
  证据引用编号加行号」。编号要从工具调用这一层发出来,P2 先把它发出来并写进审计
  事件;按编号取回输出的存储留到界面第 5 批(视图框架)一起设计,见 10.2。

**什么时候用到。** 每一次工具调用。

**机制。** 顺序和每一步失败时的错误码:

| 步 | 做什么 | 失败 |
|---|---|---|
| 1 | 按名字找工具;旧名字换成新名字并记下来 | `TOOL_NOT_FOUND` |
| 2 | 取出 `device`、`confirm`;空白 `device` 当没给 | — |
| 3 | 参数模型校验;某个字段标了 `Opt(error=…)` 就用那个码 | `INVALID_ARGUMENT`,`details.fields` 列出每个字段的问题 |
| 4 | 解析板子地址(`target` 是 `none` 时跳过) | `BOARD_NOT_FOUND`、`BOARD_NOT_SELECTED`、`BOARD_ADDRESS_AMBIGUOUS`、`INVALID_DEVICE_SERIAL` |
| 5 | 板子类型在 `kinds` 里 | `TOOL_NOT_APPLICABLE`,`details` 带板子类型和工具支持的类型 |
| 6 | `requires` 里每个能力板子都有 | `CAPABILITY_MISSING`,`details.missing` 列出缺的,`details.links` 列出板子有哪些口 |
| 7 | `rt.policy.check(caller, spec, board, params)` | `PERMISSION_DENIED`;结果是「要先问」且没带 `confirm` 时 `details.behavior = "ask"` |
| 8 | 持续输出的工具:接入面不能叫停(没给 `stop`)而 `duration` 为空 → 填 `default_seconds`;超过 `max_seconds` → `INVALID_ARGUMENT` | |
| 9 | `exclusive` 的工具拿板子锁 | — |
| 10 | 审计:开始 → 运行 → 结束 | 工具抛异常 → `INTERNAL_ERROR` |

- 步骤 3 的 `Opt(error="INVALID_DURATION")` 是为了让已有的错误码不变:文档
  (`docs/errors.md`)和现有测试都写着时长越界是 `INVALID_DURATION`。
- 步骤 7 的默认策略沿用 `docs/permissions.md` 2026-10-23 的决定:`read` 和 `board`
  一律放行,`bench` 对本机单用户放行(台架侧仍只执行自己操作表里的操作)。现在
  `infra/permissions.py:32-38` 的 `default_check` 就是恒放行,行为不变;变的是检查从
  「旧工具函数里各自调 `transport.check_permissions`」(`capabilities/` 下 11 处)挪到了调用前一处。
  `PermissionPolicy` 和 `AuditSink` 是框架设计 5.9 的两个接口,团队版换实现。
- 步骤 10 的审计事件发到 `rt.events`(就是现有的 `infra/event_bus.py`,它本来就落盘到
  `events.jsonl`),`source="tool"`,`kind` 为 `tool.call` 和 `tool.result`。

### 4.4 错误码怎么登记和核对

**是什么。** 三层:

1. 框架自己的码加进 `infra/errors.py`:`INVALID_ARGUMENT`、`TOOL_NOT_FOUND`、
   `TOOL_NOT_APPLICABLE`、`CAPABILITY_MISSING`、`BOARD_NOT_FOUND`、`BOARD_NOT_SELECTED`、
   `BOARD_ADDRESS_AMBIGUOUS`、`BOARD_DESC_INVALID`、`BOARD_LINK_UNAVAILABLE`、
   `INTERNAL_ERROR`。这十个现在都不在 `ERROR_CODES` 里(十个逐个查过,附录 A 第 6 条)。`PERMISSION_DENIED`、`INVALID_DEVICE_SERIAL`、`INVALID_DURATION`、
   `INVALID_SOURCE` 已有,直接用。
2. 工具在 `errors=[…]` 里列出自己会返回的码,登记表创建时核对都已登记(4.1 检查 3)。
3. 一个测试扫 `core/`、`tools/`、`surfaces/` 的源码,凡是 `fail("X")` 或 `fail(code="X")`
   里写死的码都必须已登记;`tools/` 里的还必须出现在所在工具的 `errors` 里。

**没有它会怎样。** 现状是 `fail()` 用到的 40 种写死的码里 20 种没登记(调查结论),
模型查 `dhands_describe_errors` 查不到一半。

**为什么这么选。** 扫描器不用新写:`scripts/gen_errors_doc.py:277` 的
`scan_returned_codes` 已经在扫全部源码、找出「会返回但没登记」的码
(`tests/test_docs_errors.py` 用它保证 `docs/errors.md` 不过期)。新测试对新包调同一个
函数,把「列出来」改成「不许有」。没选「对全仓库一次性要求全部登记」:那 20 个旧码
属于还没迁的旧工具函数,P3 迁一个工具清一批,P2 只保证新代码不欠账。

**什么时候用到。** 写工具时;CI。

## 5. 三个接入面的生成规则

### 5.1 命名

| | 规则 | `logs.dmesg` 的结果 |
|---|---|---|
| 命令行 | `dhands <组> <动词>`,下划线变连字符 | `dhands logs dmesg` |
| MCP | `dhands_` 加名字,点变下划线 | `dhands_logs_dmesg` |
| 网页接口 | `POST /api/tools/<名字>`;持续输出的另有 `WS /api/tools/<名字>/stream` | `POST /api/tools/logs.dmesg` |
| 目录接口 | `GET /api/tools`(`?device=` 时带上每个工具对这块板子能不能用、为什么不能);`dhands tools [--device]`;MCP 的 `dhands_describe` 输出里加 `tools` 一节 | |

这是框架设计 5.4 的原样。一律 `POST` 是因为请求体就是那份扁平 JSON,和 MCP 参数同形。

「对这块板子不出现」在三处的含义:

- 网页:`GET /api/tools?device=…` 给出过滤后的列表,界面只画能用的。
- 命令行:命令都在(命令行启动时不知道要对哪块板子);`dhands tools --device X`
  标出哪些能用;调了不能用的,得到 `CAPABILITY_MISSING` 或 `TOOL_NOT_APPLICABLE`。
- MCP:工具列表是整个会话固定的,而一个会话可以面对多块板子,所以全部列出;
  调了不能用的同样得到上面两个码,`details` 里写清缺什么。模型据此换工具或换口。

### 5.2 参数映射

| 参数模型里的字段 | 命令行 | MCP 和网页接口 |
|---|---|---|
| 标了 `Arg()` | 位置参数 | 普通属性 |
| 标量(`int`、`str`、`Path`、`Literal`) | `--字段名`;`Opt(short=…)` 给短选项;`Literal` 的可选值和数值范围写进帮助文字 | 属性;范围和可选值在 JSON Schema 里 |
| `bool` | 默认为假的生成 `--x`,默认为真的生成 `--no-x` | boolean |
| `list[T]` | 选项可重复写 | array |
| 标了 `Only("cli")` | 有 | 没有这个属性 |
| 保留:板子地址 | `--device` / `-d` | `device` |
| 保留:确认 | `--yes` / `-y` | `confirm` |
| 命令行独有 | `--json`(全局位置和命令后面都接受);持续输出的工具有 `--follow` / `-f` | |

- **`-d` 永远是 `--device`。** 现在 `-d` 在 47 个命令里是设备,在 `logcat`、`dmesg`、
  `timeline capture`、`serial capture` 里是时长,在 `app install` 里是降级安装
  (调查明细 `cli_device_flags.txt`)。新命令里 `-d -y -f -h` 是保留的,工具字段占用
  会在登记时报错。旧命令名 `dhands dmesg -d 5` 通过 `CliAlias(short={"duration": "-d"})`
  保持原样。
- **命令行不自己转类型。** 生成的选项除了开关和可重复选项外一律按字符串收,交给
  参数模型转。这样 `--duration abc` 和 `--duration 0` 得到的是同一种返回结构,
  `--json` 下三个接入面输出相同。可选值和范围由生成器从 JSON Schema 写进 `--help`。
- **`Only("cli")` 是登记在案的差异。** `output`(产物另存到指定路径)现在只有命令行有;
  给网页接口加上等于允许网页请求让 hub 往任意路径写文件,P2 不放开。差异写在声明里,
  `dhands tools` 和 `GET /api/tools` 都能看到,而不是像现在这样靠读三份代码才知道。

### 5.3 结果、错误、产物

一张表管三处(`surfaces/common.py`):

| 情况 | 命令行退出码 | 网页接口 | MCP |
|---|---|---|---|
| `ok: true` | 0 | 200 加返回结构 | 返回结构(结构化内容加同内容的文字) |
| `ok: false`,类别是 `input` | 2 | 200 加返回结构 | 返回结构 |
| `ok: false`,其它类别 | 1 | 200 加返回结构 | 返回结构 |
| `INTERNAL_ERROR` | 1 | 500 加返回结构 | 返回结构 |
| 旧路径(迁移前就有的网页接口路由)的输入错误 | — | 保持原来的 400 / 422 | — |
| 被中断(Ctrl-C) | 130;跟随模式下用户主动停止算正常结束,按最终结果给 0 或 1 | 连接断开 | 由 MCP 库取消 |
| 工具自定(`CliHooks.exit_code`) | 例如 `exec.run` 透传板上命令的退出码 | — | — |

- **网页接口对工具层的失败一律 200。** 依据是现有约定:设备侧失败必须是返回结构,
  不是 HTTP 错误(`infra/result.py:152-157` 的说明)。我把输入错误也并进来,理由是
  前端和 MCP 只需要处理一种形状。已定(10.2 第 1 条):新接口 `POST /api/tools/<名>`
  输入错误也是 200;旧路径保持原来的 400 / 422,前端不用改。
- **MCP 不把 `ok: false` 标成协议层错误。** 现有 36 个工具都这么做,服务说明里也是这么
  告诉模型的(`mcp/server.py:48`)。迁移期间同一个服务里新旧工具并存,两种做法混着
  会让模型无所适从。
- **产物(`artifacts`)三处都在返回结构里**,是主程序所在电脑上的绝对路径。命令行的
  普通模式照旧在结果后面列出路径(`cli/common.py:86-89`)。网页接口不再丢这个键。
- **命令行的普通模式怎么显示**:默认用现有的通用打印(`cli/common.py:170-184`)。
  工具可以给 `CliHooks(render=…, exit_code=…)`。`exec.run` 用它保留现在的行为:
  标准输出原样写出、不加标签、不折行、退出码透传(`cli/common.py:111-161` 的那套,
  有专门的测试)。

### 5.4 三个生成器各自怎么做

**是什么。** 每个生成器一个函数,输入是运行时(或拿到运行时的办法),输出是可以挂到
现有入口上的对象。

```python
# surfaces/cli_gen.py
def build_commands(
    registry: ToolRegistry,
    get_runtime: Callable[[typer.Context], Runtime],
) -> dict[str, typer.Typer]: ...      # group name -> typer app
def argv_for(spec: ToolSpec, raw: Mapping[str, Any]) -> list[str]: ...
    # the command line equivalent to a raw JSON call (tests, audit view)

# surfaces/mcp_gen.py
def build_tools(rt: Runtime) -> list["mcp.server.fastmcp.tools.Tool"]: ...
def alias_map(registry: ToolRegistry) -> dict[str, str]: ...

# surfaces/api_gen.py
def build_router() -> fastapi.APIRouter: ...   # reads request.app.state.runtime
```

**为什么这么选(三种做法的可行性见附录 A 第 1、3、5 条)。**

- **命令行**:给 typer 一个合成了签名的函数(`__signature__` 加 `__annotations__`),
  typer 0.24 照常生成选项和帮助。没选「绕过 typer 直接用 click」:现有 93 个命令都是
  typer,迁移期间新旧命令要挂在同一棵树上。
- **MCP**:继承 FastMCP 的 `Tool` 类,覆盖 `run()`,拿到原始参数直接交给 `invoke()`;
  `parameters` 直接填参数模型的 JSON Schema;用 `FastMCP(…, tools=[…])` 把生成的工具
  和旧的 `register_all(mcp)` 放进同一个服务。没选「合成一个带全部参数的函数交给
  `add_tool`」:FastMCP 会先校验再调用,越界变成 `ToolError`,未知参数被丢弃,
  和另外两处不一致。没选「换成 MCP 的底层 Server」:旧的 32 个手写工具还要在同一个
  服务里,P3 迁完后再考虑。这一块对 FastMCP 内部的依赖集中在 `mcp_gen.py` 一个文件,
  风险见 10.1。
- **网页接口**:路由函数收原始 JSON(`dict`),交给 `invoke()`;接口文档用
  `openapi_extra` 填参数模型的 JSON Schema,返回类型从 `ToolSpec.result` 生成,前端类型
  照旧由接口描述生成。没选「把参数模型直接写成路由参数」:FastAPI 会先校验并返回 422,
  要改它就得装全局异常处理器,那会改变其余旧路由(现在共 84 个)的行为。
- **进度**:MCP 一侧,`invoke()` 的 `progress` 回调接到 `Context.report_progress`。
  调查报告说 MCP「无 progress、无 Context」,准确地说是现有代码没用;FastMCP 1.27 有
  这个接口(`mcp/server/fastmcp/server.py:1162`),客户端没给进度令牌时它什么都不做。
  内嵌模型经 `mcp/executor.py:73` 调工具时不在请求里,取上下文会抛 `ValueError`,
  生成器要接住(我踩到了,附录 A 第 3 条)。

**入口接线**(各改一处):

| 入口 | 改动 |
|---|---|
| `cli/main.py:69-99` 的回调 | 把运行时的创建函数放进 `ctx.obj["runtime"]`(第一次取时才创建,`--help` 不付代价);文件末尾把 `build_commands()` 的结果 `add_typer` 进去 |
| `mcp/server.py:32` `create_server(runtime=None)` | `FastMCP("devicehands", tools=build_tools(rt))`,再 `register_all(mcp)`;覆盖 `call_tool`,旧名字先查 `alias_map` |
| `api/server.py:165` `create_app(runtime=None)` | `app.state.runtime = …`;`include_router(build_router())`;生命周期结束时 `await runtime.aclose()` |
| `api/tools_route.py:127` | `GET /tools` 的列表 = 旧的收集结果 + 登记表里的工具,类别取名字的第一段 |

### 5.5 新旧名字并存

**是什么。** 工具迁到登记表时,手写的三份包装删掉,旧名字由生成器按声明里的
`aliases` 接到同一个工具上。框架设计 5.12 规定旧名字至少保留一个小版本、调用时提示
新名字。

| 接入面 | 旧名字怎么保留 | 怎么提示 |
|---|---|---|
| 命令行 | 生成一个隐藏命令(`--help` 的列表里不出现),选项按 `CliAlias` 里的覆盖 | 标准错误输出一行:`dhands dmesg is now: dhands logs dmesg`;`--json` 的标准输出不受影响 |
| MCP | **不出现在工具列表里**,但调用旧名字仍然成功 | 返回结构里加 `"deprecated": {"use": "dhands_logs_dmesg"}` |
| 网页接口 | 旧路径照常登记;`HttpAlias` 写明方法、路径、参数在查询串还是请求体、哪些参数改了名 | 响应头 `Deprecation: true` 和 `Link: </api/tools/…>; rel="successor-version"` |

**没有它会怎样。** 试点一迁,前端(`web/src/lib/api.ts:1331,1362`、
`web/src/features/inspect/useLogcatStream.ts:77`)、测试里写死的工具名(115 行)和路径
(328 行)、用户脚本全部要同一天改。

**为什么这么选。**

- MCP 的旧名字不列出来:工具列表每次请求都发给模型,36 个工具已经约 6.4k token,
  CPU 上的模型冷读要 45 秒(`mcp/executor.py:51-52` 的说明)。新旧各列一遍等于把这个
  代价翻倍。模型每次会话都重新读列表,它不需要旧名字;需要旧名字的是按名字直接调用的
  脚本和测试,对它们来说「能调通」就够了。
- 网页接口的旧路径返回的是新的返回结构。它是旧形状的超集(多了 `artifacts`、
  `error: null`、`call_id`、`tool`、`board`),前端按 `ok / data / error` 读不受影响
  (推断:我没有逐个检查前端有没有对响应做全等比较,S7 要查)。
- `/logcat/stream` 这个 WebSocket 旧路径的协议和生成的流式协议只差几个字段名
  (见 6.3),用一个薄的转换保留,不重写前端。

**什么时候用到。** 每迁一个工具时在声明里写一次;旧名字删除时删一行。

## 6. 持续输出的工具

### 6.1 一种声明,两种跑法

**是什么。** 持续输出的工具和一次性工具是同一个签名 `(ctx, params) -> Result`,
多一项声明 `stream=Stream(event=…, default_seconds=…, max_seconds=…)`。函数体里用
`ctx.emit(event)` 往外送数据,用 `ctx.until_stopped(…)` 包住数据源,结束时照常返回
`Result`(摘要加产物)。

它有两种跑法,由接入面决定,工具自己不知道也不用知道:

- **跟随**:接入面给了事件接收方和叫停手段。数据实时送出去,直到被叫停或到时。
- **限时**:接入面两样都没给。事件没人收(`emit` 什么都不做),数据只进产物文件,
  到时后返回摘要。

```
  tool:   async for chunk in ctx.until_stopped(src, seconds=p.duration):
              write chunk to the artifact;  await ctx.emit(Chunk(chunk))
          return ok(summary, artifacts=[path])

  follow                                 bounded
  (CLI --follow, WebSocket)              (MCP, POST, CLI without --follow)
    sink = print / ws.send                 sink = None
    stop = Ctrl-C / {"type":"close"}       stop = None, duration filled in
    result = summary, shown at the end     result = summary + artifacts[0]
```

**没有它会怎样。** 现状是两套东西:53 个旧工具函数里没有一个对外持续输出(调查结论),
日志类的三个是「按时长收集到文件」(`capabilities/logging.py:155,251,476`),真正的实时流只在网页的 WebSocket 路由里,直接调
`transport.stream_read`(`api/logcat_stream_route.py:239`),不经过工具层,所以不查权限、
不记审计、命令行和 MCP 也用不上。logcat 的跟随只有网页有。

**为什么这么选。**

- 「按时长收集」本来就是「跟随」加一个时限加一个文件。把它们写成一个工具,
  `dhands_dmesg(duration=10)` 这个现有的 MCP 用法就是限时跑法,不用为没有流式能力的
  MCP 另写一个版本。
- 没选「工具写成异步生成器,`yield` 事件」:生成器不能返回最终结果,摘要得另找通道;
  而且一次性工具和持续输出工具会变成两种签名,生成器、测试、文档都要分叉。
- 没选「跟随时不落盘」(现在网页的 logcat 流不落盘):落了盘,实时看过的东西事后还能
  按调用编号引用,这是界面 v4「证据引用编号加行号」的前提。

**什么时候用到。** 抓一段日志(限时);盯着日志复现问题(跟随);以后的烧录进度、
性能采样、文件传输进度都是这个形状。

### 6.2 事件类型

```python
class StreamEvent(BaseModel):
    kind: str
class Chunk(StreamEvent):          # raw bytes; chunks are not lines
    kind: Literal["bytes"] = "bytes"
    data: bytes
class Progress(StreamEvent):
    kind: Literal["progress"] = "progress"
    done: float; total: float | None = None; message: str = ""
# tools may define their own pydantic events; `Stream(event=…)` lists them
```

`Chunk` 不按行切:串口来的数据块可能是半行或好几行(`capabilities/logging.py:808-810`
的说明),按行统计由工具里的 `LineStats` 重新拼。

### 6.3 三个接入面怎么呈现

| | 跟随 | 限时 |
|---|---|---|
| 命令行 | `--follow`:`Chunk` 原样写到标准输出;其它事件写到标准错误;`--json` 时每个事件一行 JSON(字节用 base64);结束时打印摘要 | 不带 `--follow`:安静地等,结束时打印摘要和产物路径(现在 `dhands dmesg` 的行为) |
| MCP | 不支持 | `duration` 为空时填默认值;每秒报一次进度(客户端给了进度令牌才发);返回摘要加产物路径 |
| 网页接口 | `WS /api/tools/<名字>/stream` | `POST /api/tools/<名字>` |

WebSocket 的帧(在现有 `/logcat/stream` 协议上推广,`api/logcat_stream_route.py:110-222`):

```
C -> S   first frame, JSON: the same flat object as the POST body
S -> C   {"type":"ready","v":"1","tool":"logs.logcat",
          "board":"local/default","call_id":"..."}
S -> C   binary frame              = one Chunk
S -> C   {"type":"event", ...}     = any other event
C -> S   {"type":"close"}          = stop
S -> C   {"type":"closed","reason":"ended|stopped|limit|error",
          "result":{ ...the unified result... }}
```

调用前检查没过时不发 `ready`,直接发 `closed`,`result.ok` 为假。旧路径 `/logcat/stream`
保留:它把首帧里的旧字段原样当参数,`ready` 里补上旧字段(`device`、`transport`、
`filter`、`buffers`),`closed.reason` 按错误码映射回旧值(`bad_device`、`bad_buffers`、
`bad_filter`、`init_failed`、`stream_error`)。这个转换约 60 行,放在
`api/logcat_stream_route.py`,界面 v4 换掉旧页面时删。

没选 SSE:它不能传二进制帧(日志原始字节要转码),也没有客户端到服务端的通道(叫停只能
靠断开)。现有 11 个流式路由全是 WebSocket,前端已有成套的钩子。

### 6.4 取消、时长上限、权限

- **权限和能力检查在 `invoke()` 的调用前检查里**,跟随和限时走同一段。现在
  WebSocket 路由绕过工具层的问题就此消失。
- **叫停是协作式的**:接入面置位 `stop`,`until_stopped` 结束迭代,工具走完函数体
  返回摘要。命令行的 Ctrl-C 用 `signal.signal(SIGINT, …)` 置位(`loop.add_signal_handler`
  在 Windows 上不可用,不能用);第二次 Ctrl-C 才是硬中断,退出码 130。
- **硬取消**(MCP 客户端取消请求、WebSocket 断开而工具没在 `until_stopped` 里):
  `CancelledError` 照常向上传,数据源在 `finally` 里回收,没有返回结构。
- **时长上限**:`max_seconds` 写在声明里(日志类 3600,和现有上限一致,
  `capabilities/logging.py:189,489`)。能叫停的接入面允许 `duration` 为空(一直跟);
  不能叫停的必须有限。
- **跟随时产物的大小**:限时跑法天然有界。不限时的跟随 P2 定为到 256 MiB 就结束,
  `reason` 为 `limit`。串口控制台开一整天会碰到这个上限,但 P2 的试点不含串口控制台;
  P3 迁它时再定是滚动还是不落盘,见 10.2。

## 7. 运行时对象

### 7.1 里面有什么

**是什么。**

```python
# core/runtime.py
class Runtime:
    settings: ActiveSettings          # config.toml + profile, read once
    env: Mapping[str, str]            # DHANDS_* variables, read once at creation
    registry: ToolRegistry
    kinds: dict[str, DeviceKind]
    boards: BoardTable                # descriptions, default board, addresses, links
    events: EventBroadcaster          # the one event bus
    policy: PermissionPolicy
    audit: AuditSink
    # second stage, after P4 merges:
    # benches: BenchTable             # bench connections, forwarders, flash service

    @classmethod
    def create(cls, *, profile: str | None = None, env: Mapping[str, str] | None = None,
               tool_modules: Iterable[str] | None = None,
               board_dir: Path | None = None) -> "Runtime": ...
    def spawn(self, coro: Coroutine[Any, Any, Any], *,
              name: str) -> asyncio.Task[Any]: ...
        # background tasks the runtime keeps a reference to and cancels on close
    async def aclose(self) -> None: ...
```

**没有它会怎样。** 框架设计 5.8 已写。具体到 P2:没有运行时,生成的三个接入面就得
各自去找配置、登记表、板子表,最顺手的做法是做成模块级变量——也就是再添三组全局状态。

**为什么这么选。**

- **显式传递,不提供「取当前运行时」的全局函数。** 没选 `ContextVar` 或模块级
  `current()`:那只是把 18 组全局状态换成 1 组,测试里两个运行时并存(模拟两台台架)
  还是做不到。
- **运行时不传进工具**(工具只拿 `ToolContext`),也不传进传输和 `infra`(它们收到的是
  具体要用的东西,例如 `settings`)。下层不认识运行时,导入方向不会反。

**什么时候用到。** 进程启动时创建一次;测试里每个用例创建一个。

**机制:谁创建、怎么传。** 工具模块的清单由入口传给 `Runtime.create(tool_modules=…)`
(入口导入 `devicehands.tools.BUILTIN_MODULES`)。`core/` 自己不知道有哪些工具,
不存在「下层在运行时偷偷导入上层」这种检查工具看不见的反向依赖。


| 入口 | 创建 | 往下传 |
|---|---|---|
| `dhands`(`cli/main.py`) | 回调里放一个惰性创建函数到 `ctx.obj` | 生成的命令从 `ctx.obj` 取;旧命令的 `get_transport(ctx, …)`(`cli/common.py:53`)也从这里取 `settings` |
| `dhands-mcp`(`mcp/server.py`) | `create_server(runtime=None)`,为空时自己创建 | 闭包进每个生成的工具 |
| `dhands-hub`(`api/server.py`) | `create_app(runtime=None)`,为空时自己创建 | `app.state.runtime`;生命周期结束时关闭 |

`api/server.py:221` 的 `app = create_app()` 在导入模块时就建应用,有了运行时以后等于
导入时就读配置和板子描述。改成模块级 `__getattr__`,只有取 `app` 属性时才创建。
`uvicorn devicehands.api.server:app` 这种启动方式是否照常工作,S7 要在测试里验证
(推断:uvicorn 用 `getattr` 取属性,模块级 `__getattr__` 会被触发)。

### 7.2 P2 迁哪些全局状态

调查列了 18 组(`p2-survey/globals.md`)。P2 能动的、和骨架直接相关的先迁;属于还没迁的
旧工具或旧路由的,跟着那个工具在 P3 迁;属于 `remote/` 的等 P4。

| # | 全局状态 | 什么时候 | 怎么迁 |
|---|---|---|---|
| 1 | `transport/factory.py:20` `_cached_settings` | P2(新路径);P3 删 | 新路径用 `runtime.settings`。`build_transport` 加可选参数 `settings`,不给时走旧缓存。62 个旧调用点 P2 不改,所以这个变量 P2 还在,只是新代码不碰它 |
| 2 | `infra/events.py:62` `_bus` | **P2 删** | 整个模块删掉。它没有订阅方(`grep -rn "\.subscribe(" src` 的 3 处命中都是另一个总线和采样器),两处发布(`capabilities/logging.py:843`、`capabilities/timeline.py:459`)一并删,行为不变。事件总线从此只有一个 |
| 3 | `infra/event_bus.py:170` `_BUS` | P2 接管;P3 删访问函数 | `runtime.events` 持有实例。hub 进程创建运行时时把它设成 `get_bus()` 返回的那个(加一个 `set_bus()`),旧代码里的 `get_bus()` 拿到同一个对象 |
| 17 | `api/server.py:221` 模块级 `app` | P2 | 见 7.1 |
| 新 | 登记表、板子表、口的缓存、类型表 | P2 | 生来就在运行时上 |
| 4、10、12、13、14 | 指标存储、指标采样器表、文件锁、后台任务集合、时间轴缓存 | P3 | 跟着各自的工具和路由迁;13 用 `runtime.spawn()`,12 用板子锁 |
| 11 | 模型后端探测缓存 | P3 之后 | 模型后端不在 P2、P3 范围 |
| 18 | `.env` 写进进程环境 | P3 | 运行时的 `env` 快照取代各处 `os.environ.get` |
| 5、6、7 | 台架连接表、两个转发器、烧录服务 | **第二阶段(P4 合并后)** | `runtime.benches`;`tests/conftest.py:126` 的自动重置随之删掉 |
| 8、9 | `infra/adb_endpoint.py` 的三个变量 | 第二阶段 | 它们读的是 5、6;迁完后 `.importlinter` 里 3 条 infra → remote 的白名单可以清掉 |
| 15、16 | 只警告一次的标志、给第三方库打补丁时存的原方法 | 不迁 | 15 在 `api/agent_route.py`(P4 的文件);16 本来就是进程级的事 |

**怎么保证不再长出新的。** 两个测试:

- `tests/test_no_new_globals.py`:用调查用过的办法(扫 `global` 语句;扫模块顶层值为
  空容器或 `None` 的赋值)扫 `core/`、`tools/`、`surfaces/`、`sdk/`,要求 0 个。
- 同一个测试对其余包维护一份清单(就是上表),清单外出现新的就失败;清单里的条目
  消失了而清单没删也失败——和 `.importlinter` 白名单是同一个规矩
  (`unmatched_ignore_imports_alerting = error`)。

### 7.3 测试怎么用

`tests/support/fakes.py` 提供(调查发现现在没有共用的假传输,10 个文件各写各的):

```python
class FakeExec(Exec):      # scripted: {"getprop ro.x": ExecResult(...)}; records calls
class FakeLogs(Logs):      # sources and the chunks each one yields
class FakeConsole(Console)
class FakeFiles(Files)
class FakeLink(Link):      # FakeLink("usb", caps=[FakeExec(...), FakeFiles()])

def make_runtime(tmp_path, *, boards=None, tools=None, env=None,
                 policy=None) -> Runtime: ...
    # boards: list of (id, kind, [links]); tools: module names or ToolSpec objects
```

`tests/conftest.py` 加一个叫 `runtime` 的 pytest fixture(默认一块 `android` 板子,带假的 `usb` 口)。
工具测试直接 `await invoke(runtime, "logs.dmesg", {...}, caller=TEST)`;接入面测试把同一个
运行时交给三个生成器。不需要 monkeypatch 任何模块变量。

## 8. 试点和两条验收怎么证明

### 8.1 试点选哪些

**是什么。** 四个工具,外加框架自带的一个 `board.list`(列出板子、口、类型、能力;
它是新东西,不算迁移)。

| 组 | 工具 | 顶掉的旧入口 |
|---|---|---|
| 一次性 | `exec.run` | 命令行 `shell`(`cli/main.py:224`);MCP `dhands_shell`;网页接口现在没有,生成后多出 `POST /api/tools/exec.run` |
| 一次性 | `logs.search` | 命令行 `log search`(`cli/main.py:341`);MCP `dhands_log_search`;`GET /api/log/search` |
| 持续输出 | `logs.dmesg` | 命令行 `dmesg`;MCP `dhands_dmesg`;`POST /api/log/dmesg` |
| 持续输出 | `logs.logcat` | 命令行 `logcat`;MCP `dhands_logcat`;`WS /logcat/stream` |

**为什么是这四个。** 试点要让骨架支持的每一种用法至少被真实工具用到一次,同时尽量少:

| 骨架要支持的用法 | 哪个试点用到 |
|---|---|
| 要板子、要一种能力 | `exec.run`(`Exec`) |
| 地址带口:`板子/console` 上执行 = 现在的串口执行 | `exec.run` |
| 命令行自定义显示和退出码 | `exec.run` |
| 接入面覆盖不全,生成后自动补齐 | `exec.run`(现在没有网页接口) |
| 板子可给可不给;不需要任何能力 | `logs.search` |
| 旧路径是 GET、参数在查询串、有改名(`max` → `max_matches`) | `logs.search` |
| 持续输出、跟随和限时两种跑法、产物 | `logs.dmesg`、`logs.logcat` |
| 按设备类型过滤(logcat 只有安卓) | `logs.logcat` |
| 能力由类型从多个口合成 | `logs.dmesg`(`Logs` 的 `kernel` 来源) |
| 旧 WebSocket 路径保留 | `logs.logcat` |

选日志这一组还有一个原因:调查记下的三处不一致几乎都在它身上(时长越界报法不同、
非法设备号报法不同、网页接口丢产物、`output` 只有命令行有、`-d` 的含义、tags 和
buffers 的重复校验)。拿它做试点,「行为一致」有现成的反例可以逐条对照,而不是只证明
一个本来就没问题的简单工具。

没选的:

- `power.reboot`:它要 `Reset` 加「adb 不通退到串口」加等待开机,牵涉三处调用点,
  而且重启真板子的回归代价高。机制在 2.4 备好,工具 P3 迁。
- `logs.console`(串口抓取):它带重连和台架拒绝原因的处理
  (`capabilities/logging.py:251-376`),双向模式还牵涉 `Console`。P3 迁。
- `dhands_uart_shell`、`dhands serial shell`:有了带口的地址后它们就是 `exec.run` 的
  旧名字,但 `cli/serial_cli.py` 里其它命令还没迁,P2 不动这个文件的命令结构。

如果工期紧,`logs.search` 可以砍(它验证的两种用法 P3 一开始就会碰到);另外三个不能砍。

### 8.2 证明「三个接入面行为一致」

**是什么。** `tests/contract/` 下三个测试文件。

**① 同一份输入喂三处,比返回结构**(`test_three_surfaces.py`)。一张用例表,每行是
`(工具, 一份 JSON 参数, 用哪块假板子, 期望的返回结构)`。测试对每一行:

- 命令行:`argv_for(spec, raw) + ["--json"]` 交给 `CliRunner`,解析标准输出;
- MCP:`server.call_tool(名字, raw)` 取结构化内容;
- 网页接口:`TestClient(app).post("/api/tools/名字", json=raw)`;

去掉 `timing_ms` 和 `call_id` 后,检查三个结果彼此相等并等于期望;再按 5.3 的表检查
退出码和 HTTP 状态。三处用的是同一个运行时(同一块假板子)。

用例至少覆盖下面这些,每一类对应调查里的一条不一致或一条新规则:

| 用例 | 期望 |
|---|---|
| 合法调用 | `ok: true`,`data` 相同 |
| 时长越界(0、3601) | 三处都是 `INVALID_DURATION` |
| 类型不对(`duration: "abc"`) | 三处都是 `INVALID_ARGUMENT` |
| 多了不认识的参数 | 三处都是 `INVALID_ARGUMENT` |
| 非法设备号(`../x`) | 三处都是 `INVALID_DEVICE_SERIAL` |
| 不存在的板子 | `BOARD_NOT_FOUND` |
| 空白 `device` | 等同没给 |
| 全数字序列号写成 JSON 数字 | 被当成字符串 |
| 板子没有需要的能力(只有串口的 `mcu` 板子上调 `exec.run`) | `CAPABILITY_MISSING` |
| 类型不适用(`linux` 板子上调 `logs.logcat`) | `TOOL_NOT_APPLICABLE` |
| 工具自己返回的失败 | 错误码原样 |
| 有产物 | 三处的 `artifacts` 相同且文件存在 |
| 工具抛异常(样例工具) | `INTERNAL_ERROR`;网页接口 500 |

**② 生成得全不全**(`test_generated_complete.py`)。遍历登记表里的每个工具(不只是
试点,以后加的工具自动被覆盖):三处的名字都存在;MCP 输入描述的属性名集合 = 接口文档
请求体的属性名集合 = 命令行选项集合(扣掉 `Only("cli")` 和命令行独有的);MCP 的
JSON Schema 和接口文档里的相等;每个旧名字调到的是同一个 `ToolSpec`。

**③ 持续输出的一致**(`test_stream_surfaces.py`)。假的日志源吐固定的字节序列。
命令行 `--follow --duration 1` 的标准输出、WebSocket 收到的二进制帧拼起来、MCP 限时
调用后产物文件的内容,三者字节相同;三处最终的摘要相同。再测叫停:WebSocket 发
`{"type":"close"}` 后收到 `closed` 且 `result.ok` 为真,假的日志源的 `aclose` 被调用过
(证明下面的进程会被回收)。

**这个检查自己会不会是摆设。** 每个文件带反向用例:

- 把 `cli_gen` 生成的某个选项名故意改错(monkeypatch),② 必须失败;
- 样例工具在某个接入面上被包一层改掉错误码,① 必须失败;
- 假日志源在跟随时少吐一块,③ 必须失败。

**证明到什么程度。** ① ③ 对四个试点和三个样例工具成立;② 对登记表里的一切成立。
它证明的是「同一个运行时、同一份输入下三处返回相同」,不证明真板子上的行为(那是
P3 的验收:一块安卓板和一块 Linux 板在三处都能完成五件事)。命令行的普通显示模式
(不带 `--json`)不在比较范围内,它本来就是给人看的另一种呈现。

### 8.3 证明「不再按传输名字判断」

**是什么。** `tests/test_no_transport_name_checks.py`,扫 `src/devicehands/` 下全部
源码的语法树(`transport/` 和 `core/links/` 除外——这两处就是允许认识具体传输的地方),
下面四类一个都不许有:

| 类 | 语法树上的样子 | 现在有几处 |
|---|---|---|
| A 比较名字 | 比较表达式一边是 `.name` 属性或 `getattr(x, "name", …)`,另一边是 `"adb"`、`"ssh"`、`"serial"`、`"hybrid"` 之一(含 `in (…)`) | 8 |
| B 探测成员 | `hasattr(x, "m")` 或 `getattr(x, "m", 默认值)`,m 属于「任一传输类定义的方法或属性名」这个集合(测试里用内省算出来,不手写) | 8 加 4 |
| C 判类型 | `isinstance(x, 某个具体传输类)`;调用以下划线开头的传输方法(`._run(`) | 1 加 2 |
| D 比较配置里的传输名 | 比较表达式一边是 `.primary_transport` | 1(`cli/main.py:150`) |

**为什么要比验收的字面多查三类。** 验收写的是「不再按传输名字做判断」。如果只查 A,
最省事的改法是把 `transport.name == "adb"` 换成 `isinstance(transport, AdbTransport)`
或 `hasattr(transport, "devices")`——判断还在,只是换了写法,下一种口加进来时照样
要改这些地方。B、C、D 堵的就是这条路。这一条是我加的,已定(10.2 第 3 条)。

**这个检查自己会不会是摆设。** 测试文件里带一组「坏代码」字符串(每类至少两种写法:
等号两边有没有空格、单引号双引号、`!=`、`not in`、`getattr` 带不带默认值),扫描函数
对它们必须每条都报;再带一组「好代码」(`provide(t, Console)`、对无关对象的
`hasattr(data, "to_dict")`、比较参数值 `mode == "adb"`),必须一条不报。现有的
`grep` 写法就在这上面吃过亏:`transport\.name(==|!=)` 不带 `\s*` 时是 0 命中。

再加两道:

- `.importlinter` 的第二条禁止规则(1.2):`core/` 里除 `links/` 外不许导入具体传输。
- `tools/` 的禁止规则:新工具根本导入不到传输,想按名字判断也拿不到对象。

给人看的复核命令(P2 完成后应为 0 行;在 main `57edd6b1` 上现在是 8 行,
去掉 `\s*` 的错误写法是 0 行,说明这条 pattern 确实在起作用):

```bash
grep -rn -E "\.name\s*(==|!=|in|not in)\s*[\[(]?\s*['\"](adb|ssh|serial|hybrid)" \
  src/devicehands --include=*.py | grep -v -E "^src/devicehands/(transport|core/links)/"
```

## 9. 实施步骤

### 9.1 步骤和依赖

```
 S0 --> S1 --+--> S2 ----------------------> S6 --+
             |                                    |
             +--> S3 --+--> S4a  S4b  S4c --------+--> S7 ==> (P4 merged) ==> S8
                       |                          |
                       +--> S5 -------------------+
```

每一步一个分支、一个 PR,三个系统的 CI 都过才合并。同一时间在做的步骤,文件互不重叠
(下表「只有它能改」一列);一个文件前后被两步改的,后一步等前一步合并。

| 步 | 做什么 | 只有它能改的文件 | 验收 |
|---|---|---|---|
| **S0** 地基 | 建空包;`.importlinter` 换成 1.2 的内容 | `.importlinter`;`src/devicehands/{core,core/links,core/kinds,tools,surfaces,sdk}/__init__.py`;`tests/{support,core,tools,surfaces,contract}/__init__.py` | `lint-imports` 四条规则通过;测试数和基线相同(`docs/dev/test-baseline.md` 最后一行:3427 条,3421 通过) |
| **S1** 契约 | 能力接口;`ToolSpec`、`@tool`、`ToolParams`、登记表和六条登记检查;`ToolContext`、`Caller`、事件类型(只有类型和签名);`Board`、`BoardAddress` 数据类和 `BoardTable` 的接口;`PermissionPolicy`、`AuditSink` 和默认实现;十个新错误码;返回结构对 pydantic 数据的序列化;错误类别到退出码和状态码的表;假能力和样例工具 | `core/caps.py`、`core/registry.py`、`core/context.py`、`core/policy.py`、`core/board.py`(骨架)、`surfaces/common.py`、`sdk/__init__.py`、`infra/errors.py`、`infra/result.py`、`docs/errors.md`、`tests/support/*`、`tests/core/test_registry.py`、`tests/core/test_caps.py` | 六条登记检查各有一个「故意写错必须报错」的用例;`tests/test_docs_errors.py` 通过;`sdk` 里每个名字都能导入 |
| **S2** 设备模型 | 板子描述的模型、读取、校验;默认板子;地址解析;三个口的适配器;`link_for`、`provide`;三种设备类型;`transport/factory.py` 拆出三个构造函数,`build_transport` 加可选的 `settings` 参数 | `core/board.py`、`core/boardfile.py`、`core/links/*`、`core/kinds/*`、`transport/factory.py`、`tests/core/test_board*.py`、`test_links*.py`、`test_kinds*.py`、`tests/transport/test_factory.py` | 3.3 的解析表每一行有用例,两种歧义都报错;同样的环境变量下,默认板子排第一的口和 `build_transport` 选出的传输是同一种(参数化对照 adb / ssh / serial 三种设置);只有串口的 `mcu` 板子合成出 `Console` 和只有 `console` 来源的 `Logs`,没有 `Exec`;坏的描述文件的报错里有文件名和键名;`build_transport` 的 62 个调用点不改也照常工作 |
| **S3** 运行时和调用入口 | `Runtime`;`invoke()` 的十步;`ToolContext` 的实现(`until_stopped`、`emit`、`artifact_path`);`event_bus.set_bus` | `core/runtime.py`、`core/invoke.py`、`core/context.py`(实现)、`infra/event_bus.py`、`tests/conftest.py`(加 `runtime` fixture)、`tests/core/test_invoke.py`、`test_runtime.py`、`test_context.py` | 4.3 表里每一步的失败各有用例;工具抛异常得到 `INTERNAL_ERROR` 而不是异常;`until_stopped` 在叫停和到时两种情况下都调了源的 `aclose`;两个运行时并存时互不可见对方的板子和事件 |
| **S4a** 命令行生成器 | `build_commands`、`argv_for`、旧命令名、`--follow` | `surfaces/cli_gen.py`、`tests/surfaces/test_cli_gen.py` | 用样例工具:5.2 的映射表每一行有用例;保留短选项被工具占用时登记失败;`--help` 里有范围和可选值;Windows 上跟随模式能用 Ctrl-C 停(用置位 `stop` 模拟) |
| **S4b** MCP 生成器 | `build_tools`、`alias_map`、进度 | `surfaces/mcp_gen.py`、`tests/surfaces/test_mcp_gen.py` | 生成的工具和一个手写的 `@mcp.tool()` 在同一个服务里都能列出、都能调;未知参数得到 `INVALID_ARGUMENT`;不在请求里调用(内嵌模型的路径)不抛;一个守卫测试:`Tool.run` 的签名变了就失败并说明要改哪里 |
| **S4c** 网页接口生成器 | `build_router`:POST、WebSocket、`GET /api/tools`、旧路径 | `surfaces/api_gen.py`、`tests/surfaces/test_api_gen.py` | 接口文档里有每个工具的请求体和返回类型;6.3 的帧序列有用例;旧路径带 `Deprecation` 头 |
| **S5** 试点工具 | 三个日志工具和 `exec.run`、`board.list`;把按行统计和行解析(`capabilities/logging.py:746-795`)挪到 `core/logstats.py`;`capabilities/logging.py` 里的两处名字判断、一处 `getattr` 探测、旧事件总线的发布 | `tools/*`、`core/logstats.py`、`capabilities/logging.py`、`tests/tools/*`、`tests/capabilities/test_logging*.py` | 工具测试只用 `invoke()` 加假能力,不 monkeypatch;`LogcatOptions` 的每条校验有用例;旧函数 `collect_dmesg`、`collect_logcat`、`search_logs` 这一步还留着(旧包装还在调),它们的测试照常通过 |
| **S6** 清旧代码里的判断 | 2.5 表里除 `capabilities/logging.py` 和 `api/logcat_stream_route.py` 之外的各条;`capabilities/timeline.py:80,443,459` 往旧事件总线的发布;删 `HybridTransport` 和两个没人读的标志;扫描测试(带一份「待清文件」清单,先放那两个文件) | `capabilities/{adbd,filesync,diagnose,app,breakin,timeline}.py`、`cli/{main,serial_cli,setup_cli}.py`、`api/{devices_route,uart_stream_route,files_route}.py`、`mcp/tools/devices.py`、`transport/{hybrid.py,__init__.py,base.py}`、`infra/registry.py`、对应的测试、`tests/transport/test_hybrid.py`(删)、`tests/test_no_transport_name_checks.py` | 扫描测试通过,反向用例齐全;这些模块原有的测试全部通过(替身改成 `spec=具体传输类` 或假能力);bugreport 在 ssh 连着的安卓板替身上能跑(以前被名字判断挡住) |
| **S7** 集成 | 三个入口接上运行时和生成器;删四个试点的旧包装,旧名字改由声明生成;`/logcat/stream` 的转换;删 `infra/events.py`;`GET /tools` 合并登记表;`tests/contract/*`;`tests/test_no_new_globals.py`;扫描测试的待清清单清空;文档 | `cli/main.py`、`cli/common.py`、`mcp/server.py`、`mcp/tools/{logging,shell}.py`、`api/server.py`、`api/tools_route.py`、`api/log_search_route.py`、`api/logcat_stream_route.py`、`infra/events.py`(删)、`capabilities/logging.py`(删掉三个已被顶替的函数)、`tests/contract/*`、`tests/test_no_new_globals.py`、`docs/architecture.md`、`docs/tool-writing-guide.md`、`docs/llm-integration.md`、`docs/web-api.md`、`docs/dev/test-baseline.md` | 8.2、8.3 的全部测试通过;前端代码一行不改,`web/` 的测试通过;旧命令、旧 MCP 名字、旧路径各有一个用例证明还能用并带提示;`uvicorn devicehands.api.server:app` 这种写法能取到应用(用导入方式验证,不启动服务) |
| **S8** 第二阶段 | P4 合并后:`runtime.benches` 持有台架连接表、两个转发器、烧录服务;`infra/adb_endpoint.py`、`infra/hub_serial.py` 改成收参数;`Flash` 的提供方 | `remote/*`、`api/agent_route.py`、`infra/adb_endpoint.py`、`infra/hub_serial.py`、`tests/conftest.py` | `.importlinter` 里 3 条 infra → remote 的白名单删掉后检查通过;`tests/conftest.py:126` 的自动重置 fixture 删掉后全部测试通过;一个进程里建两个运行时,各连一台假台架 |

### 9.2 怎么并行派活

- S0、S1 一个人顺序做完。S1 是所有人的接口,合并前要评审(由主对话做)。
- S1 合并后:S2、S3 两个人并行。S3 需要板子时用 `tests/support` 里的假板子,不等 S2。
- S3 合并后:S4a、S4b、S4c、S5 四条线并行(S4 的三个文件也可以一个人顺序做)。
  S2 合并后:S6 开始。
- S7 一个人,等前面全部合并。

每份交接说明要写明(项目记忆里记过子 agent 误启动 hub 的事):

1. 只许改上表里这一步的文件;发现要改别的文件,停下来报告,不要顺手改。
2. 不许运行 `dhands-hub`、`dhands`、`devicehands`、`dhands-mcp`、`alb`、`alb-api`、
   `uvicorn`;不许碰本机在用的服务端口。测试只用 `pytest`、`lint-imports`。
3. 每条线用自己的 git worktree 和自己的 `.venv`。
4. 不许往 `.importlinter` 的 `ignore_imports` 加条目;不许往 `uv.lock` 加依赖
   (本方案不需要任何新依赖)。
5. 新代码不出现只在某个系统上有的模块;路径用 `pathlib`;不用 `loop.add_signal_handler`。
6. 交回时附:改了哪些文件、测试数前后对比、三个系统的 CI 结果。

## 10. 风险和没定的事

### 10.1 风险

| 风险 | 往哪个方向坏 | 怎么控制 |
|---|---|---|
| MCP 生成器依赖 FastMCP 的内部约定(`Tool.run` 的签名、`FastMCP(tools=…)`、覆盖 `call_tool`) | 升级 `mcp` 后生成的工具调不通。会当场看见:调用直接报错 | 依赖已限在 `mcp<2`(`pyproject.toml:45`);全部集中在 `mcp_gen.py`;S4b 的守卫测试在签名变化时指出要改哪里 |
| pydantic 的宽松转换和现在 typer、FastMCP 的转换有差别(推断:我没有逐项试,例如字符串转布尔、`"1.0"` 转整数) | 以前能过的输入现在被拒,或者反过来。被拒看得见;被放过看不见 | 对比测试:四个试点的每个参数,拿旧包装接受的写法逐个喂新入口;不一致的逐条定夺 |
| 旧网页路径的错误状态从 400 / 422 变成 200 加 `ok: false` | 前端把失败当成功显示。这是看不见的那种 | S7 逐个查前端对这三个路径的处理(`web/src/lib/api.ts:1331-1370`、`useLogcatStream.ts`);`web/` 现有测试里有这三个路径的用例 |
| 串口上的 `Exec` 声明了但板子不在 shell 提示符 | 工具列表里有 `exec.run`,调用时失败(`BOARD_BOOTING` 等) | 这是运行时状态,不是能力缺失,错误码已有;「对它不出现」只承诺到静态能力这一层,文档里写明 |
| 默认板子类型定为 `android` | Linux 板用户看到一批用不了的安卓工具,调用时得到板子上命令不存在的错误 | 看得见;写一个三行的描述文件或设 `DHANDS_BOARD_KIND` 即可;`dhands doctor` 里提示 |
| 打开失败时传输往数据流里塞一行假数据(`transport/serial.py:696`、`transport/ssh.py:183`) | 产物文件第一行是 `[dhands serial open failed: …]`,摘要显示抓到 1 行 | P2 不改(要动传输和依赖它的串口抓取逻辑);P3 迁 `logs.console` 时改成抛带错误码的异常。P2 统一的是「不支持」这一种:能力不存在就在调用前返回 `CAPABILITY_MISSING`,不再有抛 `NotImplementedError` 和返回假数据这两种表现 |
| `adr069-phase-a` 分支(没合并,还是改名前的目录)里有另一套一板多口的台架板子表 | P3 做一板多口时出现两种板子格式 | P3 开始前定:板子描述是唯一的来源,台架侧的表由它导出。P2 的默认板子不读台架板子表,只用串口转发端口(它本来就跟着 `dhands bench board use` 切换) |
| P4 合并晚 | S8 开不了工;`Flash` 只有接口;一个进程仍只能连一台台架 | 第一阶段的验收不依赖 S8;P3 的烧录工具迁移要等 S8 |
| 试点期间新旧两种写法并存 | 新人照着旧工具函数写新功能 | `docs/tool-writing-guide.md` 在 S7 改成只讲新写法;旧包顶部加一句说明 |
| 生成的命令名变长(`dhands logs dmesg`) | 手敲不方便 | 旧的短名字保留至少一个小版本;要不要长期保留几个短名字见下 |

### 10.2 没定的事(要项目所有者或评审定)

前四条项目所有者 2026-11-17 已定,都按建议执行;第 5 条起仍待定。

| # | 问题 | 我的建议 | 不定会卡住哪一步 |
|---|---|---|---|
| 1 | 网页接口对输入错误返回 200 加返回结构,还是 400 / 422 | **已定**:新接口 200;旧路径保持 400 / 422(5.3) | S1 的状态码表 |
| 2 | MCP 的旧名字在过渡期要不要列在工具列表里 | **已定**:不列,只保证能调(5.5) | S4b |
| 3 | 第二条验收要不要把 `isinstance`、`hasattr`、`getattr` 探测和私有方法调用一起算 | **已定**:算(8.3) | S6 的工作量 |
| 4 | 没有描述文件时默认板子的类型 | **已定**:`android`,可用 `DHANDS_BOARD_KIND` 改,`dhands doctor` 提示(3.2) | S2 |
| 5 | 板子描述要不要分「厂商发的型号模板」和「本地的接线」(哪台台架、哪个串口号) | P2 先一个文件;P6 加 `extends = "<模板>"` | 不卡 P2 |
| 6 | 有描述的板子,工作区目录用板子 id 还是 adb 序列号。同一块实物两种地址写法会落到两个目录 | 用板子 id;`logs.search` 给了板子时两个目录都搜 | S2、S5 |
| 7 | 不限时的跟随,产物怎么限制大小 | P2 先 256 MiB 截止;P3 迁串口控制台时定滚动还是不落盘 | 不卡 P2 |
| 8 | 按 `call_id` 取回工具输出的存储(界面 v4 的证据引用要用) | P2 只发编号和审计事件;存储和界面第 5 批一起设计 | 不卡 P2 |
| 9 | `risk="bench"` 的工具默认要不要先确认 | 本机单用户默认放行(现状);P2 没有这类试点,P3 迁台架类工具前定 | 不卡 P2 |
| 10 | 常用命令要不要长期保留短名字(`dhands shell`、`dhands logcat`) | 保留为正式的短名字而不是「过期的旧名字」,数量控制在个位数 | S7 的提示文字 |
| 11 | `ssh` 这个口类型以后和框架设计第 8 节的 `usb-net` 是什么关系 | P3 做 USB 网卡时定:`usb-net` 作为类型名,`ssh` / `telnet` 作为它的协议字段,`ssh` 类型名保留为旧写法 | 不卡 P2 |

### 10.3 和框架设计不完全一样的地方

框架设计是已确认的文档,下面几条是我在它之上加的或理解上需要确认的,不是改它的决定:

1. **设备类型负责合成能力**(2.1、2.4)。框架设计 5.2 说「每种口声明自己能提供哪些能力,
   框架负责匹配」和「重启模式放进安卓类型的实现里」,没有说类型怎么参与。我的做法是
   口声明、类型合成,两句话都成立。
2. **`tools/` 按功能分文件,不按 android / linux 分目录**(框架设计第 6 节写按
   android / linux / serial / flash 分)。`logs.dmesg` 两种类型都适用,放哪个目录都不对;
   适用范围由声明里的 `kinds` 表达。P2 只有两个文件,P3 工具多了再定目录。
3. **`sdk` 包 P2 就建**(框架设计放在 P6)。只是转出口,不做兼容承诺。
4. **`rtos`、`mcu` 两个类型名 P2 就能写进板子描述**,指向通用类型。没有为它们写专门的实现。

## 附录 A:我实际验证过什么

都在草稿目录里跑,没有改仓库;用的是仓库 `.venv` 里的解释器(typer 0.24.1、
pydantic 2.13.0、fastapi 0.135.3、mcp 1.27.0、import-linter 2.16)。没有启动任何服务。

| # | 验证了什么 | 结果 |
|---|---|---|
| 1 | 给 typer 一个合成签名的函数(`__signature__` 加 `__annotations__`),由 pydantic 模型的字段生成选项 | 命令、短选项、位置参数、可重复选项、`--help` 都正常;`-t 0` 经参数模型校验得到同一种错误结构,退出码 2 |
| 2 | 三处都把原始参数交给同一个校验函数 | `duration=0`、`duration="abc"`、`mode="x"` 在 MCP、命令行、网页接口得到相同的错误;`coerce_numbers_to_str` 把数字 `12345678` 转成了字符串。对照:按 FastMCP 的常规做法(函数带具名参数)时,越界被 FastMCP 先拦成 `ToolError`,未知参数 `{"bogus": 1}` 被静悄悄丢弃并返回成功 |
| 3 | 继承 FastMCP 的 `Tool`、覆盖 `run()`、用 `FastMCP(tools=[…])` 登记 | 拿到了原始参数(含未知参数);和 `@m.tool()` 登记的工具并存;输入描述是我给的 JSON Schema;返回值有结构化内容。另外发现:不在请求里时取 `Context.request_context` 抛 `ValueError("Context is not available outside of a request")` |
| 4 | 新的 `.importlinter` 写法 | 带可选层和一条禁止规则的配置对当前代码:分析 154 个文件、446 条依赖,2 条规则都通过 |
| 5 | 网页接口路由收原始 JSON,`openapi_extra` 填参数模型的 JSON Schema | 接口文档里有请求体的结构;越界得到 200 加错误结构 |
| 6 | 4.4 的十个新错误码是否已登记 | 导入 `ERROR_CODES` 逐个查,十个都不在;`PERMISSION_DENIED`、`INVALID_DEVICE_SERIAL`、`INVALID_DURATION`、`INVALID_SOURCE` 四个都在 |
| 7 | 8.3 给人看的那条 `grep` | 在 main `57edd6b1` 上 8 行;故意去掉 `\s*` 的写法 0 行 |

没有验证、实施时要先确认的:

- `uvicorn devicehands.api.server:app` 在 `app` 改成模块级 `__getattr__` 后是否照常(7.1)。
- typer 的隐藏命令加选项覆盖(旧命令名 `dmesg -d`)。
- Windows 上 `signal.signal(SIGINT, …)` 配合 `asyncio.run` 的协作式叫停(6.4)。
- 前端对这三个旧路径的响应有没有做全等比较(5.5)。

## 附录 B:对调查报告的几处补充

读代码时和调查摘要对了一遍,下面几条和摘要的说法有出入或是摘要没写的:

| 调查摘要 | 我看到的 |
|---|---|
| MCP「无 progress、无 Context」 | 现有代码没用;FastMCP 1.27 有 `Context.report_progress`,可以给长调用报进度 |
| 测试里 29 处手设 `t.name` | 我的 pattern(见 2.5)数到 22 行、8 个文件;数法不同 |
| 8 处按名字判断 | 数量一致(同一条 `grep`,8 行)。其中 `app.py:102,120` 两个分支做的是同一件事;`diagnose.py:96` 实际只需要执行命令和拉文件 |
| — | `Transport.supports_boot_log`、`supports_recovery` 在 `transport/` 之外没有读取点 |
| — | 现有分支 `adr069-phase-a`(未合并,改名前)有一套一板多口的台架板子表,P3 要和板子描述合并考虑 |
| `get_bus()` 9 处用 | `grep -rn "get_bus()" src` 去掉定义所在文件后是 7 行;数法不同 |
