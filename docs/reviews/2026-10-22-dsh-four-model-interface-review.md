# DSH 四模型评估入口审查：TX 记录字段需对齐

时间：2026-10-22，北京时间约 19:30。模型会话本轮已结束；主项目取消/收尾任务仍在执行，未视为交付完成。

## 当前进度

模型线已交付四模型比较入口 `ml_training/fourclass_seed_validation_eval/seed_validation_eval.py`。新 10 帧 raw 尚不存在；本轮未发生新训练、采集或部署。

Codex 验证：

- `load_models()` 对 original/A/C0/C1 的 CNN、规则和共同 baseline 摘要校验全部通过。
- 在独立输出目录运行既有小夹具：PARTIAL，3 帧、2 valid、1 invalid，四模型均完成接口调用。
- 没有连接板卡或修改产品/DSH 源文件。

夹具通过不代表真实输出结构已接通。下面的问题已用真实生成器的输出在内存复现。

## 必修接口问题

真实 `rf_iq_signal.generate_digital_frame()` / `rf_tx_waveform.build_tx_waveform()` 返回的 daemon metadata 为：

```json
{"samples":2540,"bytes":10160,"format":"s16_iq_le","direction":"tx","wrap":true}
```

task73 在 client 调用边界把该对象记为 `tx_load_evidence.tx_load_metadata`，这是正确的实际协议记录。波形源参数则来自 worker 实际接收的 `supplied_args`，由 worker 返回为 `args`，runner 保存为 `tx_load_evidence.worker_args`。

但是：

- 模型 `audit_frame()` 从 tx_load_metadata 查找 mode/seed/amplitude/filter/rolloff/sample_rate/symbol_rate/waveform。
- 主线 `check_frame.py` 约 87–94 行也使用同一错误来源。
- 小夹具人为把源参数放入 tx_load_metadata，掩盖了真实对象只有五个协议字段的情况。

复现：同一个有效 BPSK 夹具，在内存中将 tx_load_metadata 替换成真实生成器 metadata，同时保留完整 worker_args。原 fixture audit 的 errors=[]，替换后产生八个 tx_load_arg 错误及 mode/seed/amplitude truth mismatch。IQ 和 payload 摘要没有改变。

因此实际新采集会被当前校验误拒；这是运行接口不匹配，需要在实测前修复。

## 统一字段约定与最小修复

1. `worker_args`：校验实际 worker 调用的源配置 mode/seed/amplitude/filter/rolloff/sample_rate/symbol_rate/waveform，并和 plan/truth 比较。缺失时明确报告，不从模型、RX expected_mode 或请求文件补造。
2. `tx_load_metadata`：保留 daemon 原始协议对象，核对 samples/bytes/format/direction/wrap 与实际 payload 和计划一致。
3. `payload_sha256/payload_length`：继续取实际发送 bytes 的记录，与独立生成的真值摘要/长度比较。
4. 保留 load_ok 和真实 response。不要给 daemon 请求添加其协议不支持的波形参数，也不要改产品 worker 来迎合夹具。

## 分工与结束条件

- 模型会话负责 evaluator、fixtures 和 HANDOFF 的字段修正。
- 主项目原代理在当前收尾任务中同步修改 check_frame.py/fake 数据，不重启或重复派取消任务。
- 用真实 build_tx_waveform → 实际 recorder/_finalize_load_evidence → 校验器的离线贯通测试，假的仅为传输响应。覆盖一个 ON、一个 OFF，以及 payload 摘要/worker_args 错配反例即可；不需要重跑训练或全部历史测试。
- 两边确认同一份结构契约后，模型线结束本轮开发，等待实际新数据。主线继续完成既有取消与独立读回；不要为此增加新的审查角色。
- 新数据就绪后直接运行已锁定四模型的比较入口。本次没有新的 RF 授权，不能复用过期窗口或已消费记录。
