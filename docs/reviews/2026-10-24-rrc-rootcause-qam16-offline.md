# REVIEW — RRC误码定位 + QAM16/RECT离线接收（增量）

日期：2026-10-24。范围：A 的 RRC 根因/观测补齐，B 的 QAM16/RECT 离线接收机，Lead 的 QAM16 离线配置接缝与集成测试。复用已安装候选 `bedf98c7…` 与既有 ARM/部署审查；不重开产品全审。零 RF/零安装/零训练/未 commit。

## 0 结论

**APPROVED（增量）**：
- RRC 旧 1/112、新 39 0/112、40 2/112 的观察与独立匹配滤波一致；**无已证接收机代码缺陷**；历史增益/AGC/in-window 观测缺失为 **UNKNOWN**，已加未来 runner 观测 sidecar，不改采集次数/控制。
- QAM16/RECT 离线接收机与独立参考/16点映射/负例测试通过；Linux/主机性能仅记录，不声明 ARM/真板。
- 产品/候选/原始证据未改；下一轮真板需新接线确认与新物理授权。

## 1 文件与 SHA

| 文件 | SHA-256 |
|---|---|
| `scripts/rf_rrc_real_validation.py` | `fce39a61e8e9d4150b823d0f0582593617b8a05a7bd05cac5d5d84e7b4968cd2` |
| `scripts/tests/test_rf_rrc_real_session.py` | `cc5c183e3675583e4a3c3f1c8e2f994fbbeab5a72ed4a7cf5f8782c35acb59cd` |
| `scripts/rf_bpsk_ui_board_session.py`（observation sidecar additive） | `cfc54e58fc3d0262a9badfeb7131a46124ac9b3862ae2105ea603ed2941a450c` |
| `pynq_overlay/sw/rf_qam16_receiver.py` | `1caefcae67c01f59ad4fa28cbfd71549c851d2fa7948a38ad5593f5890a068ce` |
| `pynq_overlay/sw/rf_qam16_pipeline.py`（Lead 离线接缝） | `3dda5a4dd027d38de25d29dc1ee6fbbf1d985674b2af189e450eff207558c640` |
| `pynq_overlay/sw/tests/qam16_reference.py` | `7a93c1ceec9a47ca1c0c1fad84195bb8402e0927f8128273b8b7375106680097` |
| `pynq_overlay/sw/tests/test_rf_qam16_receiver.py` | `4bd23861d56f1ff2345c2698d1b26a32ad21ac15b3ffd6c9d83d28a80f24d601` |
| `docs/specs/2026-10-24-qam16-rect-offline-spec.md` | `e8b818346cdb017f4bd0fd54e48cc650feb6803c27063f17b2ea1977cd0b1dc5` |

## 2 RRC 证实 / 排除 / UNKNOWN

**CONFIRMED**
- TX QPSK/RRC39 与 40 量化 RMS/peak/PAPR 完全相同（complex RMS 34.862、peak 81.394、PAPR 7.365 dB）；seed 不是 39/40 功率差异来源。
- RX 全部无削顶；QPSK40 本批 RX complex RMS 165.74/peak 409.66，低于 QPSK39 333.46/1305.65；历史增益/AGC 未记录，不能把 RMS 差当 SNR 或根因。
- 旧 1/112 错误：ref symbol **101**/bit **202**（I），comparison offset20，归一化裕量 0.15855；新 QPSK39 同参考位置无错（0/112）。
- QPSK40 2/112：ref symbol **97**/bit194（I，裕量0.014432）、ref symbol **0**/bit1（Q，裕量0.056226）；独立匹配滤波/对齐仅搜索复现这些错误，QPSK40 2/112 对 alignment-only 穷举稳健。
- 历史帧没有 capture_observation/in-window gain/chip attenuation/gain_observation/last_capture_timing；冻结 `_iq_header` 白名单丢弃 capture_observation，runner 未转发；这些历史值 **UNKNOWN**，未用当前读回回填。

**EXCLUDED（作为单一充分原因）**
- TX seed 功率差、削顶、QI/区间/分母/TX payload 损坏、新旧同位置错误。
- 两个误码不足以证明接收机代码缺陷；未改产品算法。

**UNKNOWN**
- 历史 RX 增益/AGC、in-window gain、实际 chip TX attenuation、TX-on sample edge、绝对 SNR。
- QPSK40 根因；旧 1/112 物理 timing（产品 tau1/1 vs alignment-only tau2/0；仅 timing-selection sensitivity，非已证缺陷）。
- 观测补齐：runner-only additive `capture-observation` sidecar（未来帧有效，历史 UNKNOWN）；不改采集次数/控制；fake tests 通过。

## 3 QAM16/RECT 离线接收

- 严格 TX Gray 映射：I b0b1→3,1,-3,-1；Q b2b3同；MSB-first；独立参考逐点覆盖 16 点。
- 首域 QAM16/RECT、60MSPS/3Msym/s/SPS20/127符号/4096、显式 QI/IQ；EVM unavailable。
- 对齐区 63 symbols/252 bits；comparison 63..126 = **64×4=256 bits**；失败具名、无 BER 分母。
- 测试：QAM16 receiver **88 passed**（含全16点、幅相/相位/timing/CFO、错误参考、无信号、削顶、域外、QI/IQ）；Lead 接缝 `rf_qam16_pipeline` + tests **4 passed**；集成 **136 passed in 20.46 s**。
- 主机性能冒烟：median 125.39 ms、P95 180.10 ms（20次，仅记录，无 ARM/板端结论）。
- 只声明离线能力；未接 worker/UI/ARM/真板，未改产品/候选。

## 4 集成审查

- `--plan rootcause` 新增使下一轮观测可执行；默认 six/next-min 不变。
- `rootcause` 6 帧：OFF-first → QPSK/RRC40 A → QPSK/RRC40 B → QPSK/RRC39 → QPSK/RECT41 → OFF-last；预算 6/6/6/4/6/0。
- 非零BER/合法失同步继续，结构/cleanup/超时/OFF残留停止；`execution_complete`/`structural_valid`/`metrics_passed` 分离。
- 无产品/候选/原始证据变化；无新 RF/安装。

## 5 下一轮最小真板实验（未执行；需新的接线确认与物理派发授权）

```bash
python -B scripts/rf_rrc_real_validation.py \
  --expected-release bedf98c77283002c632dd7f345abaff9 \
  --output <new-empty-dir> \
  --cable-confirmed --plan rootcause
```

- 帧序/预算：见 §4；每帧捕获 observation sidecar（in-window RX gain/chip attenuation），用于区分 QPSK40 低电平/增益假设。
- 停止：身份/配置/结果损坏/超时/cleanup失败/OFF残留；非零BER/合法失同步保留并继续，不重采/补采。
- 接线：此前确认已于 2026-10-24 19:20:05 过期；下一轮必须先取得**当前** TX1→RX1 时间确认，并重新授权一次物理派发；不得复用旧窗口。
- 下一个 QAM16 任务（仅在本轮离线通过后）建议：新建 additive QAM16/RECT 合同、ARM 同输入验证、worker/UI 最小接缝，然后才考虑真板；本轮不启动。

## 6 风险/边界

- 观测 sidecar 仅未来有效；历史 UNKNOWN 不补。
- QAM16 为离线单元/接缝，未做 worker/UI/ARM/真板，不声明发布资格。
- RRC 真板指标仍未通过（QPSK40 2/112）；本轮不改变该结论。
