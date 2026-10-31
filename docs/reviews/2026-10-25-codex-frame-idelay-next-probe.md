# Frame IDELAY 能力补充核验与下一次实验边界

结论：**SCOPED_CAPABILITY_CONFIRMED_FOR_FRAME_PROBE**。本对话 Codex 使用 Vivado 2025.2 离线打开原 routed DCP，确认 frame IDELAY 的 LD 连接为 `idelay_en[6]`，公共 CNTVALUEIN 为 `idelay_tap[4:0]`；数据 lane0..5 各连接对应 LD 位。支持当前已安装 tap-only 接口的 `mask=0x40` 帧路隔离研究。该结论不代表物理采样裕量、IDELAYE2 CNTVALUEOUT 或接收正确性已通过。

## 实际证据

- `evidence/codex-frame-idelay-check-20261025/verification.json`：实际输入摘要、逐脚连接、载荷比对。
- `evidence/codex-frame-idelay-check-20261025/vivado-permitted.log`：真实 Vivado 2025.2 输出，exit 0。
- DCP：`<LOCAL_PATH>`。
- 原同次构建的 `host_bd_wrapper.bit` 与当前 `6384a07e...` 发布 bit 从 sync 到文件末尾逐字节相同。构建关系复用 DSH 已核对的 impl_1 runme 记录；没有另行重导出 bit。
- 7 个 IDELAYE2；frame cell `IDELAY_X1Y72` 的 LD 网为 bit6，源追踪到 AXI slv_reg0[6]，公共 tap 源为 slv_reg1[4:0]。
- `vivado2025.cmd config check` 返回有效 2025.2 路径；旧报告“只有 2018.3”已不适用。本次首次受限启动因用户 apps/cache 初始化失败；允许启动缓存写入后，离线查询成功。

## 方案纠正

`rf_core_configure` 会在所有实际参数相同时提前返回，不执行 vendor setters。同参重复 configure 不能作为“再次执行 AD9361 重配置”的实验。下一轮优先只动 frame 延迟，继续排除 AD9361 重配置混杂。不新增 force-reconfigure 接口。

## 建议有界工作包

复用已部署 `6384a07e2d7d135bfb021b9ac75f337f`，不安装、不重建 bit。先用假链验证 runner 的成功恢复、失败恢复、恢复失败不虚报、先关闭 RF 再分析、接线/次数检查。然后一次主研究会话：全路29基线一帧 → `idelay.configure(0x40,25)` → frame25一帧 → 显式全路29恢复 → 关闭并独立读回。必要时仅允许一次已明确计入预算的恢复初始化，不追加采集。

研究预算建议：主会话1、总 init≤2（第二次仅恢复）、rf.configure≤2（主初始与必要恢复）、tx.load≤1、idelay.configure≤2（探测和正常恢复）、digital capture≤2、source激活≤2、physical=0、研究重试=0、总实验≤180秒。失败时 core FAULT/连接丢失不得用普通控制绕过，应先安全收尾并确认后按预留恢复流程处理。

若 frame-only 导致超时而同会话基线成功，表明在不重新配置 AD9361 的条件下，改变 frame 延迟足以复现失效，是 frame 采样/有效条件路径的强定位线索；单组结果不宣布唯一根因。若两帧均完成，不等于排除了全路延迟交互或 AD9361 影响，下一步另设计实际非幂等配置对照。

本核验未连接板卡、未初始化/采集/发射、未修改产品源码，也未重综合/实现/生成 bit。DCP 加载报告历史 methodology warning；本核验没有重跑或宣称时序/方法学验收通过。
