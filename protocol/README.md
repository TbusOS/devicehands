# protocol/:主程序和台架服务之间的协议描述

这个目录用与编程语言无关的方式,描述主程序(hub,`src/devicehands/remote/`、
`src/devicehands/api/agent_route.py`)和台架服务(`bench-py/dhands_bench.py`,以后还有
Rust 版)之间的 WebSocket 协议。两个实现都拿这里的文件做测试。

依据:`docs/devicehands-design.md` 第 5.5 节。

## 现在用的是 v2

| 目录 | 是什么 |
|---|---|
| [`v2/`](v2/) | 现行协议。主程序和本仓库的台架程序只讲 v2 |
| [`v1/`](v1/) | 历史:改名前的协议现状描述和问题清单(`known-gaps.md`),是 v2 设计的输入。不再改,代码也不再按它测 |

v2 和 v1 不兼容,握手时协商版本,对不上就明确拒绝(`v2/DESIGN.md` 第 2、3.1 节)。

## 规则:改协议先改这里

1. 先改 `v2/` 里的描述(schema、样例帧、`patterns.json`、顺序规则),再改两边的代码。
2. 两边的代码必须让 `tests/protocol/` 和 `tests/conformance/` 通过。
3. 只加可选字段、新操作、新错误码不升版本;删字段、改含义、改必填项、给固定集合加值要升版本
   (`v2/sequences.md` 第 2 节)。**不要为了让测试通过去放宽 schema**。

## v2 里有什么

```
protocol/v2/
├── DESIGN.md           为什么这么定(和 v1 的差别、没选的做法)
├── schema/             JSON Schema(draft 2020-12),description 用英文
│   ├── control.json        任意控制帧(按 verb 分到下面 11 个之一)
│   ├── hello.json … channel_error.json   每种控制帧一个
│   ├── op-table.json       hello 里的操作表:OpDesc、ParamDesc
│   ├── job-request.json    job 通道上主程序发的请求
│   ├── job-event.json      job 通道上台架发的事件(下面 4 个之一)
│   ├── job-accepted / job-progress / job-file / job-done .json
│   └── common.json         共用定义(cid、id、错误码、版本范围…)
├── frames/
│   ├── valid/          合法样例帧
│   ├── invalid/        非法样例帧,文件名 `<帧>--<非法原因>.json`
│   ├── index.json      每个样例对应哪个 schema、应该合法还是非法
│   └── binary/job-frames.json   job 帧逐字节样例(与 v1 相同)
├── patterns.json       参数规则正则子集的样例:子集内、子集外、(规则, 输入, 结果)
├── framing.md          线路格式:连接、控制帧、job 帧、字节流通道、正则子集
└── sequences.md        顺序和时间:握手与版本协商、心跳、开通道、重试、作业、锁、错误码
```

几点说明:

- schema 的 `$id` 用 `https://devicehands.invalid/protocol/v2/schema/<文件名>`。
  `.invalid` 是保留域名,不会被访问;它只是让文件之间的 `$ref` 能解析。
  校验时把目录里所有 schema 按 `$id` 登记进同一个 registry。
- schema 里以 `x-` 开头的键(`x-direction`)是注释,校验器忽略。
- 控制帧的 schema 不禁止多出来的字段:接收方忽略不认识的字段(`v2/framing.md` 第 2 节)。
- 样例帧里的设备序列号、串口名、主机名、版本号都是编造的(`EXAMPLE0001`、`COM3`、`bench-1`…)。

## 测试

```bash
.venv/bin/pytest tests/protocol tests/conformance -q --no-cov -p no:cacheprovider
```

| 文件 | 查什么 |
|---|---|
| `tests/protocol/test_v2_description.py` | 描述自身:schema 合法、样例和 `index.json` 对得上、合法样例通过非法样例被拒、去掉必填字段后被拒、`patterns.json` 的答案成立 |
| `tests/protocol/test_v2_hub.py` | 主程序:帧构造函数的输出通过 schema;解码器接受全部合法样例、拒绝全部非法样例;正则子集检查和 `patterns.json` 一致;job 帧编解码和逐字节样例一致 |
| `tests/protocol/test_v2_bench.py` | 台架程序(进程内加载):hello、各种控制帧和 job 事件通过 schema;正则子集检查和 `patterns.json` 一致;job 帧编解码和逐字节样例一致 |
| `tests/conformance/` | 一致性测试:台架当独立进程启动,只经协议交互,对面是测试自带的主程序替身。启动命令可换,Rust 版用同一套 |

测试需要 `jsonschema`(以及它依赖的 `referencing`);没有时这些文件跳过。

## 加一种帧或改一个字段

1. 改 `v2/schema/` 下对应文件(没有就新建,并在 `control.json` 或 `job-event.json` 的
   `oneOf` 里登记)。
2. 在 `v2/frames/valid/`、`v2/frames/invalid/` 各加至少一个样例,登记进 `v2/frames/index.json`。
3. 顺序、超时或错误码有变化,改 `v2/sequences.md`。
4. 改两边代码,跑上面的测试。
