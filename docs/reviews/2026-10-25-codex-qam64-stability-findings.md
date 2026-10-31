# QAM64 稳定性诊断复核：先修分析和 runner，不追加 RF

日期：2026-10-25。本次 Codex 只读已有材料，并运行纯假会话反例；没有连接板卡、初始化 RF、发射、采集、部署或修改产品。

结论：当前稳定性 runner **CHANGES_REQUIRED（后续物理诊断前）**。已有 IQ、实际等待与误码负结果保留；报告中的观测缺失判断需要追加更正。本结论不要求重审候选，也不要求回退已安装 v2。

## 1. 进度及实际操作次数

最近收尾记录 current/active=`024fb7b130dc756f648a655560ef740f`，`VERIFIED_SAFE_EXPECTED`，同 boot `268b7a15-e037-4a59-83e8-ab87ceba771e`。这不是本次新的板端读回。

第一段因调用不存在的 `capture_timing` 方法停止，已实际消耗 1 session / 1 init / 1 load / 1 ON / 2 captures。修复后又启动完整第二段，消耗 2 sessions / 2 init / 2 loads / 6 ON / 10 captures。

**累计为 3 sessions / 3 init / 3 loads / 7 ON / 12 captures，超过原提示词授权的 2/2/2/6/10。** 原授权还明确为 0 retry。虽然第二段不是为获得更好 BER 而重采，但 harness 错误后再次物理派发仍是新尝试，不能因换目录或改名为 run2 而重置预算。本次不追溯补授权，也不隐藏第一段。

## 2. 原始观测与报告矛盾

逐帧核对 run2 10 份 IQ、metadata、sidecar 的摘要和 capture/request/timestamp 绑定，均一致。**六个 ON 的原始 sidecar、metadata 和 summary 都包含**：

- `observation_present=true`、`usable=true`、`stop=false`；
- `in_window_rx_gain_index.available=true, value=76`；
- `chip_tx_attenuation_mdb.available=true, value=[46000,46000]`。

只有四个 OFF 缺少物理窗口成员，符合普通 OFF 路径。B 的 `run2-stability-analysis.json.observations` 也已经正确列出 ON 的上述值，但同一 JSON 的 `hypotheses.AGC` 和 Markdown 报告却硬编码为“每个 ON 观测缺失”，与自身数据矛盾。Lead 沿用了这一错误判断。

因此不应为了“补齐缺失的 ON 增益/衰减”再采一批。正确表述是：**存在每帧一次的增益/衰减读回，数值相同；这不是连续轨迹，不能排除所有 AGC/模拟增益动态，也不能据此证明某个物理器件正常。** 同样，“主机 TX payload/配置一致”不能无限扩展成“已排除所有实际 TX/RX 硬件路径变化”。

5000us 等待确有真实括号，三次为 5.120217 / 5.098962 / 5.098041 ms，0us 无 settle 括号；不能把 metadata 未携带 runner timing 解释为等待没执行。等待的改善方向不一致，没有证据支持继续重复同一对照即可解决误码。

建议新建更正报告及新的派生 JSON，引用旧报告，原始 IQ/日志/sidecar/收据和旧派生结果都保留。

## 3. 已复现的 runner 缺陷

文件：`scripts/rf_qam64_stability_runner.py`。纯假会话结果存于 `evidence/codex-qam64-stability-check-20261025/guard-probes.json`。

1. `_result_problem` 对 `bit_errors=-1, compared_bits=384, ber=-1/384` 返回 None，未拒绝负计数。应检查整数范围、有限 BER、三字段一致性，不能把合法非零 BER 改成失败。
2. 删除 ON 的 observation 与路径后 `_result_problem` 仍返回 None，没有兑现诊断计划所需的 ON 物理观测检查。应检查真实嵌套内容和身份；OFF 仍允许具名缺失，不能伪造。
3. 假 `cleanup()` 返回 `safe=false, disarm_ack=false` 而不抛异常时，`run_stability` 仍得到 `execution_complete=true / structural_valid=true`。应检查返回值，并阻止下一 session。此反例不表示本次真实板卡收尾不安全；本次实际独立 post 为安全。
4. API 方法缺失没有在 setup/init 之前发现，导致本可离线发现的问题消耗了物理预算。应增加实际客户端/包装器接口预检；不能仅给 FakeClient 添加方法后就认为已验证实际接口。
5. init/load/capture/TX 计数多在调用返回后增加，异常可能发生在已有副作用之后；会话计数还可能因 break 跳过增加。应分开记录“调用已开始/可能消耗预算”和“成功完成”，不将异常默认记为零副作用。
6. 每个 session 内都覆盖同一个 `frame-result.json`，只能留下最后一帧。改为逐 tag 唯一结果文件，已有会话级原文件保留。
7. 宣称“合法失同步继续”，但 ON 首先强制 carrier/timing 为 true，会把正常失败 envelope 的三同步 false 作为结构错误。补正常具名失同步全空 BER 继续、损坏 envelope 停止的对照。

这些是具体局部修复，不需要新权限体系、签名、token 或重复全链审查。使用一份工作包预算和明确停止状态即可；失败后修代码不会自动恢复已用的物理预算。

## 4. 建议下一工作包

- Lead 负责稳定性 runner、host session 必要接口预检、总预算与异常计数、逐帧保存、收尾返回值和真实相关测试。
- Agent A 负责独立核对全部已有幅度/等待/增益/衰减事实并追加更正报告，结论从实际字段生成，不硬编码已被原始数据否定的判断。
- Agent B 负责此前遗漏的真实 runner 反例、实际 wrapper 接口测试，并分析已有 IQ 的稳定低幅与残差分布；不操作板卡。

本轮只做离线工作，不新增 RF、不初始化、不安装、不发新候选、不再跑 0/5000us。完成后提交一个可执行、能区分剩余假设的最小验证方案，明确哪些观测已有、哪些确实没有；若需温度或独立 RF 路径测量，先检查是否已有读口，避免为了“补遥测”扩展大功能。

当前产品仍为实验性 v2，QAM64 稳定通过尚未达成。不要将一次读回或少量样本推广为总体低 BER/硬件根因证明，也不要把普通主机配置相同当作实际模拟路径已被排除。
