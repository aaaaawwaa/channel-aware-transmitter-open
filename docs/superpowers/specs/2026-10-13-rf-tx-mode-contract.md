# RF TX 模式合同（P2 / Task 3.1）

日期：2026-10-13（起草于 P0 阶段，属规划文档；未连接板卡、未改任何生成器代码）。

**目的：** 逐模式冻结 BPSK/QPSK/QAM16/QAM64 的星座映射、符号率、位序、RECT/RRC 系数、群延迟、量化、占用带宽与拒绝条件，并规定**独立参考向量**。本文件冻结的是"接口与判据"，不含任何实测结论：实测矩阵在 Gate P2 用逐模式证据填写。

**现状来源（本次实测，非推测）：** `pynq_overlay/sw/rf_iq_signal.py`（生成器）、`pynq_overlay/sw/rf_tx_waveform.py`（波形/帧构建）、`rf_overlay/rtl/rf_tx_source.v`（PL 只做样点搬运：DDS 音或 PS 写入的 BRAM，读取端为速率主控、比例严格 1:1）、`src/rtl/iq_constellation_mapper.v` 与 `src/rtl/prbs7_source.v`（AWG 侧 RTL，作为映射/PRBS 的**独立对照**）、`rf_overlay/sw/board_app/rf_core.c`（`rf_core_set_digital_loopback`）与 `rf_daemon.c`（`tx.load` / `loopback.digital.start|capture` / `loopback.physical.start|capture`）。

## 1. 帧结构与传输

| 项 | 冻结值 | 依据 |
|---|---|---|
| 符号数 | `PRBS_SYMBOLS = 127` | `rf_iq_signal.PRBS_SYMBOLS` |
| 采样数 | `127 * sps` | `generate_digital_frame`（`sample_count = PRBS_SYMBOLS * sps`） |
| 帧语义 | `wrap=True`：周期帧，首尾相接 | 元数据 `wrap` |
| 线格式 | `s16_iq_le`，每采样 4 字节（I 后 Q，各 `int16` 小端） | `pack_iq` / `rf_rx_types.RX_IQ_FORMAT` |
| 元数据 | `samples`、`bytes=samples*4`、`format`、`direction='tx'`、`wrap` | `generate_digital_frame` 返回 |
| 装载 | `tx.load` 携带元数据 + payload；元数据/长度/值域不符即拒绝 | `rf_status_client`（`invalid tx.load metadata or payload length`、`tx.load sample is outside signed 12-bit range`） |
| 播放 | PL 从 BRAM 读，PS 写速率不影响播放速率，比例 1:1 | `rf_tx_source.v` 头部注释 |

教学域实例：`sample_rate_hz=60_000_000`、`symbol_rate_hz=3_000_000` → `sps=20` → 2540 采样/帧（与 `rf_headroom` 的 `"ps":2540` 一致）。

## 2. 采样率与符号率

1. `symbol_rate_hz` 必须整除 `sample_rate_hz`（整数 sps）；
2. `sps = sample_rate_hz // symbol_rate_hz` 必须落在 **2..32**（`SPS_MIN/SPS_MAX`），即 60 MSPS 下符号率范围 1.875–30 MSym/s；
3. `rolloff ∈ {'0.2','0.35','0.5'}`（`ROLLOFFS`），非集合内值拒绝；
4. `seed ∈ 1..127`（整数，布尔不算整数）；
5. `amplitude ∈ 0..2047`。

以上拒绝条件的**完整文本**以 `_validate_digital_args` 的实现为准，本文件不改写其措辞；测试需逐条覆盖（含 `bool`、浮点、字符串等类型混淆）。

## 3. PRBS7 位流

- 约定（Python 实现）：`state` 初值 = seed（1..127）；每次输出 `(state>>6)&1`；状态更新 `state = ((state<<1)&0x7F) | (bit6 ^ bit5)`；共取 `127 * bits_per_symbol(mode)` 位。
- 独立对照（RTL）：`src/rtl/prbs7_source.v` 输出 6 位窗口 = `{state[6], state_1[6], ..., state_5[6]}`（当前 bit6 在前，后续 5 个前瞻位），移位与反馈由 `lfsr_step` 定义。**P2 必须用该 RTL 的仿真/黄金序列逐位比对**，而不是复述 Python 注释。
- 位序：每符号取 MSB 先的连续 `bps` 位；I 段先于 Q 段（与 `iq_constellation_mapper.v` 的 `consume_bits`/位切片一致）。
- `bits_per_symbol`：BPSK 1、QPSK 2、QAM16 4、QAM64 6。

## 4. 星座映射（逐项冻结）

Python `_map_axis` 与 RTL `iq_constellation_mapper.v` 的逐项对照（RTL 为 8 bit 幅度、Python 为 ±1 归一化，比例不同但**相对电平必须相同**）：

| 模式 | Python 轴表 | RTL 轴表 | 判定 |
|---|---|---|---|
| BPSK | `2 级: (1, -1)`（bit0→I） | `symbol_bits[5] ? -127 : +127` | 一致（比例 127） |
| QPSK | `2 级: (1,-1)` 分别给 I/Q | `[5]→I, [4]→Q`，±127 | 一致 |
| QAM16 | `4 级: (3, 1, -3, -1)` | `2'b00→126, 01→42, 10→-126, 11→-42` | 一致（126/42 = 3/1） |
| QAM64 | `8 级: (7, 5, 1, 3, -7, -5, -1, -3)` | `000→126, 001→90, 010→18, 011→54, 100→-126, 101→-90, 110→-18, 111→-54` | 一致（18 的整数倍） |

**必须明确记录的结论：** QAM16/QAM64 的轴表**不是标准 Gray 序列**（QAM64 的第 4–7 项相对 Gray 顺序有置换；QAM16 的 2 比特轴表恰好与 Gray 一致）。P2 的规格修订必须二者择一并写明：
- **A（保持现实现）**：与 AWG RTL 逐项一致，接受非 Gray 轴表，理由是"与既有 PL 参考向量可比"；或
- **B（改为 Gray）**：同时修改 Python 与 RTL，生成新参考向量，并重跑本文件全部门禁。
在选定之前，任何"已实现 Gray 映射"的表述都不成立。

## 5. 成形滤波（RECT / RRC）

- RECT：`repeat(symbol, sps)`——符号保持，无跨符号记忆。
- RRC：
  - 抽头数 `8*sps+1`（sps=20 → 161），中心在 `length//2`；
  - 脉冲响应为连续单位能量 RRC 闭式（`_rrc_value`），含 `t=0` 与 `1-(4αt)²≈0` 两个特殊值分支；**不做能量重归一化**；
  - 抽头 `roll(h, -center)`，使符号 n 落在采样 `n*sps`（群延迟按峰值对齐记为 0 采样，滤波器记忆跨 ±4 符号）；
  - 卷积为**循环卷积**（FFT，无零填充接缝），与 `wrap=True` 帧语义一致；首尾符号互相交叠是**设计意图**，不是缺陷；
  - 拒绝条件：`filter ∉ {RECT, RRC}` 拒绝；`rolloff` 非三档拒绝（即使 `filter=RECT` 也校验）。

## 6. 幅度、量化与打包

- 幅度：按**帧内峰值**归一化到 `amplitude`（`iq * amplitude / max|iq|`）；峰值为 0 时输出全零帧。
  - 含义：同一 `amplitude` 下，RECT 与 RRC、不同 seed、不同模式的实际 RMS/PAPR **不同**；因此"幅度"不是功率标定，占用带宽/dBFS 必须实测而非推算。
- 量化：`np.rint`（**四舍六入五成双**），越界（>2047 或 <-2048）**抛错拒绝**，不做饱和/削顶。
  - 已知边界：`amplitude=2047` 叠加任何非零噪声损伤时，峰值样本必然越界并被拒绝——这是"拒绝而非削顶"的既有语义，P2 必须显式冻结并加测试，不能改成静默饱和。
- 打包：`struct.pack('<hh', I, Q)`，I/Q 各 `int16` 小端，交错排列。

## 7. 可选信道损伤（生成器侧模型）

- 4 抽头循环 FIR：`gain_q8 ∈ -256..255`（单位增益 256 无法表示，用 255 ≈ 0.996）、`delay_samples ∈ 0..511`；
- 噪声：I/Q 独立均匀分布，峰值 `round(2047 * noise_pct / 100)`，`noise_pct ∈ 0..100`，RNG 为 `np.random.default_rng(noise_seed)`；
- **不作为验收参考路径**：`default_rng` 的具体比特流依赖 numpy 版本，跨主机/ARM 不保证逐位一致。需要跨平台一致时只用确定性波形或固定 payload，并在证据里写明所用 numpy 版本。

## 8. 未冻结 / 待实测项（`NOT_RUN`，不得据现有代码推断）

| 项 | 为什么未冻结 | 归属门 |
|---|---|---|
| 各模式占用带宽（含 RRC α 档） | 需频谱实测；与 AD9361 滤波/采样率耦合 | P2-DL-01 / P2-PHY-01 |
| 群延迟的实测确认 | 当前只有"峰值对齐"的实现约定，未在收发链上量测 | P2-DL-01 |
| RRC 与 AD9361 半带/抽取的相互影响 | 需真实链路测量 | P2-PHY-01 |
| AD9361 片内数字回环逐模式结果 | 有 `loopback.digital.start|capture` 操作，但**未按模式执行过矩阵** | P2-DL-01 |
| 有线 TX1→RX1 逐模式实采 | 仅 BPSK 3 MSym/s RECT 有历史证据 | P2-PHY-01 |
| PS 生成/拷贝/PL 传输的吞吐、CPU、RSS、延迟分解 | 未测 | P2-ARM-01 |
| 用户数据比特流（USER 源） | **RF 路径当前只有 PRBS 帧**；AWG 侧才有 USER 上传。若要"用户数据位序"，须先定义 RF 侧注入路径 | P2-OFF-01 |
| 削顶/回退条件在 TX 侧的判据 | 现有实现是**拒绝**（量化越界抛错），不是削顶；若未来要饱和语义须新规格 | P2-OFF-01 |

## 9. 独立参考向量的要求（P2-OFF-02）

参考必须与被测生成器**来源独立**，且每条都可复算：

1. **PRBS**：用 RTL 黄金序列或独立 LFSR 描述（不复用 `rf_iq_signal.prbs7_bits`）生成 127*bps 位，逐位比对。
2. **星座**：用解析表（本文件 §4 的数值）逐码字比对 4 个模式的全部码字（`2^bps` 个）。
3. **RRC 系数**：用连续公式在有理数/高精度下独立计算 161 个抽头（α 三档），容差按浮点误差上限写定。
4. **量化/打包**：对边界值（-2048/-2047/-1/0/1/2047、半整数）用字节级黄金比对，覆盖"五成双"。
5. **帧结构**：`samples/bytes/wrap` 元数据与 payload 长度的关系逐模式验证。

## 10. 执行阶梯与每级记录字段

逐模式（BPSK/QPSK/QAM16/QAM64 × RECT/RRC）执行，**不用一个 BPSK pilot 覆盖其他模式**：

1. **离线 golden**：§9 全部向量通过，且生成器不依赖硬件/网络。
2. **AD9361 片内数字回环**：`tx.load` → `loopback.digital.start` → `loopback.digital.capture`（`rf_core_set_digital_loopback` 经 SPI 置 BIST）。每模式记录：实际配置、采集点数、频谱、削顶计数、关闭读回（`close_confirmed`）。**片内回环只证明对应收发路径，不证明 PL 算法正确**。
3. **有线 TX1→RX1 实采**：按冻结构型逐模式采数；物理 BER/EVM 门限必须在 TEST 采集前写定。

每级证据字段（写入收据）：模式、filter、rolloff、seed、sample_rate_hz、symbol_rate_hz、amplitude、samples/bytes、payload SHA-256、发布摘要、连接代次与配置修订、前后安全读回、失败原因（具名）。

## 11. 门的判据与变更控制

- **Gate P2 判据**：模式矩阵逐行有发布身份与实测状态；简介中"PL 实时样点处理"能对应具体模块与性能证据（或明确写 `NOT_IMPLEMENTED`）。
- **变更控制**：本文件任何数值/映射改动 = 新版本号 + 新参考向量 + 重新跑本文件门禁；**旧收据不继承**。
- 本文件不改动任何生成器、RTL 或 daemon 代码；实现或修正必须在 Gate P0-0 通过后、按 Chunk 顺序、各自带规格与反例进行。