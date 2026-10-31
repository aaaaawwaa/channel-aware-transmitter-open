# New-seed 10-frame frozen four-model comparison — handoff

状态：**入口就绪；新 10 帧尚未下载（默认 NO_FRAMES）**。
仅新增 `ml_training/fourclass_seed_validation_eval/`；未训练、未改阈值/模型/规则/产品文件、
未连接设备。四个模型是**诊断比较版本，不视为批准发布**。

## 1 入口

新 10 帧下载到：

```text
evidence/rf-teaching/s2-46db-fourclass-seed-validation-2026-10-22/raw/
  01-off-first/
  02-bpsk-2/
  03-qpsk-38/
  04-qam16-74a/
  05-qam16-74b/
  06-qam16-75/
  07-qam64-125a/
  08-qam64-125b/
  09-qam64-126/
  10-off-last/
    <label>.iq
    <label>.json
    final.json / records.json
```

默认入口：

```bat
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\fourclass_seed_validation_eval\seed_validation_eval.py
```

也可显式指定：

```bat
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\fourclass_seed_validation_eval\seed_validation_eval.py ^
  --frames-root evidence\rf-teaching\s2-46db-fourclass-seed-validation-2026-10-22\raw ^
  --truth-file evidence\rf-teaching\s2-46db-fourclass-seed-validation-2026-10-22\truth.json ^
  --plan-file evidence\rf-teaching\s2-46db-fourclass-seed-validation-2026-10-22\plan.json ^
  --out-dir ml_training\fourclass_seed_validation_eval\results\new_seed_eval
```

无 `raw/`、无 `.iq`、或 truth/plan 缺失时返回 **`NO_FRAMES`** 并写字段要求，退出 0；
不循环等待、不生成模拟数据。

字段要求可直接打印：

```bat
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\fourclass_seed_validation_eval\seed_validation_eval.py --print-fields
```

## 2 锁定的四个模型

见 [models_manifest.json](models_manifest.json)；入口会逐项校验路径和 SHA。

| 名称 | CNN | CNN SHA | rules | rules SHA |
|---|---|---|---|---|
| original | `ml_training/artifacts/cnn/model.json` | `25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc` | `ml_training/round2/artifacts/rule_config.json` | `9aceebbdb6b9b9a54ad0dd09ebd61935ae8c8fd276ba18d8b9db69e7949619e0` |
| A | `ml_training/qam16_domain_repair/results/candidate_A/model.json` | `b840f9dbaebc5db855ef1535d091e1f1583e12368ca62742f517a8d38123a28e` | `.../candidate_A/rules.json` | `10a0bef4d237818eaa39dcd4e3048466de60017dbf0545c02bcbf405da2cdf64` |
| C0 | `ml_training/qam16_bpsk_control/results/C0/model.json` | `0847607d5edb6f3fde42fd5106622a54f0ed5294c5b6da88f62bc1252148cb04` | `.../C0/rules.json` | `fe59e166d4b22346ea1182efa8d79b5d7a74fc73be23f9fceb149a8ceacc8d53` |
| C1 | `ml_training/qam16_bpsk_control/results/C1/model.json` | `1be11069e9a49ed989090ddbf304d3eefc721739412966a735edf7a4208b80bf` | `.../C1/rules.json` | `9f94900ff0482ded05edbf92822da26637f080a11bf151c2f7a8bcee404cc2e1` |

共同 baseline：`ml_training/artifacts/baseline/model.json`
SHA `caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014`。
四个模型都使用原预处理和原决策阈值；入口只替换 CNN/规则文件，不重校准。

## 3 每帧校验

入口对每个 `.iq` 做：

1. **IQ**：`domain.load_iq` 校验 4096×2、s16_iq_le、range、SHA；
2. **身份**：`capture_id`/`request_id`/`release_id`/`iq_sha256`，IQ SHA 与 metadata/record/
   frame identity 一致；`release_id` 与 plan 的 `4776…` 一致；
3. **输入域**：`input_domain_status=VALID`，并重新用 `real_campaign.domain.validate_context`
   校验 60 MSPS / decim1 / 3 MSym/s / RECT / 4096；
4. **实际 TX 下发记录**（缺任何实际字段即报错，不从模型/RX expected_mode/计划补造）：
   - `tx_load_evidence.worker_args` 是波形源参数：校验 mode/seed/amplitude/filter/
     rolloff/sample_rate_hz/symbol_rate_hz/waveform 与 plan/truth 一致；
   - `tx_load_evidence.tx_load_metadata` 是 **daemon 协议结构**：
     `samples/bytes/format/direction/wrap`；不在此处期待 mode/seed 等源参数；
   - ON：`load_ok=true`，`payload_sha256`/`payload_length` 与独立 truth 行一致；
   - OFF：runner 仍记录 load evidence，但必须 `off_tx_ok=true`、pre/post
     `tx_source_selected=false`；不把 OFF 的 load evidence 当实际 RF TX；
5. **独立 truth**：ON 从 `truth.json` 行 + plan 取 class/mode/seed/amplitude/payload SHA；
   OFF 从 plan + runner `off_tx_ok/no TX source` 取；**绝不**使用 modrec4 预测、
   RX expected_mode、environment 或旧六类标签填 truth；
6. **无效输入**：任何校验失败都记入 `invalid_frames`，不进入四个模型的任何计数。

## 4 四模型同 IQ 比较

入口把同一原始 IQ 分别送入 `original`、`A`、`C0`、`C1` 的 `Round2Predictor`；输出：

- 每类：`correct` / `misclassified` / `rejected`；
- OFF：`false_accept` / `correct_rejection`；
- 每帧每模型的 label、accepted、reason、QAM16/QAM64 分数；
- `invalid_frames` 及原因；
- board `modrec4` 仅作诊断记录，不是 truth。

输出：

```text
results/new_seed_eval/seed_validation_result.json
results/new_seed_eval/seed_validation_result.md
results/new_seed_eval/validated_truth_side.json
results/new_seed_eval/field_requirements.json
```

## 5 夹具验证（非真实数据）

`fixtures/` 是明确标记 `SYNTHETIC_FIXTURE` 的小夹具（2 个有效 + 1 个故意无效），
不生成大型模拟数据，不进入 TRAIN。

```bat
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\fourclass_seed_validation_eval\fixtures\make_fixtures.py
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\fourclass_seed_validation_eval\fixtures\selfcheck.py
```

结果：**selfcheck PASS**，`PARTIAL`，3 frames / 2 valid / 1 invalid；四个模型对 BPSK
正确、OFF 正确拒识；无效长度帧被排除。

### 5.1 离线贯通测试（真实 build_tx_waveform → 真实 runner 记录 → evaluator）

`fixtures/integration_build_runner.py` 调用**产品真实** `build_tx_waveform`，再调用
**runner 真实** `LiveUiSession._record_control_op/_finalize_load_evidence`；只有传输响应
使用 fake。它覆盖 ON、OFF、payload SHA 错配、worker_args 错配：

```bat
ml_training\.venv\Scripts\python.exe -B ^
  ml_training\fourclass_seed_validation_eval\fixtures\integration_build_runner.py
```

结果：**PASS**，`PARTIAL`，4 frames / 2 valid / 2 invalid；
`03-bpsk-payload-mismatch` 报 `tx_payload_sha_mismatch`，
`04-bpsk-worker-mismatch` 报 `worker_arg:seed`；实际 daemon metadata 键为
`samples/bytes/format/direction/wrap`，`worker_args` 为
`mode/seed/amplitude/filter/rolloff/sample_rate_hz/symbol_rate_hz/waveform`。
夹具标记为 `SYNTHETIC_FIXTURE`，不是真实 RF。

## 6 边界

- 新 10 帧是**独立诊断批次**，不是 TRAIN、不是独立验收；不得据其结果现场调参后宣称验收。
- 四个模型均为实验/诊断版本，**不部署、不安装、不冻结**。
- 不训练、不改阈值、不连接设备、不新增审查代理。
- 14 帧旧数据及变换副本不参与本入口，也不作为新批次 truth。
