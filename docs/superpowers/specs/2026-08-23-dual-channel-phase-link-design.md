# 双通道相位联动(相位差可调)设计

**Date:** 2026-08-23
**Status:** 已确认(brainstorming 完成)
**Next:** writing-plans 生成实施计划

## 背景

AC820 AWG 当前双通道完全独立:各自自由运行的 DDS 累加器,即使同频也无确定相位关系。
电赛定位下,双通道相位差联动(李萨如/相位差测量题考点)是高频需求。

## 功能定义

**相位联动(PHASELINK)**:CH2 专属开关,语义为「CH2 跟随 CH1」。

开启后:
1. **CH2 频率强制跟随 CH1**(改 CH1 频率,CH2 同步变化,始终同频)
2. **CH2 输出相位 = CH1 相位 + 相位差**(0~360.00°,实时可调,改滑块立即生效、不重新触发);CH2 保留独立的静态相位偏移
3. CH2 的**波形/幅度/基波/谐波仍独立可调**(相位差模式下谐波跟随整体移相,复合波形整体平移)
4. CH2 自身调制参数被忽略:CH1 的 FM/SWEEP 瞬时频率路径由 CH2 继承,CH2 自身的 AM/PM 注入被抑制;GUI 联动禁用 CH2 调制区

关闭后:恢复完全独立行为,互不影响。

**相位采样延迟**:CH1 累加器的组合路径被 CH2 下游逻辑时钟采样,实际关系为
`phase_ch2[n] = phase_ch1[n-1] + offset`,约有一个 50MHz 时钟周期延迟。相位误差随频率增长,
约为 `360 × f_hz / 50e6` 度;本设计不宣传为任意频率下严格零延迟。

**相位差精度**:协议单位 0.01°(`SET CH2 PHASE 9000` = 90.00°),硬件 32 位相位字(实际输出粒度受 8 位查表限制 ~1.4°,设置精度 0.01°)。

## 硬件改动

| 模块 | 改动 |
|---|---|
| `dds_core.v` | 加 `phase_ext_valid` + `phase_ext[31:0]` 输入:valid 时 `phase_acc <= phase_ext`(主从),否则自累加(现状);加 `phase_acc_out[31:0]` 输出(CH1 主相位源) |
| `harm_synth.v` | 同样加外部相位注入(`acc0` 主从模式:valid 时 `acc0 <= phase_ext`) |
| `awg_top.v` | 联动 mux:`link` 时 `fword_inst2_used = fword_inst1`;`phase_ext2 = phase_acc1 + off32`;CH2 的 `pm_off` 置 0、`am_gain` 用 OFF 模式值(调制抑制) |
| `reg_bridge.v` | 拆新字 `gpio10 = phase_off[31:0]`;`gpio11[0] = link_en`(32 位全周期相位字,联动使能独立占用 bit0) |
| `create_bd.tcl` | 加 `axi_gpio_5`(dual 32-bit all-output)+ M05_AXI 地址映射(沿用现有 foreach 模式) |

**相位推导**:CH2 相位 = CH1 phase_acc + off32(组合加法,由 CH2 的 dds_core/harm_synth 各自打拍采样)。
off32 是静态偏移,CH1 累加时 CH2 逐拍跟随;由于采样时序,实际为前一拍 CH1 相位加偏移,而非严格零延迟。

## 协议改动(PS 桥 sdk/src/main.c)

```
SET CH2 PHASE <0..36000>      # 相位差 0.01° 单位,0~360.00°;非法值报错 ERR
SET CH2 PHASELINK ON|OFF      # 联动开关;GET CH2 回读
```

PS 侧换算:`phase_off[31:0] = (angle_0_01 × 2^32) / 36000`(整数向下取整),写入 gpio10;
联动使能写入 gpio11 bit0。GET 回读:现有格式扩展 PHASE(0.01° 整数)与 PHASELINK。

## GUI 改动

- CH2 面板加**联动区**:复选框「跟随 CH1」+ 相位差滑块(0~360°,0.01° 精度)+ 数值显示
- 联动开启时:CH2 频率控件、调制区禁用(与硬件一致);关闭恢复
- **预览叠加双通道**:`WavePreview` 加第二条曲线(洋红);以 CH1 为主,联动时叠加 CH2;
  `make_plot_data` 加 `phase_off` 参数(绝对时间轴机制天然支持:同一窗口 CH1/CH2 相位差直接可见);
  FM/SWEEP 共享 CH1 调制路径,调制源黄线保持;图例 3 条
- 状态回读显示相位差

## 验证

| 层 | 用例 |
|---|---|
| RTL TB | dds_core 外部相位模式(注入值采样、自累加恢复);awg_top 联动(同频、采样延迟相位关系、off 变化即时生效、谐波整体移相、关联动恢复独立) |
| 协议 | test_bridge.py 加 PHASE 范围/非法值、PHASELINK 回读用例 |
| 示波器 | scope_verify.py 加相位差用例:CH1/CH2 双探头,90°/180° 两档实测(优先 `:MEAS:PHAS?`,不可用则过零时间差换算) |
| 上位机 | pytest:联动复选框置灰联动、滑块发协议、预览叠加渲染 |

**板级验证步骤**:先执行 `bash scripts/program_board.sh`;若 SDK 工作区未重新链接/构建或 PS 桥未运行,
先重新链接/构建并执行 `cli-anything-vivado sdk run`,再运行 `python scripts/test_bridge.py COM11`
与 `python scripts/scope_verify.py COM11`。后两项需要真实板卡,其中示波器闭环还需要 DSOX1102A,
不能在本机无硬件条件下宣称已运行。

## 实施顺序

1. create_bd.tcl 加 axi_gpio_5 + 地址映射
2. reg_bridge 拆 gpio10
3. dds_core / harm_synth 外部相位模式
4. awg_top 联动 mux
5. RTL TB 扩展 + xsim 仿真全过
6. sdk main.c 协议命令(PHASE / PHASELINK)
7. build_bitstream.py 重建 + program_board.sh 下载
8. test_bridge.py 扩展(板级)
9. scope_verify.py 相位差用例(板级)
10. GUI 联动 UI + 预览叠加 + pytest
11. README / CLAUDE.md / 协议文档更新,提交

## 不做(本次范围外)

- 双通道频率联动之外的耦合(如幅度联动)— 保持独立
- 任意波形 / 数字调制 — 后续独立方向(联动使能已使用 gpio11 bit0)
- 相位差的调制内联动(联动时调制本就抑制)
