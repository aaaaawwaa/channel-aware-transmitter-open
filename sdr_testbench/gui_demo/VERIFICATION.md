# 本机离线验证与一次增量自检

日期：2026-10-26（Asia/Shanghai）。新增范围仅sdr_testbench/gui_demo/；未修改既有radio_test.py、板卡产品、RTL、模型、发布或已保存实验收据。

## 已运行

- Radioconda Python 3.12 / GNU Radio 3.10.12.0：test_profile.py，6/6通过。覆盖已有发送器样本一致性、默认配置、越界/错误采样率拒绝、显式接线与板卡TX关闭声明、结果不冒充RF验证及协作停止。
- verify_offline.py：真实GNU Radio Vector→Repeat→Multiply→Vector Sink链运行，4064个complex样本与既有发送器匹配，最大绝对误差1.5250577745096194e-09；未构造IIO块。
- make_grc.py + 本机grcc -o：bpsk_preview.grc编译成功，生成generated/bpsk_preview.py。
- 生成的GRC QtGUI程序实际启动、运行约1.2秒并正常stop/wait关闭。确认生成代码不包含fmcomms2_sink构造，硬件Sink处于disabled状态。
- bpsk_tx_gui.py：默认纯软件预览实际启动和关闭，检查时域、相对频谱、两点星座与中文标签；preview.png为此次运行截图，1450×960。
- --dry-run输出PREVIEW_ONLY/hardware_accessed=false，配置与默认JSON一致。

## 一次增量自检结论

默认启动不枚举USB/建立IIO/发射；只有显式按钮、实际接线描述、板卡TX关闭声明和确认弹窗后才启动既有有限发射器。实际启动参数逐次保存，不自动循环。停止通过文件请求使既有流等待路径抛出可处理异常，复用既有stop/wait和finally关断；关闭窗口同样不强杀子进程。15秒超时提示不等同于已关断。未确认LO关闭时禁用再次发射。旧任务定时器不会作用于新任务。

GUI只修改和展示自身参数/基带，不操控板卡，也不复用“回环捕获”。本地曲线和禁用硬件块均明确标识用途，不能替代板卡真实接收、同步或BER证据。GR预览与实际TX共用既有PRBS/量化发生器；GRC手动参数修改不会隐式改变独立控制GUI的JSON。

软件预览/配置部分通过上述检查；实际硬件发射路径仅是既有工具的GUI封装，**未在本轮硬件验证**。USB访问、当前序列号、实际RF功率/带宽、频偏、板卡接收稳定性、异常设备阻塞下的关断和当前物理接线均不能据这些离线结果宣称通过。未运行probe、TX、板端RX或任何自回环。

## 已修正的本机开发问题

本机grcc使用-o而非-d；GRC导入块实际ID为import（库文件声明import_经过加载归一化）；GRC求值阶段没有__file__，故流图由make_grc.py写入当前模块路径；Windows中文用户目录下FFT缓存引发字符编码异常，使用项目内XDG_CACHE_HOME；offscreen截图显式加载中文字体并设置QT_QPA_FONTDIR。正常桌面启动器不设置offscreen。

## 后续

使用README中的两个启动器进行拍摄。RF运行仅在当前设备和Pluto TX→板卡RX1接线确认、双方发射状态核对及约定实验预算成立后进行；本轮不执行。历史7个外部源未同步帧的流间歇/弱信号问题尚未由新增GUI解决。
