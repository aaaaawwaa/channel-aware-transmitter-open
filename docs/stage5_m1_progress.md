# Stage5 M1 故障闭环进度（2026-09-06）

> 本文记录 Stage5 M1「bounded reload 可靠性 / 故障闭环」执行状态。
> 只登记**已真实发生**的证据；四种故障均已完成板级注入与恢复验证。

## 里程碑状态

| 里程碑 | 状态 | 说明 |
|---|---|---|
| Stage5 M0 DDR 双 bank | 冻结 + 板测 | 见 `build_manifest.json` `stage5_m0_elf`（BOARD_GATE_PASS）与 `project_stage5_m0_pp` 记忆 |
| M1 fault seam（编译期） | 完成 | `sdk/src/main.c`，不新增公开 `SET DDR PP TEST` |
| M1 静态测试 | 全绿 | `test_stage5_m1_faults.py` 7 passed；`test_stage5_m1_gate.py` 5 passed |
| M1 正式 + 4×fault ELF 本地构建 | 完成 | 详见下节；宏已确认编入（mtime/哈希证据） |
| M1 COM11 板级注入 | **完成** | 四种 fault 均取得真实 UART transcript；STOP/IDLE/清错/重启通过，active BRAM 元数据不变 |
| M1 正式 ELF 恢复与旧协议回归 | **完成** | 正式 ELF 已恢复到 COM11；旧协议 161/161 PASS；全量 Host 回归 472 passed |
| M2 | **PASS_WITH_LIMITS** | 400 KiB 低速与 1 MiB 高速 PP 双 bank profile 已板测；PP 专属 sink CRC/TLAST/TKEEP/FIFO/gap 观测仍未完成，详见 `docs/stage5_m2_progress.md` |
| M3 | 未开始 | 需等待 SG/PL reader 架构决策 |

## M1 fault seam 设计（已冻结）

- 编译宏 `STAGE5_FAULT_INJECT` + `STAGE5_FAULT_MODE`：
  `1=DMA_ERROR`、`2=RESET_TIMEOUT`、`3=STREAM_UNDERFLOW`、`4=TIMEOUT`。
- 故障注入点全部位于**生产状态观察点**（DMA SR error、stream underflow、
  timeout、`dma_wait_reset`），一次触发（`stage5_fault_consumed`）。
- 正式构建不定义宏 → seam 编译为零代码路径。
- 冻结错误码：`0x20` layout/start、`0x21` reset 失败、`0x22` transfer、
  `0x23` DMA error、`0x24` timeout、`0x25` underflow。
- PP CLEAR 只清 staging（`ddr_pp_stage_*`），**保留已 commit 的
  `ddr_pp_valid/bit_length/crc16`**；重复 START 在 parser 层被 `ddr_pp_active`
  guard 拒绝。
- 板级 gate：`python scripts/stage5_fault_gate.py COM11 --fault <MODE>`，
  流程 = 记录 active BRAM 元数据 → PP 装载 → START → 轮询 ERROR 码 →
  STOP 断言 IDLE/清错误 → 断言 BRAM 元数据不变 → 再 START 可运行 → STOP。

## 本地构建证据（2026-09-06）

构建命令必须用 CLI（shell 无 `arm-none-eabi-gcc`，直接 `make` 失败）：

```bash
cli-anything-vivado sdk build awg_bridge_stage5_m0 --workspace <ws> --type app
```

fault 宏通过在 `Debug/src/subdir.mk` 编译行追加
`-DSTAGE5_FAULT_INJECT -DSTAGE5_FAULT_MODE=N` 注入；
`cli-anything-vivado sdk build` 不重生成 subdir.mk，补丁保留。
验证方式：subdir.mk 补丁 mtime < main.o/ELF mtime，且各 ELF 哈希互异。SDK CLI 的 app 默认构建目标可能只执行首个对象目标并仍返回成功；因此最终构建证据额外使用 SDK toolchain 执行 `make all`，必须看到 linker output、重新生成 ELF 并复核 SHA256，不能只依据 `Build finished` banner。

| 镜像 | 工作区 | 编译宏 | ELF SHA256 |
|---|---|---|---|
| 正式 | `sdk/ws_stage5_m0_fresh` | 无 | `be9db4a29c1c540d49bf6825bc5b4684e6bea76086f30639d9dc19cbb5985657` |
| DMA_ERROR | `sdk/ws_stage5_m1_dma_error` | `MODE=1` | `dff0d135e6c9e2a515ecd1f5e119b188196975086c6b4658d603646540ee574c` |
| RESET_TIMEOUT | `sdk/ws_stage5_m1_reset_timeout` | `MODE=2` | `ecfabf1230bd0d78fb17aa364c165d2f67aaa57c874c38b2c98aa012d2bd37e7` |
| STREAM_UNDERFLOW | `sdk/ws_stage5_m1_stream_underflow` | `MODE=3` | `061f3d2d43e3fb5686d41b2817d10ec155e858c5276f5bb126da0e4880b585d4` |
| TIMEOUT | `sdk/ws_stage5_m1_timeout` | `MODE=4` | `df18190dd51f3acd4bd07ec12c2ada7b39bca28e93d88d15f906e1b102a918db` |

源码：`sdk/src/main.c` SHA256
`9c1eae081f318bf136df225963df84e6f968dce9720410cc8ff23d8d89584c43`
（已同步至各工作区 `src/main.c`，sha256sum 一致）。
gate 脚本：`scripts/stage5_fault_gate.py` SHA256
`aabc21aec3b4d0dd80765085f3a95fe26fc65cc55be8f2f8d0c7d7e70d82b4ba`。

## M1 板级停止门证据（COM11，2026-09-06）

四种编译期 fault ELF 均已逐一下载到 COM11，并由 `scripts/stage5_fault_gate.py` 取得真实 UART transcript：

| Fault | Transcript | 预期 ERROR | 结果 |
|---|---|---:|---|
| DMA_ERROR | `evidence/stage5_m1/DMA_ERROR_uart.txt` | `0x23` | PASS |
| RESET_TIMEOUT | `evidence/stage5_m1/RESET_TIMEOUT_uart.txt` | `0x21` | PASS |
| STREAM_UNDERFLOW | `evidence/stage5_m1/STREAM_UNDERFLOW_uart.txt` | `0x25` | PASS |
| TIMEOUT | `evidence/stage5_m1/TIMEOUT_uart.txt` | `0x24` | PASS |

每份 transcript 都实际记录了 `FAULT INJECTED` → 预期 ERROR →
`FAULT RECOVERED`（STOP/IDLE/清错/再启动）→ `ACTIVE_BRAM_UNCHANGED`。
四种 fault 完成后已恢复正式 ELF；`evidence/stage5_m1/formal_download.log` 记录下载，
`evidence/stage5_m1/formal_protocol_uart.txt` 记录旧协议 `161/161` 全通过。

正式 ELF 与 fault ELF 均绑定同一份 `sdk/src/main.c`（源码哈希见上），但 fault
宏只存在于隔离测试工作区；正式 ELF 使用 `flags=none`。

## M1 收尾回归

- Host 全量回归：`host/.venv/Scripts/python.exe -m pytest host/tests/ -q` → **472 passed**。
- M1 聚焦回归：`test_stage5_m1_faults.py` **7 passed**，`test_stage5_m1_gate.py` **5 passed**。
- Python 字节码编译：`python -m compileall -q host/siggen host/tests scripts` → **PASS**。
- SDK C 语法：SDK `arm-none-eabi-gcc -fsyntax-only` → **PASS**（仅有 Xilinx `#pragma message` 提示）。
- `git diff --check` → **PASS**。

## 边界声明

- 本地构建通过 ≠ 板级故障通过。
- static C / Python 测试 ≠ 生产注入证据。
- 仍不宣称 zero-gap seamless ping-pong（simple DMA bounded reload）。
- DDR PCB 物理签核仍 BLOCKED（见 manifest `ddr_hardware_signoff`）。
