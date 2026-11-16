# v1 顺序和时间规则

结构描述(`schema/`)只管单个帧长什么样。本文管帧之间的先后、谁等谁、等多久。
只写现状(仓库 `p0/baseline`,HEAD `9423271`;`src/`、`clients/` 与 `a073158` 相同)。两边做法不一样的地方照实写,
标「现状」;改法留给 v2,问题汇总在 [known-gaps.md](known-gaps.md)。

出处缩写:`P` = `src/alb/remote/protocol.py`,`J` = `src/alb/remote/jobframe.py`,
`R` = `src/alb/remote/registry.py`,`AR` = `src/alb/api/agent_route.py`,
`F` = `src/alb/remote/forwarder.py`,`FL` = `src/alb/remote/flash.py`,
`O` = `src/alb/remote/bench_ops.py`,`A` = `clients/windows-agent/alb_agent.py`。

## 1. 连接与鉴权

| 连接 | URL 路径 | 凭证放在哪 | 出处 |
|---|---|---|---|
| 信令 | 台架配置里的 `hub_url`,主程序在 `/agent/connect` 上收 | `hello` 帧体里的 `token`;不用任何 HTTP 头 | AR:178,AR:192,A:5270-5271 |
| 数据 | `/agent/channel?cid=<cid>`,scheme 和主机取自 `hub_url`,路径固定 | 头 `x-alb-token`(台架配了 token 才带)和 `x-alb-csecret` | A:456-476,AR:279-298,P:40-41 |

- 主程序的 token 来自环境变量 `ALB_AGENT_TOKEN`;没设就不校验(AR:53-63)。
  比较用 `hmac.compare_digest`(AR:63)。
- 数据连接上主程序先读头;两个头都没有时,退回读查询参数 `token` / `csecret`,
  给 ADR-055 之前的旧台架用,每个进程只警告一次(AR:69-93)。
- 数据连接 `hub_url` 里的路径前缀会被丢掉:台架只取 scheme 和主机,路径写死
  `/agent/channel`(A:464)。
- 主程序先 `accept` 再校验,校验失败用关闭码 1008 关掉(AR:281-298)。

## 2. 握手

```
 台架                                       主程序
  │ WebSocket 连 /agent/connect               │ accept (AR:180)
  │──────────────────────────────────────────▶│
  │ hello {agent_id, ..., token}               │ 10 s 内必须到 (AR:49, 187)
  │──────────────────────────────────────────▶│ 超时/不是 hello/解析失败 → 1002
  │                                            │ token 错            → 1008
  │                                            │ agent_id 去空白后为空 → 1002
  │                                            │ 登记(按 agent_id 递增 epoch)
  │                                            │ 挂上 adb / serial 转发器
  │◀──────────────────────────────────────────│ hello_ok {server_version: 1}
  │◀──────────────────────────────────────────│ list_adb
  │◀──────────────────────────────────────────│ list_com
  │ 之后每 20 s 一个 heartbeat                 │
```

- 主程序的检查顺序:先看是不是 hello(AR:188-191),再看 token(AR:192-195),
  最后看 agent_id(AR:196-200)。登记、挂转发器、发 hello_ok 都在检查之后
  (AR:205-236)。登记之后立刻发 list_adb、list_com(AR:238,R:148-157)。
- 台架发完 hello 就等一条回复(A:5271-5276),**没有自己的超时**,只靠
  WebSocket ping。回复的处理:
  - 连接以 1002 或 1008 关闭 → 认定被拒,**不再重连**,进程停在那里(A:5277-5279、
    A:5371-5374)。
  - 回复不是 `verb == "hello_ok"` → 同样不再重连(A:5281-5283)。
  - `server_version` 和 `v` 不看(A:5281-5283)。主程序也不看 hello 的 `v` 和
    `agent_version`(AR:205-226)。现状:版本号两边都不校验(known-gaps G1)。
- 其他原因断开(网络、对端关闭、OSError、WebSocket 异常、RuntimeError)→ 台架按
  1 s、2 s、5 s、10 s、10 s… 退避重连(A:111、A:5375-5386);一次会话正常结束后退避
  从头算(A:5370)。
- 同一 `agent_id` 重连:新连接 epoch 加一并替换旧登记;旧会话收尾时只在 epoch
  仍是自己时才删除登记,不会误删新连接(R:211-230)。
- 多个台架同时在线:设备流量只发给 epoch 最大、也就是最近登记的那一个(R:235-241)。

## 3. 心跳与会话结束

- 台架在收到 hello_ok 后启动心跳任务,先睡 20 s 再发第一个 heartbeat,之后每 20 s
  一个(A:110、A:5103-5109、A:5296)。
- 主程序不发心跳。它每次等下一帧最多 60 s;收到任何一个能解析的控制帧都重新计时
  (AR:50、AR:241-248)。60 s 内什么都没收到 → 结束会话。
- 主程序侧会话结束时:按 epoch 注销登记(AR:268-276)。adb / serial 转发器是进程级的,
  不随会话拆掉,只是之后找不到台架,新的本地连接立刻失败(AR:228-232)。
- 台架侧会话结束时:取消心跳,**也取消所有还在跑的数据通道任务,包括正在跑的作业**
  (A:5348-5363)。作业里正在跑的 fastboot 子进程会被结束(A:3680-3684)。现状。

## 4. 设备列表请求

```
 主程序                       台架
  list_adb ────────────────▶  开一个任务跑 `adb devices`
  ◀────────────── adb_list    (设备为空时才去找冲突进程)
  list_com ────────────────▶  开一个任务列串口
  ◀────────────── com_list
  restart_adb ─────────────▶  [结束冲突进程] → adb kill-server → start-server
  ◀────────────── adb_list
```

- 请求和回复之间没有编号。台架每个请求开一个独立任务(A:5329-5347),回复顺序不保证。
  主程序不等回复,收到 adb_list / com_list 就覆盖缓存(R:148-167,AR:144-154)。
- 触发时机:hello_ok 之后(AR:238);每次有人 GET `/agent/status`(AR:343);
  POST `/api/agent/adb/restart`(AR:310-334,发 restart_adb)。

## 5. 开通道

```
 主程序                                       台架
 1 cid = uuid4 hex,csecret = token_urlsafe(32)
 2 先登记 pending(cid, csecret)    R:182-184
 3 open_channel ─────────────────────────────▶
                                   4 按类型做检查,打开本地端点
                                     任一步失败 → channel_error
   ◀───────────────────────────── channel_error{cid, reason}
   立刻让等待方失败,不等超时   AR:249-258, R:301-319
                                   5 拨回 /agent/channel?cid=<cid>
                                     头 x-alb-token、x-alb-csecret
   ◀══════════════════════════════ 数据连接
 6 cid 为空或 token 错 → 1008      AR:282-287
 7 resolve_pending:cid 不存在/已结束/secret 不符 → 1008
   secret 用常数时间比较           R:279-299
 8 等待方拿到通道;等不到就超时,最后删除 pending   R:191-193
```

规则:

1. **先登记,后发送**(R:179-190)。这样台架拨回得再快,也能找到对应的 cid。
2. **没有「打开成功」帧**。拨回成功本身就是成功信号;`channel_opened` 定义了但没人发
   (P:143、P:254-255)。
3. 超时后 pending 被删除(R:193);之后迟到的拨回按 cid 不存在处理,被 1008 关掉。
4. pending 不和台架绑定:任何已通过鉴权的信令连接发来的 channel_error 都能让任意 cid
   失败(R:311-313,DEBT-083)。
5. 台架拨回失败(连不上、被 1008 关掉)时只写日志,不补发 channel_error
   (A:586-587、A:641-642、A:3223-3224);主程序这时只能等到超时。现状。

台架在第 4 步按通道类型做的事,顺序如下,任何一步失败都回 channel_error:

| 类型 | 步骤 | 出处 |
|---|---|---|
| 任何类型 | 缺 cid → **不回任何帧,直接忽略**;台架正在退出 → channel_error;channel_type 不认识 → channel_error | A:522-538 |
| tcp | 目标不在白名单(只有 `127.0.0.1:5037`)→ 端口解析 → 连本机 adb server → 拨回 | A:545-573 |
| serial | 缺 com → 没装 pyserial → 打开串口 → 拨回 | A:600-638 |
| job | 读 `params.op` 提示,决定要不要 fastboot、要不要锁 → 需要 fastboot 却没有 → 需要锁而锁被占 → 占锁 → 拨回 | A:3176-3220 |

## 6. 三种通道的重试

| 通道 | 主程序 | 台架 | 理由(代码注释) |
|---|---|---|---|
| tcp | 不重试,一次失败就关掉本地连接(F:181-197)。每条本地 TCP 连接各开一条通道(F:175-210) | 只试一次(A:562-568) | adb server 是常驻服务,连不上就是真出错(P:12-16、P:58-60) |
| serial | 只对 channel_error(`ChannelOpenError`)重试:共 3 次,两次之间等 0.15 s、0.35 s;拨回超时不重试(F:54、F:530-555)。所有本地连接共用一条通道,引用计数,最后一个离开才关(F:508-568);打开期间持锁,后来的连接等结果再复用(F:508-514) | 只试一次(A:618-628) | 串口独占,上一个会话刚释放时可能短暂打不开(F:51-53、F:534-540) |
| job | 不重试(FL:843-851) | 只试一次 | 作业可能已经改了设备,再来一次是第二次写入(P:63-68) |

## 7. 作业通道

```
 主程序                                          台架
 自己的锁被占 → FASTBOOT_BUSY,不排队   FL:725-737
 open_channel(job, params.op) ──────────────────▶ 判 fastboot / 锁,占锁,拨回
 ◀═══════════════ 数据连接 ═══════════════
 C 请求 {op, 参数…, [size, sha256], [board]} ──▶ FL:853
                                    op 与提示不符 → done BENCH_PARAM_REJECTED
                                    op 不在表里   → done BENCH_OP_UNKNOWN
                                    参数不合格    → done(参数自己的错误码)
 [image 类] D × n(每块 64 KiB)+ D(长度 0) ─────▶
                                    先查临时空间、设备在不在、板子对不对
                                    → 不满足就直接 done,不收镜像
 ◀──────────────────────────────────── accepted(收镜像之前就发)
 ◀──────────────────────────────────── progress{transfer} 每收一块一帧
                                    收满 size 字节 → 校验 sha256 → 不符 done
 ◀──────────────────────────────────── progress{flash | edl | tool} 每行输出
 ◀──────────────────────────────────── file{…}(只有 tool-run)
 ◀──────────────────────────────────── done → 台架立刻释放锁
 收到 done → 关通道
```

- 请求帧是通道上的第一帧,必须是控制帧;不是控制帧或不是 JSON 对象 → 台架回
  code 为空的 done(A:3234-3239,JSON 解析错误按 ValueError 接住)。
- `params.op` 提示缺失 → 台架按旧主程序处理,当作独占的 fastboot 作业(A:3176-3185);
  提示不在操作表里 → 不占锁,拨回后回 BENCH_OP_UNKNOWN,除非它是 fastboot 操作而本机没有
  fastboot,那样在拨回前就回 channel_error(A:3186-3194)。
- 请求和提示不一致 → done BENCH_PARAM_REJECTED(A:3243-3251)。
- 非 image 操作:参数通过、前置条件通过后才发 accepted(A:3283-3317);参数不过直接 done,
  没有 accepted(A:3286-3289)。
- image 操作:accepted 在收镜像**之前**发(A:3547)。主程序一边发镜像一边读事件
  (FL:862-912);done 可能在镜像没发完时到达(拒绝),主程序随即停止发送。
- 主程序忽略 accepted 和不认识的 ev(FL:1007-1009)。
- 通道关了却没收到 done → 主程序报 FLASH_FAILED,`data.result_lost = true`,
  表示设备状态未知(FL:954-967)。
- 会改设备、需要 fastboot 设备的操作,在台架配了 `fastboot_product` 时先核对板子
  身份,不符就 done FLASH_BOARD_MISMATCH / FLASH_BOARD_NOT_LISTED(A:3450-3498)。

## 8. 作业锁

两边各有一把锁,互相独立,规则相同:**被占就立刻拒绝,不排队**。

| | 主程序 | 台架 |
|---|---|---|
| 锁 | `FlashService._lock`,每个主程序进程一把(FL:257) | `_job_lock`,每个台架进程一把(A:745) |
| 谁要锁 | `OpDesc.exclusive = true` 的操作(FL:452、FL:752-758) | 同左,按 `params.op` 提示查表;提示缺失也要锁;提示不在表里不要锁(A:3181-3191) |
| 被占时 | 立即返回 FASTBOOT_BUSY(FL:734-736) | 拨回前回 channel_error "another flash job is already running"(A:3204-3206) |
| 何时占 | 进入 `_run` 时(FL:760) | 判断和占用之间没有 await,在拨回之前(A:3198-3207) |
| 何时放 | 作业结束(含录 UART 的尾段)后(FL:760-773) | **done 一发出就放**(A:3356-3366);没发 done 就在通道结束时放(A:3221-3227) |
| 其他 | 刷机计划多行共享一次持锁(FL:688) | 收到「新实例要求停止」后永久占住锁,不再接作业(A:4532-4537) |

台架在发完 done 就放锁,是因为主程序读到 done 就会发下一行的 open_channel,
那时台架还在关上一个通道(A:3357-3363 注释,2026-10-20 的实测)。

## 9. 超时一览

| 名称 | 值 | 哪一方 | 管什么 | 出处 |
|---|---|---|---|---|
| `HELLO_TIMEOUT_S` | 10 s | 主程序 | 连上后等 hello | AR:49 |
| `HEARTBEAT_TIMEOUT_S` | 60 s | 主程序 | 两帧之间最长间隔 | AR:50 |
| `HEARTBEAT_INTERVAL_S` | 20 s | 台架 | 心跳间隔 | A:110 |
| `RECONNECT_BACKOFF_S` | 1, 2, 5, 10 s | 台架 | 重连退避,之后一直 10 s | A:111 |
| `DIAL_BACK_TIMEOUT_S` | 10 s | 主程序 | tcp / serial 通道等拨回 | F:41 |
| `DIAL_BACK_TIMEOUT_S` | **30 s** | 主程序 | job 通道等拨回(同名、另一个常量) | FL:76 |
| 设备轮询 | 40 s | 主程序 | 刷机计划里等板子回 fastboot 时,每次 `devices` 作业的总时限 | FL:715-718 |
| `JOB_TIMEOUT_S` | 1800 s | 主程序 | 单个作业总时长 | FL:82 |
| `SEND_FAILED_GRACE_S` | 5 s | 主程序 | 发镜像失败后等台架报告 | FL:86 |
| serial 重试间隔 | 0.15 s, 0.35 s | 主程序 | 串口打开失败后的重试 | F:54 |
| `OpDesc.timeout_s` | 每个操作不同(devices 20 s,flash 900 s,edl-program 1500 s…) | 台架 | 操作里工具子进程的时限,超时杀掉,rc = -1 | A:2791-3136、A:3639-3666 |
| `_DEVICE_QUERY_TIMEOUT_S` | 20 s | 台架 | 查 `fastboot devices` / `getvar product` | A:2788 |
| WebSocket ping | 20 s 间隔 + 20 s 等回应 | 两边 | 库默认值,代码没设(本机 websockets 16.0 / uvicorn 0.44.0;台架实际版本没验证) | FL:870-875 注释 |

主程序不用 `OpDesc.timeout_s`,它只有 `JOB_TIMEOUT_S` 一个总上限(`grep -n timeout_s src/alb/remote/flash.py` 只命中刷机计划等待的参数)。

## 10. 收到意外内容时(现状,两边不对称)

| 情况 | 主程序 | 台架 |
|---|---|---|
| 信令上未知 verb | 结束整个会话(P:287-289 → AR:138-141 → AR:243-245) | 不处理,不回复(A:5322-5347 的 if/elif 没有 else) |
| 信令上二进制消息 / 非 JSON | 结束会话(AR:135-141) | 跳过(A:5316-5321) |
| 信令上 JSON 但不是对象(如 `[1]`) | 结束会话(P:285-286) | `frame.get` 抛 AttributeError,`_main_loop` 不接这个异常,**台架进程退出**(A:5322、A:5371-5377;本次用进程内替身连接实测) |
| 帧里多出未知字段 | 不读 | 不读 |
| `v` / 版本号不是 1 | 不查 | 不查 |
| open_channel 缺 cid | — | 不回任何帧,直接忽略(A:524-525) |
| open_channel 的 channel_type 不认识 | — | channel_error(A:536-538) |
| 作业请求里多出的键 | 发送前就拒绝 BENCH_PARAM_REJECTED(O:337-345) | 忽略(A:1190-1194) |
| int 参数给的是十进制字符串 | 转成 int 再发(O:119-127、O:356-363) | 必须是真 int,否则拒绝(A:1077-1081) |
| 参数 kind 不认识 / pattern 编译失败 | 放行,交给台架判断(O:135-141) | 不会发生:建表时就报错(A:1009-1019) |
| 未知 job 事件 | 忽略(FL:1007-1009) | — |
| hello 里某一行 OpDesc 坏了 | 丢掉这一行,会话照常(O:180-211、O:232-244) | — |
| hello 缺 ops / build / boards / update / flash_partitions | 当作旧台架:ops 从 caps 推出 5 行旧表,其他取空值(AR:210-223、O:251-295) | — |
| adb_list 缺 conflicts | 当作空列表(AR:150-152) | — |
