# v1 现状里的问题(v2 设计的输入)

本清单只记录,不修。每条写:现状、两边的出处、会造成什么、留给 v2 的问题。
出处缩写同 [sequences.md](sequences.md)(`P` `J` `R` `AR` `F` `FL` `O` `A`)。
基线:仓库 `p0/baseline`,HEAD `9423271`;`src/`、`clients/` 与 `a073158` 相同。

「测试」一栏写的是 `tests/protocol/test_golden_frames.py` 里对应的
`xfail(strict=True)` 测试。代码修好后这条测试会变成通过,strict 让它报错,
提醒把标记去掉。

| 编号 | 一句话 | 测试 |
|---|---|---|
| G1 | 版本号两边都不校验 | — |
| G2 | 主程序的 hello 构造器比台架实际发的少 5 个字段 | `test_hub_hello_builder_matches_schema` |
| G3 | 主程序的 adb_list 构造器缺 `conflicts` | `test_hub_adb_list_builder_matches_schema` |
| G4 | `JobEvent` 枚举缺 `file` | `test_job_event_enum_covers_every_wire_event` |
| G5 | `JobPhase` 枚举缺 `edl`、`tool` | `test_job_phase_enum_covers_every_wire_phase` |
| G6 | `JobOp` 只有 6 个值,台架操作表有 16 行 | — |
| G7 | 未知 verb、坏消息:主程序断会话,台架忽略 | — |
| G8 | 信令上收到非对象 JSON,台架进程退出 | — |
| G9 | 参数校验规则是 Python 正则写法;分区名规则不上线路 | `test_bench_op_patterns_use_no_python_only_syntax` |
| G10 | `src/alb/api/schema.py` 里的协议说明过时 | — |
| G11 | 调查报告列的漂移 #27 不构成现役不一致 | — |
| G12 | 缺字段时两边的默认值不同 | — |
| G13 | 错误码:channel_error 没有码,done 有两条空码路径 | — |
| G14 | 台架发送侧不查 8 MiB 上限,done.error 不截断 | — |
| G15 | 数据连接上文本消息、空消息的约定只在代码里,两边不同 | — |
| G16 | 台架拨回失败不发 channel_error | — |
| G17 | `tests/remote/test_protocol.py` 里 com_list 的形状和线路不同 | — |
| G18 | `role` 台架不读;3 个 verb 定义了没人用 | — |
| G19 | 信令断开会取消台架上正在跑的作业 | — |
| G20 | 主程序解码控制帧只查 verb | — |
| G21 | serial 的 baud 不是整数时,台架不回 channel_error | — |

---

## G1 版本号两边都不校验

- 现状:控制帧都带 `v: 1`(P:182-183,A:404-405);hello 带 `agent_version`(值是协议号 1,
  A:419),hello_ok 带 `server_version`(AR:236)。
  - 主程序:`decode_control` 只查 verb(P:278-290);握手只查 verb、token、agent_id
    (AR:188-200);`agent_version` 存进 `AgentConnection.version`(AR:208),之后只在
    `/agent/status` 里显示(R:248),不比较。
  - 台架:只查回复的 `verb == "hello_ok"`(A:5281-5283),`v` 和 `server_version` 不看。
- 会造成:协议改了以后新旧混用不报错。现在区分台架版本靠的是 `build`(文件哈希,
  A:118-131,R:115-118)和「字段在不在」(见 sequences.md 第 10 节最后两行)。
- v2 要定:版本范围怎么表示、谁判、不相交时用什么关闭码或错误帧、旧台架(只发 `v: 1`)
  算哪个版本。

## G2 主程序的 hello 构造器比台架实际发的少

- 现状:`protocol.hello()` 只放 `agent_id, name, agent_version, caps, token`(P:186-196);
  台架的 `_hello()` 还发 `build, ops, flash_partitions, boards, update`(A:408-441)。
- 主程序生产代码不调用这个构造器;`grep -rn "protocol.hello(\|p.hello(" src tests` 只命中
  `tests/remote/test_protocol.py:27`。主程序收 hello 时这 5 个字段缺了也接受(AR:210-223)。
- 会造成:构造器给人「hello 就这几个字段」的错误印象;以它为准写第二个实现会漏字段,
  而主程序会把漏了 `ops` 的台架当成旧台架,只给 5 行推断出来的操作表(O:251-295)。
- schema 以台架实际发送的为准(`schema/hello.json` 里 12 个键全部必填)。

## G3 主程序的 adb_list 构造器缺 conflicts

- 现状:`protocol.adb_list(devices)` 只放 `devices`(P:211-212);台架总是同时发
  `devices` 和 `conflicts`(A:5199-5207)。主程序读的时候缺 `conflicts` 当空列表(AR:150-152)。
- 生产代码不调用这个构造器,`tests/remote/test_protocol.py:31` 在用。

## G4 JobEvent 缺 file

- 现状:`JobEvent` 只有 `accepted / progress / done`(P:110-115)。台架 tool-run 还发
  `ev: "file"`(A:2626-2637),主程序靠字符串字面量 `"file"` 处理(FL:979)。
- 主程序的事件构造器 `job_accepted / job_progress / job_done`(P:377-420)全仓没有调用方:
  `grep -rn "job_done\|job_progress\|job_accepted" src tests clients` 去掉定义行后,剩下的结果
  只有台架自己的同名私有函数 `_job_progress`。
- `file` 帧的字段是 `name, size, offset, b64, end`(A:2626-2637);调查报告漏了 `size`。

## G5 JobPhase 缺 edl、tool

- 现状:`JobPhase` 只有 `transfer / flash`(P:118-125)。台架还发 `edl`(A:1903)和
  `tool`(A:2451)。主程序对 `phase` 不做枚举检查,原样转给调用方(FL:969-975)。
- 另:主程序在自己的事件流里还会插入 `wait` 等阶段(FL:1061-1066 `_say`),这些不上线路。

## G6 JobOp 只有 6 个值

- 现状:`JobOp` = `flash, reboot, devices, getvar, oem, erase`(P:97-107);台架操作表
  最多 16 行(`_build_ops`,A:3140-3156)。主程序发请求走通用的 `job_op(name, ...)`
  (P:361-374),`JobOp` 只被那 6 个专用构造器用。
- 不影响线路;问题是枚举看起来像「全部操作」,其实不是。v2 要定:操作名是开放集合
  (由 hello 里的表决定)还是封闭枚举。

## G7 未知 verb、坏消息:两边处理相反

| 收到 | 主程序 | 台架 |
|---|---|---|
| 未知 verb | `decode_control` 抛 ProtocolError(P:287-289)→ `_recv_control` 返回 None(AR:138-141)→ 会话结束(AR:243-245) | if/elif 没有 else,不处理不回复(A:5322-5347) |
| 二进制消息、非 JSON | 会话结束(AR:135-141) | 跳过(A:5316-5321) |

- 会造成:主程序加一个新 verb,旧台架不声不响地忽略;台架加一个新 verb,主程序直接断开,
  台架按退避重连,再断,循环。
- 设计文档 5.5 节已定方向:未知字段忽略,未知帧类型回错误帧、不断开。

## G8 信令上收到非对象 JSON,台架进程退出

- 现状:台架对每条文本消息 `json.loads` 后直接 `frame.get("verb")`(A:5319-5322)。
  消息是合法 JSON 但不是对象(`[1,2]`、`"x"`、`3`)时抛 AttributeError。`_main_loop` 只接
  `OSError / WebSocketException / RuntimeError`(A:5375),AttributeError 一路传到
  `asyncio.run`(A:6179-6180),进程退出。
- 验证:本次在进程内加载台架程序,用替身连接依次喂 hello_ok、`[1,2]`,`_run_session`
  抛出 `AttributeError: 'list' object has no attribute 'get'`;喂未知 verb 的对象则正常
  跳过。没有在真实进程上跑。
- 主程序发的都是对象,今天不会触发;第二个主程序实现或一个出错的代理会触发。

## G9 参数校验规则是 Python 正则写法

- 现状:操作表里 `text` 类参数的 `pattern` 原样随 hello 发出,两边都用 Python
  `re.fullmatch` 执行(A:1085,O:136)。现役取值(本次从台架进程内生成的 16 行操作表里取):
  - getvar 的 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z`(A:884)用了 `\Z`。ECMA-262(JSON Schema
    的 `pattern` 用它)不认 `\Z`;Rust `regex` 库写作 `\z`。
  - 其余 7 个 pattern 没有 `\Z`,但都依赖 full-match 语义(整串匹配),而 ECMA-262 和
    JSON Schema 的 `pattern` 是「包含即可」。例:oem 的 `[^\s\x00-\x1f\x7f-][^\x00-\x1f\x7f]{0,255}`
    (A:910)按包含语义几乎什么都放行。
  - `\s` 在 Python、ECMA-262、Rust 里覆盖的空白字符集合不完全相同。本次没有逐字符比对。
- `partition` 类参数的规则 `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z` 根本不上线路,两边各写死一份
  (O:36,A:95)。
- 主程序遇到编译不过的 pattern 放行,交给台架判断(O:135-139)。
- 会造成:Rust 台架不能照搬这些字符串;主程序换成别的语言后预检行为会变。
- v2 要定:规定一个各语言都能执行的正则子集(写明 full-match),还是改成结构化规则
  (字符集 + 长度 + 首字符);`partition` 是否也发规则。

## G10 schema.py 里的协议说明过时

`src/alb/api/schema.py:778-830`(`/api/version` 返回的接口说明)里:

| 那里写的 | 实际 |
|---|---|
| hello = `{agent_id, name, agent_version, caps, token}`(:786-787) | 台架发 12 个键(A:408-441) |
| `/agent/connect` 只列 5 种消息(:783-811) | 线路上用 10 种(见 `schema/control.json`) |
| 拨回用 `?csecret=` 查询参数认证(:816-818) | 用 `x-alb-token`、`x-alb-csecret` 头(AR:69-93,A:467-476);查询参数只为旧台架保留 |
| `/agent/channel` 是原始字节,「no framing」(:820-825) | job 通道有 5 字节帧头(J:20-24) |

同类过时的还有两处模块文档串:P:4-7 说每条数据连接都是「raw bytes (no per-frame header)」,
AR:5-8 说 `/agent/channel` 「carrying raw bytes」,都早于 job 通道。

## G11 调查报告的「漂移 #27」核对后不算现役不一致

- 调查报告把主程序旧表的 getvar / oem 规则(O:247-248、O:267、O:274)和现役台架的规则
  (A:884、A:910、A:2818)对比,列为漂移。
- 核对:旧表只在 hello **没有** `ops` 时使用(R:133-134,O:251-259),描述的是 ADR-059
  之前的旧台架;现役台架每次 hello 都带 `ops`(A:429)。所以两者不比较。
- 剩下的真实差异只有一条:旧表 getvar 的 `name` 默认 `""`(O:267),现役台架默认 `"all"`
  (A:2818);两者在各自的台架上都表示 `getvar all`(P:335)。

## G12 缺字段时两边的默认值不同

| 字段 | 主程序读到缺失时(O:166-211) | 台架内部默认(A:1121-1142) |
|---|---|---|
| `record` | `events`(O:177、O:190) | `none`(A:1134) |
| `timeout_s` | `0.0`(O:178、O:192) | `300.0`(A:1135) |
| `mutates` / `exclusive` | `true`(O:207-208) | `true` |
| `group` | `""`(O:199) | 无默认,必填 |

- 台架的 `describe()` 总是把字段写全(A:1175-1188),所以今天线路上不出现缺字段,差异不生效。
  schema 把这些字段都设成必填。

## G13 错误码

- `channel_error` 只有自由文本 `reason`,没有码(A:505-516);主程序原样转成
  `ChannelOpenError(reason)`(R:318)。调用方只能看文字。
- done 帧有两条失败路径发空 `code`:请求帧解析失败(A:3238)、该收镜像却收到控制帧(A:3575)。
- 台架发出的码散落在字面量里,没有集中定义;只有「台架的码都在主程序目录里」这一条测试
  (`tests/remote/test_agent_codes_in_catalog.py:39-41`)。

## G14 发送侧上限不对称

- 台架 `_job_encode` 不查 8 MiB 上限(A:762-763),主程序查(J:65-66)。
- 台架 done 的 `stdout`、`stderr` 各截到最后 64 KiB(A:3708-3709、`_JOB_OUTPUT_CAP` A:106),
  但 `error` 取的是完整 stderr 或 stdout(A:3710),不截。工具输出很长又失败时,done 帧可能
  超过 8 MiB,主程序读帧时报 JobProtocolError(J:107-108),结果变成 FLASH_FAILED
  「unexpected frame」(FL:821-822)。这一条是读代码得出,没运行验证。

## G15 数据连接上文本消息、空消息的约定

见 [framing.md](framing.md) 第 5 节。主程序把文本消息和 0 字节二进制消息都当连接结束
(AR:103-114);台架把文本消息转成字节当数据(A:667-668、A:698-699、A:806-807),job 通道上
0 字节消息当结束(A:808)。现有两端都不发这两种消息,所以今天不出问题;第三方实现必须遵守。

## G16 台架拨回失败不发 channel_error

- 现状:三种通道里,拨回连接失败(连不上、被 1008 关掉)都只写日志(A:586-587、A:641-642、
  A:3223-3224)。主程序那边只能等拨回超时(10 s / 30 s)才知道。
- job 通道这时已经占了锁,失败后在 finally 里释放(A:3225-3227),不会卡住。

## G17 test_protocol.py 里 com_list 的形状和线路不同

`tests/remote/test_protocol.py:33` 用 `{"com": "COM3", "baud": 115200}` 调 `com_list`;
线路上台架发的是 `{"port", "desc"}`(A:5244)。构造器不检查形状,所以测试能过。
`frames/invalid/com_list--old-shape.json` 就是这个形状。

## G18 role 台架不读;三个 verb 没人用

- 主程序总按通道类型配对发 `role`(tcp→daemon、serial→gateway、job→job,P:74-78);
  台架 `_handle_open_channel` 不读 `role`(A:519-538)。
- `channel_opened / close_channel / channel_closed` 在 `Verb` 里有、有构造器
  (P:143-146、P:254-267),两边都不发,台架也不处理。主程序 AR:259-261 的注释把它们叫
  「advisory」。schema 里标了 `"x-status": "unused"`。

## G19 信令断开会取消台架上正在跑的作业

- 台架 `_run_session` 结束时取消所有通道任务(A:5354-5363),包括正在跑的 job 通道;
  fastboot 子进程在取消时被结束(A:3680-3684)。数据连接本身其实还活着。
- 主程序侧作业不依赖信令连接,看到的是通道被关、没有 done → `result_lost`(FL:954-967)。
- 这是读代码得出的,没有在真实环境复现。v2 要定:数据通道的生命周期是否独立于信令连接。

## G20 主程序解码控制帧只查 verb

`decode_control` 只检查「是 JSON 对象且 verb 已知」(P:278-290),字段由各处用
`.get` 加强制类型转换宽松地读(AR:196-223、AR:148-154)。例如 `caps` 若是字符串
`"adb"`,`list(...)` 会得到 `["a","d","b"]`(AR:209)。schema 比现有解码器严格。

## G21 serial 的 baud 不是整数时台架不回 channel_error

`baud = int(params.get("baud") or 115200)` 在 try 之外(A:602)。baud 是非数字字符串时抛
ValueError,任务异常结束,不发 channel_error,主程序等 10 s 超时;而超时不在串口重试范围内
(F:550 只接 ChannelOpenError)。主程序自己总发整数(F:435),今天不会触发。读代码得出,没运行验证。

---

## 附:调查报告「有漂移的 5 处」逐处核对

| 报告编号 | 报告的说法 | 核对结果 |
|---|---|---|
| #5 hello 字段 | 主程序构造器缺 build/ops/flash_partitions/boards/update | 属实,见 G2 |
| #7 adb_list | 主程序构造器无 conflicts | 属实,见 G3 |
| #16 JobEvent | 台架多发 file | 属实,见 G4;另外报告漏了 file 帧的 `size` 字段 |
| #17 JobPhase | 台架多发 edl、tool | 属实,见 G5 |
| #27 旧表 getvar / oem | oem 规则和 getvar 默认值不同 | 比较对象不对,不构成现役不一致,见 G11 |
