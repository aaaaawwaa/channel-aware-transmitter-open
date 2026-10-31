# AD9361 数字调制与信道损伤兼容契约

日期：2026-10-01。属于已批准双外设UI方案的必做C实现包，使用现有RF二进制load，不新增硬件写入口或修改RTL。工程参数选择在本契约具体化，独立审查后执行。

## 用户操作与后端

固定ACM2108/AD9361目标按钮贯穿发射、数字调制、信道损伤页。ACM2108保持原有命令及FPGA实现；AD9361使用独立数字调制和损伤草稿，软件合成有限周期复数IQ，并通过现有RfStatusWorker → RfStatusClient → daemon的tx.load/start执行。

RF工具操作顺序是先准备完整射频参数并成功configure，再编辑工具参数、显式加载IQ、确认发射。编辑/切换外设不发命令；编辑RF信号参数取消待确认与加载证据，运行中禁用影响当前信号的编辑。RF数字调制/损伤的应用按钮必须走RF生成/load，不能发SET DIGMOD或SET CHx CHAN。两个后端草稿独立保存，异步结果按发起target落地。

停止不需确认；不自动恢复发射，30秒仅成功控制请求续期的规则不改。发射仍由新鲜遥测证明，生成成功不等于正在发射。

## 容量、采样与源

已有协议/PL最大4096复数点、每点s16_iq_le四字节。常见波形保持1024点；数字调制用完整127符号PRBS周期，每符号sps个采样，帧长127*sps。Fs必须来自成功configure的应用参数；symbol_rate为正整数Hz且Fs可整除symbol_rate，sps=Fs/symbol_rate须为整数2..32，帧长254..4064，不得超过4096。

明确拒绝非整数sps、低于Fs/32或超过Fs/2的符号率，并显示“当前有限帧支持Fs/32..Fs/2且须整除Fs”。例如Fs60MSPS、1MSym/s需7620点，拒绝；2MSym/s为sps30、3810点，可加载。60MSPS、10Sym/s不支持。这个差异必须展示，不能沿用ACM2108的10..1MSym/s范围误导用户。

首轮源为PRBS7；USER长流在RF模式禁用并说明容量边界，ACM2108保留。PRBS非零seed1..127，按RTL `prbs7_source` 位6输出、反馈bit6 XOR bit5、左移更新。每符号消费1/2/4/6比特；127符号消费127*m比特，恰为PRBS周期整数倍，回放不会缩短符号或丢比特。明确区分本工具PRBS7与常见波形随机Noise。

## 星座与成形

支持BPSK/QPSK/QAM16/QAM64，复用 `src/rtl/iq_constellation_mapper.v` 的顺序与符号极性。BPSK bit0→+1、bit1→-1；QPSK独立I/Q同规则；QAM16每轴00→+3、01→+1、10→-3、11→-1；QAM64每轴000→+7、001→+5、010→+1、011→+3、100→-7、101→-5、110→-1、111→-3。按bits顺序先I后Q，每轴最早输出比特为MSB，按发出顺序左移组装。与六位RTL输入比较时明确 `packed6 = serial_m_bit_value << (6 - m)`：BPSK最高位bit5、QPSK bits5:4、QAM16 bits5:2、QAM64全部6位；与RTL的symbol_bits[5:4]/[3:2]及[5:3]/[2:0]一致，不通过库的默认星座猜映射。必须测试PRBS→打包符号的已知前缀，避免仅星座表正确但比特顺序反了。

RECT重复每个符号sps点。RRC先符号冲激上采样，再以8符号总span、8*sps+1个实系数作周期卷积，rolloff选0.2/0.35/0.5。使用解析根升余弦系数并正确处理t=0与t=±1/(4*alpha)，能量归一化；围绕零时刻对齐滤波器，使用numpy循环移位叠加或FFT周期卷积，允许浮点误差但不允许零填尾再循环造成人为接缝。RRC与ACM2108固定4相/定点系数不是逐样本同一波形，UI/矩阵注明软件浮点成形。

滤波后按本帧所有分量的max(abs(I),abs(Q))归一到1，再乘用户峰值码amplitude0..2047；全零时保持零，避免除零。Amplitude代表成形后损伤前分量峰值，不称平均功率或Vpp。0幅度生成零调制IQ；若损伤噪声启用，噪声仍按其独立满量程百分比生成，界面说明其含义。

## 信道损伤

RF损伤草稿支持enabled、noise_pct0..100、4个tap：gain_q8 -256..255、delay_samples0..511，以及显式noise_seed非负整数。数学式 `y[n]=sum(g[k]/256*x[(n-d[k]) mod N])+noise[n]`；复数FIR将相同实tap作用于I/Q，周期回放使用环绕延迟，无尾部丢弃。disabled返回原IQ，不改噪声/增益。

noise_pct映射为round(2047*pct/100)的独立I/Q均匀整数噪声峰值，同seed/同帧重生成结果一致；这是有限周期噪声，不宣称高斯AWGN或指定SNR。设置页标注“噪声：相对满量程”，不将AWG近似噪声算法与RF序列宣称等效。

幅度百分比、噪声峰值和最终I/Q量化统一使用最近整数、恰半数取偶(ties-to-even，如±0.5→0、1.5→2、-1.5→-2)，分别可用Decimal ROUND_HALF_EVEN和numpy.rint/Python round实现，并补边界测试。FIR和噪声后量化，逐点检查[-2048,2047]。越界明确拒绝加载并提示降低信号/噪声/抽头增益；不暗中clip、自动归一化或允许PL截断。一般波形及数字调制均可经过本损伤模块，避免损伤只有工具界面而实际未接线。

数字调制页“加载调制”设置本次信号kind=digital；常见波形页加载设置kind=waveform。损伤页显示当前kind，应用损伤重新生成该kind的IQ；未选信号时提示先在对应信号页选择并加载。草稿kind与已加载kind分别保存；失败不能将草稿kind伪装为已加载。损伤应用覆盖新load，不在TX_ACTIVE时偷偷改缓冲。

## 组件与测试

`rf_iq_signal.py`为纯模块，提供PRBS、星座、RRC、数字帧生成与IQ损伤函数；函数只收显式参数，不访问模型/寄存器/socket。`rf_tx_waveform.py::build_tx_waveform`分派waveform=digital，并为全部信号应用optional impairment字典，返回实际sample_count/bytes。既有worker继续取冻结args生成payload，client照旧校验12位/4096点。

模型拥有RF digital/channel独立草稿、dirty/loaded取消、当前signal kind与target计划；视图复用概念和单位输入，按target切换数据/范围/能力文案；唯一控制出口明确RF load/start/stop与ACM command区别。

验证：PRBS127周期与已知前缀、PRBS→符号MSB顺序；全部星座点映射；RECT符号长度与周期；RRC奇异点有限、能量归一、频域高频抑制及周期卷积参考；frame3810有效/7620拒绝；FIR纯延迟和混合抽头的直接数值参考；独立IQ噪声seed一致/不同与边界，验证I/Q并非同一序列；±0.5舍入与全零归一；越界拒绝、disabled透明；所有模式metadata和little endian正确。真实UI按钮→模型→worker/ACM计划做双向路由负控，编辑/确认取消与STOP可达覆盖两target。

离线通过只能证明生成和软件调用契约。板上load/start、物理频谱、RRC接收端解调与损伤测量仍需独立证据，未执行标NOT RUN；本轮不开始TX1→RX1回环。
