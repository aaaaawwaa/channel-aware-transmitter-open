---
name: final-prj-rf-development
description: "维护final_prj的AC820/Zynq+AD9361射频教学平台时使用：定位三条构建链、板载触屏与daemon、接收算法、外部SDR联调、候选发布和证据交付。适用于该工程，不把AWG或历史回环结果当作RF产品当前验收。"
---

# final_prj RF 平台开发

以当前工作区为根目录，不固定盘符、个人用户名、板卡地址或发布ID。先确认这是`final_prj`，不要在历史`signal_generator`副本、worktree或SDK生成目录中误改主工程。

## 最少入口

1. 读根目录`AGENTS.md`索引及`CLAUDE.md`相关章节；历史“当前/最新”段落按日期理解。
2. 审查节奏以`docs/requirements/development-review-policy.md`为准；部署看`docs/rf/deployment-trial-runbook.md`。
3. 改硬件或运行流程前，按需读`docs/memory/03-development-discipline.md`与`04-engineering-pitfalls.md`。源码发现优先知识图谱，工具缺席再用已知入口和文本搜索。

## 工作路由

- 目录、构建、服务拥有权和受影响测试：读[工程地图与入口](references/project-map.md)。
- 接收模式、外部Pluto源、人工验收与历史状态边界：读[接收与SDR联调](references/receiver-and-sdr.md)。
- 候选、执行预算、试部署、收据及分享清理：读[交付与真实证据](references/release-and-evidence.md)。

## 本项目的关键决定

- 维护RF主线、PYNQ板载应用与辅助AWG时区分目标与产物；不混算位流或验收。
- Vivado/Vitis使用2025.2。AWG中BD引用的RTL改动走`scripts/build_bitstream.py`，不能只重跑旧synth拿缓存产物；独立RF/PYNQ构建不直接覆盖产品merged位流。
- Overlay只有一个拥有者。`pynq-status`负责已核验发布及PL加载，`rf-daemon`使用已加载PL；测试脚本不二次`Overlay()`。板端由一位操作者串行控制。
- 当前交付范围已移除QAM64；BPSK/QPSK为主要接收演示，QAM16保留实际误码限制。现场采用外部SDR发射、板卡纯接收，不再强制自回环作为演示前置。后续范围可由用户改变，不能将这些阶段决定升级成硬件永远不支持的规则。
- 日常差分由主agent一次增量检查。只有新候选/安装/跨设备联调里程碑/发布等关键阶段增加一次适用独立审查；复用未改变部分，不重复全仓审查。

## 推进方式

先核对最新原始收据和实际代码，再选一个有辨识力的最小问题。相关离线任务可按互斥文件范围并行，硬件、发布切换与git index串行。减少重复实验与重复审查，不能通过放宽判据、补采筛帧或隐藏失败加快进度。

本技能不包含凭据，不授予硬件动作权限。连接和现场参数从私有配置与当前操作者确认取得；过去接线、预算、nonce和旧安全读回不能自动复用到新实验。
