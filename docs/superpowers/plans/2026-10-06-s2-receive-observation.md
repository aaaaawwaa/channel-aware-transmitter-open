# S2接收稳定性调查：先只读观测，再修复

用户继续授权既定教学＋环境诊断方向。2026-10-06检查当前7be30345安全、双服务active；两轮开发pilot因复合/窄带真实CLIPPED停止，原始失败帧与解除ACK保留。正式TEST未开始。AGC为待验证假设，不能仅凭跨会话RMS判断根因。

截至本次开发：core规范/质量审核通过；daemon RED 244/5→255/0；客户端39新测试＋原客户端/候选联合71通过；完整板载软件1707通过4跳过。ARM原生daemon45源摘要匹配、假硬件测试通过；663b9f7296cb55d4bdc4ae212a8d0774离线ARM100次P95=294.90ms、主机/ARM一致。接口、打包及probe整合规范/质量复审批准；版本已安全安装并保留7be30345回退。初始化后3次真实读取完成（mode2/index76/digital0），0新IQ帧；解除ACK与最终安全/双服务检查通过。

## 本轮最小范围

增加由唯一daemon/core拥有者读取的可选RX1硬件寄存器观测，不改变gain、AGC、TX、RF配置、BPSK或环境判据。仅在device.info显式布尔参数observe_rx_gain=true时调用；旧握手、默认poll、capture均不增加gain寄存器访问。保留status_only readback:null。不调用Overlay或另开MMIO；未初始化、非READY、pending、TX选中、CAP_ARM（即便DONE）时返回unavailable。任何读取失败不得变成0测量，也不得core_fail或停止业务。

先记录硬件模式、full-table标志、RX启用、RX1 gain-index和digital-gain原码及观察时间/顺序一致性。原码不是总接收增益dB；不依据缓存模式宣称硬件读回，不调用可能写gain-table地址的vendor getter。只读观测不是采集期间的AGC轨迹。

## 实现与审阅顺序

- [x] 只读追踪core/device.info/vendor getter，确认split getter写表地址、模式getter仅缓存以及错误索引风险。
- [x] 独立审核本计划，保留dirty tree；core与daemon按文件分工，不改vendor。
- [x] core RED→GREEN：READY raw读回、逐项SPI失败、full/split拒绝、无handle、活动路径和DONE+ARM拒绝、非法index。每条观察验证chip/control写计数不增加；失败不改变状态。
- [x] daemon RED→GREEN：可选observation且旧readback/ops保持；未初始化/活动/pending显示null与原因，frame/capture路径不增加寄存器读写。
- [x] Python只读client/证据采集补充：显式device.info读取，记录发布/时间、硬件原码与缓存来源分开，观察和IQ身份不冒充原子同时采样。
- [x] host core/daemon完整C测试、vendor-integrity、协议兼容及1707软件回归（4跳过）；独立规范与质量复核。
- [x] ARM隔离构建/host-fake测试，不安装、不发射、不Overlay；候选manifest覆盖新daemon摘要与版本。
- [x] 安全安装新daemon候选（保留7be30345回退）后先只读核验，记录真实寄存器观察与安全状态。
- [x] 确定需要有限启动时序调查；另立2026-10-06-s2-capture-timing规格/计划并完成2会话4新帧、同IQ重放及关闭证明。未改gain/AGC/幅度/判据，削顶根因仍未证明；详见2026-10-07-s2-capture-timing-progress报告。

## 后续固定门槛

若确认需要改变接收模式/增益，应另作明确配置＋读回＋支持域修改，重新TRAIN/VAL校准与S1/BPSK回归，然后新18帧开发pilot、同IQ重放、冻结代码/基线/场景/全部正式工具，再90＋18实板＋12离线TEST，最后实体屏功能验收。正式测试开始后禁止调阈值或替换失败帧；专门触摸计时仍由用户豁免，S3须等S2闭合。

2026-10-06 checkpoint: observer 663b9f7296cb55d4bdc4ae212a8d0774 safely installed; independent spec/quality APPROVED. Three initialized, TX/RX-idle hardware reads succeeded (mode2/index76/digital0), zero new IQ frames. Disarm ACK and independent final safe/services check passed. Previous pending installation/review text above is historical. Next: bounded investigation of startup transient versus receiver gain stability. No gain/AGC/delay change; formal TEST and human acceptance NOT_RUN.
