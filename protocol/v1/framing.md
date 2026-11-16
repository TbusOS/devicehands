# v1 线路格式:WebSocket 消息、job 帧、字节流通道

本文只写 v1 的现状(仓库 `p0/baseline`,HEAD `a27ad4a`;`src/`、`clients/` 与 `e36c43f` 相同),不提改法。改法的输入在
[known-gaps.md](known-gaps.md)。出处里 `P` = `src/alb/remote/protocol.py`,
`J` = `src/alb/remote/jobframe.py`,`AR` = `src/alb/api/agent_route.py`,
`F` = `src/alb/remote/forwarder.py`,`FL` = `src/alb/remote/flash.py`,
`A` = `clients/windows-agent/alb_agent.py`。

## 1. 两类连接,四种用途

```
 台架                                       主程序(hub)
 ────                                       ───────────
 信令连接  ws(s)://<hub>/agent/connect ───▶  JSON 文本消息,一条消息 = 一个控制帧
 数据连接  ws(s)://<hub>/agent/channel?cid=  一个 open_channel 对应一条
   ├ tcp     (adb)    二进制消息 = 原始字节流
   ├ serial  (串口)   二进制消息 = 原始字节流
   └ job     (作业)   二进制消息里承载 job 帧流(第 3 节)
```

数据连接永远由台架拨出,主程序不主动连台架(A:456-464 拼 URL,AR:279 收)。

## 2. 信令连接:控制帧

- 每个控制帧是一条 WebSocket **文本**消息,内容是一个 JSON 对象。主程序用
  `json.dumps(ensure_ascii=False)` 编码(P:273-275),台架同样写法(A:404-405),
  所以非 ASCII 字符按 UTF-8 原样出现。接收方只按 JSON 解析,不依赖空白和键序。
- 外壳固定两个字段:`v`(协议号,恒为 1)和 `verb`(帧类型),其余字段平铺在同一层
  (P:182-183,A:404-405)。每种 verb 的字段见 `schema/<verb>.json`。
- 一条消息只放一个帧,不拼接、不分片。
- 接收方对**未知字段**:两边都按字段名取值,不认识的字段不读,也不报错
  (AR:187-226 全用 `.get`;A:5322-5347 同)。
- 接收方对**二进制消息、非 JSON、未知 verb**:两边处理相反,见 known-gaps G7、G8。

单条消息的大小上限没有在代码里写,用的是 WebSocket 库默认值:台架信令连接
`ws_connect(args.hub_url)` 未设 `max_size`(A:5270),本机 `.venv` 里
websockets 16.0 的默认是 1 MiB;主程序由 uvicorn 收,本机 uvicorn 0.44.0 默认
`ws_max_size` 16 MiB(`server.py:254-260` 没覆盖)。台架机器上实际装的库版本没验证。

## 3. job 通道:帧格式

job 通道的数据连接上,两个方向的二进制 WebSocket 消息拼起来是一条连续字节流,
字节流被切成 job 帧。**帧边界和 WebSocket 消息边界无关**:接收方必须自己缓冲、
重组(J:80-145,A:772-815)。现有两端发送时恰好一条消息放一个完整帧
(FL:853、FL:933-936;A:766-769 的结果整段 `send`),接收方不能依赖这一点。

### 3.1 帧头

| 偏移 | 长度 | 字节序 | 字段 | 含义 |
|---|---|---|---|---|
| 0 | 1 | — | kind | `0x43`(ASCII `C`)= 控制帧;`0x44`(ASCII `D`)= 数据帧 |
| 1 | 4 | 大端,无符号 | length | payload 的字节数,0 ~ 8 388 608(8 MiB) |
| 5 | length | — | payload | C:UTF-8 编码的 JSON 对象;D:原始字节 |

出处:`struct.Struct(">cI")`(J:46,A:734),kind 常量 J:38-39 / A:735-736,
上限 `MAX_FRAME = 8 * 1024 * 1024`(J:44,A:737)。

### 3.2 接收规则

| 情况 | 两边的处理 | 出处 |
|---|---|---|
| 读帧头时连接干净结束(缓冲为空) | 正常结束(EOF) | J:101-103,A:780-782 |
| 帧头或 payload 读到一半连接结束 | 错误 "closed mid-frame" | J:135-141,A:805-811 |
| kind 不是 C / D | 错误 | J:105-106,A:784-785 |
| length > 8 MiB | 错误,不分配缓冲 | J:107-108,A:786-787 |
| 该收控制帧时收到数据帧 | 错误 | J:125-126,A:796-797 |
| C 帧 payload 不是 JSON 对象 | 错误 | J:127-132,A:798-800 |

发送侧只有主程序检查 8 MiB 上限(J:65-66);台架的 `_job_encode` 不检查(A:762-763),
见 known-gaps G14。

### 3.3 控制帧的 JSON 部分

- 主程序 → 台架:作业请求,只有一个,是通道上的第一帧(`schema/job-request.json`)。
- 台架 → 主程序:事件,`ev` 取 `accepted` / `progress` / `file` / `done`
  (`schema/job-event.json`)。
- 两端编码都用紧凑分隔符 `separators=(",", ":")` 且 `ensure_ascii=False`
  (J:73,A:766-769)。这只是现有写法,接收方按 JSON 解析,不比较字节。

### 3.4 数据帧

- 只有主程序发,只在 image 类操作(`OpDesc.image = true`)的请求帧之后发。
- 主程序每块 64 KiB(FL:71、FL:921-933),最后发一个 **length = 0 的 D 帧**表示镜像发完
  (FL:936)。
- 台架按请求里的 `size` 收:收满 `size` 字节就停止读,不再等那个 0 长度结束帧
  (A:3564);先收到 0 长度 D 帧就提前结束接收,再按大小和 sha256 判定(A:3577-3578、
  A:3594-3600)。收到的累计超过 `size` → `FLASH_IMAGE_CORRUPT`(A:3579-3583)。

### 3.5 样例字节

`frames/binary/job-frames.json` 里有逐字节的样例,两端的编码器和读取器都对它测过
(`tests/protocol/test_golden_frames.py`)。例如请求 `{"op":"devices"}`:

```
43 00 00 00 10  7b 22 6f 70 22 3a 22 64 65 76 69 63 65 73 22 7d
C  len=16       {"op":"devices"}
```

镜像结束帧:`44 00 00 00 00`。

## 4. tcp 和 serial 通道:原始字节流

- 两个方向都是 WebSocket **二进制**消息,内容就是要转发的字节,没有任何帧头
  (F:212-251 主程序侧,A:649-706 台架侧)。
- 发送方每次最多发 64 KiB(台架 `_CHUNK = 65536` A:112;主程序 `_CHUNK = 65536` F:42)。
  接收方不依赖这个大小。
- tcp 通道只转发台架本机的 adb server `127.0.0.1:5037`,台架自己再查一遍白名单
  (A:82-83、A:545-551)。
- serial 通道的一端是台架上打开的串口(A:618,`timeout=0.05`、`write_timeout=2`)。
  主程序侧所有本地连接共用这一条通道,收到的字节复制给每个本地连接,
  写入按到达顺序合并(F:296-403)。

## 5. 文本消息、空消息在数据连接上的处理

这两条规则只存在于实现里,第三方实现必须照做,否则会被对端当成连接结束:

| 收到 | 主程序 | 台架 |
|---|---|---|
| 文本消息(tcp / serial / job) | 当作连接结束(EOF)(AR:110-114) | 转成 UTF-8 字节,当数据处理(A:667-668、A:698-699、A:806-807) |
| 长度为 0 的二进制消息 | 当作连接结束(AR:103-112 返回 `b""`,调用方按 EOF 处理) | job 通道当作连接结束(A:808);tcp / serial 写入 0 字节,无效果 |

所以:**发送方不得在数据连接上发文本消息,也不得发 0 字节的二进制消息。**
现有两端都满足:台架读到空数据就不发(A:659-660、A:689-690),主程序的结束帧是
5 字节的 `44 00 00 00 00`,不是空消息(FL:936)。

## 6. WebSocket 层的其他参数

| 项 | 值 | 出处 |
|---|---|---|
| 压缩(permessage-deflate) | 主程序默认关,`ALB_WS_DEFLATE=1` 才开 | `src/alb/api/server.py:245-260` |
| 台架数据连接收消息上限 | 不限(`max_size=None`) | A:573、A:638、A:3220 |
| ping / pong | 两端都用库默认值,代码没设。本机 `.venv`:websockets 16.0 为 20 s 间隔 + 20 s 超时;uvicorn 0.44.0 为 20 s + 20 s | FL:870-875 的注释记录了台架侧这个超时引起过的问题;台架实际库版本没验证 |
