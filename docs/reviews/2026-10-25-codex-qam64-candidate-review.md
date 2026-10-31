# QAM64/RECT 候选一次增量审查 — 2026-10-25

结论：**APPROVED_FOR_TRIAL_PREPARATION**。在本次检查范围内未发现 P0/P1；允许复用以下候选的代码、安装包布局及现有 ARM 证据，继续制作试部署工作包。**尚不存在的 QAM64 trial stage、验收入口和物理 runner 不在本次批准范围内；本报告不证明安装或真实接收已经通过，也不授予板端操作权限。**

审查者：当前 Codex 对话，未参与本轮 QAM64 实现。未派发子代理，未连接板卡，未安装、切服务、初始化 RF、发射或采集。

## 受审材料

- 候选：`4ea1c0a68f03773fe1a77d1876ab1548`。
- 父/回退版本：`cd76818afc3bee7c610678574e892b26`。
- 合同：`cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1`，64 个 software 成员。
- 候选包：`evidence/rf-teaching/qam64-rect-product-2026-10-25/candidate.tgz`。
- SHA-256：`5dd1b2eeb15befbffaf0e181fbf53ad19b374245ddb2457e3270501ba44ddb91`。
- 父完整包：`evidence/rf-teaching/qam16-rect-product-2026-10-24/bundle-layout2/candidate.tgz`，SHA-256 `440ba377a84be4ac2ee6e6cd85980106271cff4a26863235cb84718bc8273c17`。
- 当前流程依据：`docs/requirements/development-review-policy.md`。不复用旧 QAM16 候选批准作为本新候选的批准。

## 检查及结果

1. 从父、候选实际归档逐字节比较，软件变化限于 `install.sh`、`merged_release.py`、`rf_status_worker.py`、`ui_model.py`、新增 QAM64 receiver/pipeline 六项。bit、HWH、daemon、六类模型、modrec4 模型未变。
2. `verify_release(require_software=True)`、`verify_panel_imports` 成功。归档为 138 个常规文件，无重名或路径穿越；74 个 release 文件与冻结目录一致，64 个源路径副本与 manifest 摘要一致，真实 `pynq_overlay/sw/install.sh` 存在。未重现旧 release-only 安装布局缺陷。
3. 阅读冻结 receiver/pipeline 与 worker/UI/installer/合同差分。QAM64 为显式模式，参考表与独立参考一致；同步和增益使用候选对齐窗，比较区独立计数 384 位；删除了曾导致比较区污染同步的全帧均值。失败无 BER，EVM unavailable。同帧、版本/摘要、配置清理与取消沿用现有产品路径。
4. 本次重新执行 QAM64 receiver/pipeline/product、QAM16 product、QAM16 runner 测试：**169 passed in 7.03 s**，无 xfail。另执行安装合同测试：**6 passed in 0.13 s**。与 DSH 历史 163 项统计使用范围/时点不同，以各自日志为准。
5. ARM 证据索引 19 个文件摘要均匹配；实际上传/下载包均为上述候选。固定输入 18 个测量字段 host/ARM 一致，0/384、valid127、align378、start63；五次计算耗时 2.550824–2.997835 秒，median 2.554862 秒。该结果仅证明候选在 ARM 的固定输入计算，不证明 worker 全链耗时、ARM 取消延迟或真实 RF。
6. capture deadline 已通过参数实际传给会话，默认仍为 15 秒、QAM16 runner 显式为 10 秒。不能将 QAM64 单纯算法耗时当成完整采集和收尾时限。

机器检查与新测试日志：`evidence/codex-qam64-review-20261025/`，包括 `candidate-verification.json`、`related-tests.log`、`install-contract-tests.log`。

## 非阻塞注意事项

- 工作树 `rf_environment_baseline.json` 与候选不同；候选基线与父包逐字节相同，六项受审软件均与工作树相符。初次检查把“所有工作树文件都应相等”作为过严断言而失败，进一步比较已确认不是候选损坏。后续从实际冻结包取文件，不从可变工作树重新组装或替换基线。
- ARM 单帧约 3 秒，本阶段可作为有限快照能力推进；未证明实时吞吐。先给无 RF 包内验收合理总预算，记录 worker/取消耗时，不为优化性能重开整套算法开发。
- 原 QAM16 物理结果继续有效；RRC 历史 1/112、2/112 问题仍未解决，不被 QAM64 离线结果关闭。

## 下一工作包与剩余审查范围

并行制作：（A）基于既有 layout2/trial wrapper 的完整 stage、QAM64 无 RF 固定 IQ 验收、回退和预检；（B）四帧 QAM64 真实接收 runner 及假链，当前不执行 RF。Lead 负责共同入口与最终交付。

新阶段材料交付时仅检查新增验收/stage/runner 差分，不重复审查本候选、不重复固定输入 ARM 计算。试部署成功条件预写：`TRIAL_ACCEPTED`、真实验收输出、candidate current/active、服务正常、独立 RF 安全读回及 boot 一致；功能失败走既有事务回退到父版本，UNKNOWN 先只读观察同一作业。

候选包字节保持不变时，延续本报告。若实际修改六项产品软件或其他交付成员，按真实新字节生成身份并补受影响验证。制作文档、stage 或 host runner 本身不自动使未改候选失效。
