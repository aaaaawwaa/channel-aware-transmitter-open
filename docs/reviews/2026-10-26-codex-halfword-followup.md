# 半字定位本轮结果与下一步（2026-10-26）

本次只做离线检查，未连接板卡或修改产品文件。查看 DSH 本轮 conclusion、TX/RX 报告、testbench 与评分器，并运行一个独立评分器反例。

## 可保留的进度

`evidence/rf-teaching/qam64-halfword-conclusion-2026-10-26/REPORT.md`：已完成现有IQ复算和模块级集成仿真；未修改产品RTL、未综合或生成候选。没有复现到实板问题前不应用隔离候选，这一决定合理。历史036a安装与恢复状态仅引用上轮报告，本次未重新读板。

## 需要修正的结论范围

1. `evidence/rf-teaching/qam64-halfword-rtl-2026-10-26/rx/classify_rx.py` 的 exact 只检查 d/q 各自是否属于任意参考字；没有要求正确frame/slot、顺序、同epoch、通道或valid节拍。frames_ok/cadence_ok虽计算，却未进入结果或通过条件。verdict另允许95%的分类比例。因此136/136 exact不能单独证明输出顺序和拼接正确，更不足以排除RX整个路径。
2. 可执行反例：`evidence/codex-halfword-followup-20261026/check_scoreboard.py`。输入让D1/D2、I/Q始终重复同一个参考字，valid间隔故意为1而非4。当前评分器输出24/24 exact、CLEAN_SAME_EPOCH。反例结果在同目录JSON和console中。这证明评分器存在缺口，不证明产品RX必然错误；也不抹去此前独立参考测试。
3. TX 2R2T仍有28/216 unknown，尚未逐一解释，不能按“启动/边界”直接视为通过。RX细报告说相位0–11无稳态valid、12–15有效，与总报告“8个无valid”不符。无valid相位应明确列举，不能在数据正确性统计中暗含通过。
4. TX测试复制了复位同步器、分开测试TX/RX，尚非完整TX→受控芯片数字传输模型→RX→capture链；不能把剩余根因写成仅有物理采样或版本不一致两种。

## 避免无效重建

已有 `evidence/codex-frame-idelay-check-20261025/verification.json` 记录归档bit与已装bit从sync到EOF的载荷相同，SHA256为0ce74071ee4f4ee625539a19ef3eef124f89aeeddb09c5ac68e157451f4ffb5e。关联归档DCP：

`<LOCAL_PATH>`

DCP摘要为63f679bcce815ba6c883f2e47b286dfc35a549ae9d040eba028548bd2be7c305。该证据覆盖当时检查的IDELAY路径，不是整个RTL的源文件追溯证明；当前036a报告仍使用同一bit摘要。应先核验现有工件摘要及关联构建记录，再从DCP批量提取复位链、shadow使能/计数、source strobe、RX配对和capture连接。不能把“无完整源码清单”等同于“旧bit已证实错误”，也不必仅为补历史清单立即重建。

## 本轮后续执行顺序

并行：A仅修复诊断评分器并重评现有CSV/VCD，B仅核对归档DCP关键逻辑。按首个分歧合并结果，必要时补一个实际2R2T完整链定向仿真；预期值独立于DUT内部shadow，使用固定对齐后的严格顺序/通道/节拍校验。半字分类仅用于失败解释。

若复现具体产品缺陷，做最小修复、相关红绿测试，再按merged流程构建候选；若工件映射确实无法核验，可准备绑定当前源码的诊断重建，但明确其只解决输入身份，不保证修复误码。若两者均未发生，不制造候选；交付首次分歧或有辨识力的一次有界板测方案，避免重复扫延迟。

执行现行个人开发审查规则：诊断脚本与补测由Lead增量检查；新候选准备完成后才交Codex一次安装前审查。上述测试缺口不触发全仓重审，不阻塞其他独立工作。
