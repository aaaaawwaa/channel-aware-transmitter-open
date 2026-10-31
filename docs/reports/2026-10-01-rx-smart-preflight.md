# 接收与CNN设计前置核查

2026-10-01。本轮完成官方输入电平资料核对、真实回环复验及接收链设计核查；尚未实施接收UI接入或CNN训练。

## RX输入电平

AD9361 Rev.G Table11的单端RF绝对最大值为+2.5dBm，ADI官方支持文按50Ω正弦换算约0.843Vpp。这是损伤边界，不能称正常工作的最大输入。UG-570 p35内部ADC节点0.625Vpeak满量程/0.5Vpeak建议值不等于板卡RX1插口Vpp。当前slow-attack AGC，实际线性工作上下限仍需50Ω电平校准、频点分支损耗和增益记录；功分器“插损优于6.2dB”不能作固定衰减器，1MΩ示波器分支不等于RX1 50Ω端口电压。

官方来源：

- https://www.analog.com/media/en/technical-documentation/data-sheets/AD9361.pdf
- https://support.analog.com/en-us/knowledgebase/article/6EFN2248
- https://www.analog.com/media/en/technical-documentation/user-guides/AD9361_Reference_Manual_UG-570.pdf

## 本轮物理回环

命令：`python scripts/rf_loopback_acceptance.py --expected-release 56d0d0d4bd36d4e22d1c030926917a16 --tap 29 --amplitude 150 --cable-confirmed --output evidence/2026-10-01-rx-smart-preflight-loopback`

退出码0、remote_exit_code=0、passed=true。LO80MHz/Fs60MSPS/RX与TX带宽30MHz/内部40dB，TX1经用户确认的功分器到测量仪与RX1。3帧4096点加OFF前后对照：±1.875MHz均命中，最小局部谱峰突出度48.88dB、ON/OFF差41.53dB、削顶0。ON最大I/Q分量绝对值1389码，约12位满幅的67.9%；这是输出ADC码值统计，不是RX插口电压标定或上限测试。

数据：`evidence/2026-10-01-rx-smart-preflight-loopback/raw-iq.json`和`analysis.json`。结束明确control.disarm成功，PL source_selected=false、RX armed=false。readlink与systemctl复核发布仍为56d0…，pynq-status与rf-daemon均active，panel up日志存在。没有新Overlay或PL综合。

## 工程集成与新增工作

现有AWG→RF的常见波形、BPSK/QPSK/QAM16/QAM64、PRBS7、RECT/RRC和有限帧信道损伤软件路径已存在；它们不等价于任意低频电压、长流或独立双RF输出。当前硬件正弦回环通过，全部调制与损伤的真实验收尚未完成。

接收页面有数据契约和控件，但capture按钮被禁用，RF client只处理JSON结果，不能直接接收daemon iq_frame。ratio为0..4选择码，Fs为抽取前；wire没有config/session字段，需要本地锁与采集前后一致性绑定。单次capture目前保持CAP_ARM高，tx.stop未保证清理DONE后的ARM；正常接收接入应补daemon有限采集收尾，不能把DONE当解除布防。

用户本轮选择“小型CNN，再完善设计”。候选规格：`docs/superpowers/specs/2026-10-01-rx-smart-design.md`，三层Conv1d+GAP+6输出、1662参数、约128万MAC、2×4096输入，宿主机PyTorch训练、板端numpy推理、实测分组与未知负控。当前Python3.8系统与host环境、bundled Python3.12均无torch；批准后建立工程内训练环境。设计候选的性能和准确率是验收目标，不是现有训练结果。
