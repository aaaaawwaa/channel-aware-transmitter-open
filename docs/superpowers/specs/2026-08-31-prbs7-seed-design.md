# 阶段 1：PRBS7 seed 可配置数字源 — 设计规格

日期：2026-08-31（基于已批准计划 `stateful-roaming-fairy.md` 阶段 1）

## 目标

将固定 `7'h7f` 的 PRBS7 变为可配置、可重复的数字源，同时保持 RECT/RRC、所有星座和现有默认行为完全兼容（默认 seed=127 波形逐样点不变）。

## 协议

- `SET DIGMOD SEED <1..127>` — 仅 `MODE OFF` 时有效；运行中返回 `ERR DIGMOD RUNNING`。
  0、非整数、超 7 位值 → `ERR SEED OUT OF RANGE`。严格拒绝零，禁止静默截断。
- `SET DIGMOD RESET` — 仅 `MODE OFF` 时执行；运行中返回 `ERR DIGMOD RUNNING`。
  语义：确认下一次启动从当前 seed 的首 bit 开始。
- `GET DIGMOD` 新格式：
  `DIGMOD MODE <m> SOURCE PRBS7 FILTER <f> ROLLOFF <r> SEED <s> SYMRATE <Hz> STATE <st>`
  兼容解析：旧 9-token（无 FILTER/ROLLOFF/SEED）→ RECT/0.35/127；
  11-token（FILTER，无 ROLLOFF/SEED）→ 0.35/127；
  13-token（FILTER+ROLLOFF，无 SEED）→ 127。

## 语义决策（按计划推荐）

1. seed 修改仅 OFF 状态；运行中 `ERR DIGMOD RUNNING`。
2. RESET 不允许运行中打断；仅 OFF 状态执行。
3. **MODE OFF→ON 是隐式 reset**：每次启动 PRBS 从当前 seed 首 bit 开始。
   这兼容现有行为——现有 `reset_seed = !enable` 在停止时把状态保持于 seed，使能时从 seed 首 bit 推进；
   默认 seed=127 逐样点不变。故 `RESET` 是显式确认命令，观察上等价于"下次启动从 seed 首 bit 开始"，
   保证序列可重复（满足计划验收"RESET/重启后序列可重复"）。
4. IQ 与 RRC 从同一 seed 边界开始：两者各自实例化 PRBS，seed 为 OFF 态准静态值，
   OFF→ON 隐式加载，切换时序一致，`consume_bits` 契约不变。

## GPIO 编码

- GPIO11 bit[29:23] = `SEED[6:0]`（7 bit，值 1..127；0 视为默认 127）。
- 无新增事件位：seed load 事件即 MODE OFF→ON 边沿。reg_bridge 输出 `digmod_seed`，
  iq/rrc 的 PRBS `reset_seed = !enable` 在停止时连续保持状态于 seed（准静态，仅 OFF 态变化），
  使能时从 seed 首 bit 推进——这是 epoch/toggle 语义，不是把 GPIO 电平当窄脉冲。

## RTL 改动

- `src/rtl/prbs7_source.v`：新增 `input [6:0] seed_in`；
  `!rst_n` 与 `reset_seed` 时 `state <= (seed_in == 7'd0) ? 7'h7f : seed_in`。
- `src/rtl/iq_digital_mod.v` / `src/rtl/rrc_digital_mod.v`：新增 `input [6:0] prbs_seed`，
  透传给 `u_prbs.seed_in`。
- `src/rtl/reg_bridge.v`：新增 `output [6:0] digmod_seed`，`assign digmod_seed = r11[29:23];`。
- `src/rtl/awg_top.v`：接 `digmod_seed` 到 `u_dig.prbs_seed` 与 `u_rrc.prbs_seed`。

## SDK 改动（sdk/src/main.c）

- `static u32 digmod_seed = 127;`
- `pack_word_f` 增加 `(digmod_seed << 23)`。
- GET DIGMOD 增加 `SEED %lu`（插在 ROLLOFF 与 SYMRATE 之间）。
- SET DIGMOD SEED <1..127> / SET DIGMOD RESET（均仅 OFF）。
- 错误：`ERR SEED OUT OF RANGE`、`ERR DIGMOD RUNNING`。

## host 改动

- `protocol.py`：常量 `DIGMOD_SEED_MIN=1`、`DIGMOD_SEED_MAX=127`；`digmod_seed_cmd(seed)`、
  `digmod_reset_cmd()`；`parse_digmod_status` 支持 15-token（含 SEED），返回
  `(mode, source, filter, rolloff, seed, symrate_hz, running)`，9/11/13-token 兼容（seed 默认 127）。
- `controller.py`：`DigitalModState.seed = 127`；`set_digmod_seed` / `set_digmod_reset`；
  digmod 确认流包含 seed。
- `ui/widgets.py` 数字调制页：SEED spin（1..127）+ 设置按钮（仅 OFF 可编辑）；已确认 SEED 显示。
- `main_window.py`：信号接线。

## GOLD 与测试

- `tools/generate_rrc_vectors.py` 增加 `--seed`；生成多 seed mem（如 seed=5 QPSK α0.35）。
- RTL TB：`prbs7_source_tb`（seed 1/127/中间值/零→默认）；`iq_digital_mod_tb`（多 seed 首符号向量）；
  `rrc_digital_mod_tb`（seed GOLD）；`reg_bridge_tb`/`awg_top_tb`（digmod_seed 接线）。
- 默认 seed=127 现有 mem 与向量逐样点不变。
- host 单测、`scripts/test_bridge.py`（SEED/RESET 边界、仅 OFF、GET 回读）。

## 验收映射

RTL TB（多 seed）→ host 单测（兼容解析）→ 协议回归 → 重建 bitstream（0 setup/0 hold 失败）→
板级：不同 seed 下 OFF→MODE 首段 I/Q 与参考位流一致；RRC 至少 QPSK/QAM16/QAM64 BER=0；
RESET/重启后序列可重复；既有 54/54 用例不回退。
