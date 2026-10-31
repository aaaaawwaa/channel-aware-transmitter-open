# S2有限回环启动时序调查

## 授权与目的

承接用户已批准总体方向和“继续”：当前663b9f72只读增益观察已完成；本调查属于削顶定位，不扩展自动发射产品功能。保持已有dirty tree，不重置/提交混合修改。旧60个开发RF帧和三次静止原码观察保留。正式冻结、TEST和S3尚未开始。

已知实际idle RX1为slow-attack/index76/digital0；源码请求AGC更新1000us，窗口4096/60MHz约68.27us，物理捕获开启TX后立即准备/武装RX。尚未测得控制调用间隔，也没有采集中gain轨迹。不得把该差异直接宣称根因。

三条路线：先只增加单调时钟调用边界（本轮采用）；直接加固定等待（会改变RF开启时间且未证因，暂不采用）；改MGC/增益档位（需新的配置读回/支持域及校准，后续若证据需要再做）。

## 最小实现

仅loopback.physical.capture允许可选布尔observe_timing=true；缺省/false保留原控制顺序，增加零次时序时钟读取、零次gain/SPI读取，不增加等待。参数非bool在硬件前拒绝。默认UI/capture/普通采集不请求它。显式功能要求daemon1.0.2、协议major1/minor4，客户端发送前以当前保留连接hello版本确认支持，旧daemon不应因忽略新参数而先发射再报告不支持。

唯一daemon所有者在原成功交易中记录8个时间戳：TX源开启调用begin/end、RX启动调用begin/end、DONE等待/BRAM复制/解除RX调用begin/end、TX源关闭调用begin/end。每次end仅在相应调用成功后取得；任何硬件、转换、header或发送错误都不得发布成功trace。控制步骤、增益、AGC、等待、原IQ header/payload和实际关闭读回维持现有实现。不新开MMIO或Overlay，不改vendor/core。

trace只描述monotonic_api_brackets_not_sample_edges，不能当作RF已稳定、真实样点边沿或AGC更新事件。TX开启至RX_ARM的调用边界差给出lower=max(0,rx_begin-tx_end)、upper=rx_end-tx_begin，不给出没有依据的精确硬件时延。

字段固定为tx_on_begin_ns/end_ns、rx_arm_begin_ns/end_ns、drain_begin_ns/end_ns、tx_off_begin_ns/end_ns。8次clock读取对控制间隔有小的观测扰动；数据仅属于instrumented capture，不倒推旧失败采集的精确间隔。现有manifest protocol_version只表示major=1，不添加新的协议定义；build来源收据记录minor=4、daemon1.0.2并绑定真实binary/source摘要，部署后hello独立确认minor4。

trace绑定capture_request_id、capture_timestamp_ns（与原IQ header同一次clock值）、samples、ratio。仅完整frame成功发送后标记可用；每个控制操作或连接结束/解除/看门狗停止都清旧trace，默认/失败采集也清旧trace。device.info observe_timing=true显式返回last_capture_timing或null+原因；普通请求不返回有效trace、不增加任何硬件读，不延长控制看门狗。gain观察可独立请求，不混成原子快照。

客户端capture允许physical args中的observe_timing布尔，ordinary禁止。新增observe_capture_timing(frame)：IO锁复用同一连接；当前frame必须与本客户端最新真实physical帧一致，校验release/request_id/daemon_timestamp/samples/ratio、scope、uint64、时间有序，旧版本/missing报告UNSUPPORTED。完整JSON可选诊断的schema错误保留现有frame/configrevision，传输/帧错误沿用drop安全语义，无重试。诊断不进入DSP模型、基线、误码或场景真值。

## 有限证据实验

主机/ARM/C门禁与独立规范、质量审阅通过后构建不可变候选，bit/HWH/CNN/BPSK/环境基线不变，新daemon来源和全部45个build input摘要绑定，ARM100次P95仍须<=300ms；以663b9f72作为安装回退。

最多两个新初始化会话，参考seed1和37；每会话normal一帧、narrowband一帧，共最多4帧，实际场景PRBS7 seed1保持独立声明。TX1→RX1已由用户确认，attenuation40000mdB、LO80MHz/Fs60MHz/BW30MHz/2R2T/IDELAY1/tap29、3M RECT/D1/4096点固定。捕获通过真实worker/UiView两次有限回环确认，透明证据代理仅为physical请求加observe_timing。

每帧保留原IQ/header/handoff/环境显示/实验记录，独立诊断sidecar保留前后idle gain、trace及5项完整帧身份，原码peak/rail统计和间隔上下界。前后idle gain不冒充采集中gain。每个owned会话finally解除并检查ACK；初始unsafe或错发布不取得控制、不解除别人的会话。CLIPPED、缺少trace、身份错配、重复IQ、采集/收尾失败即停止，没有自动重试或以成功帧覆盖失败。

实验完成只表示证据完整与安全收尾，不表示消除削顶或S2正式通过。若数据不足以确认根因，下一项根据证据设计有限等待/固定增益对照及必要配置/校准，不调诊断阈值掩盖rail。

## 验证

C RED→GREEN：真实transport、默认无新增clock、bool拒绝无写、旧帧不复用、发送失败/转换失败/活动拒绝无trace、8个有序bracket、frame timestamp绑定、原IQ字节不变、旧关闭路径及看门狗保持。Python RED→GREEN：旧daemon拒发、explicit wire flag、时间/身份/范围/当前frame错配拒绝、完整JSON错误不清frame、传输错误drop、默认poll零时序请求、finite2x2/削顶停止/异常保留/ownership/源SHA和独立inspect守卫。全板载软件回归、C完整目标/vendor integrity、native ARM假硬件测试、精确候选离线资格、safe install与一轮有限实采。正式TEST NOT_RUN。
