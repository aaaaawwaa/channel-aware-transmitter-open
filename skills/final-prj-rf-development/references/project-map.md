# 工程地图与开发入口

下列路径相对已确认的项目根目录；文件名是入口，不是对历史内容仍最新的保证。

| 流水线 | 源码与入口 | 构建/验证要点 |
|---|---|---|
| 辅助AWG/裸机PS | `src/rtl/`、`src/tb/`、`sdk/src/main.c`；PC入口`host/main.py` | `scripts/build_bitstream.py`会重建BD；`host/tests/`及对应桥/示波器工具验证这条链 |
| PYNQ板载Linux | `pynq_overlay/{bd,rtl,constraints,sw}`；`pynq_status.py`、`ui_model.py`、`ui_view.py` | `pynq_overlay/build.tcl`；软件测试`pynq_overlay/sw/tests/`；独立bit/HWH与合并目标先区分 |
| AD9361 RF | `rf_overlay/rtl/`、`rf_overlay/sw/board_app/`；`rf_status_client.py`、`rf_status_worker.py` | `rf_overlay/build.tcl`是独立RF目标；C假硬件测试在`board_app/test/`，主机`make host-test`/`host-test-iq`，ARM另核对 |

产品merged发布由`scripts/package_merged_overlay.py`、`pynq_overlay/sw/merged_release.py`和`install.sh`等既有入口管理。使用脚本`--help`及实际构建收据确定本次参数，不从旧文档猜一个bit路径。核对当前manifest的地址、软件合同、模型与daemon；Standalone RF/PYNQ bit不能冒充合并目标。

## 控制和显示

- `pynq-status.service`是唯一Overlay拥有者；加载并校验发布后报告READY，写active-release标记。
- `rf-daemon.service`依赖拥有者，按active-release映射PL；本机控制/状态接口是守护进程服务，不从UI直接MMIO。
- `rf_status_client.py`封装协议，worker执行后台操作，model处理草稿/状态，view绘制。`host/main.py`仅是辅助AWG上位机入口，不是RF产品唯一入口。
- 调试脚本需要当前板端解释器环境。PYNQ源码目录和XRT位置以镜像/服务文件为准；不要复制密码式sudo命令到新工具。
- Windows远程连接复用已有Paramiko封装；入口缺失先定位或补最小配置适配，不直接启动可能弹凭据框的后台SSH。

## 真正会影响结果的陷阱

- BRAM `Enable_32bit_Address`配错可造成空网表/读恒零；查BD及实际网表，不只看握手。
- RF `DATA_CLK`、PL AXI时钟、复位释放与I/Q半字流水级分开建模。仿真中共同时钟/固定理想延迟不能证明真实采样裕量。
- IDELAY公共tap寄存器不是逐线`CNTVALUEOUT`；mask只控制哪些线加载，上一点的逐线状态不会自动恢复。
- `src_cfg/cap_cfg`按具名位解释，commit-toggle不等于运行/布防。
- `.sh/.service`保持LF；修改锚点前确认唯一命中。Windows行尾变化不应被误认为QPSK/串口逻辑变化。
- 运行候选用`-B`，pytest用项目内唯一`--basetemp`，避免污染不可变发布和并行测试互踩。

只跑改动相关的测试。文档、小工具、采集判据、原生控制及RTL分别判断影响范围，未触及的模块不重新构建。
