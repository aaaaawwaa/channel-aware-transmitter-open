# DSH task68 模型评估接入增量审查

日期：2026-10-22（北京时间）；日志读取至约 16:43。

结论：**CHANGES_REQUIRED，仅针对新的真实帧评估接入**。不要求重训、调阈值、重新生成四类载荷或重新发布板端产品。

## 已有进展

- 模型会话 16:36 交付 `ml_training/real_campaign/task68.py`、独立 TX 真值导入器、字段文档与三个定向测试；目前输出 NO_FRAMES，没有 task68 真实采集结果。
- 主项目 A 已交付 task68 四类 runner，目标为 4 会话/4 初始化/4 load/12 ON+2 OFF。B 的 task69 复核指出单帧非 BPSK 仍受 BER 门限制、无效 OFF 不立即停止、缺失 accepted 被算正确拒识等问题；主线应继续收敛这些已有问题。
- 本次没有连接板卡或运行 RF。

## 模型侧必须修复

### M1：RX 解调模式不能否决独立 TX 类别

位置：`ml_training/real_campaign/importer.py` 的 `_task68_truth_problem()`，约 574–576 行。

当前只要独立 TX mode 与 `demod_result.submitted.config.mode` 不同就返回 `truth_rx_expected_mode_conflict`。实际四类 runner 按设计保留 BPSK 解调配置，四类识别由 modrec4 负责。因此 TX=QPSK/QAM16/QAM64、RX demod=BPSK 是预期配置，不能据此排除识别评估。

使用当前测试辅助函数，只做离线导入（不调用模型），固定 RX=BPSK，独立 TX 真值及帧身份匹配，实测：

| TX | 导入状态 | truth_evaluable |
|---|---|---|
| BPSK | OK | true |
| QPSK | UNEVALUABLE | false |
| QAM16 | UNEVALUABLE | false |
| QAM64 | UNEVALUABLE | false |

后三类均为 `truth_rx_expected_mode_conflict`。修复时保留 RX mode 作为诊断字段；只有独立 TX 声明之间的实际矛盾、帧身份/摘要不符、输入域不符或真值缺失等才阻止评估。同步修正文档和原错误预期的测试。

上一轮 Codex 提出的“RX 模式冲突检查”在此项目配置下不适用，本次明确纠正。

### M2：多帧导入在第二帧崩溃

位置：`parse_task68_frames()`，约 614、628、677 行。

循环外 `source` 是传给 `_base_record()` 的字典；第一帧匹配真值后又被赋值为 `selected.get("truth_source")` 字符串。第二帧再次调用 `_base_record(source, ...)` 时出现：

```text
TypeError: string indices must be integers, not 'str'
```

已使用两个不同 capture_id/request_id、各自匹配独立真值的临时离线输入复现。临时文件已自动清理，未修改原始证据。

修复：将来源配置字典与真值来源字符串使用不同变量名；增加两帧以上的批量案例，确认每条记录的 source_id、truth_source 和身份正确。

## 最小收尾与任务分工

1. 模型会话只修 M1/M2，复用 `real_campaign`；补批量导入及三类非 BPSK 的预期配置案例，并保留缺真值、身份/摘要错误、无效 OFF 不计成功的行为。
2. 与主线对齐实际元数据和独立 TX truth 导出方式，避免要求人工从模型预测补真值。先用标明为测试夹具的数据验证接口，不冒充真实帧。
3. 主项目原代理继续 runner/OFF/单帧判定修复；模型会话不改 runner、不另派代理重复主线工作。
4. 主代理合并做一次相关差分检查即可；不重开历史全量审查。真实 RF 执行仍按已有授权范围和预算确认，不把本次离线修复视为新的 RF 授权。
5. 真实帧到位后，模型会话执行冻结评估、单列 ON 分类/OFF 拒识/无效输入/主机板端差异。小样本只报告本批结果，不声明正式总体准确率或统计 BER 达标。
