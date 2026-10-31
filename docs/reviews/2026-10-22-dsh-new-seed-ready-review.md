# 新 seed 10 帧诊断包：离线准备收尾审查

日期：2026-10-22，北京时间约 19:55。模型交付已结束，主项目 task76/77 已结束。

## 决定

**本轮离线准备可收尾，可进入有界真实诊断。** 本结论不授予新的 RF 操作权限，也不是实验模型发布批准。执行前仅需完成已有运行前预检、取得当前接线确认及覆盖本批次数的新一次性授权；不再重开相同差分的离线审查。

## 本次独立验证

- 主线 SHA256SUMS 中 21 个交付文件全部匹配。
- runner SHA 为 a6a2e9183db267c5d19e1d2124d5cf312913c88137e102e694f52e88ba20a212，与交付一致。
- runner/fourclass/modrec4 相关测试：15 passed，0.46 秒。
- 真实 build_tx_waveform → LiveUiSession 记录方法：源参数准确进入 worker_args，daemon metadata 保持五个协议字段，实际 payload 为 10160 字节，load_ok=true。
- 模型贯通夹具重放到独立输出目录：4 帧、2 valid、2 invalid；payload 摘要错配与 worker seed 错配均被正确排除。
- original/A/C0/C1 及共同 baseline 的摘要锁定全部通过。
- raw 目录不存在，新真实 IQ 数量为 0。本次没有连接设备、发射、采集或安装。

主线已新增信号处理进入既有 cleanup，并在取消/超时后使用独立连接读回；无法确认时保留 UNKNOWN/RECOVERY_REQUIRED，不继续采集。DSH 报告 17 项 fake 全链路通过；本次独立复跑范围为上面的 15 项 runner 测试与模型贯通检查。强杀分支在 Git Bash 的测试限制照实保留，不再为此增加一轮开发阻塞。

## 下一次执行范围

保持当前发布 4776，不安装实验模型。按现有 plan 顺序采集：OFF、BPSK2、QPSK38、QAM16 74×2/75、QAM64 125×2/126、OFF。

预算为 10 sessions/10 init/10 load/8 TX/10 captures，无重试、无补采。每帧超时 120 秒、研究派发预算 900 秒；取消宽限与独立读回属于收尾，不增加采集次数。TX-on 仍未独立测量，不把名义每帧 1 秒/总 8 秒或采集窗写为已验证上限。

主项目一次完成：预检 → 在新授权内采集 → 独立收尾读回 → 下载并校验原始证据 → 本机运行既有四模型评估 → 输出逐帧和逐类结果。分类错误如实保留；结构/身份/存储/RF 状态异常按既有条件停止。部分结果仍保存，不自动补齐。

本机评估入口：

```powershell
ml_training\.venv\Scripts\python.exe -B ml_training\fourclass_seed_validation_eval\seed_validation_eval.py --out-dir ml_training\fourclass_seed_validation_eval\results\new_seed_eval
```

诊断结果不自动加入训练，不自动成为发布批准。当前模型会话无需新增训练或空转等待任务；主项目可以直接调用已交付的评估脚本，避免再次人为分段派发。

## 尚需用户提供的执行前信息

旧 17:00 接线窗口已过、task71 一次性授权已消费。新实验必须使用当前真实接线确认时间和覆盖上述预算的新授权；不能改写旧确认时间或复用旧 nonce。只需一次性补齐这两项，不把已完成的离线工作重新请求授权。
