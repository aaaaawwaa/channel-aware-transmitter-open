# SDR 测试台一次独立增量审查

日期：2026-10-19。审查者：独立代理 `sdr_incremental_review`。结论：**APPROVED**，本次范围无阻塞项。

## 范围与依据

按项目 `docs/requirements/development-review-policy.md` 的 R2 一次增量审查，核对 `radio_test.py`、`scope_test.py`、`config/pluto.json`、`run.ps1` 和 `test_radio_test.py`。未修改实现、未执行实际 TX、未访问 COM11 或 FPGA 板卡。

批准范围：用户确认 Pluto TX 接示波器 RX1（按 CH1）后的基础冒烟；默认 LO 433.9 MHz、偏移 100 kHz、幅度 0.25、TX 衰减 40 dB、持续 2 秒。不是对 RF 输出功率、调制质量或完整 SDR 收发性能的验收。

## 核对结果

- Pluto 按明确串号发现并二次读回 hw_serial；示波器按明确 VISA 资源与 IDN 检查。实现不打开串口，COM11 不在控制路径中。
- 配置限制有限数值、RF 范围、采样率、幅度、衰减和最多 5 秒时长；TX 另要求真实接线描述。
- GNU Radio 流程正常/异常路径均进入停止和释放；异常转为文本后离开持有 USB 对象的栈，再重新打开 libiio 做收尾。
- TX 收尾写最大衰减及 TX LO powerdown，并验证 powerdown 读回；失败记 CLEANUP_FAILED，不能作为成功结果。
- 接收数据只声明 RX_STREAM_PASS；TX 只声明 TX_STREAM_COMPLETED_RF_UNVERIFIED。示波器带宽不足的结论明确保留，没有把设备识别或流完成当成 RF 验收通过。
- 独立执行 `<LOCAL_PATH> -X utf8 -B -m unittest discover -s sdr_testbench -p test_radio_test.py`：5 项通过。

## 必须保留的实际限制

1. 示波器的三次 `during` SCPI 查询不保证全部落在 2 秒发射窗内；本次仅作观测，不能据此证明 434 MHz 单音已输出。70/100 MHz 带宽亦不足以确认该载波。
2. 驱动挂死、进程强杀、断电时无法保证 Python finally 执行。scope 包装器超时强杀会留下 cleanup_required；须现场断开 Pluto USB/电源并确认停止后再试。不得宣称所有故障均会自动关断。
3. 本审查允许执行已授权冒烟，不预先判定实际硬件测试通过；实际结果以新增 result.json 和收尾读回为准。

## 受审文件 SHA256

| 文件 | SHA256 |
|---|---|
| radio_test.py | 9E15D295CACC01D0F152B3AB818B093EC1795D1EEE067251B9C54A1C7876DE45 |
| scope_test.py | 0E7206C3ECEAC434BDC66C57A74459C4D1326E22A0A7709C8825E50127D26E7B |
| config/pluto.json | A5CB201297BF432E3A20E28F867902ABDC44AC4659F010184617B496B07D9219 |
| run.ps1 | F89E7AA6C958F74CD0E876D48A159E48857E9B2997A2FB09A60B0E6333BC6CA5 |
| test_radio_test.py | F71D0EFB31ECD54EC0425E181D75FEB996F94D70075453AADEB4FB98E8D5367C |
