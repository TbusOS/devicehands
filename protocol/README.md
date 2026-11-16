# protocol/:主程序和台架服务之间的协议描述

这个目录用与编程语言无关的方式,描述主程序(hub,`src/alb/remote/`、
`src/alb/api/agent_route.py`)和台架服务(`clients/windows-agent/alb_agent.py`)之间
的 WebSocket 协议。Python 和以后的 Rust 实现都拿这里的文件做测试。

依据:`docs/devicehands-design.md` 第 5.5 节。

## 规则:改协议先改这里

1. 先改 `protocol/` 里的描述(schema、样例帧、顺序规则),再改两边的代码。
2. 两边的代码必须让 `tests/protocol/` 通过。
3. 描述和代码对不上,而一时不改代码:把问题写进 `v1/known-gaps.md`,对应测试标
   `xfail(strict=True)` 并在 reason 里写条目编号。**不要为了让测试通过去放宽 schema**。

## 里面有什么

```
protocol/
├── README.md               本文件
└── v1/                     现状(协议号 1),只描述,不改行为
    ├── schema/             JSON Schema(draft 2020-12),description 用英文
    │   ├── control.json        任意控制帧(按 verb 分到下面 13 个之一)
    │   ├── hello.json … channel_closed.json   每种控制帧一个
    │   ├── op-table.json       hello 里的操作表:OpDesc、ParamDesc
    │   ├── job-request.json    job 通道上主程序发的请求
    │   ├── job-event.json      job 通道上台架发的事件(下面 4 个之一)
    │   ├── job-accepted / job-progress / job-file / job-done .json
    │   └── common.json         共用定义(v、cid、csecret、错误码…)
    ├── frames/
    │   ├── valid/          合法样例帧
    │   ├── invalid/        非法样例帧,文件名 `<帧>--<非法原因>.json`
    │   ├── index.json      每个样例对应哪个 schema、应该合法还是非法
    │   └── binary/job-frames.json   job 帧逐字节样例
    ├── framing.md          线路格式:job 帧布局、字节流通道、文本帧
    ├── sequences.md        顺序和时间:握手、心跳、开通道、重试、锁、超时
    └── known-gaps.md       现状里的问题清单,v2 设计的输入
```

几点说明:

- schema 的 `$id` 用 `https://devicehands.invalid/protocol/v1/schema/<文件名>`。
  `.invalid` 是保留域名,不会被访问;它只是让文件之间的 `$ref` 能解析。
  校验时把目录里所有 schema 按 `$id` 登记进同一个 registry。
- schema 里以 `x-` 开头的键(`x-direction`、`x-status`)是注释,校验器忽略。
  `x-status: "unused"` 表示定义了但两边都不发。
- schema 按**发送方实际发的内容**写必填项。接收方比 schema 宽松的地方(例如主程序接受缺字段
  的旧台架 hello)写在 schema 的 description 和 `sequences.md` 第 10 节。
- 样例帧里的设备序列号、串口名、主机名、版本号都是编造的(`EXAMPLE0001`、`COM3`、
  `bench-1`…)。
- JSON Schema 管不到的东西(谁先谁后、等多久、锁)在 `sequences.md`,每条带 `file:line`。

## 怎么跑测试

```bash
.venv/bin/pytest tests/protocol -q --no-cov -p no:cacheprovider
```

测试需要 `jsonschema`(以及它依赖的 `referencing`);没有时整个文件跳过。

`tests/protocol/test_golden_frames.py` 查三件事:

1. 描述自身:每个 schema 是合法的 draft 2020-12;`index.json` 和目录里的文件一一对应;
   valid 样例通过、invalid 样例被拒;把 valid 样例去掉一个必填字段后必须被拒
   (确认校验真的在起作用)。新加样例只要登记进 `index.json`,不用改测试。
2. 主程序:`alb.remote.protocol` 的每个帧构造函数输出通过对应 schema;
   `decode_control` 接受每个合法控制帧样例;枚举和 schema 一致;job 帧编解码和逐字节样例一致。
3. 台架:进程内加载 `alb_agent.py`,生成完整 16 行操作表的 hello、各种控制帧和 job 事件,
   全部通过 schema;job 帧编解码和逐字节样例一致。

## 加一种帧或改一个字段

1. 改 `v1/schema/` 下对应文件(没有就新建,并在 `control.json` 或 `job-event.json` 的
   `oneOf` 里登记)。
2. 在 `v1/frames/valid/`、`v1/frames/invalid/` 各加至少一个样例,登记进 `v1/frames/index.json`。
3. 顺序或超时有变化,改 `v1/sequences.md`。
4. 改两边代码,跑上面的测试。

v1 描述的是已经在用的协议,原则上只在「描述写错了」时改。行为变化(版本协商等)进 v2。
