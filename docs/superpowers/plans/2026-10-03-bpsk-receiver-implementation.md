# BPSK有限快照接收 Implementation Plan

用户已选择有线BPSK路线并要求continue；采用 `2026-10-03-bpsk-receiver-design.md` 首轮范围。使用writing-plans和TDD，保持现有dirty tree，不提交无关修改，不改当前板端发布。

**Goal:** 独立实现60MHz/D1/3MHz/RECT BPSK同步、判决和PRBS分区BER，提供离线重放和有限有线验收证据。

**Architecture:** 一个纯numpy模块处理不可变RxIqFrame；CLI只做校验、文件读写；独立板卡验收经既有唯一daemon。不在首轮扩展UI、software map或CNN。

**Tech Stack:** Python3.8、numpy（板端1.17.4）、pytest、Paramiko helper。

## Task 1: 接收算法

- [x] 保存相关源码hash与当前发布身份到 `evidence/rx-bpsk/2026-10-03/`。
- [x] 新建 `pynq_overlay/sw/tests/test_rf_bpsk_receiver.py`：独立PRBS解析参考，20采样相位、多载波相位/频偏、参考缺席、固定后半段翻转分子/分母、OFF/TONE/噪声/削顶/非法配置负控和不可变结果。
- [x] `python -m pytest pynq_overlay/sw/tests/test_rf_bpsk_receiver.py -q` 保存模块缺席红例。
- [x] 实现 `pynq_overlay/sw/rf_bpsk_receiver.py`：冻结config/result、平方前馈频偏、模π相位、RECT积分定时、63位对齐与剩余独立BER；不使用参考选择载波/定时。
- [x] 跑新增及RX分析/类型相关回归，保留实际输出；失败先定位再改，门限修订记录。

## Task 2: 离线工具与已有开发数据

- [x] 为 `scripts/rf_bpsk_replay.py` 写 `host/tests/test_rf_bpsk_replay.py` 红例：真实metadata解析、hash失配、拒覆盖、显式种子、算法结果及失败CLI退出码。
- [x] CLI调用同一算法，输入限长16384字节，元数据构造RxIqFrame，结果输出hash/config/版本及统计口径。
- [x] 只读取旧TRAIN/VAL原始BPSK3MHz RECT和负控作诊断；旧CNN TEST不用于阈值选择或正式本阶段验收。

## Task 3: ARM与有线pilot

- [x] 冻结正式pilot声明，3独立会话×10ON+每会话OFF，种子1/37/73、幅度60、既有40dB/tap29/LO80MHz/Fs60MHz/BW30MHz/PHY0；失败保留，不调已读pilot数据门限。
- [x] ARM纯计算同输入对照/100次热P95，独立临时目录且不创建Overlay；不将库单元测试性能冒充实板。
- [x] 复用有限采集脚本并保存每帧原始IQ/metadata/结果；结束验证安全状态，任何关闭未确认、超时或削顶停止后续会话。
- [x] 对30帧同步率与成功帧总errors/总bits报告，保留所有失败；达到≥27/30且聚合BER≤1%才记该工况验收PASS。

## Task 4: 交付

- [x] 独立代码审阅，修复具体问题后重跑对应测试；检查diff与证据hash。
- [x] 更新报告、CLAUDE和ledger，明确算法/ARM/pilot哪些完成，当前板屏同步/BER仍未接入。未完成的实板项记录NOT RUN，不用旧CNN验收替代。

完成记录：260tests PASS；新物理30/30、0/4140 errors、3OFF无BER；33帧host/ARM一致；100ARM P95 92.88ms，独立资源复测RSS22052KiB。独立审阅完成。详见docs/reports/2026-10-03-bpsk-receiver-acceptance.md及最终收据。本计划首轮完成，production worker/UI集成属于后续范围。
