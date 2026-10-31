# G1 agent执行交接清单（2026-10-08）

本轮只完成transport离线补齐。G1由用户后续安排agent执行，当前 G1/ARM/Linux runtime 为 NOT_RUN；G2安装/RF不在G1范围。先读本轮报告与 `evidence/rf-teaching/s2/g1-transport-closure-2026-10-08/stage-checkpoint.json` 的最终复审状态。

## 0. 输入与边界

- 核对本轮 `source-sha256.json` 全部51 native输入，以及 `handoff-source-sha256.json` 中采集/候选工具输入。冻结源码在 `package-check/sources/`。任何漂移需重新审阅和资格化，不能套用旧收据。
- 当前源码daemon1.0.3/major1 minor5；最后核验过的安装是692904251e024a1254aecee679c6322a（1.0.2/minor4），这是历史读回，本轮未连板重新检查。
- 按适用ac820-pynq技能使用Paramiko helper。可隔离构建/运行假硬件与socket测试；不得加载Overlay、直接MMIO/SPI、武装真实core、替换current或已安装发布、开真实RF。
- 保留G1失败与原始日志；不要无说明覆盖。当前dirty tree不reset/stash，不自动commit。

## 1. 隔离原生 ARM 构建

核对输入后，使用工程原有builder，输出到独立G1证据目录，不能覆盖 `rf_overlay/out/rf_daemon`。下列是**待G1 agent执行的命令，本轮未运行**：

```powershell
python scripts/rf_build_daemon.py --board-app rf_overlay/sw/board_app --out evidence/rf-teaching/s2/g1-qualification-2026-10-08/rf_daemon --keep-scratch
```

builder使用专用 `/home/xilinx/rfbuild/build_script_src`，不安装发布。先确认该build scratch没有其他并行构建占用；如有，占用结束后再执行或为本次构建设置独立scratch，不互相清除。执行的是 `make -j1 all host-test host-test-core host-test-iq host-test-protocol vendor-integrity`。`host-test → host-test-daemon → host-test-transport` 已确保新增策略测试会运行，daemon生产链接也包含同一policy。

必须留存ARM ELF（32位little-endian ARM）、`.build.json`、`.build.log`、完整51源码输入SHA，日志含BUILD_RC=0/vendor10；通过 `scripts.rf_environment_candidate.verify_observer_build` 对准确产物/输入验证。另在同一隔离源码目录执行 `make host-test-sleep`，保留独立结果；不得为了凑结果改变receipt规定target集合。预期各C用例数见本轮报告；内层selftest负例与整体exit区分。

## 2. 真实 Linux transport 资格

Windows当前只证明共享策略和假core收尾。现有portable策略测试**不含真实异步信号/socket syscall**。G1 agent应在隔离测试目录构建Linux harness，调用生产 main的POSIX适配器/真实handler（通过测试translation unit引入同一实现或等价的最小受审测试缝），不能复制一份不同的发送/读算法，也不能用PLATFORM_HOST把待测POSIX适配层排除。该harness尚未提供或运行，编译/运行和测试源码SHA属于G1交付。

| 场景 | 具体观测与通过条件 |
|---|---|
| 初始化成功与失败 | 正常pipe/两端fcntl/两次sigaction成功；逐项注入失败（包括无FD/部分FD），服务拒绝启动、不进入listener/控制获取；检查FD数、已装handlers恢复、撤销handler可见FD后再关闭、重复cleanup无双重关闭 |
| 停止时点 | SIGTERM与SIGINT分别覆盖检查前、检查后/阻塞recv前、阻塞recv中、分片请求之间、完整行派发前、settle等待中；停止后已排队控制请求不能派发。实际异步送信号，不用serve前设置布尔值替代 |
| 发送背压与停止 | socketpair/本地TCP缩小发送缓冲，客户端不读、部分读、慢读产生EAGAIN/partial；实际SIGTERM/SIGINT唤醒生产socket+self-pipe poll。停止优先于socket就绪，进入断连和退出收尾，不等原30秒网络超时 |
| 固定发送期限 | 非空write从首次取得起始时钟、首次send之前计时，整体1000ms；每次partial/EAGAIN/EINTR只用剩余额度。最后send后也检查期限；报告实际耗时与系统调度偏差，不能通过缓慢进度反复刷新。该期限不是从首次EAGAIN开始 |
| EINTR/错误 | 无停止的send/poll EINTR可重试但共用总期限；伴停止立即失败；零send、硬send错误、poll错误/HUP/NVAL、时钟失败均不返回成功 |
| 正常payload | 无停止无错误时partial发送拼接字节与输入完全相同；未完成/超时/停止不能记成成功frame或成功capture trace |
| FAULT与拥有资源 | 故障core与错误回复背压组合，观察假的TX source/RX arm先清除，随后owned fake core归还，关闭确认/失败原因保留；不以进程退出代替假寄存器与释放顺序证明 |
| 最终收尾 | 真实Linux进程退出符合现有5秒service预算；记录停止发送时刻、poll返回、资源关闭、退出的时点。1秒发送期限、5秒退出预算、5ms采集后测量资格上限分开记录 |

测试只链接/运行假硬件，禁止执行生产main的真实设备开控制路径。不为验证而调用Overlay或武装RF。

## 3. 准确候选离线分析资格

使用通过前两步的准确ARM ELF及现有环境模型/冻结baseline构建候选（不安装），复核candidate/source/build门禁；主机和ARM同IQ分析一致性。运行完整base/CNN/BPSK/星座/环境诊断路径100次，报告实际P95、峰值RSS、原生模型状态，按现有≤300ms后台计算预算判断。用户已取消专门触摸计时；不把纯计算时间写成触摸延时。

旧IQ离线派生不计新RF帧。候选尚未冻结不得做8帧物理campaign；本阶段成功只能证明G1资格。

## 4. 输出与交接

- 绑定冻结输入、ARM ELF SHA、构建/harness源码与原始日志/退出码。
- Linux场景逐项结果、实际信号时点、耗时/deadline/FD/fake core收尾证明；失败逐项保留。
- 准确候选、同IQ与100次完整资源收据；规范和质量复审意见。
- 明确 `G1 PASS` 或失败具体项，同时列 `G2 NOT_RUN`。全部门禁通过后再交用户安排G2安装与8帧对照，不自行开始RF。

本轮主机验收：policy64/daemon445/main92/state64/platform39/core98/iq40/protocol58/sleep22，相关Python78，vendor10；这些数字不代替G1新结果。
