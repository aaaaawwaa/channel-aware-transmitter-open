# 幅度 60 实际 TX 载荷：冻结模型离线检查
> **离线生成载荷**，不是真实 RF 接收；真值来自 manifest，预测未用于调参。不构成真实准确率或统计 BER 验收。

## 输入
- 60 MSPS / 3 MSym/s / RECT / 4096 complex reference window；amplitude=60 codes
- 冻结入口：`ml_training.real_campaign + Round2Predictor`

## 结果
| truth | final label | accepted | reason | rms | truth match |
|---|---|---|---|---:|---|
| BPSK | BPSK | True |  | 59.917 | True |
| QPSK | QPSK | True |  | 84.789 | True |
| QAM16 | QAM16 | True |  | 62.825 | True |
| QAM64 | UNKNOWN | False | below_energy_floor | 55.264 | False |

模型评估 4 帧：accepted 3、rejected 1、correct 3、wrong 0。

## 误判/拒识（如有）
- QAM64 -> **UNKNOWN**（accepted=False, reason=below_energy_floor, cnn_class=QAM64）

## 边界
- 幅度固定 60；没有为通过而调 amplitude/模型/阈值。
- 若 QAM16/QAM64 因能量门限被拒，保留真实拒识原因，不视为失败重试。
- 仅离线生成载荷检查；真实 RF IQ 不要求与理想载荷字节相等。
