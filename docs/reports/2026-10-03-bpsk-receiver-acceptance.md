# BPSK首轮有限快照接收交付

用户选择有线BPSK同步、解调和BER，并要求continue。已完成首轮纯numpy算法、离线重放、ARM计算验证及新TX1→RX1有限pilot。当前仅验收BPSK/RECT/3MSym/s、60MSPS/D1、4096点；板屏同步/解调/BER控件尚未集成。

## 结果

| 验证 | 实测结果 | 口径 |
|---|---|---|
| 数学与相关回归 | 260项PASS | 20整数采样起点×8载波相位，±100kHz边界、分数样点、I/Q交换、AWGN、固定3误码、负控与清理 |
| 旧开发数据 | 60/60 BPSK同步，0/8280错误 | 仅TRAIN/VAL原始3MHz RECT；OFF/TONE/NOISE各60帧无BER；不是本轮正式验收 |
| 新有线pilot | 30/30同步，0/4140错误，3OFF均无BER | 3独立连接/init/config会话，种子1/37/73，各10ON+1OFF |
| 主机/ARM同输入 | 33/33一致 | 33个不同IQ hash；状态、偏移、判决、分子分母一致；浮点最大差1.82e-12，容差1e-6 |
| ARM热运行 | 100次P95 92.88ms、P50 68.84ms | 完整接收算法纯计算，Python3.8.2/numpy1.17.4，目标P95≤200ms |
| 独立资源复测 | 100次P50 69.11ms/P95 72.01ms/max77.85ms；峰值RSS22052KiB | 重放同一已保存IQ，无硬件访问；独立进程，RSS含解释器/numpy及输入 |
| 测试结束 | 3会话均disarm回执成功并核验safe=true | source=false、RX ARM=false、控制解除；服务PID6200/6220仍active，NRestarts=0 |

零误码只是本次4140个比较位的观察值，不承诺真实BER永远为零。PRBS周期对齐不是协议帧同步，没有帧协议、CRC、EVM或校准SNR结果；使用同一AD9361共享时钟，未验收独立设备的时钟漂移。

## 实现与问题修复

`pynq_overlay/sw/rf_bpsk_receiver.py`提供冻结配置/结果，平方FFT粗频偏、64点复数块平均后相位拟合细频偏、二阶矩载波相位、20候选RECT积分定时和实轴符号判决。载波/定时不读取PRBS参考。

每帧等长候选203符号，丢弃首尾各1个后201符号；前63位仅搜索PRBS周期偏移/极性，相关≥0.80且与第二周期偏移差≥0.20才接受。冻结偏移/极性后，后138位全部进入BER。记录原IQ比较区起止样点；参考缺席或对齐失败时BER为空，后半段误码不重新寻优或删除。

v1合成通过但旧真实BPSK60帧全部失败：粗频偏接近0且相干度约0.96，逐样点unwrap在噪声过零位置积累整周误差，细估计出现约−0.3至−0.4MHz。新增滤波过零合成回归先RED，再修改为64点复数平均后unwrap，GREEN且旧BPSK60帧全部通过；此修正在正式pilot之前完成。物理pilot声明冻结后没有调门限或算法。

离线工具`scripts/rf_bpsk_replay.py`绑定IQ SHA256和单次读取的metadata SHA256，限长、拒绝声明错误格式和覆盖输出；引用配置必须显式传入。`scripts/rf_bpsk_verify_pilot.py`重放全部33帧核对ARM结果。原生RxIqFrame元数据隐含s16_iq_le，外部format字段若出现必须一致。

有限pilot代码只通过现有唯一daemon，上传独立/tmp目录；不替换current、software map、CNN、bit/HWH，不创建Overlay。初始已布防即拒绝接管；超时、削顶或关闭未确认停止后续会话。缺失最后一次disarm回执即使读回安全仍不得PASS，已覆盖测试。ARM资源第一次以普通用户无法读root发布目录，导入失败且未操作硬件；按原权限用root纯重放复测通过。

## 证据与使用

- 设计：`docs/superpowers/specs/2026-10-03-bpsk-receiver-design.md`；计划：`docs/superpowers/plans/2026-10-03-bpsk-receiver-implementation.md`。
- 总收据：`evidence/rx-bpsk/2026-10-03/final-receipt.json`，同时门控测试、物理pilot、100次ARM性能和同输入一致性。
- 新物理数据：`evidence/rx-bpsk/2026-10-03/physical-pilot-v2/`中的declaration、campaign、33对IQ/metadata、host-arm-parity、arm-benchmark和arm-resources。
- 开发诊断：`evidence/rx-bpsk/2026-10-03/development-diagnostic.json`。缺失的历史读回/代次以显式占位标记，不伪造实时遥测。

在工程根目录重放一帧，无需连板：

```powershell
python scripts/rf_bpsk_replay.py --iq evidence/rx-bpsk/2026-10-03/physical-pilot-v2/s2-on-10.iq --metadata evidence/rx-bpsk/2026-10-03/physical-pilot-v2/s2-on-10.json --sha256 5d17d31e822b28e8f2717b666128f0889e9fd9d487c9c12e2bb6ec3eb16b9332 --reference-prbs7-seed 73 --output evidence/rx-bpsk/2026-10-03/manual-replay
```

输出目录只能使用一次；省略参考种子仍可查看同步/判决，BER为空。不同非零PRBS7种子只是同一序列的周期偏移，不能用换有效种子证明错误参考或发送身份。

## 下一阶段与真人测试内容

本轮由脚本采集，不需要额外真人重复算法验收。当前生产屏继续显示同步/解调未接入；下一阶段将显式接收配置/参考、结果identity和失效处理接入worker及板屏，再进行软件发布和真人测试。

以下是UI集成后的具体真人测试预案，不能拿当前屏验收未发布功能：

1. 保持TX1→RX1现有接线，接收配置BPSK/RECT/3MHz/D1，显式PRBS7参考启用且种子1；LO80MHz/Fs60MHz/BW30MHz，40dB/tap29/峰值60。布防→初始化→配置→仅加载，进入接收页；记录READY且TX关闭。
2. 回环捕获→确认，连续执行3次；每次记录新的capture_id、载波/定时/参考状态、bit_errors/compared_bits和BER。正常帧预计参考同步、比较位138；不要只记录百分数。界面等待期间仍应能操作导航/停止。
3. 关闭显式PRBS参考后再捕获：同步/判决仍可用，BER应显示“无参考”，不能保留上帧BER。改成不支持的2MHz或RRC后应明确“未支持”。
4. 恢复配置并成功捕获后，修改配置、切换设备或停止；旧同步/BER应失效。保持页面静默超过既有30秒有效期也应失效，不自动发射/重新布防。
5. 取消回环确认不得产生新capture；停止并解除布防后验证TX关闭、RX ARM关闭。以OFF正常采集时应显示未同步且无BER，记录实体屏观察与响应时间。

UI发布后的按钮名称和入口以实际版本清单为准；本轮不将MemoryLcd事件或纯计算P95写成真人触摸结果。
