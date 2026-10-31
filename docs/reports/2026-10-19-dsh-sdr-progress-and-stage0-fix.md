# DSH进度核对与阶段0执行入口修复建议

时间：2026-10-19 18:40–18:44，Asia/Shanghai。读取本机最新DSH session与当前源码；未连接SDR/板卡、未改变接线、未发射、未中断或发送DSH消息、未修改DSH实现文件。

## 相比17:50有推进

- 主会话最新18:39:46，旧planfix复核已18:09结束：主要代码通过，P2父安装证明不兼容仍是阻塞。
- 新具名格式`2026-10-18-baseline`与相应验证器已经实现。作者报告真实56项材料的离线prepare/verify_bundle通过，新测试23项、受影响集合410项通过；不得把同为03b9b883的candidate/parent路线验证当成新部署。
- 新独立审查任务`189ef6b6…`仍在运行，最终结论本次未取得；另一任务`3a7babd9…`正在收尾旧planfix文档。
- `sdr_testbench/board_integration/`已生成工具、配置、接线/电平/能力/BPSK说明和31项离线测试。本次重新运行`--self-test`与31项测试均通过，没有硬件访问。
- 尚无新观测点候选的ARM资格/板端部署完成证据；原pilot仍8/9、passed=false，normal→CLEAR仍NOT SATISFIED。
- 主会话记录操作者已确认Pluto自回环接线，要求阶段0首次运行；但截至目录取样没有新结果。`sdr_testbench/results`最新仍为17:34旧RX，board_integration/results无新session。此结论仅针对已检查目录，不排除用户在其它位置运行，DSH应按命令/输出定位而非推定已通过。

## 需要先修的具体问题

### 1. 停止读回字段位置错误：真实结果必被拒绝

`sdr_testbench/board_integration/board_integration.py:_check_pluto_post()`从`after.channels.altvoltage1_out.hardwaregain`取增益。

真实原始收据` sdr_testbench/results/20261019-173154-376468-tx/result.json`中：

```json
{
  "altvoltage1_out": {"frequency": "433900000", "powerdown": "1"},
  "voltage0_out": {"hardwaregain": "-89.750000 dB"}
}
```

用实际函数离线重放该收据，得到：

```text
ExecutionError Pluto TX gain readback is None, not -89.75
```

应从TX LO通道读取powerdown，从TX电压通道读取hardwaregain，并分别检查。不要改写历史收据补字段。

### 2. 带单位增益解析错误

`_pluto_safe_off()`直接`float(gain)`，真实值为`'-89.750000 dB'`。用保存的真实字节转换已复现`ValueError could not convert string to float`。共享解析器应支持真实libiio的带单位文本以及合法纯数值，严格拒绝错误单位、空串、非有限值和不匹配值；不能只把测试夹具改成不带单位的值。

### 3. preflight成功不等于板卡空闲

当前`--preflight`分支在提供token-file时读取板卡poll并转字典，随后无条件设置`passed=True`，没有使用已有`_safe_snapshot()`判定板卡的online/stale/io_ok/TX/RX状态。因此板卡即使仍在TX，也可能打印passed=true。修复为确实执行相应状态门；纯Pluto预检与板卡预检的覆盖范围应分别记录。该命令会写Pluto关断状态，不应笼统称全只读。

### 4. 阶段0命令不能提供其放行条件

DSH要求OFF对照、无CLIPPED、发射前LO关闭时-60dB读回、结束双读回；给出的命令仍是`run.ps1 loopback`旧入口。当前`radio_test.py`只有一个ON流图，结果不包含OFF_PRE/OFF_POST、不计算削顶比例，也没有目标60dB在LO关闭时的独立读回记录。

这不是实测失败，属于**执行命令与要求证据不对应**。先实现小型阶段0编排/结果对象，保留USB分阶段所有权和总TX预算，不将未生成字段当成通过；不追加无关全仓测试。

## 下一步

1. 父证明独立审查让现有任务收尾，不重复启动复审；真实部署仍需新候选和对应ARM资格。
2. 只修阶段0及共享读回/preflight，使用本次真实收据加直接反例验证，不覆盖他人正在修改的文件。
3. 硬件控制修改做一次适用增量审查，随后在用户有效授权与实际接线范围内由DSH执行阶段0并自动保存结果，不让操作者手跑后再找输出目录。
4. 阶段0通过后，再给阶段A换线命令；A/B自己的OFF、削顶、异常收尾与判据须实质核对，不能从planner31项通过推断执行器已可用。434MHz新域结果仍不证明80MHz S2通过。

可复制指令见：`sdr_testbench/DSH-STAGE0-FIX-PROMPT.txt`。
