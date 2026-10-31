# skill/ —— 技能包入口（指南 3.3.5.2）

本目录是指南推荐结构中的技能包入口。**技能包的实际内容维护在 [`../skills/`](../skills/)，不在本目录复制副本**（避免两份内容漂移）。本 README 说明目录映射与使用入口。

## 1. 实际技能包（`../skills/`）

| 技能 | 入口 | 类型 | 通用性 |
|---|---|---|---|
| `final-prj-rf-development` | [`../skills/final-prj-rf-development/SKILL.md`](../skills/final-prj-rf-development/SKILL.md) | 本项目 RF 平台开发流程 | 与本项目强绑定 |
| `pynq-linux-development` | [`../skills/pynq-linux-development/SKILL.md`](../skills/pynq-linux-development/SKILL.md) | 通用 PYNQ 板载 Linux 开发 | **面向 PYNQ 框架，不绑定本题目**；跨项目/跨板卡/跨版本复用尚未验证 |
| `pluto-gnuradio-testbench` | [`../skills/pluto-gnuradio-testbench/SKILL.md`](../skills/pluto-gnuradio-testbench/SKILL.md) | 外接 PlutoSDR / GNU Radio 测试台 | 可移植模板；有线 RF 回环与 RF 输出尚未验证 |

完整入口、每项技能的适用范围、使用方法、失效条件、来源、已验证效果与提炼过程，见 **[`../skills/README.md`](../skills/README.md)**。

## 2. 与指南 `skill/` 目录的关系

指南 3.3.5.4 的推荐结构为 `skill/`（含 `README.md`），3.3.5.4 同时注明“推荐结构非强制，采用其他组织方式的队伍须在 README 中给出目录对照说明”。本仓库选择：

- 技能内容：`skills/`（历史路径，已在大量文档与脚本中被引用；不搬迁以避免断链与构建引用失效）；
- 指南入口：本文件 `skill/README.md`（说明映射，指向 `skills/`）。

根 README 的“赛事指南对照”一节也给出同一映射。

## 3. 使用与边界

- 技能记录方法与边界，不保存个人凭据，不新增硬件操作授权。
- 每项技能的适用范围与失效条件以 `skills/README.md` 与各 `SKILL.md` 为准；没有验证过的跨项目适用性会明确写“尚未验证”，不当作已实证的加分项。
- 技能包中涉及的设备串号、VISA 资源与主机路径在本公开仓库中已按脱敏规则替换为占位符；使用前需按你自己的环境配置（见根 [README](../README.md) 的“证据与隐私边界”）。
