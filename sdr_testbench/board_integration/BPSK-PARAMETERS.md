# BPSK 参数统一（板卡接收机合同 vs Pluto 发射）

## 结论

**当前 2.4 MSPS 模板不能直接发送 3 MSym/s 的板卡 BPSK 信号。**
纸上可行、且**不需要改板卡接收机**的唯一参数组是：

- Pluto Fs = **6 MSPS**，sps = **2**，符号率 **3 MSym/s**；
- BPSK/RECT，PRBS7 seed 1，**无差分编码**；
- 板卡仍按 60 MSPS、4096 点、ratio 0 接收（板内看到 20 sps）。

这条路径的 USB2 吞吐（6 MSPS × 4 B = 约 24 MB/s）和 Pluto TX FIR 对 RECT 的滤波影响**未验证**，必须先过 Pluto 自回环/离线解调，不能直接上板。

## 板卡接收机的硬参数（file:line）

| 参数 | 值 | 依据 |
|---|---:|---|
| 输入采样率 | 60 MSPS | pynq_overlay/sw/rf_bpsk_receiver.py:96-99 |
| 每帧样本 | 4096 | 同上 |
| decimation ratio | 0 | 同上 |
| 符号率 | 3,000,000 Sym/s（精确相等） | 同上 |
| sps | 20 | pynq_overlay/sw/rf_bpsk_receiver.py:17 |
| 成形 | RECT（只接受 RECT） | pynq_overlay/sw/rf_bpsk_receiver.py:96-99 |
| PRBS7 | seed 1..127；bit6 输出/bit6^bit5 反馈 | pynq_overlay/sw/rf_bpsk_receiver.py:65-70 |
| 对齐 | 前 63 个判决用于对齐 | pynq_overlay/sw/rf_bpsk_receiver.py:15 |
| 最小比较 | 64 位 | pynq_overlay/sw/rf_bpsk_receiver.py:16 |
| CFO 上限 | ±100 kHz | pynq_overlay/sw/rf_bpsk_receiver.py:18 |
| 差分编码 | **没有** | 接收机直接判决，再用参考相关性定极性（:155-175） |

## 冻结发生器的硬参数（file:line）

| 参数 | 值 | 依据 |
|---|---:|---|
| PRBS7 | bit6 输出、bit6^bit5 反馈 | pynq_overlay/sw/rf_iq_signal.py:50-61 |
| BPSK 映射 | bit 0 -> +1，bit 1 -> -1（绝对，非差分） | pynq_overlay/sw/rf_iq_signal.py:88-89 |
| RECT | np.repeat(symbols, sps) | pynq_overlay/sw/rf_iq_signal.py:131-132 |
| 帧 | 127 符号 × sps | pynq_overlay/sw/rf_iq_signal.py:300-312 |
| 量化 | 12-bit signed，越界拒绝 | pynq_overlay/sw/rf_iq_signal.py:262-268 |

## 2.4 MSPS 为什么不行

- 3 MSym/s 的复数基带至少需要大于 3 MSPS 的采样率才能保留主瓣；2.4 MSPS 的 Nyquist 只有 1.2 MSym/s。
- 2.4 MSPS / 3 MSym/s = 0.8 样点/符号，连一个符号一个样点都不到。
- sdr_testbench/radio_test.py:20-33 自己也把模板 Fs 限制在 2.084–3 MSPS，幅度 ≤0.25、衰减 ≥40 dB；它没有 6 MSPS 的 BPSK 路径。
- 因此**不能**把 sdr_testbench/config/pluto.json 的 2.4 MSPS 模板直接改成 3 MSym/s，也不能用“把幅度调小”绕过采样率问题。

## 6 MSPS 参数组（纸上可行）

| 项 | 值 | 备注 |
|---|---:|---|
| Pluto Fs | 6,000,000 S/s | 3 MSym/s × 2 |
| Pluto sps | 2 | RECT，2 个样点/符号 |
| 符号率 | 3,000,000 Sym/s | 与板卡接收机精确相等 |
| 成形 | RECT | 不能用 RRC |
| PRBS7 seed | 1 | 127 符号周期 |
| 差分编码 | false | 两端都不实现；接收机用极性搜索 |
| 板卡 Fs | 60,000,000 S/s | 不改 |
| 板卡 sps | 20 | 60/3 |
| 板卡帧 | 4096 点，ratio 0 | 不改 |
| 幅度 | Pluto 0.1（≤0.25） | 60 dB 内部衰减 |
| CFO 预算 | ±100 kHz（接收机上限） | 外部时钟不同源；不套自回环 ±2 kHz |

## 离线验证（已做）

tests/test_planner.py 的 test_pluto_bpsk_parameters_are_demodulated_by_board_receiver：

1. 用 planner.bpsk_rect_payload 生成 6 MSPS、sps=2、seed=1、幅度 0.1 的 127 符号帧；
2. 用 np.repeat 上采样 10 倍到 60 MSPS（等价于板卡看到的 20 sps RECT）；
3. 平铺到 4096 点，装进板卡 RxIqFrame；
4. 调用生产 rf_bpsk_receiver.receive_bpsk(BpskConfig(reference_prbs7_seed=1))；
5. 结果 reference_sync=true、bit_errors=0、ber=0。

这只证明**参数与算法合同一致**，不证明 Pluto 6 MSPS 的 USB/FIR/模拟链路可用。

## 若 6 MSPS 不可行，需要改什么

- 方案 1：板卡接收机支持更低符号率（例如 1.2 MSym/s、2.4 MSPS 的 Pluto 模板，sps=2）。这要改 BpskConfig 的符号率/SPS 校验、重跑算法测试、重新审查、重新部署；属于**新合同**，本轮不做。
- 方案 2：换有 USB3/以太网、能稳定 6 MSPS 以上连续发射的 SDR；仍要保持 3 MSym/s/RECT/PRBS/无差分。
- 方案 3：在 Pluto 与板卡之间加数字/模拟上变频器，把低符号率信号搬到 3 MSym/s 域；这会引入新的频响/相位问题，另立实验。
- 任何方案都不能用 RRC、差分编码或 2.4 MSPS 模板冒充当前冻结 BPSK 合同。

## 结论

- **当前不可行**：2.4 MSPS 模板 -> 3 MSym/s。
- **纸上可行但未验证**：Pluto 6 MSPS、sps=2、3 MSym/s、RECT、PRBS7 seed 1、无差分编码、板卡 60 MSPS 接收。
- 在 Pluto 6 MSPS 自回环和离线解调通过之前，不申请 BPSK 上板预算，也不把任何 434 MHz 结果写成原 S2 BPSK 通过。
