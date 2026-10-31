# G1 资格验证执行记录（2026-10-08，离线/隔离，未安装、零 RF）

按 `docs/reports/2026-10-08-g1-agent-checklist.md` 执行 G1。本轮**接触了板卡**（这是 checklist 要求：ARM 只能由板端原生 gcc 构建），但**没有**加载 Overlay、没有安装或替换任何发布、没有 MMIO/SPI、没有武装 core、没有发射或采集任何 RF。收据 `evidence/rf-teaching/s2/g1-qualification-2026-10-08/stage-checkpoint.json`。

**结论：G1 = PARTIAL。** 清单的 0–3 四个步骤全部完成并留证；清单第 2 节的八行场景中 **7 行已完成**，**第 7 行（真实背压下的 FAULT/owned-core 收尾顺序）未在本 harness 中重跑**，见下「未覆盖」。因此**不宣称 G1 PASS，也不进入 G2**。

## 步骤 0：冻结输入核对

- 51 个 native 输入逐文件 sha256 对比上一轮冻结收据：**零漂移**；17 个 handoff 输入同样零漂移。
- 因此本轮 ARM 产物与 `g1-transport-closure-2026-10-08` 冻结的源码是同一份，可以套用该轮的双审阅结论。

## 步骤 1：隔离原生 ARM 构建

- 板端无并行构建占用；构建走工程原有 `scripts/rf_build_daemon.py`，输出到独立 G1 证据目录，**未覆盖** `rf_overlay/out/rf_daemon`，也**未触碰**已安装发布（`current` 仍指向历史 `692904251e024a1254aecee679c6322a`）。
- 目标集合 `all host-test host-test-core host-test-iq host-test-protocol vendor-integrity`，`BUILD_RC=0`，`vendor integrity: 10 files passed`。
- 套件结果（板端 armv7l，gcc 9.3.0）：transport **64/0**、state **64/0**、daemon **445/0**、startup **92/0**、platform **39/0**、core **98/0**、iq **40/0**、protocol **58/0**，另 `5/0` 一项；`test_selftest` 的两条 FAIL 是 harness **自带的注入负例**（`SELFTEST HARNESS OK`，整体 exit 0），不是回归。
- 额外在**同一隔离源码目录**单独跑 `make host-test-sleep`：**22 PASS / 0 FAIL，exit 0**（清单要求，且不并入 receipt 规定目标集合）。该结果不是凭记忆写的：原始输出保存在 `board-step1-2-captures.log`，收据从该文件读出 22/0/0。
- 产物：32 位小端 ARM ELF，sha256 `4246903635ceee26…`；`verify_observer_build` 通过，识别为 **daemon 1.0.3 / major 1 / minor 5**。
- 构建收据 `source_sha256` 与冻结 51 项**逐项相等**（Windows 反斜杠键名归一化后；`verify_observer_build` 内部也做了同样的归一化）。

## 步骤 2：真实 Linux transport 资格

### harness 的设计（这是本轮唯一新增的 C 源码）

待测的 `conn_read` / `conn_write_all` / stop channel / 发送期限全部位于 `rf_daemon_main.c` 的 `#ifndef PLATFORM_HOST` 区，且都是 `static`；MinGW 根本编不了这一段，所以 Windows 侧只能测「注入 syscall 结果」的可移植策略——**从未跑过真实信号、真实 EAGAIN、真实阻塞 poll**。

到达真实实现有三条路，只有一条诚实：

| 做法 | 问题 |
|---|---|
| (a) 把适配器抄进测试 | 测的是副本，会与生产漂移 |
| (b) 给生产源码加测试缝 | **改冻结产物**——51 个输入已为 G1 冻结，改一个就作废了本轮资格 |
| (c) 测试 TU 直接 `#include` 生产 TU | **本轮采用** |

harness 以 `#define main …` 改名生产 `main` 后 `#include "rf_daemon_main.c"`，用 `-ffunction-sections -Wl,--gc-sections` 丢掉改名后的 `main` 及其独占可达的一切，于是**链接里只多出 transport 本身**。它调用的就是板上跑的那几个函数，没有第二份发送/读算法。51 个冻结输入按 sha256 原样打包上传（`harness_sources.sha256.json`），**未修改任何生产源**。

### 板端实测：**74 PASS / 0 FAIL**（armv7l，Linux 5.4.0-xilinx-v2020.2）

harness 源码 sha256 本地与板端一致 `88df90ea…`；二进制 sha256 `ea6bc4eb…`（**二进制已从板端拉回** `harness_linux.board`，摘要可在本机复算）。

### 独立审阅抓到的一个真缺口：self-pipe 分支本来测不到

质量审阅按要求做变异（改的是**副本**，冻结输入未动），**两个变异在最初的 68 项版本上全部存活（68/0）——我独立复现了。** 根因值得记下来：信号送到**正在 poll 的那个线程**时，poll 返回 EINTR，循环回到顶部由**旗标前置检查**返回 0，**pipe 可读分支从来没有被执行**。也就是说那一版里 self-pipe 的功效**没有任何证据**，而它的存在理由恰恰是「信号由别的线程处理、poll 未被中断」这一类。

修法：新增 `signal_from_other_thread`——在主线程**屏蔽**该信号，再由辅助线程解除屏蔽并 `kill(getpid(), …)`，于是 handler 照常运行（置旗 + 写 pipe），但被阻塞的 poll **不**被中断，只剩 pipe 可读能结束等待。读、发两条路各加一个用例。**无缓冲计时确认该用例确实等满 150 ms 并从 `why=pipe` 返回**（我先前一次时间戳读数是管道缓冲造成的假象）。

| 变异（`rf_daemon_main.c` 副本） | 基线 | 变异后 | 抓住它的断言 |
|---|---|---|---|
| read 的 pipe 分支返回 timeout | 74/0 | **73/1** | `a blocked read is ended by the stop pipe alone` |
| send 只 poll socket | 74/0 | **73/1** | `...well inside the send deadline, so it did not wait for the socket` |
| 正对照：handler 不再写 self-pipe | 74/0 | **70/4** | 上述两条 + `...and it made the self-pipe readable` + `...promptly, so the pipe is what woke it` |

| 清单场景 | 实测 |
|---|---|
| 初始化成功与失败 | 真实 pipe/两端 fcntl/两次 sigaction 成功；两端非阻塞；**无 `SA_RESTART`**；真实 SIGTERM 既置旗又让 self-pipe 可读；cleanup 恢复初始化时的 handler、关闭两个 FD、重复 cleanup 不双关；7 个 syscall 逐个注入失败 → 拒绝启动、**不泄漏 FD**、旧 handler 完好、半成品通道撤销两次安全 |
| 停止时点 | 检查前 / 阻塞 recv 中（**SIGTERM 与 SIGINT 各一遍**）/ **已消费一个分片之后的下一次读** / 40 次跨「检查到阻塞」全窗口采样（两种信号交替），全部及时返回 clean-close |
| 发送背压与停止 | socketpair 缩到 4 KiB、对端不读 → 真实 EAGAIN；**真实 SIGTERM 与真实 SIGINT** 各自在期限内**远早于**发送期限唤醒，停止优先于 socket 就绪 |
| 固定发送期限 | 对端永不读 → 在 **1000 ms 期限**上放弃，而不是 30 s 客户端超时 |
| EINTR/错误 | 死对端判为错误、绝不返回成功 |
| 正常 payload | 9 KB 跨多次 partial send，字节序列**逐字节相同**，且 transport 未开任何自己的 FD |
| 最终收尾 | 真实 `fork` 子进程：**从收到信号**到被 reap **5 ms**，远在 5 s service 预算内 |
| settle 等待中的停止 | 单次中断不放弃有界等待且不超出请求；**10 次中断的 burst 下仍是 ~200 ms**（复算 201 ms）——这就能区分「续睡剩余量」与「每次重睡整个请求」 |

### 未覆盖（**如实列出，是本轮真正的剩余缺口**）

1. **真实背压 socket 下的 FAULT/owned-core 收尾顺序未重跑。** 该顺序由 `test_rf_daemon`（445 项，真 daemon + 真 core + fake PL 跑真 framing loop，含 transport-closure 轮为「故障拒绝回复遇停止／期限超出／poll 错误」新增的 21 项）覆盖，而该套件**已在步骤 1 的板端原生 ARM 构建里 445/0 通过**——但那里的 transport 结果是**注入**的，不是真实 socket。
2. **「停止后已排队的完整请求不得派发」同样只由 `test_rf_daemon` 覆盖**：那里停止旗是从**读回调内部**置起的（不是在 serve 前预设布尔值），但仍不是真实异步信号。
3. **发送侧「无停止的 EINTR 重试共用期限」只在可移植策略层被测**（宿主 64 项）；Linux harness 驱动了真实 send，但没有制造一次「无停止的 EINTR」。

## 步骤 3：准确候选离线分析资格

- 用步骤 1 的准确 ARM ELF + 现有环境模型/冻结 baseline 构建候选 `adfac1f14c3cdb379cb92569a21a9054`（父发布 `c3bf915c…`，契约 `cnn-bpsk-environment-v1`，daemon 1.0.3/minor5，**`installed: false`**）。
- 板端隔离运行（候选传 `/tmp`，**已安装板载软件未动**）：
  - 主机 / ARM 的**环境分析记录**按 `rf_teaching_verify.compare` 的规则判等：键集合必须相同、列表等长且逐元素、所有浮点必须有限且 `|a-b| ≤ 1e-6`、其余值类型与数值必须相等；`parity_mismatches: []` 的含义是该规则通过，**不是**「两侧逐字节相同」（实测两侧确实存在末位浮点差，正是该容差允许的）。**该比较只覆盖环境分析记录**；解调、星座、CNN 三段跑在 100 次基准环里，其时延在 `timings_ms`/`component_timings_ms`，**不在被比较的记录内**，且只在 ARM 侧运行；
  - 100 次完整 base/CNN/BPSK/星座/环境诊断路径 **P95 = 290.81 ms**（≤300 ms 后台计算预算内），**max = 441.83 ms**（离群值一并记录，不藏），峰值 **RSS = 30816 KiB**，`native_cnn: true`；
  - `formal_physical` 与 `human_acceptance` 均 **NOT_RUN**；旧 IQ 派生不计新 RF 帧；本轮新增物理帧 **0**。

## 独立审阅

两项独立审阅（规范、质量）均为**只读**，未代跑 ARM、未接触板卡。

**规范审阅：首轮 CHANGES_REQUIRED（6 项），修复后重审 APPROVED。** 首轮发现的关键一类是**收据完整性**——我第一版 `_receipt.py` 把 `make host-test-sleep` 的 22/0 写成字面量、把 `verify_observer_build` 结论写成一句话、把板端摘要重新手打。这些数字**当时都是真的**，但没有任何产物能让人复算，等于「声明」而不是「证据」。已全部改为**从产物推导**：sleep 结果读自新捕获的 `board-step1-2-captures.log`；observer 结论由脚本真正调用 `verify_observer_build` 取回；harness 的源码与**二进制**摘要均从磁盘文件计算，且**二进制已从板端拉回**（`harness_linux.board`）使摘要可在本机复算。另三项：读侧停止补了 **SIGINT**（原只有 SIGTERM）；settle 等待的时限断言改为**可判别**的 10 次中断 burst（原来 `< 600 ms` 无法区分「续睡剩余量」与「每次重睡整个请求」）；退出预算的 `t0` 改到 `kill()` 时刻（原先把信号前的 `usleep` 也算进去，把真实 5 ms 报成了 155 ms）。

复验后的三条 LOW 残留也已一并修掉：板端摘要改从**改动之后**新捕获的日志读取并修正了键值方向；`not_covered` 补上「SIGINT 未覆盖其余三个时点」；去掉一处字面 `\n`。

**质量审阅：首轮 CHANGES_REQUIRED，修复后问题全部处置。** 它的核心发现就是上面那个 self-pipe 缺口，并用了**正对照**（handler 不再写 pipe，必须失败 → 4 FAIL）与**负对照**（同一行做字节等价改写，必须仍全绿 → 74/0）来证明结论不是陈旧二进制或过敏感造成的；它还独立用线性插值复算了 100 次迭代，得到 p50/p95/max 与收据**逐位相同**。其余发现也一并修掉：

- **证据矛盾**：step-1 的捕获日志里混着上一代 harness 的摘要，与 step-2 记的互斥，同一份收据指向两个 harness。已重做 step-1 捕获（只含 sleep 结果/工具链/`current`），harness 摘要只留在 `board-step2-digests.log`。
- **parity 无判据**：已把判等规则与覆盖范围写进收据（见步骤 3）。
- **注释与实测相反**：读侧停止用例的注释原称覆盖整个窗口，实际由旗标结束——已改述，并注明 pipe 分支由独立用例覆盖。
- **测试内数据竞争**：settle burst 的 10 个线程原先共用一个 `struct killer`，主线程在创建之间改字段；已改为每线程一个。
- **卫生**：删除死代码（未调用的 `drain_stop_pipe`、未读取的计数器、未使用的变量），8 处未检查的 `pthread_create` 归并为受检路径（失败即 `exit(2)`，不再对未初始化的 `pthread_t` 做 join）；采样循环在 socketpair 创建失败时不再把资源失败归因成时序失败。
- **报告指向不存在的字段**：`reviews` 字段现已真正写入收据。

**质量审阅最终的剩余项**（非阻塞，已如实记录）：parity 比较覆盖分析记录而非 100 次测量；SIGINT 未覆盖清单的其余三个时点（已在收据 `not_covered` 列出）。

## 明确 NOT_RUN

**G2 安装与 8 帧物理对照**、S2 正式 freeze/108 实板 + 12 离线 TEST、真人验收、S3 —— 全部未开始。**削顶根因仍未证明**，本轮不作任何相关声明。

## 下一步

1. 由人决定是否接受「第 7 行为组合缺口」并进入 G2，或先补一个真实 socket 下的 FAULT/owned-core 组合用例。
2. G2 只做：安全安装**已通过本轮资格的准确候选**、按批准声明一次最多 8 帧对照、两会话各自解除 ACK、独立读回最终安全态。
3. 三个时限继续分开记录，不互相冒充：**1 s 发送期限**、**5 s service 退出预算**、**5 ms 采集后测量资格上限**。
