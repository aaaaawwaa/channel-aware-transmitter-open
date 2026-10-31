# 有线BPSK有限快照接收设计

日期：2026-10-03。状态：用户选择有线BPSK路线并要求continue，首轮范围已进入实现；板卡验证以交付报告为准，生产UI尚未集成。

用户已选择“有线数字接收：先做BPSK同步、解调和BER”。本设计将其拆为一个可独立验证的首轮交付：有限IQ快照的纯numpy BPSK接收算法、离线重放工具和有线实板验收。算法验收通过后另做板屏集成；当前板端生产发布继续提供已经验收的采集和CNN识别。

## 1. 方案与交付边界

推荐先做有限快照接收器。它复用现有4096点真实IQ和PRBS7发射契约，可以独立核对频偏、定时和误码分母，再接入后台worker。直接同时加入四种调制、RRC和板屏会增加不易分离的失败因素；引入GNU Radio连续接收器则需要新运行环境和持续数据通路，当前4096点BRAM快照并不提供该输入。本轮选第一条路线。

首轮支持域固定为：BPSK、RECT、输入60MHz、抽取D1、符号率3MHz、每快照4096个RX1复样点、signed12编码的s16_iq_le。载波频偏设计测试范围±100kHz，估计误差验收容差1kHz。接收机根据操作员显式配置的符号率/成形工作，不由CNN类别开启同步或从TX草稿读取接收参考。

交付包括：

- 独立纯计算模块，可由主机和板端Python3.8/numpy1.17.4调用，无torch/scipy/GNU Radio运行依赖。
- 冻结结果对象、有限的符号/比特预览，和可核查的BER分子/分母。
- 主机离线命令，对已有IQ文件和显式参考配置输出JSON，保留输入SHA256和算法版本。
- 独立有线采集验收脚本、冻结判据、原始IQ与结果收据。沿用唯一daemon，测试结束解除布防并验证source/ARM关闭。

本轮不增加板屏控件、不改变CNN、不解锁AUTO、不新增空口发射。帧协议、载荷、帧同步、CRC保持未接入；PRBS周期对齐不冒充协议帧同步。QPSK/QAM、RRC、其他符号率、抽取及连续接收不在首轮支持域中，返回明确UNSUPPORTED。

## 2. 本地已核对的契约

`pynq_overlay/sw/rf_iq_signal.py`：PRBS7输出bit6，反馈bit6 XOR bit5；种子1..127；BPSK位0→+I，位1→−I、Q=0。每次生成127符号并周期播放，RECT重复每符号sps个样点。3MHz/60MHz的TX缓冲为2540复样点。

`rf_rx_types.RxIqFrame`：IQ是一个RX1通道的I/Q分量，payload不可变，4096点最多16384字节；本地capture_id、连接/配置代次和完成单调时刻绑定已验证采集。

`rf_status_worker`：采集和分析在后台执行，显示线程只领取冻结结果；取消、断连、配置变化可使结果失效。现有分析与CNN不报告同步、BER或CRC成功。

当前板端发布上下文：e7f1fc6962179eaeb57be15dcad07790。现有有线工况LO80MHz、Fs60MHz、RX/TX带宽30MHz、PHY0、tap29、内部40dB、峰值码60，低削顶采集中必要时使用40。峰值60仅为本轮实板pilot起点；遇削顶保留FAIL并停止，不现场通过调门限接受失真帧。

## 3. 输入、输出与文件职责

建议模块 `pynq_overlay/sw/rf_bpsk_receiver.py`：

- `BpskConfig`：显式mode、symbol_rate_hz、shaping；可选reference_prbs7_seed（None或1..127）。输入Fs和抽取来自实际frame，不能由调用方覆盖。
- `BpskResult`：冻结对象，含capture_id、release_id、config_revision、connection_generation、completed_at_monotonic、algorithm_version、支持域、状态/拒绝原因、carrier_sync、timing_sync、reference_sync、cfo_hz、定时偏移、相位模π、alignment_bits、compared_bits、bit_errors、ber及有界预览。
- `receive_bpsk(frame, config)`：只使用已验证IQ和显式接收配置；无socket、寄存器、文件或模型访问。零能量、削顶、域外配置、同步失败返回明确拒绝结果，不能保留上次BER。输入字节为signed12整数，无法编码NaN/Inf；错误长度由RxIqFrame构造器拒绝，格式由离线导入层拒绝。

结果的raw_symbols是203个积分窗口，total_symbols是丢弃首尾各1个之后的201个可用符号。前63个只作参考对齐，后138个是独立比较区；记录比较区在原始IQ中的起止样点，不将对齐位计入BER分母。

`scripts/rf_bpsk_replay.py`：读取明确的IQ路径和frame元数据文件，核对长度、格式、hash和支持域，构造RxIqFrame后调用同一算法；参考种子必须通过CLI明确给出。输出目录拒覆盖，绝对路径与输入hash进入收据。不加载任意pickle或执行附件内容。

`scripts/rf_bpsk_run_pilot.py` / `rf_bpsk_board_pilot.py`：使用规定Paramiko helper与已安装client，显式expected-release/cable-confirmed，有限采集；仅用现有控制接口，不创建Overlay、不改bit/HWH、不直接写RF寄存器。参考从冻结实验声明传入，不能从RX判决反推“真值”。pilot退出码只代表有限物理验收；首轮总收据还必须同时验证ARM性能和主机/ARM一致性。

测试为 `pynq_overlay/sw/tests/test_rf_bpsk_receiver.py` 与 `host/tests/test_rf_bpsk_replay.py`。新增文件不放进当前生产software map；板上纯算法验证使用独立临时目录。UI和新软件契约在本轮验收后单独规划。

## 4. 同步和判决

1. 验证实际4096点/D1/Fs60MHz与BPSK/RECT/3MHz；先对原始码值检查削顶（任一分量达到−2048或2047）和能量，再去复数DC。
2. 平方消除理想BPSK的符号正负，以有限FFT全域峰粗估频偏；去粗频偏后的平方复数按64点块平均，再对块相位展开及加权拟合，细化频偏，超过±100kHz加1kHz估计容差则拒绝。v1逐点unwrap在真实滤波过零处积累整周误差，TRAIN/VAL诊断60帧全失败；v2在正式pilot之前修复并加滤波过零回归，门限未放宽。
3. 补偿频偏，以平方二阶矩估计相位模π；相位补偿后的归一二阶相干度至少0.70，否则carrier_sync=false。相干度采用 `abs(mean(z*z))/mean(abs(z)**2)`，不存在的分母不产生NaN。
4. 对RECT使用一符号长度的积分匹配滤波，搜索20个采样相位；只依赖IQ匹配输出的能量选择定时，不使用参考比特或BER区间选择定时。积分符号平均功率/去DC输入平均功率至少0.55，且至少得到127个完整符号，才允许timing_sync=true。
5. 丢弃两端各一个完整积分符号；按实轴正负判决，0→正、1→负。没有PRBS参考时可以给出同步与判决预览，但reference_sync未启用，BER/bit_errors/compared_bits均为空。

同步状态是当前快照内的有限估计，不承诺长期锁定或独立时钟的采样率漂移跟踪。符号率不匹配、强ISI和范围外频偏不在首轮支持域；必须覆盖能明确拒绝的负控，不能将范围外输入默认为测试通过。

## 5. PRBS参考与BER统计

有显式PRBS7参考时，仅使用丢边后的前63个判决比特搜索127个PRBS周期偏移和两种极性，解决周期起点和BPSK的π歧义。最优绝对归一相关≥0.80，且比第二个不同周期偏移的候选高至少0.20，才认定reference_sync=true。同一周期偏移的正负极性不当作两个独立候选计算差距。

偏移和极性确定后冻结，不再利用剩余比特重新寻优。剩余所有有效比特作为BER区间，至少64位；记录排除的63位对齐区间、两端丢弃范围、总符号数、实际比较起点、PRBS周期偏移和极性。`ber=bit_errors/compared_bits`，不足64位或对齐失败时不给BER。已经锁定但后半段出现大量误码仍报告真实非零BER，不通过丢弃这些位改善数字。

所有非零PRBS7种子对应同一m序列的不同循环相位，因此“换一个有效种子”不能作为独立错误参考负控，BER也不能证明发送种子的唯一身份。错误参考测试使用不同多项式/非PRBS序列或故意破坏后半段比特；对后半段注入k个翻转，必须得到确切k错误和固定分母。周期反复播放的前63位只用于本快照参考对齐，不称训练集或生产泛化能力。

本轮不报告校准SNR、dBm/Vpp或EVM；避免有限短快照和参考拟合造成未经定义的指标。本模块不读取CNN预测、TX模式标签或TX正在播放的IQ来产生接收判决。

## 6. 冻结验证与失败处理

先保存源码/原发布基线和设计hash，再按TDD红→绿推进；不得把旧回归计数当新结果。

数学测试：所有20个整数采样起点、至少八种相位（含π与π/2）、±100kHz范围内的解析频偏、I/Q交换、符号反相、非整数窗口起点、固定随机AWGN、确定后半段翻转。无噪理想BPSK要求零BER，频偏估计绝对误差≤1kHz；AWGN试验固定独立种子，不通过搜索最有利噪声样本验收。

负控：OFF、非PRBS连续TONE、独立噪声、QPSK/QAM、削顶、过短、signed12越界、错误Fs/抽取/符号率/成形、参考对齐失败；必须不给成功参考同步或有效BER。NaN/Inf不能编码进整数IQ字节。无需PRBS参考时同步/判决与BER可用性分别报告。输入IQ不得被原地修改，结果不得后续改变。

离线已有数据仅使用TRAIN/VAL的原始非增强帧研发，保留来源hash；旧CNN TEST不用于调本接收器阈值，也不当作本阶段正式验收数据。既有CNN权重/阈值保持冻结。

板端验证：纯算法与主机同输入，状态、偏移、比特和BER分子/分母一致；100次完整4096点热运行记录P50/P95/max/RSS和实际numpy/Python版本。新接收器P95目标≤200ms，独立于此前CNN门槛；是否能满足以实测为准。

正式有线pilot预声明后采新数据：BPSK3MHz RECT，三个独立配置/连接会话，各10个完整低削顶快照，配各会话至少一帧OFF；固定三组非零PRBS种子（1、37、73，仅作为会话标识，不视作不同信号族）、LO80MHz/Fs60MHz/BW30MHz/40dB/PHY0/tap29/幅度60。30帧中同步及参考对齐成功≥27帧，所有成功对齐帧的总errors/总compared_bits≤0.01；同步失败保留并计入分母30，不把失败帧排除后宣称成功率100%。每帧输出其实际bit_errors/compared_bits；本轮只对该单一工况作结论。

任何关闭未确认/采集超时/削顶立即停止后续实板采集并留失败证据；不自动重试发射、不自动布防，不把纯算法失败升级成设备控制成功。门限修订须先修改声明、记录原因，并采新独立验收数据，不用已经看过的验收帧调门限。

## 7. 集成后续与文档

交付报告说明哪些结果来自解析合成、既有TRAIN/VAL、ARM纯计算、当前真实有线pilot。当前UI的同步/解调/BER仍显示未接入，直到后续后台worker绑定当前capture与显式接收参考、生命周期负控、版本化软件发布及真人验收都完成，才解锁对应能力。

天线/LNA路线暂不执行，仍需实物型号/频段/已知信号源。CRC/帧协议另定，不用PRBS周期对齐代替帧同步。

## 8. 参考与区别

GNU Radio官方将载波恢复与符号同步作为不同处理模块：[Costas carrier recovery](https://www.gnuradio.org/doc/doxygen/classgr_1_1digital_1_1costas__loop__cc.html)、[symbol synchronizer](https://www.gnuradio.org/doc/doxygen/classgr_1_1digital_1_1symbol__sync__cc.html)。本轮是结合本工程有限快照契约提出的numpy前馈方案，不是调用或照搬其连续流模块；上面的数值门限是本工程预声明目标，并非这些文档给出的保证。
