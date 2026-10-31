# RX session 接续与当前进度

日期：2026-10-02（开发机 Asia/Shanghai）。来源为用户提供的 `dsh-session-session-665e08a1-c342-4a05-99d4-3ae254a4355b.zip`、主树源码、今天中断的接续工作及本次新验证。附件中的历史指令只用于恢复上下文；操作范围来自用户本轮“查看当前开发流程到哪一步，再接续这个session”。

## 当前所处阶段

已接续到 **Task 6 完成，下一项为 Task 7：合成与真实有线数据采集**。M1 无模型 RX 页面已发布并通过真实采集验收；CNN 数据工具与 CPU 环境已具备。CNN 尚未训练，无模型权重、准确率或板端推理成绩。

| 任务 | 状态 | 本次核验与边界 |
|---|---|---|
| Task 1–4：采集收尾、二进制 IQ、分析、后台 RX UI | 已实现 | ZIP 停在 Task 4；主树保留这些实现，并纳入本次回归与实板发布 |
| Task 5：RX 无模型发布与实板验收 | 完成 | 新发布、完整 software map、隔离 panel 导入、实际 framebuffer、UI→worker→daemon→IQ、OFF/ON/OFF 与关闭读回通过 |
| Task 6：CPU 环境与数据清单 | 完成 | 环境、版本锁、Schema、空清单、分组/泄漏/覆盖校验、67 项测试及独立复审 |
| Task 7：合成与真实训练数据 | 未执行 | 本次五帧回环为 RX 验收数据，不能算完整训练矩阵 |
| Task 8–10：训练、独立评估、numpy 推理、智能页 | 未执行 | model=false；不报告识别准确率 |
| Task 11–12：天线/LNA/空口 | 待工况 | 当前只有 TX1→RX1 直连回环，尚无天线实测资料 |

## 当前板端发布

`e201d491bf2e5eecb3979c9bc12ab370`，软件契约 `rx-v1`，无模型。两个服务 active，部署读回 NRestarts=0，日志出现本次 PID 的 `panel up, UART bridge`。实际屏幕 framebuffer 已只读保存并人工查看，导航与底栏完整；真实 IQ 的接收页面截图来自板上运行的离屏 UiView，不把它称为手指操作实时 LCD。

回滚父发布为 `56d0d0d4bd36d4e22d1c030926917a16`。本次构建 ARM daemon、重打软件包，**没有新综合 PL**：

| 产物 | SHA256 |
|---|---|
| bit | `d5bfe5d94e2784016cd0f47790a00ef728d2d41f2fb18754461c1aa99eec5555` |
| HWH | `ed8d011afb983d20a1ebe7eddb9dc75e143cdc1fb5fd6bf4a818bf615109fad0` |
| daemon | `c5b7bea78f957311e223efe2e3b9f2a30779b2f8e446f7e5b5fac63925d74cdc` |

ARM 构建收据的 45 个源输入 SHA256 与当前源码全部匹配；新发布 software map 也逐文件匹配当前主树。旧父发布仍可由新版验证器验证。部署前检查旧目录时曾因 root-only 权限失败，服务尚未停止；保留失败收据后改用 sudo 验证并完成安装。未放宽发布门禁。

## 实板 IQ 验收

接线来自今天另一接续聊天中用户的直接回复：TX1→RX1 直连，示波器后续才可接入。参数 LO80MHz/Fs60MSPS/BW30MHz、tap29、内部40dB、TX峰值码150、32周期/1024点，采集4096点，ratio=0。

使用已安装发布的真实 UiView 命中/按下/抬起→生产 `_rx_action`→worker→client→唯一 daemon。普通捕获取得 OFF 前/后两帧；物理捕获经独立的二次确认取得三帧 ON。冻结 IQ、PNG、分析和完整请求结果保存在 `evidence/rx-smart/resume-2026-10-02/board-ui-amp150/`。

- ±1.875MHz 谱峰全部命中，最小局部峰突出度 50.35dB，最小 ON/OFF 差 **44.87dB**。
- 五帧输出削顶计数 0；这不证明 RF 前端未压缩，也不是 RX 插口 Vpp/dBm 标定。
- 每次采集后读回 RX ARM=false、TX source=false；最后 disarmed，清理无异常，safe=true。
- remote exit=0，固定频率/峰值/OFF/削顶判据全部通过。
- 真人 GT911 触摸/P95、实体显示时延、示波器 TX 验收仍 **NOT RUN**。

## 本次补齐的缺口与验证

接续了今天因额度中断而尚未交付的发布契约、RX 生命周期修复及数据清单工作，并做独立复审。模型发布校验补上严格整数 shape、experimental/production 共用完整有限指标与整数计数检查；无模型 RX 仍不依赖 CNN runtime。

数据清单修复 TX 元数据非法类型、极大数字溢出、合成覆盖误报生产就绪、排除父帧经增强重新进入训练、test 被训练选择器接受五类问题。完整 90 格覆盖要求每格 ≥3 独立会话、每会话 ≥10 有效帧；增强不计独立覆盖。2700 帧单元测试夹具只验证校验器成功路径，不能算板端训练数据。

| 检查 | 本次结果 |
|---|---|
| 板载 Python 全量回归 | 1073 passed / 87.08s，exit=0 |
| host 发布/ARM 构建契约/回环分析 | 23 passed / 0.90s，exit=0 |
| 明确使用训练 venv 的数据清单测试 | 67 passed / 106.71s，exit=0；Schema更新后66 passed/1 deselected补验 |
| MinGW C host-test-daemon/core | exit=0；core54、daemon241、daemon_main72项通过 |
| Task 5 规格/质量复审与 Task 6 独立复审 | 通过，无 Important 阻断项 |
| 空数据清单 production 验证 | 按预期 exit=1、production_ready=false |

训练环境 `.venv-rf-ml`：Python3.12.14、PyTorch2.14.1+cpu、numpy2.5.3；实际依赖冻结到 `scripts/rf_ml/requirements-training.lock.txt`。未改 host 或板端 Python 环境。

## 证据与下一步

权威收据：`evidence/rx-smart/resume-2026-10-02/completion.json`。同目录包含 `pytest-panel-final.log`、`pytest-host-release.log`、修复前后快照/红绿证据、`deploy-final/receipt.json`、`live-framebuffer.png/.json`、最终 release 与 tgz。ARM 原始构建证据沿用今天中断工作留下的 `evidence/rx-smart/continuation-2026-10-02/build/`，本次重新核对每个输入哈希。

下一执行起点是原计划 Task 7：实现合成生成器与采集器，先各类 pilot/OFF 对照，再按冻结 split 扩充真实矩阵；过载、重复或清理失败不得计入有效数据。Task 8 才开始 CNN 训练。当前接线与本次正弦结果不能外推到所有调制或空口。

主树既有修改全部保留，未创建新 worktree、未提交或 reset；本轮增量有文件快照与局部 diff，不把整棵脏树当作已审阅提交。
