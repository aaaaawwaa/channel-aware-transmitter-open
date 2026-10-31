# S2 阶段 D 第一步：新候选上的 18 帧开发 pilot 执行包（2026-10-15）

**本文件是执行说明，不是授权**：不连板、不发射、不采集。真正动作由人工接线确认 + 现场执行承载。适用前提：候选 `399b300e59b8d00e41d43c8123884c7f` 已安装并通过独立读回（阶段 C，收据 `evidence/rf-teaching/s2-environment-live-install-2026-10-15/`）。 【**已过期**：截至 2026-10-17 的实际状态——该适用前提已失效：399b 仅在 2026-10-15 短暂作为 `current`，同日经授权切回 b82（`evidence/rf-teaching/s2-headroom-switch-2026-10-15/NOTES.md`）；10-16/10-17 只读读回均为 `b82b141e…`。本执行包不能按原前提直接执行】

## 1. 关于"无中间衰减元器件"——先澄清再决定

1. **S2 冻结域只用 AD9361 内部衰减**：`fixed_rf.attenuation_mdB = 40000`（40 dB），由发布/worker 配置在板内生效；本合同**不要求、也不引用**任何外部衰减器、功分器或连接件。
2. 因此"没有外部衰减器"**本身不构成阻塞**——历史全部开发帧（含 S2 的 18/18/16/8 帧与 v1/v2/v3 pilot）都是在 **TX1→RX1 直连电缆 + 内部 40 dB** 下取得的。
3. 但必须如实说明已知物理风险：直连 + 40 dB 历史上**出现过削顶**（`CLIPPED`），这正是 P1 HEADROOM-1 研究（只把内部 TX 衰减 40→46 dB）存在的原因。本执行包的处置是：
   - 削顶帧**判 `INVALID/CLIPPED` 并原样保留**，绝不当作有效帧；
   - pilot 是否通过由冻结合同判据决定（`known_correct=9`、`negative_false_clear=0`、6 会话/18 帧按合同形状）；
   - **禁止**为了过门而改衰减/阈值/基线——那属于新合同（46 dB HEADROOM 路径），要新基线 + 重新资格 + 新授权。
4. 若你希望走 46 dB 内部衰减路径（同样不需要外部器件），那是**另一条合同线**（HEADROOM-1），需单独授权与单独基线；本文件不涵盖。

**需要你确认的一句**：是否同意在上述 40 dB 冻结域 + 直连（无外部衰减器）条件下执行 18 帧 pilot；若出现削顶，按第 3 条停止并保留失败帧、不调参重试。

## 2. 人工前置（不可跳步）

| 项 | 要求 |
|---|---|
| 接线 | TX1→RX1 直连在位；由人**当场**确认并写下固定语句 `TX1->RX1 physical path present`（该字符串是冻结常量） |
| 确认时间 | ISO8601 带时区；超过 12 小时即失效（无 override） |
| 确认人 | 写下姓名/工位标识（写入收据） |
| 板卡状态 | 执行前**新会话只读预检**：`current=active-release=399b300e…`、双服务 active/`NRestarts=0`、RF 未布防/TX 未选源/RX 未武装、dmesg 无新存储故障；【**已过期**｜截至 2026-10-17：现行 `current=active-release=b82b141e…`，不是 399b；按本行原判据执行会在预检第 1 项即失败，且不允许绕过】 |
| 执行额度 | **单次会话**；断线/超时/UNKNOWN 一律停止，不重试、不补采 |

## 3. 执行命令（板端 root，唯一一次）

```bash
# 1) 主机侧：上传 pilot 所需脚本与结果目录（沿用已安装发布，不重装）
#    工具：scripts/rf_environment_board_run.py（开发 pilot 入口，18 帧）
python -B scripts/rf_environment_board_run.py \
  --candidate evidence/rf-teaching/s2-candidate-environment-2026-10-15/candidate/releases/399b300e59b8d00e41d43c8123884c7f \
  --cable-confirmed \
  --output evidence/rf-teaching/s2-development-pilot-2026-10-15/run
# 【已过期（截至 2026-10-17）】以上命令的 --candidate 前提已失效：现行 current=active-release=b82b141e…（不是 399b300e…）；本段仅保留为历史执行包记录
```

- 该入口**不带 `--freeze`**＝开发 pilot（18 帧、3 会话），不是 108 帧正式 TEST；
- 它会在板端跑生产 worker/UiView 路径并逐帧保留原始 IQ、metadata、environment sidecar、record；
- **不得**同时调用 `Overlay()`、不得手工改板端文件、不得并发第二条会话。

## 4. 收据与判据（pilot 通过才允许生成正式 freeze）

`rf_environment_freeze.build()` 要求（缺一不可，生产代码为准）：
- 同 IQ 重放 `replay['passed']` 为真且 18 帧形状/标签合格（`known_correct==9`、`negative_false_clear==0`）；
- `result.json.passed is True`、`receipt.json.passed is True`、`board_state==VERIFIED_SAFE`、`release_id` 与候选一致；
- 随后生成 `development_receipt_sha256` 非空的**正式 freeze**（当前离线声明该字段为空，不能用于正式 108 帧）。

## 5. 停止规则（任一命中即停、原样留证、不重试）

1. 任何帧 `CLIPPED` / `INVALID_IQ`；2. 任何会话未确认关闭或未收到 disarm ack；3. 板端 unsafe 或 `safe!=true`；4. dmesg 出现新存储/MMC 故障；5. SSH 超时或结果 UNKNOWN；6. 板卡重启（`boot_id` 变化）。

## 6. 本轮不做

正式 108 物理帧、真人验收、N1–N12 收据绑定、46 dB HEADROOM 研究、任何回退演练。全部维持 `NOT_RUN`，直到各自执行门开启。