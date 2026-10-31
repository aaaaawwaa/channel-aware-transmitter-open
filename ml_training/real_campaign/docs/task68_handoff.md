# task68 四类真实接收：冻结评估接入交接

日期：2026-10-22；状态：**入口就绪，NO_FRAMES（尚无 task68 真实帧）**。
只修改/新增 `ml_training/real_campaign/`；未改主项目 runner、产品、模型、阈值、RTL；未连接设备。

## 1. 入口命令

```bat
ml_training\.venv\Scripts\python.exe ml_training\real_campaign\task68.py ^
  --frames-root <task68 run dir> ^
  --truth-file <independent_tx_truth.json> ^
  --out-dir ml_training\real_campaign\results\task68
```

无帧时（当前）：

```bat
ml_training\.venv\Scripts\python.exe ml_training\real_campaign\task68.py
```

返回 `status=NO_FRAMES`、写入字段要求，不循环等待。
字段要求：[task68_truth_fields.md](task68_truth_fields.md)；也可运行
`task68.py --print-fields`。

## 2. 最小差分

1. `importer.py`：
   - 新增独立真值来源 `independent_tx_config`（加入允许来源）；
   - 新增 task68 真值文件 schema/reason codes；
   - 新增 `parse_task68_frames()`：按 `capture_id + iq_sha256`（及可选 request/release/
     generation/revision）匹配独立 TX 真值，匹配/缺失/身份不符都显式标注；RX mode 仅存诊断；
   - **M1 修复**：RX demod mode 不一致不再作为 `truth_rx_expected_mode_conflict` 否决；runner
     对四类 TX 保留 BPSK 解调配置，modrec4 负责分类；
   - **M2 修复**：来源配置字典与真值来源字符串分开命名，第二帧起不再被字符串覆盖导致
     `_base_record` TypeError；
   - 原有 `import_all`、board/teaching/fixture 解析器未改。
2. 新增 `task68.py`：调用上述 parser，复用现有
   `real_campaign.evaluate.evaluate()` + `Round2Predictor` 冻结入口；逐帧输出身份、输入域、
   IQ SHA、TX 真值、host 分类/接受/拒识/原因、可获得的 board-host 差异。
3. 新增 `tests/test_task68_truth_import.py`（8 个定向测试）和本交接/
   [字段文档](task68_truth_fields.md)。
4. 未修改 `run.py`、`sources.json`、历史索引/报告或 task65 结果。

## 3. 真值规则

- 四类 truth 只来自独立 TX `tx.mode/seed/config` 与帧身份；`truth_source` 必须为
  `independent_tx_config`（或明确等价别名）。
- `demod_result.submitted.config.mode`（RX expected_mode）仅作**诊断记录**；当前 runner 对
  四类 TX 均保留 BPSK 解调配置，modrec4 负责分类，因此 TX=QPSK/QAM16/QAM64 而 RX=BPSK
  正常且不否决独立 TX 真值。**不得**用 RX mode 补真值。
- `model_result`、环境标签永不作为 truth；只有模型预测、没有独立真值时
  `truth_missing` + `UNEVALUABLE`。
- 缺失/格式错误独立真值 → `UNEVALUABLE`，不进入统计。

## 4. 统计语义

- ON clean 分类与 OFF quality 拒识分开报告。
- 无效 IQ/域/哈希 → `INVALID`；**不**算正确拒识，单独计 `invalid_excluded`。
- 四类不足时四类总体准确率保持 `NOT_RUN`；不伪造分母。
- 真实帧作为评估数据，**不自动加入 TRAIN**。

## 5. 相关测试（本轮只跑本文件）

```bat
ml_training\.venv\Scripts\python.exe -m pytest ^
  ml_training\real_campaign\tests\test_task68_truth_import.py -q -p no:cacheprovider
```

结果：**8 passed**。

覆盖：

1. 两帧批量导入正常，逐帧 `capture_id`/身份/`truth_source` 保持正确（M2 回归）；
2. QPSK/QAM16/QAM64 TX + BPSK RX 解调配置 → `OK`，仅记录 RX mode 诊断，不否决（M1 回归）；
3. 只有模型预测、无独立真值 → `UNEVALUABLE` / `truth_missing`；
4. 身份/摘要不匹配 → `UNEVALUABLE`/`INVALID`，不进入有效统计；
5. 无效 OFF 输入 → `INVALID`，quality total=0、rejected=0，不把无效帧算正确拒识。

未重跑历史全套测试，未新增审查代理。

## 6. amp60 说明

`hardware_amp60` 的 ideal QAM64 数字向量 RMS=**55.264**，低于冻结门限
`min_complex_rms=57.060`，所以离线冻结模型给出 `below_energy_floor`。这是 **TX 数字向量的
离线拒识**，不能据此提高物理发射幅度、修改门限或判定实际 RF 失败。真实 RF 接收是否可用，
由真实帧的独立 TX 真值与 task68 入口判断。

## 7. 主项目交接步骤

1. task68 真实 IQ/元数据放到 `--frames-root`；元数据需含 `capture_id`、`request_id`、
   `iq_sha256`、格式/样本数/采样率、可选 board results。
2. 独立 TX 配置生成 `tx_truth.json`（schema 见字段文档），每帧 identity + TX mode/seed/config。
3. 运行入口命令；查看 `results/task68/task68_result.json/.md`。
4. 若出现 `UNEVALUABLE`，修正独立真值/身份，不要使用 RX expected_mode 或模型结果补齐；
   若出现 `INVALID`，保留无效原因，不能计入正确拒识。
5. 真实帧仅供评估，不加入 TRAIN。

输出：`ml_training/real_campaign/results/task68/task68_result.json`、`.md`、
`field_requirements.json`。
