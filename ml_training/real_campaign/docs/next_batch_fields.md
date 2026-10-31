# 下一批实测数据最小字段清单

> 这是**数据携带字段**要求，不是正式验收门限。缺少身份/真值字段的数据可以被索引，但不会进入
> 干净四类评估；不补造、不用模型或环境标签反填。

## 1. 每个会话（session）

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema` | 是 | 例如 `real-campaign-session-v1` |
| `session_id` | 是 | 独立会话标识；同一 release 下不得复用 |
| `split` | 是 | `TRAIN`/`VAL`/`TEST`；`UNASSIGNED` 只能索引，不能计入评估 |
| `release_id` / `software_contract` | 是 | 板卡实际 release；不得与帧内 release 矛盾 |
| `boot_id` | 条件 | 能读到就必填；读不到明确写 `NOT_DECLARED`（不得伪造） |
| `operator` / `captured_utc` | 是 | 现场责任人与时间 |
| `route` | 是 | `board_loopback` 或 `external_sdr_to_board_rx`；不得混用 |
| `lo_hz` / `sample_rate_hz` / `decimation` | 是 | 板卡 RX 实际值；本工具只支持 60MSPS/decim1 |
| `rx_bandwidth_hz` / `attenuation_mdB` | 是 | 实际值，不写默认值代替读数 |
| `tx_source` | 是 | 外部 SDR 型号/URI 或板卡 TX 配置；当前主线要求板卡 TX 关闭 |
| `truth_source` | 是 | 显式信号配置或独立实验记录；禁止 `model_result`/环境标签 |
| `experiment_record_path` / `sha256` | 是 | 独立实验/声明记录及其摘要 |
| `iq_convention` | 是 | 例如 `board_rx_conjugated_vs_pluto_tx`；方向 B 已知频谱反转 |
| `notes` | 否 | 接线、衰减、异常/削顶等 |

## 2. 每个帧（frame）

| 字段 | 必填 | 说明 |
|---|---|---|
| `frame_id` / `capture_id` | 是 | 唯一；与文件一一对应 |
| `session_id` / `split` | 是 | 与上方会话一致 |
| `iq_file` | 是 | 原始 `s16_iq_le` 文件路径 |
| `format` | 是 | 必须 `s16_iq_le` |
| `samples` / `bytes` | 是 | 4096 / 16384 |
| `sample_rate_hz` / `decimation` | 是 | 60,000,000 / 1 |
| `symbol_rate_hz` / `shaping` | 是 | 3,000,000 / `RECT` |
| `modulation` / `mode` | 是 | BPSK/QPSK/QAM16/QAM64（质量帧可为 OFF） |
| `mapping` / `prbs7_seed` / `differential` | 是 | 映射表版本/种子/是否差分；QAM64 注明轴表非 Gray |
| `scenario` | 是 | normal/off/narrowband/... |
| `physical` | 是 | 是否物理采集；OFF 等按实际记录 |
| `truth_source` | 是 | `signal_config` 或 `experiment_record`；附记录路径/ID |
| `iq_sha256` | 是 | 文件摘要；导入时与实算比较 |
| `clipping` / `rails` / peak/RMS | 建议 | 质量诊断；不替代真值 |
| `tx_config`（外源） | 条件 | Pluto Fs/幅度/衰减/模式/seed；B 路线必填 |

## 3. 最小示例

```json
{
  "schema": "real-campaign-frame-v1",
  "frame_id": "qpsk-s01-0001",
  "session_id": "qpsk-s01",
  "split": "TRAIN",
  "release_id": "3f84f6a189e833b6986f4c819ff89be0",
  "boot_id": "NOT_DECLARED",
  "route": "external_sdr_to_board_rx",
  "lo_hz": 433900000,
  "format": "s16_iq_le",
  "samples": 4096,
  "sample_rate_hz": 60000000,
  "decimation": 1,
  "symbol_rate_hz": 3000000,
  "shaping": "RECT",
  "modulation": "QPSK",
  "mapping": "frozen-rf-iq-signal-v1",
  "prbs7_seed": 1,
  "differential": false,
  "scenario": "normal",
  "physical": true,
  "truth_source": "experiment_record",
  "experiment_record_path": "evidence/.../session.json",
  "experiment_record_sha256": "<sha256>",
  "iq_file": "frames/qpsk-s01-0001.iq",
  "iq_sha256": "<sha256>",
  "iq_convention": "board_rx_conjugated_vs_pluto_tx",
  "tx_config": {
    "sdr": "PlutoSDR Rev.B",
    "fs_hz": 6000000,
    "sps": 2,
    "tx_attenuation_db": 60,
    "digital_amplitude": 0.1
  }
}
```

## 4. 导入器的硬性检查

- 缺 `release_id`/`session_id`/`split`/`truth_source`/显式信号配置或独立实验记录：不进入干净
  四类评估（可索引为 invalid/不可用）。
- 只有 `model_result.label` 或 `environment.label`：直接拒绝作为真值；
- `iq_sha256` 与实算不一致：拒绝该帧；
- 格式/长度/采样域/符号域不符：标 `NOT_RUN_DOMAIN`，不改写、不重采样冒充；
- 同一 IQ 出现在多个 split：报告泄漏；指标按 IQ digest 去重，不重复计数。
