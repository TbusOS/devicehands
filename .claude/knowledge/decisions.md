# 决策档案(已合并)

> 2026-11-16 起,决策记录已合并到 [`docs/design-decisions.md`](../../docs/design-decisions.md),编号不变。
> 新的决策写到那份文件,不再写到这里。
>
> - 本文件原有的 ADR-018~070 原文搬到了那份文件,按编号排在 017 之后。
> - 本文件原有的 007、016、017 简版,附在那份文件同号条目的末尾;本文件的 001(Transport 抽象)附在那份文件 ADR-004 的末尾。
>
> 下面是本文件合并前开头的说明和索引,原样保留,等所有者决定去留。其中的索引已经过期,以那份文件开头的索引为准。

---

# 决策档案（ADR 风格）

每个重大决策一篇，agents 评审时用作"为什么这么设计" 的权威来源。
新决策由主对话写入，agents 不写。

格式：
- 标题 / 日期 / 状态（accepted / superseded by N / rejected）
- 上下文：当时面临什么问题
- 决策：选了什么
- 备选：考虑过的其他方案
- Trade-off：放弃了什么、获得了什么
- 后续可能推翻条件：什么情况下应该重新评估这个决策

---

## 索引（按主题 · 47 ADR + 5 seed · 2026-05-26 update）

**核心架构（transport / backend / event）**
- ADR-001 · Transport 抽象 + 4 实现
- ADR-016 · LLMBackend ABC
- ADR-024 · LLMBackend ABC capability 改用 class attribute（amends ADR-016）
- ADR-018 · audit 升级为事件总线（C 档重构）
- ADR-026 (seed) · backend 配置边界：CLI flag 还是 config 文件
- ADR-027 · BackendSpec.runs_on_cpu → host_compute_type 三态
- ADR-033 (seed) · transport capability 用 ABC class-attr 标，不 hasattr 检测
- ADR-035 · LlamaCpp 嵌入式 backend deferred · 改 step 顺序 step 2/3 互换

**流 / 协议（WS / SSE / chat / stream）**
- ADR-007 · long log 不进 LLM context
- ADR-019 · token 事件不广播
- ADR-021 · token 不广播但聚合 tps_sample
- ADR-022 · Dashboard 同页双 WS 实例
- ADR-030 (seed) · stream hook 抽象时机评估
- ADR-036 · 流式文件传输用 WS 而非 SSE / Job-Model（MID-6）
- ADR-037 (seed) · transport 配置常量（retry / timeout / backoff）用 class attribute
- ADR-045 · `lib/ws.ts` shareKey opt-in pool + late-joiner microtask replay（关 DEBT-047）
- ADR-046 · `lib/ws.ts` pool view.send() epoch-scoped payload dedup（5/26 AR-1 · 关 ui-f HIGH-1 reconnect storm）
- ADR-047 · `lib/ws.ts` Pool-level Options first-caller-wins 语义（5/26 AU-4 · 关 arch MID-4）

**Web Tier / 前端**
- ADR-017 · Web Tier 1 技术栈
- ADR-023 · SPA fallback 跨部署 surface 异构实现
- ADR-025 · per-backend 并行 useQueries · Dashboard 健康探测 polling 分层
- ADR-028 · device 信息分层 · dashboard summary vs inspect details
- ADR-029 · device 信息刷新策略 · auto polling vs button vs WS push
- ADR-032 · Inspect 8 tabs 走 unmount/remount，不做 keepAlive

**安全 / 部署**
- ADR-031 (seed) · filesync HITL 写在 endpoint 层 vs PermissionEngine
- ADR-034 (seed) · alb-api 默认 bind 127.0.0.1，0.0.0.0 须显式开启

**工程方法 / 团队**
- ADR-020 · agents 团队工程方法论

---
