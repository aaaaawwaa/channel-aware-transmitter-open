# AWG 基线冻结（P0，2026-09-13）

把 TFT/触摸并入 AWG 位流**之前**冻结的基线。改动位流/固件之后，用这里的哈希判断
"是不是同一条基线"，用两套件 + 金标判断"行为有没有变"。

## 四件套（哈希即身份）

| 件 | 路径 | md5 | 时间 |
|---|---|---|---|
| 位流 | `awg/awg.runs/impl_1/host_bd_wrapper.bit` | `97e4da807bdd375f9b432f3ba2217d1f` | 2026-09-12 14:14 |
| BD 交接 | `awg/awg.srcs/sources_1/bd/host_bd/hw_handoff/host_bd.hwh` | `b5e60f2c3a0b52f23a8adb58911548b7` | 2026-09-12 13:39 |
| 固件 ELF | `sdk/ws2025_gem_fresh/awg_bridge/build/awg_bridge.elf` | `3def6b9b81300c05531749ebf9381aa8` | 2026-09-12 08:38 |
| 固件源 | `sdk/src/main.c` | `866cb70427cb3e4db97872b1cc27d6af` | 2026-09-12 08:36 |

`sdk/src/main.c` 与工作区副本 `sdk/ws2025_gem_fresh/awg_bridge/src/main.c` **逐字节一致**；
ELF 晚于源 ✓（上板前的版本核对按 `CLAUDE.md` 的惯例做过）。

## 验收结果

- `python scripts/test_bridge.py COM11` → **230 PASS / `RESULT: ALL PASS`**（退出码 0），
  全文归档在 `test_bridge_baseline.log`。
- `python scripts/scope_verify.py COM11` → **61/61 PASS**（2026-09-14 补跑，示波器接上后）。
  全文归档在 `scope_verify_baseline.log`，覆盖校准、8 个波形用例、PULSE/NOISE、谐波 FFT、
  5 个调制、5 个相位联动、6 个数字调制、4 个信道损伤、3 个复数 I/Q、13 组 RRC BER。
  这一项同时是"**寄存器打包是否正确**"的端到端判据（它从模拟输出反推参数），
  所以它是 P3/P4 的**对照基准**：合流位流与 Linux+桥 上必须同样 61/61。

**两份日志的用途**：`test_bridge` 判协议/语义，`scope_verify` 判打包与模拟通路 ——
合流（P2/P3）与改跑 Linux（P4）之后，两套都必须在**同一份脚本、不改断言**下复现相同结果。

## 命令金标（731 条）

`command_replies.json` = 逐条 `(命令, 固件原始回包)` + 元信息（端口/时间/上面四个哈希）。

采集器：`python scripts/capture_golden.py COM11 docs/golden/command_replies.json`

- 采集前先发 `QUIET` 把器件压到确定默认态，采集后再压一遍并**读一遍全状态**
  （场景名 `__defaults_state__`）—— 那份"规范默认态"要能复现，也不能给后来的人留污染
  （**信道损伤那一级是相加的**，上一轮就被残留的 `IMPULSE 1000 8 -100` 坑过）。
- 覆盖：波形/占空比/频率/幅度、调制（AM/FM/PM/SWEEP）、CH2 相位与联动、谐波、
  信道损伤（噪声/多径抽头/单音/冲激）、IQ 损伤、DIGMOD 五种模式×三种滚降、
  以及越界与解析错误的**文案**（错误文案也是契约）。
- 用途：P1 的 Python 控制库要在**没有板子**的环境里被验收 —— 逐条复现这些回包。

## 采集时学到的两条（已固化进采集器）

1. **命令真语法**（`CLAUDE.md` 里的简写易误读）：`SET CHx NOISE <0..100>` 与
   `SET CHx TAP <k> <gain> <delay>` 是 **`tok[2]`**，**不在 `CHAN` 之下**；
   `CHAN` 下只有 `ON|OFF|COMMIT|TONE|IMPULSE`。`PMDEV` 范围是 `0..511`；
   `MODE` 只有 `OFF|AM|FM|PM|SWEEP`；`DIGMOD` 的 `FILTER/ROLLOFF/SYMRATE/SEED/RESET`
   必须在 `STATE STOPPED` 时改（**先参数、后 `MODE`**）。
2. **`BASE` 会被 `normalize()` 重算**：清掉谐波之后 `SET CH1 BASE 100` 读回来是 `70`。
   这是固件的派生行为、不是残留；Python 库必须照样复现。
