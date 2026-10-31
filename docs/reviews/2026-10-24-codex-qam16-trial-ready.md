# Codex 独立增量审查：QAM16 无 RF 试部署

日期：2026-10-24。审查者：当前 Codex 对话。此结论来自对实际文件的独立核对及本轮复跑，不引用无返回的 Codex CLI 调用作为批准。

## 结论与范围

**APPROVED_FOR_LIMITED_NO_RF_TRIAL**：未发现阻塞下述冻结候选进行一次无 RF 开发版试部署的问题。范围为一次安装、包内 120 秒 fixture 产品链验收、失败时既有事务回退父版本，以及独立读回。是否执行以用户给出的安装授权及新鲜板端预检为准。本次审查未连接板卡、未安装、未切换服务、未发射或采集。

> **2026-10-25 独立复核纠正：本冻结包的部署放行已被新增 P1 阻塞，不能继续按以上旧结论重派。** 原始板端日志明确出现 `Can't open .../unpacked/pynq_overlay/sw/install.sh`，随后 `INSTALL FAILED: restoring previous release and service units`。当前 candidate.tgz 只有 `release/` 树，而真实外层安装器需要完整源目录布局。前次审查漏查归档布局与安装入口的适配；111 项测试通过不覆盖这条真实安装调用。本记录保留历史审查和测试事实，但本包需要先修复打包/安装前布局检查，再只审相关差分。详见 `2026-10-25-qam16-install-archive-layout-blocker.md`。QAM 算法及既有 ARM 数值证据不因此失效。

该结论不证明已经部署、真实 QAM 接收通过、ARM worker 取消通过、实体屏验收通过或项目正式发布。RRC 历史 1/112、2/112 及根因 UNKNOWN 保留，不属于本次 fixture 试部署的阻塞项。

## 冻结身份

| 项 | 值 |
|---|---|
| 候选 | `cd76818afc3bee7c610678574e892b26` |
| 父/回退 | `bedf98c77283002c632dd7f345abaff9` |
| 合同 | `cnn-bpsk-modrec4-qpsk-rrc-qam16-v1`，62 个 software 成员 |
| 实际 candidate.tgz SHA256 | `181bf027a321da6d4ceadd664a7ea24fdaa1660918a72a908deb35e8fb2a4816` |
| 实际 trial-stage.tgz SHA256 | `0a847d19b2c247ff09f9777c0c8d30ebd7e459c0c2f60779fba7d4271dd114dd` |
| stage-sha256.json SHA256 | `345461624b16212d0b8c011b389c2059b3cee331b23e8c9e13f781286cdd199c` |
| rollback-parent.tgz SHA256 | `f17b53d8b0f8e16fbeb04f6714f941d4fcc3e043b703eb41bab78502ce4f95ac` |

包位置：`evidence/rf-teaching/qam16-rect-product-2026-10-24/bundle/`。只读审查入口及既定执行命令见 `2026-10-24-qam16-trial-codex-entry.md`；本结论优先于该入口旧的待审状态，不改写历史入口。

## 本轮核对及验证

- 阅读父候选至 QAM 候选的 `install.sh`、`merged_release.py`、`rf_status_worker.py`、`ui_model.py` 差分，以及新增 receiver/pipeline、包内验收程序、preflight、unit 和事务入口。新合同显式增加两个模块；原算法文件未改变。
- 候选压缩包内全部 release 文件与松散候选逐字节一致；候选 62 个、父包 60 个 software 摘要分别成立；实际 stage 清单各文件和 stage 压缩包一致。
- 本轮独立运行 `verify_release(require_software=True)`、`verify_panel_imports()`，并从实际冻结候选调用 worker 的 `_ensure_demod()`，没有 mock 能力加载；BPSK、QPSK、RRC、QAM16 能力均为 true。
- 独立复跑 bundle 6 项与 QAM receiver/pipeline/product 105 项，**111 passed in 8.75s**。其中从 stage 压缩包解出的实际验收程序在主机运行，覆盖 QAM 0/256、错误参考/域外、worker/UI 同帧、停止和配置清旧结果，以及 BPSK/QPSK/RRC 兼容冒烟；软件成员数错误反例确实失败。受测工作区六个新增/变更文件与冻结候选字节一致。
- 核对既有 ARM 输入、host-result、arm-result 与 compare 中各摘要一致，原始执行日志包含上传包身份和实测结果。同输入 0/256、测量字段一致、symbol 最大差 8.88e-16；655.36 ms 中位值是既有单输入 ARM 记录，本轮没有重新实测或扩大其证明范围。
- stage 的基础 `install.sh`、`install_state.py`、`trial-install.sh` 与此前 RRC 包逐字节一致；复用其既有事务回退验证。本轮不重复做真板回退演练。

机器记录：`evidence/codex-qam16-review-20261024/verification.json`；本轮测试日志：同目录 `pytest.log`。测试使用现有 Python 的 pytest/numpy，设置项目模块路径、单线程 OpenBLAS及工作区临时目录；不将其耗时作为 ARM 性能。初始验证命令遇到缺 pytest、缺项目导入路径及临时目录父路径的问题，修正本机运行配置后完整重跑通过，没有为此修改产品或验收源码。

## 执行条件与注意事项

1. 通过 Paramiko 先读取 current/active、服务、boot_id、存储、RF 空闲及残留作业。预期父版本仍为 bedf；不符时先确认，不按本包盲目重装。
2. 实际展开到 `/var/lib/rf-deployment-trials/rf-qam16-cd76818a/stage`，以既定 `/tmp/rf-teaching-install-rf-qam16-cd76818a` 链接供入口使用。核对远端 stage 和上传包摘要，再运行包内 preflight。元数据中旧的 `/tmp` durable_dir 文字不是实际持久目录，不据此删除现场证据。
3. unit 以全名 `rf-qam16-trial-cd76818a.service` 注册并只启动一次，不 enable、不创建通用 alias、不重复走另一条安装入口。使用包内 120 秒验收和既有回退事务，不手动额外调用 Overlay。
4. 成功必须同时有 `TRIAL_ACCEPTED`、通过的 trial-acceptance、实际 current/active 候选以及独立安全读回；失败依赖既有事务回到 bedf，并独立确认。SSH 中断只观察同一作业，不重新派发安装或并行回滚。
5. Windows 主机运行 `rf_environment_readonly_check.py --rollback-state` 时，参数必须是已经下载到本地的原始回退 JSON 路径，不能直接传板端 `/var/lib/...` 路径。先下载状态/日志，再独立读回；原始摘要保留。
6. 未确认终态和下载证据前不清理 stage/unit。安装成功不能写成真实 QAM RF 通过，历史 RRC 误码不能因此关闭。

以上是此次执行注意事项，不要求修改冻结包或重开资格。若实际候选代码、安装/验收行为或包内字节改变，明确记录差分，只重新核对受影响范围；不要改写旧摘要冒充同包。
