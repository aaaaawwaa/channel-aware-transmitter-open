# task68 四类真实接收：独立 TX 真值与输入字段要求

> 本文件是 `ml_training/real_campaign/task68.py` 的输入契约。只描述离线导入/冻结评估，
> 不连接设备、不训练、不改模型/阈值/RTL。真实帧仅作为评估数据，不自动加入 TRAIN。

## 1. 目录

```text
<frames-root>/
  s00-on-00.iq        # 4096 complex, s16_iq_le, 16,384 bytes
  s00-on-00.json      # 捕获元数据（含帧身份与可选板端结果）
  ...
<tx_truth.json>       # 独立 TX 配置/真值文件（与本目录分开）
```

`task68.py` 递归扫描 `frames-root` 下带 `capture_id` 与 `iq_sha256` 的 JSON；每帧对应同名
`.iq`。不会复制或修改原始文件。

## 2. 帧元数据必需字段

顶层：

| 字段 | 要求 |
|---|---|
| `capture_id` | 非空；用于与 TX truth 的帧身份匹配 |
| `request_id` | 有则用于身份校验 |
| `iq_sha256` | 与实际 `.iq` 文件 SHA-256 一致 |
| `format` | `s16_iq_le` |
| `samples` | 4096 |
| `sample_rate_hz` | 60000000 |
| `decimation` | 1 |

可选但建议记录：`release_id`、`connection_generation`、`config_revision`、`demod_result`、
`environment_result`、`model_result`、`modrec4_result`。这些结果只作诊断/passthrough；
**模型预测、环境标签和 RX expected_mode 都不能成为真值**。

`demod_result.submitted.config.mode` 是 RX 解调配置，**仅作诊断记录**；当前 runner 按设计对
四类 TX 始终保留 BPSK 解调配置，modrec4 负责四类分类。因此 TX=QPSK/QAM16/QAM64 而 RX=BPSK
是正常情况，不能否决独立 TX 真值，也不得用 RX mode 代替真值。

## 3. 独立 TX truth 文件

```json
{
  "schema": "ml-task68-tx-truth-v1",
  "truth_source": "independent_tx_config",
  "config": {
    "sample_rate_hz": 60000000,
    "symbol_rate_hz": 3000000,
    "shaping": "RECT",
    "amplitude": 60,
    "rolloff": "0.35"
  },
  "frames": [
    {
      "frame_identity": {
        "capture_id": "rx-...",
        "request_id": 21,
        "iq_sha256": "<exact IQ SHA-256>"
      },
      "tx": {
        "mode": "BPSK",
        "seed": 1,
        "sample_rate_hz": 60000000,
        "symbol_rate_hz": 3000000,
        "shaping": "RECT",
        "amplitude": 60,
        "rolloff": "0.35"
      }
    }
  ]
}
```

- `truth_source` 必须是 `independent_tx_config`（或明确等价的
  `independent_tx_configuration`/`tx_config`）；`model_prediction`、`environment_label`、
  RX expected_mode 一律拒绝。
- `frame_identity` 必须包含 `capture_id` 和 `iq_sha256`；可选 `request_id`、`release_id`、
  `connection_generation`、`config_revision` 用于严格身份校验。
- clean 类必须提供 `mode`（BPSK/QPSK/QAM16/QAM64）、`seed`（1..127）和 TX 采样率/符号率/
  成形/幅度/rolloff；OFF 类 `mode` 为 `"OFF"`。
- 缺少匹配 entry → `truth_missing` → `UNEVALUABLE`；不使用模型预测补真值。

## 4. 判定语义

| 情况 | 状态 | 统计 |
|---|---|---|
| 独立 truth + 有效 IQ + 域 OK | `OK` | 进入 ON 分类 / OFF 拒识统计 |
| 找不到独立 truth | `UNEVALUABLE` | 不进入分子或分母 |
| TX mode 与 RX expected_mode 不同 | `OK` | 诊断记录；不否决、不参与真值 |
| truth 不是独立来源 | `UNEVALUABLE` | 不进入统计 |
| IQ 哈希不符 / 长度/格式/域无效 | `INVALID` | **不**算正确拒识 |
| OFF truth 但输入无效 | `INVALID` | 单独计 `invalid_excluded`，不算拒绝成功 |

ON clean 与 OFF quality 分开汇总；不足四类时四类总体准确率保持 `NOT_RUN`，不伪造分母。

## 5. 入口

```bat
ml_training\.venv\Scripts\python.exe ml_training\real_campaign\task68.py ^
  --frames-root <task68 run dir> ^
  --truth-file <tx_truth.json> ^
  --out-dir ml_training\real_campaign\results\task68
```

无帧/无 truth 时：

```bat
ml_training\.venv\Scripts\python.exe ml_training\real_campaign\task68.py
```

输出 `status=NO_FRAMES` 与字段要求，退出 0；不循环等待、不生成模拟数据。

## 6. 与 amplitude-60 载荷的关系

`hardware_amp60` 的 QAM64 理想数字向量 RMS=**55.264**，低于冻结
`min_complex_rms=57.060`，因此离线冻结模型给出 `below_energy_floor`；这是**TX 数字向量的
离线拒识**，不能据此提高物理发射幅度、修改门限或判定实际 RF 失败。真实 RF 接收是否可用由
实际帧的独立 TX 真值和本入口判断。

## 7. 边界

- 不连接设备/Pluto/daemon；不训练、不调阈值/模型、不新增审查代理。
- 真实帧作为评估数据，**不自动加入 TRAIN**；不构成产品资格或统计 BER 验收。
