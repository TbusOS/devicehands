# 测试基线

记录 P0 阶段一次全量测试的结果,作为以后判断「改动有没有把东西改坏」的对照。
依据:`docs/devicehands-design.md` 第 9 节 P0 行(「本仓库建测试环境并记下基线」)。

## 1. 这次跑的是什么

| 项 | 值 |
|---|---|
| 日期 | 2026-11-16 |
| 提交 | `e36c43f`(`git rev-parse --short HEAD`),分支 `p0/baseline` |
| 工作区 | `pyproject.toml`、`uv.lock` 有未提交改动:开发依赖加了 `import-linter>=2.0`,不涉及被测代码 |
| 系统 | Linux 5.4 x86_64 |
| Python | 3.11.15(仓库内 `.venv`) |
| pytest | 9.0.3 |
| 本机外部工具 | 有 `bash`、`node`、`adb`、`fastboot`;没有 `mksh` |

命令(在仓库根目录执行):

```bash
.venv/bin/pytest -q --no-cov -p no:cacheprovider
```

- `--no-cov`:关掉 `pyproject.toml` 里 `addopts` 默认开的覆盖率统计,省时间,也不在仓库里生成 `htmlcov/`。
- `-p no:cacheprovider`:不写 `.pytest_cache/`,跑测试不改仓库目录。

## 2. 结果

| 项 | 数 |
|---|---|
| 收集到 | 3037 |
| 通过 | 3036 |
| 失败 | 0 |
| 报错(error) | 0 |
| 跳过 | 1 |
| 警告 | 2 |
| 耗时 | 218.90 秒(pytest 自报) |

同一天用同一条命令加 `-rfEs --durations=30`(只多打印报告,不改变跑哪些测试)又跑了一次,
数字完全相同,耗时 224.71 秒。最慢的一条是
`tests/remote/test_agent_update_e2e.py::test_a_build_stuck_before_its_first_log_line_is_written_down_then_rolled_back`,11.59 秒。

没有测试卡住,没有用 `--deselect` 排除任何测试。

运行时机器上有别的服务占着 8765 端口(也可能占 5037、9001)。这次没有测试因为端口被占而失败。

## 3. 失败和报错的分类

失败 0 条、报错 0 条,下表各类都是 0。保留这张表,是为了以后出现失败时按同样的类别归档。

| 类别 | 条数 | 代表测试 | 报错原文 |
|---|---|---|---|
| 缺构建产物(如没构建的网页) | 0 | — | — |
| 缺外部工具(node、adb 等) | 0 | — | — |
| 端口被占 | 0 | — | — |
| 依赖真实硬件或真实配置 | 0 | — | — |
| 环境差异 | 0 | — | — |
| 其他 | 0 | — | — |

## 4. 跳过的分类

| 原因 | 条数 | 测试 | 跳过原文 |
|---|---|---|---|
| 缺外部工具 `mksh` | 1 | `tests/transport/test_exit_marks.py::test_setup_on_real_mksh` | `needs mksh and a pty` |

## 5. 警告

2 条,都是第三方库的弃用提示,出自同一条测试 `tests/api/test_ws_drain_fix.py::test_long_push_with_pings_survives`:

- `websockets.server.WebSocketServerProtocol is deprecated`(uvicorn 导入 websockets 旧接口)
- `remove second argument of ws_handler`(websockets 旧版服务端)

不影响结果。升级 uvicorn 或 websockets 时要留意。

## 6. 以后怎么判断一次改动有没有把东西改坏

判断依据:**用上面同一条命令跑,失败集合不新增。**

- 失败集合 = 结果为失败或报错的测试 ID 的集合。本基线的失败集合是空集。
- 改动后跑出的失败集合里,只要有一条不在基线里,就算改坏了,不管总通过数是否变多。
- 只看「通过数」不够:新加 10 条通过的测试、同时弄坏 1 条旧测试,通过数照样上涨。
- 跳过数变化要单独看一眼:一条测试从「通过」变成「跳过」,失败集合不变,但它已经不再检查任何东西。

取失败集合的做法:

```bash
.venv/bin/pytest -q --no-cov -p no:cacheprovider -rfE > after.txt 2>&1
grep -E '^(FAILED|ERROR) ' after.txt | awk '{print $2}' | sort -u
```

`-rfE` 只是让 pytest 在末尾列出失败和报错的测试 ID,不改变哪些测试会跑、结果是什么。

这份基线在什么条件下成立:

- 只对这台机器的这套工具成立。没装 `node`、`adb`、`fastboot` 的机器,结果可能和这里不同,本次没有在那样的机器上验证。
  三个系统各自的基线,要等 P0 的自动构建跑起来后分别记。
- 测试数会随提交变化。换了基线提交,就重跑一次,更新本文件的提交号和数字。

## 7. 基线更新记录

同一条命令,同一台机器。失败集合始终是空集。

| 日期 | 提交 | 收集到 | 通过 | 跳过 | 预期失败(xfail) | 失败 | 变化原因 |
|---|---|---|---|---|---|---|---|
| 2026-11-16 | `e36c43f` | 3037 | 3036 | 1 | 0 | 0 | 首次记录 |
| 2026-11-16 | `1caf3f6` | 3294 | 3288 | 1 | 5 | 0 | 新增 `tests/protocol/`:252 条通过,5 条预期失败,各对应 `protocol/v1/known-gaps.md` 里的一条 |
