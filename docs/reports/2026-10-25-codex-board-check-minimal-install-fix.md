# 板卡简查与安装包最小修复

日期：2026-10-25；当前 Codex 对话执行。目标是保留正常运行的父版本，修复已证实的安装失败，推进到下一次可执行试部署。未安装、未重启、未发射或采集。

## 板卡实读（北京时间约 10:07）

- 主机 `192.0.2.10`，Paramiko 连接成功。boot_id `268b7a15-e037-4a59-83e8-ab87ceba771e`，uptime 约 13 分钟。
- current 与 active 均为父版本 `bedf98c77283002c632dd7f345abaff9`；`pynq-status` PID555、`rf-daemon` PID621，均 active/running，NRestarts=0。
- 旧 QAM trial inactive/dead；该新 boot 的退出值不是上一 boot 的验收证明。候选 cd76818a 发布目录未发现。
- 通过实际 release 的 RfStatusClient 仅读取状态：online=true、stale=false、io_ok=true，armed=false、pending=false，TX source=false、RX armed=false、core=false，尚未初始化 RF，headroom 未使用。可保留父版本继续准备工作。
- root ext4 为 rw，剩余 5.6 GiB；读取到的近期 dmesg 有旧 FAT 未干净卸载提示及断电后的 user journal 恢复提示，没有据此断言 SD 已损坏，也不做 fsck 等现场修改。
- 证据：`evidence/codex-board-check-20261025/readonly-020727.json`。最初沙箱连接被 WinError10013 拒绝，随后在本轮明确只读授权下通过工具审批执行，未扩大到板端写操作。

## 工程与最小修复

QAM 算法、产品接入、既有 ARM 数值结果继续保留；真实 QAM RF 还没有执行。两个实测 runner 问题已由 DSH 修复，不再重复处理。工作区还有其他任务的既有改动，未回退或覆盖。

此次仅修确定的安装包缺目录问题：

1. `.../qam16-rect-product-2026-10-24/build_candidate.py` 增加 `write_candidate_archive()`：从冻结 release 同一字节生成 `release/` 及其软件清单声明的安装源目录；不从未冻结工作树拼入产品文件。原 main 使用该打包函数。
2. `scripts/rf_teaching_install.sh` 在复制旧 unit、设置回退 trap 和停止服务之前，核对每个安装源路径及摘要；旧的 release-only 坏包会以 `REFUSE INSTALL_LAYOUT` 退出，避免先停服务再找不到安装入口。
3. 新增 `scripts/tests/test_qam16_install_archive.py`，实际解包并运行安装脚本的相应路径。隔离测试只替代板端状态/目标与 systemctl，不将整个安装器 mock 成成功。

没有修改 pynq_status、时钟、RTL、位流或产品算法。本次修复消除了已证实的“缺安装入口 → 进入回退”触发链；尚未证明运行中的热重载硬件卡死机制已经修复。

## 验证

- 先复现旧包可以走到停服务边界：`red-layout.log` 的目标断言失败。
- 修复后，新四项安装布局/真实复制校验测试，加既有十一项 trial 成功/失败/超时/回退测试，**15 passed in 37.67s**。
- 真实内层脚本在隔离目录完成文件复制与 `--require-software --check-panel` 校验，发布文件逐字节等于冻结候选；测试在激活前停止，不将其说成实际 systemd/板端验收。
- 新包主机实际产品验收 19 项检查通过；新 preflight 7 项（含 installer_layout）通过；shell 语法检查通过。
- 最终核对新压缩包/松散 stage/清单一致，旧 candidate 与旧 trial-stage 的摘要未改变。证据与完整测试日志见 `evidence/codex-board-check-20261025/`，汇总 `final-verification.json`。
- 本地测试初期遇到 Git bash 将 Windows `D:` 识别为远程 tar 地址、Windows Python 输出 CRLF 的问题，均只在测试 shim 修正；未为此更改 Linux 产品行为。打包曾拒绝瞬时可见的 `__pycache__`，后续完整重跑及最终归档确认无该成员，未放宽发布成员检查。

## 已生成的新工作包

目录：`evidence/rf-teaching/qam16-rect-product-2026-10-24/bundle-layout2/`，同目录 `NEXT-STEP.md` 是 DSH 接续入口。旧失败包及板端现场保留。

| 项 | 值 |
|---|---|
| release_id（产品字节未改） | `cd76818afc3bee7c610678574e892b26` |
| parent | `bedf98c77283002c632dd7f345abaff9` |
| software/归档普通文件 | 62 / 134（release72 + 安装源62） |
| 新 candidate.tgz SHA256 | `440ba377a84be4ac2ee6e6cd85980106271cff4a26863235cb84718bc8273c17` |
| 新 trial-stage.tgz SHA256 | `746e156f092f44d5f2d053e0471fa2eb427541898965869d6bee66a6f5e2299d` |
| 新 stage-sha256.json SHA256 | `441c2f485104352a189a0af1cf2efb82acfd1c38dc710a8bfb8c7eec609c5593` |
| 新 trial_id | `rf-qam16-cd76818a-layout2` |
| 新 unit | `rf-qam16-trial-cd76818a-layout2.service` |

## 推进判断

**推荐继续 QAM16，暂不扩散成 RTL/时钟重构。** 此包已完成本机相关验证；下一步只需对打包/预检/安装入口差分作一次适用的增量核对，然后在新的明确安装授权下进行一次 120 秒无 RF 试部署及失败回退/独立读回。既有算法和 ARM 不需要因为包布局改变全部重跑；原 ARM 传输包摘要仍是旧值，用产品文件等同性衔接新包。

本轮没有重新部署。若新试部署仍卡死，依实际停服务/安装/启动/回退日志定位热重载，不再原样重派。安装并独立验收成功后，再进行已经准备好的四帧 QAM 真实接收；它需要另一次物理实验授权与当前接线确认。
