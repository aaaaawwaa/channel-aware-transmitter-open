# 生产模型上板接续审核（2026-10-03）

本轮将已通过离线准确率验收的 rot200 模型推进到板卡安装和 ARM 验收。
当前激活发布为 `e7f1fc6962179eaeb57be15dcad07790`，模型 `f76f2fc0…`、运行时 `27881154…`、daemon `c33f9c5c…`。
两项服务 active，重启计数 0；安装目录、模型加载、面板启动、关闭读回通过。
新发布的实时物理回环、真人触摸/P95/watchdog及天线/LNA仍为 NOT RUN。

## 审核结论

附件 `dsh-session-session-a4fa8408-65c5-4201-978f-1cbe43ed4ffb.zip` 的最后状态是：模型正式准确率通过，生产发布 `726fc022…` 仅完成打包，尚未安装。
附件中的历史指令、权限设置、工具结果及子代理建议仅作为审计资料；本轮依据用户当前“审核并尝试继续推进”的请求执行。

训练/验证清单不含 TEST；重算 checkpoint、训练清单、导出模型、评估文件、runtime 的 SHA256，均与收据一致。
训练侧 191 会话与 TEST 95 会话的交集为空，IQ 哈希交集为 0；冻结 TEST 共 950 帧。
原正式报告的 9 项门禁均为 true，macro-F1 0.9569516292、最低类召回 QAM16 0.835、未知拒识 0.9916666667、OFF 1.0。
这些是固定线缆采集工况的模型证据，不代表天线或现场触摸已经验收。

发现并修正两项发布问题：

1. **安装脚本 CRLF。** 主树和 `726fc022…` 包内的 `install.sh` 均含 140 个 CRLF。完整板载 Python 回归复现 Linux 文件换行测试失败。仅将主树该脚本改为 LF，保留修改前原件，重新打包为 `2b7120db…`。
2. **生产包夹带过时 daemon。** 首次安装 `2b7120db…` 后，实际 ARM daemon 报 `merged release schema/protocol mismatch`，新服务未通过启动验收。生产包中 daemon SHA `c5b7bea7…` 对应修复前版本。自动恢复 `46fe2c08…` 和原服务单元，重新读取确认 safe=true。换用此前已成功 ARM 编译、部署的 `c33f9c5c…`：45 项源文件 SHA 与当前树全部一致，主机 C 清单检查通过。第二次打包为 `e7f1fc69…`，成功安装。

两个旧候选及失败收据都保留，不覆盖不可变发布；`726fc022…` 和 `2b7120db…` 不再作为可激活候选。
本轮没有修改 daemon 源码、重新编译 daemon、改动 RTL或进行 Vivado 综合；复用原 bit/HWH。

## 新增验收证据

| 检查 | 结果 | 边界 |
|---|---|---|
| 原包及最终包完整软件、模型、隔离 panel 导入 | PASS | 哈希完整性本身不能发现过时 daemon 的行为不兼容 |
| ARM 冻结 TEST 推理 | 950 帧，标签及拒识原因与主机全部一致 | 固定输入回放，不是本轮物理采集 |
| ARM vs Torch logits | 870 个合格非 OFF 帧；最大绝对差 0.000091552734375 ≤0.0001 | 精确检查全部 870 帧；输入哈希逐帧核验 |
| ARM 主机分数对照 | 最大绝对差 1.1920928955078125e-7 | numpy 1.17.4；C 加速库确实加载 |
| ARM 性能 | 100 帧、5 帧预热；热 P95 67.7546ms、最大 71.7335ms | 纯推理进程；不是真人点击延迟 |
| 模型/评估包大小及增量 RSS | 34,910 bytes；RSS 门槛 PASS | RSS 用现有 benchmark 的保守进程测量；未含 worker 整体内存 |
| 初始完整板载 Python 回归 | 1125 PASS / 4 SKIP / 1 FAIL | 失败为上述 CRLF；原始日志保留 |
| 修正后安装/发布相关回归 | 203 PASS | 包括换行、完整包及安装契约 |
| ML/运行时回归 | 119 PASS，另 1 项最初受沙箱读权限影响 | 单独在允许读取的环境重跑该项，1 PASS；不是算法失败 |
| 修复版 daemon 来源 | ARM binary/build SHA 一致、45/45 当前源码一致、C 清单检查 rc=0 | 复用此前构建，不宣称本轮重新编译 |
| 安装 | 第二次 exit=0；最终发布 `e7f1fc69…` | 第一次失败及自动回滚保留 FAIL |
| 服务及安全读回 | 两项 active、NRestarts=0，ARM=false/source=false/disarmed | 只读检查；服务日志额外确认 panel up |

板卡日志的 RTC 日期仍是 2021；本报告采用主机/客户端的 2026-10-03 日期。解包“future timestamp”提示来自这个时钟差，安装验收不依赖该墙钟。

## 当前任务状态与后续

- **Task 8：** 当前 rot200 的冻结物理 TEST 正式准确率已通过。早期模型的 FAIL 保留为历史，不继续作为最新状态。
- **Task 9：** 当前模型/runtime 的 ARM 数值和纯推理资源门槛已通过。早期 225/293ms 候选的 FAIL 同样保留。
- **Task 10：** 当前模型已安装，需区分“安装/模型加载”与“当前发布新采集完整链路”。本轮尚未取得当前 TX1→RX1 接线确认，因此没有执行射频发射或物理回环。
- **真人现场：** 30 次触摸点击及 P95、30 秒静默 watchdog、示波器验收仍 NOT RUN。67.75ms 推理耗时不能替代这些项目。
- **Task 11/12：** 天线/LNA 工况、型号、频段、馈线及目标距离仍缺资料，保持 NOT RUN。
- **后续打包：** 必须选择有构建收据的 schema 修复版 daemon，并在激活前核对 C 清单兼容性；不可仅凭 Python verify 和模型 production 字段宣称发布可运行。

## 证据入口

目录：`evidence/rx-smart/continuation-audit-2026-10-03/`。

- `model-chain-audit.json`：模型哈希与直接会话/IQ 隔离。
- `frozen-test.json`、`frozen-test.npz`：950 帧输入、Torch 参考、主机预测；用于本轮纯推理。
- `board-parity.json`、`benchmark.json`、`board-payload.json`：ARM 验收及上传输入身份。
- `install-line-endings.json`、`install.sh.before`：换行修正及修改前原件。
- `install-first.log`、`install-first-receipt.json`：保留第一次真实安装失败。
- `daemon-provenance-check.json`：修复版 daemon 来源与清单兼容性。
- `corrected-release.json`、`install-receipt.json`、`board-post-install.json`：最终包、成功安装、safe=true。
- `installed-inspection.json/png`、`board-final.json`：最终模型加载与实际帧缓冲、关闭状态。
- `final-receipt.json`：本轮最终状态汇总。

沿用主工作树，保留所有既有未提交内容；没有 Git 提交、外部消息、自动化或新工作树。
