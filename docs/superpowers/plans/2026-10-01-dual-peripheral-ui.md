# Dual Peripheral UI Implementation Plan

> **For agentic workers:** REQUIRED: Use $subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** 让ACM2108与AD9361共享便捷操作，正确路由波形/调制/损伤，修复单位输入与触摸响应。

**Architecture:** 已批准规格为 `docs/superpowers/specs/2026-10-01-rf-awg-ui-continuation-design.md`。生产候选位于 `<LOCAL_PATH>`，基线 `acadbdc`，已快照集成树9个脏文件；原树不被整体覆盖。纯模型保存目标与独立草稿，UI使用单位转换纯函数；RF波形及调制生成器生成IQ，既有worker/client是唯一RF出口。

**Tech Stack:** Python/PIL/numpy/pytest，现有PYNQ与native RF daemon；不改RTL、不重建Vivado。

## Chunk A: 目标切换、单位与输入响应

### Task 1: 统一交互与调度

Files: create `pynq_overlay/sw/ui_units.py`; modify `ui_model.py`, `ui_view.py`, `pynq_status.py`; test `pynq_overlay/sw/tests/test_dual_peripheral_ui.py`, `test_ui_units.py`, `test_touch_latency.py`。

- [x] 写失败测试：`parse_frequency('100','MHz')==100000000`，`parse_frequency('0.1','kHz')==100`，拒绝亚Hz/负/多点/溢出；单位切换等值且取消不调用setter。
- [x] 写失败测试：模型默认ACM2108，固定选择按钮可通过真实按下/抬起路径选择AD9361；草稿互不覆盖；RF运行时可切换编辑对象且STOP可达；事务中拒绝切换；旧确认取消；任何外设切换零硬件调用。
- [x] 冻结dispatch时target和草稿参数副本；延迟结果测试：提交ACM动作后程序化改变选中目标，结果仅更新ACM applied/observed，RF亦然；未知状态拒绝受影响硬件动作但仍可本地选择，确认/异步更新均不误路由。
- [x] 写失败测试：TouchCursor第2帧抬起立即触发，I2C每tick读取一次，切页当帧内容/命中可见性正确，签名不变不整屏重画。
- [x] 运行 `python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_ui_units.py pynq_overlay/sw/tests/test_dual_peripheral_ui.py pynq_overlay/sw/tests/test_touch_latency.py -q`，确认预期缺失行为导致失败。
- [x] 最小实现Decimal单位模块、键盘小数点/单位选择、设备目标模型与固定按钮、活动设备停止入口、草稿路由。默认旧API保留ACM行为，AD9361不误发AWG SET。
- [x] 采样触摸先于replay；当帧输入后重新取得页面和sync。主循环20ms输入tick，0.1秒状态周期，慢采集独立；避免同帧双读GT911与网络等待。
- [x] 同命令确认绿，再运行 `python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_ui_view.py pynq_overlay/sw/tests/test_rf_touch_view.py pynq_overlay/sw/tests/test_pynq_status.py -q`。Windows特有fixture限制单列，不能放宽业务断言。
- [x] 实现者自审并提交仅本任务文件；独立规格审查后再独立质量审查。

## Chunk B: 波形正确性与常见波形映射

### Task 2: IQ生成及模型映射

Files: modify `pynq_overlay/sw/rf_tx_waveform.py`, `ui_model.py`, `ui_view.py`; tests `test_rf_awg_alignment.py`, `test_rf_waveform_quality.py`。

- [x] 失败测试：方波cycles1/17/32的周期相位与FFT主峰；双音amplitude和>2047拒绝；Pulse0/50/100占空比；零幅度所有波形零IQ；频率输入1MHz/Fs60MHz得到17cycles和996093.75Hz。
- [x] 运行上述测试确认红。修正Square相位，Pulse加入`duty_pct`；全部12位范围校验；旧周期/码值模式保留。
- [x] 加入基带频率/百分比/占空比草稿，使用已应用Fs生成有效周期。普通/高级模式有明确切换，不混用旧值，低于一步进和超过可表示范围拒绝。
- [x] UI名称选择取代编号；字段按波形显隐，复杂参数分页；描述CW/Noise/双音/Chirp与频率量化。RF初始化LO70MHz..2.1GHz、Fs<=61.44MSPS，与生成器/模型/键盘一致。
- [x] 运行 `python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_rf_awg_alignment.py pynq_overlay/sw/tests/test_rf_waveform_quality.py pynq_overlay/sw/tests/test_rf_touch_model.py pynq_overlay/sw/tests/test_rf_tx_transport.py -q`，再复测所有RF控制测试。
- [ ] 自审提交、规格审查、质量审查；生成并核看全部波形状态PNG。

## Chunk C: 数字调制与信道损伤双后端

### Task 3: 有限帧复数IQ后端及共享工具页

Files: create `pynq_overlay/sw/rf_iq_signal.py`; modify `rf_tx_waveform.py` dispatch、`ui_model.py` RF工具草稿/计划、`ui_view.py`工具显隐/后端标签、`pynq_status.py`目标计划路由、`rf_status_worker.py` load生成器入口。测试 `test_rf_iq_signal.py`, `test_dual_backend_routing.py`。

- [x] 先将独立C契约写到 `docs/superpowers/specs/2026-10-01-rf-modulation-impairment-design.md` 并审查：PRBS7、BPSK/QPSK/QAM16/QAM64、RECT/RRC、帧长/整数每符号采样、周期FIR/独立IQ噪声、峰值/种子与无长流承诺。
- [x] 写失败测试：已知PRBS序列、星座点/Gray映射、符号率与元数据帧长、RRC频谱/周期卷积、FIR可验证脉冲响应、噪声可复现、12位溢出拒绝、低速和长帧拒绝。
- [x] 写生产路由失败测试：ACM2108共享按钮发原有command plan；AD9361生成独立load计划并走RF worker；非目标设备零调用，失败不伪装成功，STOP两侧明确可达。
- [x] 运行 `python -m pytest -p no:cacheprovider pynq_overlay/sw/tests/test_rf_iq_signal.py pynq_overlay/sw/tests/test_dual_backend_routing.py -q`，确认红后实现纯numpy生成器及统一UI后端。
- [x] RF软件有限帧不承诺任意低符号率，现有USER长流明确不可用；模型和UI显示Fs相关范围、实际帧时长与重复特征。所有修改使加载状态失效，编辑不发送命令，真实发射仍遥测证明。
- [ ] 运行新增测试及PYNQ全部回归，针对性worker/client二进制测试；自审提交后规格/质量审查。

## Chunk D: 交付与源码整合

2026-10-01 后续修复：历史 `45e756` 的“部署通过”未验证完整 UI，实际缺少 rf_iq_signal/ui_units 导致状态页退路。本轮已修正软件 map、安装/启动严格验证和 LF，部署 `622838c506a8359f8d91423d9169a92f`，712 项回归及真实 panel 启动/帧缓冲核验通过。详见 `docs/reports/2026-10-01-session-repair.md`。以下旧验收数值/部署声明保留为历史记录；主树 RF daemon 源码构建闭环与物理验收仍未完成。
- [x] 集成 B/C 后离线 host 全量回归通过：`pytest pynq_overlay/sw/tests` 697 passed, 1 skipped；未运行 PYNQ/RF binary 与上板项，记录 NOT RUN。

- [x] 生成800×480截图：两设备发射/调制/损伤、单位键盘、复杂波形、运行、错误与确认。截图已生成至 evidence/2026-10-01-ui-implementation/；修复 AD9361 数字调制页峰值码/加载调制 5px 重叠后，程序化检查 12/12 通过；实体屏仍待验收。
- [x] 运行适用PYNQ/RF/host回归，记录平台限制、源hash、命令与退出码；没有上板证据的项目记录NOT RUN。已运行 pynq_overlay/sw/tests: 697 passed, 1 skipped, exit 0，记录见 evidence/2026-10-01-ui-implementation/test-report.md；已通过 fpga-runner 部署 release 45e75640323a61ed4df15545ad92b1b0，板卡 pynq-status/rf-daemon active；物理回环/频谱验收仍 NOT RUN。
- [x] final独立规格与质量审查。源树原始hash复核后，仅回写候选新增/修改文件；D:主工程重叠文件做三方比较，保留原先修复，不整树复制。
- [x] 新模块加入immutable release的软件清单/install流程并测试，准备可审阅软件产物。既有bit/HWH/daemon身份不得推定。软件清单/发布说明已准备于 docs/reports/2026-10-01-dual-peripheral-ui-software.md；release 45e75640323a61ed4df15545ad92b1b0 已在 VM 重建并部署到板卡，rf_deploy 返回 PASS；物理回环/频谱测试未运行。
- [x] 更新能力矩阵、测试结果、启动/发布入口与后续物理验收项。已更新 capabilities、发布说明与 release 入口；物理验收（示波器/回环/实体触摸）仍待进行。

