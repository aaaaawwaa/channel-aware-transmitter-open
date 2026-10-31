# report/ —— 设计报告与大模型协作记录入口（指南 3.3.5.3）

本目录是指南推荐结构中的 `report/`（设计报告 + 大模型协作记录）入口。**报告正文维护在 `../docs/engineering/`，本目录不复制副本**（避免内容漂移）。指南的推荐目录非强制；根 README 给出完整目录对照。

## 1. 设计报告

- 主报告：[`../docs/engineering/DESIGN-REPORT.md`](../docs/engineering/DESIGN-REPORT.md)
  - 选题背景与范围、总体架构、关键设计决定、接收/发射/发布链路、质量策略、已知限制与参考。
- 系统结构图：[`../docs/engineering/system-architecture.md`](../docs/engineering/system-architecture.md)
  - 外部 SDR 激励与板卡接收的功能框图（PL/PS 分工与边界）。

## 2. 综合、实现与实测指标

- [`../docs/engineering/SYNTHESIS-METRICS.md`](../docs/engineering/SYNTHESIS-METRICS.md)
  - 资源占用、时钟/时序、功耗与实现状态、上板与 ARM 性能、主机/ARM 判等、优化前后对照（当前为 `未取得` 同口径基线）、缺口清单。
- [`../docs/engineering/EVIDENCE-PACK.md`](../docs/engineering/EVIDENCE-PACK.md) 与 [`../docs/evidence/`](../docs/evidence/README.md)
  - 交付版本证据身份（bit/DCP/payload_equal 绑定）、三份实现摘要、原件 SHA-256；功耗明确为 Vivado 估计值；摘要不可复算。
- [`../docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md`](../docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md)
  - 优化前后基线核查与最小补测方案；工具链/依赖/构建/产物/缺失项的从零复现说明。
- [`../docs/engineering/RF-INTERFACE-TIMING.md`](../docs/engineering/RF-INTERFACE-TIMING.md)
  - AD9361 源同步 DDR 接口的未约束端口、参数来源、计算方法与分阶段最小方案（板级未测，不宣称通过）。

## 3. 大模型协作记录

- [`../docs/engineering/AI-COLLABORATION-RECORD.md`](../docs/engineering/AI-COLLABORATION-RECORD.md)
  - 3 个真实案例的“提示词/任务 → 模型回答 → 错误或反馈 → 纠正 → 结果与证据”，以及智能体工作流与可复用提示词骨架。
- [`../docs/engineering/AI-COLLABORATION-CASE.md`](../docs/engineering/AI-COLLABORATION-CASE.md)
  - 7 个有出处的协作事件与可复用硬规则（方法与教训）。

## 4. 竞赛对照与材料索引

- [`../docs/engineering/GUIDE-CONFORMANCE.md`](../docs/engineering/GUIDE-CONFORMANCE.md) —— 指南 3.3 逐条映射、状态与缺口。
- [`../docs/engineering/COMPETITION-MATERIALS.md`](../docs/engineering/COMPETITION-MATERIALS.md) —— 提交材料清单、无法证明事项、匿名与隐私处理、提交前检查清单。
- [`../docs/engineering/DELIVERY-INDEX.md`](../docs/engineering/DELIVERY-INDEX.md) —— 全仓库材料索引与证据边界。

## 5. 复现与证据边界

- 复现步骤：根 [README](../README.md)「构建与运行」、[OPTIMIZATION-AND-REPRODUCTION](../docs/engineering/OPTIMIZATION-AND-REPRODUCTION.md) 与 [`../docs/engineering/DESIGN-REPORT.md`](../docs/engineering/DESIGN-REPORT.md) §9。
- 原始 IQ、收据、位流/HWH、数据集与模型产物不在本仓库；`SYNTHESIS-METRICS.md` 中的指标按“文档记载/本地原件”分级标注，不能在本仓库内复算。
- 公开证据摘要见 [`../docs/evidence/`](../docs/evidence/README.md)；原件仍留在开发机，摘要与原件的 SHA-256 不同。
