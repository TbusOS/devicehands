# v2 顺序和时间规则

结构描述(`schema/`)只管单个帧长什么样。本文管帧之间的先后、谁等谁、等多久、
出错时回什么。两个实现都按它做;一致性测试(`tests/conformance/`)按它查台架。
线路格式见 [framing.md](framing.md)。

## 1. 连接与鉴权

| 连接 | 路径 | 凭证 |
|---|---|---|
| 信令 | `/bench/connect` | 头 `x-dhands-token`(台架配了 token 才带) |
| 数据 | `/bench/channel?cid=<cid>` | 头 `x-dhands-token`、`x-dhands-csecret` |

- 主程序没配 token 时不校验 token。比较用常数时间比较。
- 信令:主程序接受 WebSocket 后**先查 token**,不对就用关闭码 1008 关掉,不读任何帧。
- 数据:`cid` 为空或 token 不对 → 1008;`cid` 不存在、已经结束、`x-dhands-csecret`
  不对 → 1008。查询参数里的凭证不读。

## 2. 握手和版本协商

```
 台架                                        主程序
  │ 连 /bench/connect(头里带 token)           │ token 不对 → 关闭 1008
  │──────────────────────────────────────────▶│
  │ hello {proto:{min,max}, bench_id, …}       │ 10 s 内必须到,否则关闭 1002
  │──────────────────────────────────────────▶│ 检查 hello(见下表)
  │                                            │ 取两边版本范围交集里最大的
  │◀──────────────────────────────────────────│ hello_ok {proto}
  │◀──────────────────────────────────────────│ list_adb {id}
  │◀──────────────────────────────────────────│ list_com {id}
  │ 之后每 20 s 一个 heartbeat                 │
```

主程序收到的第一帧:

| 情况 | 主程序回 | 然后 |
|---|---|---|
| 10 s 内没有帧 | — | 关闭 1002 |
| 二进制消息、不是 JSON 对象、缺字段、类型不对、`proto.min > proto.max`、操作表里的 `pattern` 不在子集里 | `error` `BAD_FRAME` | 关闭 1002 |
| 不认识的 verb,或者不是 `hello` | `error` `UNKNOWN_VERB` | 关闭 1002 |
| 版本范围没有交集 | `error` `PROTO_VERSION_UNSUPPORTED`,带 `supported: {min, max}` | 关闭 1002 |
| 都对 | `hello_ok {proto}` | 登记台架,进入会话 |

握手阶段出错要关连接,而不是像会话中那样继续:台架下一次重连发的还是同一个 hello,
不关就会停在一个什么都做不了的会话里。

台架发完 hello 后:

| 收到 | 台架做什么 |
|---|---|
| `hello_ok`,`proto` 在自己的范围内 | 进入会话,启动心跳 |
| `hello_ok`,`proto` 不在自己的范围内 | 关连接,**不再重连**,记日志 |
| `error` `PROTO_VERSION_UNSUPPORTED` | **不再重连**;比较对方的 `supported` 和自己的范围,在状态页和日志里写明是哪一边需要升级 |
| 别的 `error`,或别的帧 | **不再重连**,记下对方的码和原文 |
| 连接以 1002 或 1008 关闭 | **不再重连**(1008 = token 不对) |
| 10 s 内什么都没收到 | 关连接,按退避重连 |
| 网络断开、对端异常关闭 | 按退避重连 |

- 退避:1 s、2 s、5 s、10 s,之后一直 10 s;一次会话正常进入后从头算。
- 「不再重连」:台架停止拨号(进程是否退出由实现定,Python 版退出)。这些情况重连
  也只会得到同样的回答,不停就是无限循环。
- 同一 `bench_id` 重连:新连接替换旧登记;旧会话收尾时只删自己那一份登记。
- 多个台架同时在线:设备流量发给最近登记的那一个。

### 版本号规则

- 只加可选字段、只加新的操作、只加新的错误码,不升版本。
- 删字段、改含义、改必填项、给固定集合加值(verb、job 事件、`group` 等枚举),升版本。
- 主程序同时支持当前版本和上一个版本。v2 是第一个带协商的版本,所以现在两边的范围都是 `{min: 2, max: 2}`。

## 3. 心跳与会话结束

- 台架在进入会话后启动心跳:先等一个间隔,再发第一个 `heartbeat`,之后每个间隔一个。
  间隔 20 s。
- 主程序不发心跳。它每次等下一帧最多 60 s;收到任何一帧(含会被回 `error` 的帧)都重新计时。
  60 s 内什么都没收到 → 结束会话。
- 主程序会话结束时注销登记,并让这条信令连接上还在等的开通道请求立刻失败。
- 台架会话结束时取消心跳,也取消所有还在跑的数据通道任务(含正在跑的作业)。
  这一条和 v1 相同(known-gaps G19 留给以后单独设计)。

## 4. 设备列表请求

```
 主程序                        台架
  list_adb {id:1} ──────────▶  跑 adb devices(设备为空时才去找冲突进程)
  ◀────────── adb_list {re:1}
  list_com {id:2} ──────────▶  列串口
  ◀────────── com_list {re:2}
  restart_adb {id:3} ───────▶  [结束冲突进程] → adb kill-server → start-server
  ◀────────── adb_list {re:3}
```

- 台架每个请求开一个独立任务,回复顺序不保证。
- 主程序只接受 `re` 等于本会话里自己发过的对应请求 `id` 的回复;同一种列表只用
  `re` 比已经采用的更大的那份,迟到的旧回复丢弃。`re` 对不上的回复记日志后丢弃,不回 `error`
  (帧本身是合格的)。
- 主程序发请求的时机:`hello_ok` 之后;有人查看台架状态时;要求重启 adb 时。

## 5. 开通道

```
 主程序                                       台架
 1 cid、csecret 随机生成
 2 先登记 pending(cid, csecret, 这条信令连接)
 3 open_channel ─────────────────────────────▶
                                   4 按类型检查,打开本地端点
   ◀───────────────────────────── channel_error{cid, code, reason}   任一步失败
   立刻让等待方失败
                                   5 拨回 /bench/channel?cid=<cid>
                                     头 x-dhands-token、x-dhands-csecret
   ◀══════════════════════════════ 数据连接
                                   5' 拨回失败 → channel_error{cid, BENCH_DIAL_BACK_FAILED}
 6 校验 cid、token、csecret,不对 → 1008
 7 等待方拿到通道;超时后删除 pending
```

规则:

1. **先登记,后发送**。台架拨回得再快,也能找到对应的 cid。
2. **没有「打开成功」帧**。拨回成功就是打开成功;关闭数据连接就是关闭通道。
3. 台架拨回失败(连不上、被 1008 关掉)也发 `channel_error`:信令连接还在,发得出去。
4. `channel_error` 只接受来自发出那个 `open_channel` 的信令连接。别的连接发来的,
   主程序忽略并记日志。
5. `open_channel` 读不出合法的 `cid` → 台架回 `error` `BAD_FRAME`。能读出 `cid`、
   但别的字段不合格(类型不认识、参数缺失或类型不对)→ 台架回 `channel_error`
   `BENCH_PARAM_REJECTED`,因为主程序那边有人在等这个 cid。
6. 超时后 pending 被删除;之后迟到的拨回按 cid 不存在处理,被 1008 关掉。

台架在第 4 步按通道类型做的检查,顺序如下:

| 类型 | 检查 → 失败时的码 |
|---|---|
| 任何类型 | 台架正在退出 → `BENCH_STOPPING`;`channel_type` 不认识 → `BENCH_PARAM_REJECTED` |
| tcp | `target` 不是 `127.0.0.1:5037` → `BENCH_PARAM_REJECTED`;连不上本机 adb server → `ADB_SERVER_UNREACHABLE` |
| serial | `com` 缺失、是 `loop://` 以外的 URL,或 `baud` 不是正整数 → `BENCH_PARAM_REJECTED`;没装串口库 → `SYSTEM_DEPENDENCY_MISSING`;串口不存在 → `SERIAL_PORT_NOT_FOUND`;打不开(被占用、没权限)→ `SERIAL_PORT_BUSY` |
| job | `params.op` 缺失或不是合法操作名 → `BENCH_PARAM_REJECTED`;要 fastboot 而本机没有 → `FASTBOOT_UNAVAILABLE`;要锁而锁被占 → `FASTBOOT_BUSY` |

## 6. 三种通道的重试

重试规则由通道类型决定,帧里不带:

| 通道 | 规则 | 理由 |
|---|---|---|
| tcp | 不重试。每条本地 TCP 连接各开一条通道 | adb server 是常驻服务,连不上就是真出错 |
| serial | 只在 `channel_error` 的码是 `SERIAL_PORT_BUSY` 时重试,共最多 3 次,两次之间等 0.15 s、0.35 s;拨回超时不重试。所有本地连接共用一条通道 | 串口独占,上一个会话刚释放时可能短暂打不开;别的码重试也不会变 |
| job | 不重试 | 作业可能已经改了设备,再来一次是第二次写入 |

## 7. 作业通道

```
 主程序                                          台架
 open_channel(job, params.op) ──────────────────▶ 判 fastboot / 锁,占锁,拨回
 ◀═══════════════ 数据连接 ═══════════════
 C 请求 {op, 参数…, [size, sha256], [board]} ──▶
                                    不是控制帧、不是 JSON 对象      → done BENCH_PARAM_REJECTED
                                    op 与 params.op 不同            → done BENCH_PARAM_REJECTED
                                    op 不在表里                     → done BENCH_OP_UNKNOWN
                                    有表里没有的键                  → done BENCH_PARAM_REJECTED
                                    参数不合格                      → done(参数自己的错误码)
 [image 类] D × n + D(长度 0) ──────────────────▶
 ◀──────────────────────────────────── accepted
 ◀──────────────────────────────────── progress{phase …}
 ◀──────────────────────────────────── file{…}(只有 tool-run)
 ◀──────────────────────────────────── done → 台架立刻释放锁
 收到 done → 关通道
```

- 请求里允许的键:`op`;image 类操作的 `size`、`sha256`;`board`;这个操作的参数名。
  别的键一律拒绝 —— 拼错的参数名被不声不响地丢掉,操作就会用默认值跑。
- `int` 参数只接受 JSON 整数,十进制字符串拒绝。主程序的命令行可以把用户输入的字符串
  先转成整数再发,线路上永远是整数。
- 非 image 操作:参数通过、前置条件通过后才发 `accepted`;参数不过直接 `done`。
- image 操作:先查临时空间、设备在不在、板子对不对,通过后发 `accepted`,再收镜像;
  主程序一边发镜像一边读事件,`done` 可能在镜像没发完时到达(拒绝),主程序随即停止发送。
- 收镜像时该收数据帧却收到控制帧 → `done` `BENCH_PARAM_REJECTED`。
- `done` 失败时 `code` 必须非空。主程序收到不合格的事件(缺字段、`ev` 不认识、
  失败的 `done` 没有码)→ 按通道出错处理,结果是 `FLASH_FAILED`。
- 通道关了却没收到 `done` → 主程序报 `FLASH_FAILED`,`data.result_lost = true`,
  表示设备状态未知。
- 会改设备、需要 fastboot 设备的操作,在台架配了板子身份时先核对,不符就 `done`
  `FLASH_BOARD_MISMATCH` / `FLASH_BOARD_NOT_LISTED`。

## 8. 作业锁

两边各有一把锁,互相独立,规则相同:**被占就立刻拒绝,不排队**。

| | 主程序 | 台架 |
|---|---|---|
| 谁要锁 | `exclusive = true` 的操作 | 按 `params.op` 查表,`exclusive = true` 的要;不在表里的不要 |
| 被占时 | 立即返回 `FASTBOOT_BUSY` | 拨回前回 `channel_error` `FASTBOOT_BUSY` |
| 何时占 | 开始一个作业时 | 判断和占用之间不让出执行权,在拨回之前 |
| 何时放 | 作业结束后 | `done` 一发出就放;没发 `done` 就在通道结束时放 |

台架发完 `done` 就放锁,是因为主程序读到 `done` 就会发下一个作业的 `open_channel`,
那时台架可能还在关上一个通道。

## 9. 大小和时间

两边用同一组值:

| 项 | 值 |
|---|---|
| 主程序等 `hello` / 台架等 `hello_ok` | 10 s |
| 心跳间隔 / 主程序多久没收到帧就断开 | 20 s / 60 s |
| tcp、serial 通道等拨回 | 10 s |
| job 通道等拨回 | 30 s |
| 单个 job 帧的 payload 上限 | 8 MiB,发送方和接收方都查 |
| `done` 里 `error` 文本的上限 | 4096 个字符,发送方截断 |
| `done` 里 `stdout`、`stderr` | 各取最后 65536 个字符 |
| 信令上单条消息 | 不超过 1 MiB |
| 重连退避 | 1、2、5、10 s,之后一直 10 s |

心跳间隔在测试里可以调短(台架配置项 `heartbeat_s`),这是测试用的口子,正式部署不改。

## 10. 错误码

协议层自己的三个码,只出现在 `error` 帧里:

| 码 | 意思 |
|---|---|
| `PROTO_VERSION_UNSUPPORTED` | 两边的版本范围没有交集。带 `supported` |
| `UNKNOWN_VERB` | 不认识这个 verb,或者这一方不该收到它 |
| `BAD_FRAME` | 帧不合格:不是 JSON 对象、缺字段、类型不对、信令上的二进制消息 |

`channel_error` 和作业 `done` 里的码来自主程序的错误码目录(`src/devicehands/infra/errors.py`),
每个码在目录里有一句解释和一条处理办法。台架新加一个码不升版本,但要同时加进目录。
