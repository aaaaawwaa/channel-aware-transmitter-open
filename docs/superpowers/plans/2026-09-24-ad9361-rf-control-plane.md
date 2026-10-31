# AD9361 RF 控制面与原始 IQ 数据面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 ACM2108 AWG 主线的前提下，交付可独立验证的 AD9361 RF 控制面、原始 IQ 数据面、常驻板端 daemon、独立 Python 客户端和 RF GUI，并以分层证据完成数字环回与物理环回验收。

**Architecture:** 保留现有 `rf_overlay/sw/board_app` CLI 作为诊断入口，把 vendor API、MMIO、SPI、BRAM、初始化和硬件回读语义抽成共享的 `rf_core`。C daemon 独占 AD9361/PL 资源并提供单租约 TCP 服务：JSON 行负责控制，精确长度的小端 `s16_iq_le` 二进制帧负责 IQ。新增 `rf_host/` 是完全独立的 Python 客户端和 PySide6 GUI；它不导入 `host/siggen/`，也不接触寄存器。

虽然批准的规格同时覆盖 core、daemon、协议、GUI、overlay 生命周期和板级验收，但这些部分共享同一套协议与硬件安全门禁；本计划将它们拆成每阶段都有离线测试和明确输出的任务，而不是拆成互相会漂移的多份实现计划。

**Tech Stack:** C99、现有 AD9361 vendor API、`/dev/mem`/`fake_pl` 平台层、POSIX socket/poll/thread、Vivado 2025.2、Verilog/SystemVerilog、Python 3、PySide6、NumPy、pytest、PYNQ 2.7。

**Spec:** `docs/superpowers/specs/2026-09-24-ad9361-rf-control-plane-design.md`

## Global Constraints

- ACM2108 主线继续使用 `host/main.py`、COM11 协议、`sdk/` 和原有 AWG bitstream；RF 改动不得接入或替换它。
- 绝不修改 `rf_overlay/sw/board_app/vendor/`，包括文件内容、文件名、顺序和生成产物；每个 C 阶段都运行 vendor 完整性检查。
- 绝不使用裸 `ssh`/`scp`；板上部署和管理只使用 `<LOCAL_PATH>
- C daemon 不调用 Python `Overlay()`；overlay 加载只能由受控 launcher 完成，检测到 `pynq-status`、残留 Overlay 使用者或版本不匹配时失败退出，不强行重置共享硬件。
- 物理 loopback 必须明确确认线缆和端口，并始终使用显式 `40000 mdB`；`rf.init` 必须显式传入 `attenuation_mdB`，不得隐式使用 0 mdB。
- TX、RX 和 loopback 默认互斥；pending 事务持有锁，冲突命令只返回 `BUSY`；停止成功必须由硬件回读确认，失败进入 `FAULT`。
- `phy_mode` 只能在 DATA_CLK 停止的窗口写入；不得以“phase increment 为 0”冒充时钟停止。
- AXI BRAM 仍使用字节地址；`Enable_32bit_Address=true` 必须保留；软件 word index 由平台层统一乘 4。
- RX 12-bit 样本必须符号扩展为 `int16`；TX `int16` 越界必须拒绝，不能静默截断。
- 采样、同步、解调、CRC、BER、EVM 分开表示；第一版没有实现的指标显示 `unsupported` 及原因，不填 0。
- host-test、SPI PRODUCT_ID、控制面回读、数字 loopback、物理 RF loopback 分开报告；任何一层不能替代后续层。
- 没有真实板级证据的项目写 `NOT RUN`，不得写 `PASS`；不得为了让测试变绿放宽 mask、threshold 或判据。
- 不修改、清理、覆盖当前工作区已有的用户修改和未跟踪探针文件；实现阶段只添加/修改本计划列出的文件。

---

## 文件地图与边界

### 将创建的文件

- `rf_overlay/sw/board_app/rf_core.h`：共享 RF core 的 opaque handle、配置结构、状态和硬件原语接口；不暴露 vendor 结构布局。
- `rf_overlay/sw/board_app/rf_core.c`：从现有 `rf_main.c` 提取初始化参数、FIR 配置、采样率/LO/衰减设置、关键回读、TX/RX primitive 和安全停发。
- `rf_overlay/sw/board_app/rf_iq.h`：12-bit 补码与 32-bit BRAM word、`int16 I/Q` 与 wire frame 的纯函数接口。
- `rf_overlay/sw/board_app/rf_iq.c`：IQ 范围校验、pack/unpack、精确字节长度检查。
- `rf_overlay/sw/board_app/rf_protocol.h`：NDJSON 请求/响应、错误码、状态枚举和帧头结构。
- `rf_overlay/sw/board_app/rf_protocol.c`：受限 JSON 字段解析、响应序列化、IQ frame header 校验和精确 payload I/O。
- `rf_overlay/sw/board_app/rf_daemon.c`：监听 socket、单控制租约、请求分派、pending 锁、disconnect watchdog 和 daemon 主循环。
- `rf_overlay/sw/board_app/rf_daemon.h`：daemon 配置和可注入 transport/hardware 回调接口。
- `rf_overlay/sw/board_app/test/test_rf_iq.c`：IQ pack、范围、符号扩展和 frame length 的 C 测试。
- `rf_overlay/sw/board_app/test/test_rf_protocol.c`：请求解析、错误响应、frame header 和 payload timeout 的 C 测试。
- `rf_overlay/sw/board_app/test/test_rf_state.c`：状态迁移、互斥、pending、stop/fault 和物理安全门禁测试。
- `rf_overlay/sw/board_app/test/test_rf_daemon.c`：socket loopback、租约、请求/二进制帧交错和断线安全停发测试。
- `rf_overlay/sw/board_app/test/test_vendor_integrity.py`：读取 `vendor/SHA256SUMS` 并校验 vendor 文件完整性。
- `rf_overlay/sw/board_app/rf_daemon_main.c`：真实板端启动入口，检查版本参数后调用 daemon。
- `rf_overlay/sw/board_app/rf_version.h`：daemon 版本和协议主版本的编译时常量。
- `rf_overlay/rtl/tb/rf_sample_reader_tb.sv`：TX reader 的 BRAM 延迟、节拍、顺序、wrap/hold TB。
- `rf_overlay/rtl/tb/rf_decimate_tb.sv`：D=1/2/4/8/16 逐点 golden TB。
- `rf_overlay/rtl/tb/rf_sample_capture_tb.sv`：commit/arm/done/busy、RX BRAM 写入和 continuous TB。
- `rf_overlay/sw/rf_overlay_launcher.py`：PYNQ overlay 所有权、bit/hwh/manifest 校验、SLCR 时钟检查后启动 daemon。
- `rf_overlay/sw/rf-daemon.service`：板端 systemd unit，唯一指向当前 RF deployment 目录。
- `rf_overlay/sw/rf_deploy.py`：调用 `pynq_ssh.py` 的部署/重启/状态检查脚本，不实现裸 SSH。
- `rf_overlay/sw/rf_board_e2e.py`：按验收阶段连接 daemon 的板级测试驱动；每层单独输出 PASS/FAIL/NOT RUN。
- `rf_overlay/sw/tests/test_rf_release.py`：release manifest、所有权和时钟检查的 fake 环境测试。
- `rf_host/__init__.py`、`rf_host/main.py`：独立 RF GUI 包和唯一 RF GUI 入口。
- `rf_host/client.py`：`RfClient` TCP、协议协商、请求匹配、frame 收发和重连状态。
- `rf_host/iq.py`：Python IQ 序列、范围校验、wire bytes、时域/频域数据转换。
- `rf_host/model.py`：GUI 使用的 draft/observed RF 状态和 unsupported 指标模型。
- `rf_host/ui/main_window.py`：RF GUI 主窗口和页面，只调用 `RfClient`/model。
- `rf_host/ui/widgets.py`：RF 状态、回读、时域、频谱和安全确认控件。
- `rf_host/tests/test_protocol.py`：Python 协议与 IQ frame 测试。
- `rf_host/tests/test_client.py`：socket fake daemon、请求匹配、重连和 error mapping 测试。
- `rf_host/tests/test_model.py`：状态/回读/unsupported 显示模型测试。
- `rf_host/tests/test_ui.py`：离屏 GUI golden path、BUSY/FAULT/物理确认测试。
- `scripts/rf_release_manifest.py`：读取 bit/hwh/daemon version，生成含 SHA-256、Vivado 版本、WNS/WHS 的 release manifest。
- `scripts/rf_verify_release.py`：部署前只读核对 bit/hwh/manifest/daemon version。

### 将修改的文件

- `rf_overlay/sw/board_app/rf_main.c`：保留现有 CLI 命令和诊断文本，将初始化、配置、TX/RX primitive 委托给 `rf_core`；删除重复的 vendor handle/配置实现，不复制出第二套语义。
- `rf_overlay/sw/board_app/Makefile`：增加 core、IQ、protocol、daemon 目标和 host-test 子目标；vendor 源仍按当前原样编译。
- `rf_overlay/sw/board_app/platform_rf.h` / `platform_rf.c`：只补 daemon 所需的可中止等待、capture status、版本/时钟读回接口；不改变已验证地址、SPI CNT 规则、GPIO 位序和 BRAM 乘 4 规则。
- `rf_overlay/sw/board_app/test/fake_pl.c` / `fake_pl.h`：增加 TX/RX BRAM、capture done、SPI readback 和 fault 注入点，保留现有合法/非法 CNT 判据。
- `rf_overlay/build.tcl`：构建结束后输出同次构建的 overlay metadata，并保留现有综合/实现/timing 失败即退出行为。
- `rf_overlay/bd/create_bd.tcl`：只有在 TB/生成 `.hwh`/综合网表核对发现接口不一致时才修改；`Enable_32bit_Address=true`、TX/RX BRAM 地址和 AXI M00..M04 映射不可回退。
- `rf_overlay/sw/board_cmd.py`：若需要复用客户端只抽取 helper，不改变其 `pynq_ssh.py` 路径和 sudo 方式。
- `docs/rf/boardtest.md`：加入 daemon 协议、overlay manifest、分层板级验收记录和未实测项表；历史结果不改写为本轮 PASS。
- `rf_overlay/sw/board_app/vendor/*`：禁止修改。

---

## Task 1: 固化 RF core、IQ 格式和版本契约

**目标：** 先把所有后续任务依赖的硬件与数据契约写成可编译接口，不改变板上行为。

**Files:**
- Create: `rf_overlay/sw/board_app/rf_core.h`
- Create: `rf_overlay/sw/board_app/rf_iq.h`
- Create: `rf_overlay/sw/board_app/rf_iq.c`
- Create: `rf_overlay/sw/board_app/rf_version.h`
- Create: `rf_overlay/sw/board_app/test/test_rf_iq.c`
- Create: `rf_overlay/sw/board_app/test/test_vendor_integrity.py`
- Test: `rf_overlay/sw/board_app/Makefile`

**Interfaces:**

```c
typedef struct rf_core rf_core_t;

typedef enum {
    RF_STATE_OFFLINE, RF_STATE_INITIALIZING, RF_STATE_READY,
    RF_STATE_TX_ACTIVE, RF_STATE_RX_CAPTURING,
    RF_STATE_DIGITAL_LOOPBACK, RF_STATE_PHYSICAL_LOOPBACK,
    RF_STATE_STOPPING, RF_STATE_FAULT
} rf_state_t;

typedef struct {
    bool attenuation_mdB_present;
    uint32_t attenuation_mdB;
    uint64_t lo_hz;
    uint32_t sample_rate_hz;
    uint8_t phy_mode_1r1t;
} rf_init_request_t;

typedef struct {
    uint64_t lo_hz;
    uint32_t sample_rate_hz;
    uint32_t rx_bandwidth_hz;
    uint32_t tx_bandwidth_hz;
    uint32_t attenuation_mdB;
    uint8_t phy_mode_1r1t;
    uint8_t idelay_en;
    uint8_t idelay_tap;
} rf_config_t;

typedef struct {
    uint64_t lo_hz;
    uint32_t sample_rate_hz;
    uint32_t attenuation_mdB;
    uint8_t phy_mode_1r1t;
    uint8_t product_id;
} rf_readback_t;

typedef struct {
    bool tx_active;
    bool rx_busy;
    bool rx_done;
} rf_hw_status_t;

typedef struct {
    bool done;
    bool busy;
    uint16_t position;
} rf_capture_status_t;

typedef struct {
    bool two_rx_two_tx;
    bool trace_enabled;
} rf_core_open_args_t;

int rf_iq_validate(int i, int q);
uint32_t rf_iq_pack12(int i, int q);
void rf_iq_unpack12(uint32_t word, int *i, int *q);
size_t rf_iq_wire_bytes(size_t samples);
int rf_iq_wire_to_bram(const uint8_t *src, size_t bytes,
                       uint32_t *dst, size_t words);
int rf_iq_bram_to_wire(const uint32_t *src, size_t words,
                       uint8_t *dst, size_t capacity);
```

`rf_version.h` 定义 `RF_DAEMON_VERSION`、`RF_PROTOCOL_MAJOR`、`RF_PROTOCOL_MINOR`，后续 manifest、daemon 和 client 只能引用这些名字，不各自硬编码版本。

- [ ] **Step 1: 先写失败的 C IQ 测试。** 测试 `-2048/-1/0/2047` 的 I/Q round-trip，检查负数符号扩展、`bytes == samples * 4`、过界输入返回非零、短 payload/长 payload 被拒绝。
- [ ] **Step 2: 运行失败测试。**

```bash
make -C rf_overlay/sw/board_app host-test-iq
```

Expected: 编译失败，因为 `rf_iq.c` 和 `test_rf_iq.c` 的目标尚不存在；不能用现有 `test_platform_rf` 的通过代替此门。

- [ ] **Step 3: 实现最小 IQ 转换。** 只使用 `rf_iq_pack()` 现有 12-bit 补码规则；wire 固定为 `I little-endian, Q little-endian`，不使用隐式截断。`rf_iq_wire_to_bram()` 在发现任何样本越界或长度不等于 `words * 4` 时不写入目标缓冲。
- [ ] **Step 4: 运行 IQ 测试并加入现有 Makefile。**

```bash
make -C rf_overlay/sw/board_app host-test-iq
```

Expected: 所有边界和负数用例 PASS；`make -C rf_overlay/sw/board_app host-test` 仍执行已有 platform/selftest。

- [ ] **Step 5: 添加 vendor 完整性门。** `test_vendor_integrity.py` 读取 `board_app/vendor/SHA256SUMS`，按其路径计算所有摘要；缺文件、额外被纳入的 vendor 源、摘要不匹配都失败。Makefile 增加：

```make
vendor-integrity:
	python test/test_vendor_integrity.py
```

运行 `make -C rf_overlay/sw/board_app vendor-integrity`，输出应明确列出校验文件数；不得修改 manifest 以适应变更。

**完成门：** IQ C 测试、既有 platform/selftest、vendor-integrity 全部通过；没有 RF 硬件动作。

---

## Task 2: 从 `rf_main.c` 抽取共享 RF core，并保持 CLI 语义不变

**目标：** 让 CLI 与 daemon 调用同一个硬件实现，解决当前 `phy` 仅存于单次 CLI 进程的问题。

**Files:**
- Create: `rf_overlay/sw/board_app/rf_core.c`
- Modify: `rf_overlay/sw/board_app/rf_core.h`
- Modify: `rf_overlay/sw/board_app/rf_main.c`
- Modify: `rf_overlay/sw/board_app/platform_rf.h`
- Modify: `rf_overlay/sw/board_app/platform_rf.c`
- Modify: `rf_overlay/sw/board_app/Makefile`
- Test: `rf_overlay/sw/board_app/test/test_platform_rf.c`
- Test: `rf_overlay/sw/board_app/test/test_selftest.c`

**Interfaces:**

```c
int rf_core_open(rf_core_t *core, const rf_core_open_args_t *args);
int rf_core_init(rf_core_t *core, const rf_init_request_t *request,
                 rf_readback_t *readback);
int rf_core_configure(rf_core_t *core, const rf_config_t *config,
                      rf_readback_t *readback);
int rf_core_write_tx(rf_core_t *core, const uint32_t *words, size_t count);
int rf_core_prepare_tx(rf_core_t *core, size_t count, bool wrap);
int rf_core_start_tx(rf_core_t *core);
int rf_core_stop_tx(rf_core_t *core, rf_hw_status_t *status);
int rf_core_prepare_rx(rf_core_t *core, uint8_t ratio, size_t count,
                       bool continuous);
int rf_core_start_rx(rf_core_t *core);
int rf_core_poll_rx(rf_core_t *core, rf_capture_status_t *status);
int rf_core_read_rx(rf_core_t *core, uint32_t *words, size_t count);
int rf_core_set_digital_loopback(rf_core_t *core, bool enable,
                                 uint8_t *readback);
int rf_core_safe_shutdown(rf_core_t *core, rf_hw_status_t *status);
void rf_core_close(rf_core_t *core);
```

`rf_core` 内部持有 vendor `struct ad9361_rf_phy *`，但该类型不出现在 daemon/client 协议。`rf_core_init()` 要求 `attenuation_mdB_present=true`；缺失即 `RF_ERR_INVALID_ARGUMENT`。`rf_core_set_digital_loopback()` 必须读取 0x3F5 原值、只修改 bit 0、再读回，不接受“写入即成功”。

- [ ] **Step 1: 为共享 core 写 host fake 测试。** 扩展 `fake_pl` 注入：合法 PRODUCT_ID、关键寄存器回读失败、TX reader position 不动、capture done 未出现、stop 后仍 active、0x3F5 bit0 未改变。测试先验证这些故障都会转为非零错误或 `FAULT`，而不是打印 PASS。
- [ ] **Step 2: 运行失败测试。**

```bash
make -C rf_overlay/sw/board_app host-test-core
```

Expected: 新测试目标不存在或未定义 core symbols；现有测试仍可单独运行，证明不是用删掉旧测试换绿。

- [ ] **Step 3: 提取 `rf_main.c` 的真实逻辑。** 迁移 `rf_init_param_fill()`、FIR 表使用、采样率设置、LO/衰减设置、DATA_CLK alias-aware status、PL readback、BRAM 配置、capture done 等到 `rf_core.c`。保留 `rf_main.c` 的命令名和诊断输出，但每个命令只调用 core。不得复制 vendor 表或修改 vendor 文件。
- [ ] **Step 4: 明确硬件时序。** `rf_core_init()` 对 1R1T 保持 `phy_mode=1`；2R2T 只在 RESETB 拉低、DATA_CLK 未出现时写 `phy_mode=0`，并读回确认。`rf_core_configure()` 在 READY 才运行，写采样率/LO/FIR 后逐项读回；任何不匹配返回 `RF_ERR_HARDWARE_IO` 并使 caller 进入 FAULT。
- [ ] **Step 5: 实现 capture/TX stop 的回读。** TX stop 要清 source/arm，并连续读取 `RF_REG_SMP_POS`/硬件状态确认停止；RX one-shot 只能在 `RF_CAP_DONE` 为 1 后读 RX BRAM；continuous capture 在 stop 前禁止读作“完成帧”。
- [ ] **Step 6: 运行全 C 离线门。**

```bash
make -C rf_overlay/sw/board_app clean
make -C rf_overlay/sw/board_app host-test
make -C rf_overlay/sw/board_app vendor-integrity
```

Expected: 原有 platform/selftest 与新增 core tests 全部通过；CLI 的 `selftest` 不需要 vendor handle；没有板级 PASS 声明。

**完成门：** CLI 和未来 daemon 共用 core；fake 中每个硬件故障都有失败证据；`rf_main.c` 不再持有 daemon 所需的第二份初始化/配置语义。

---

## Task 3: 为现有 TX/RX RTL 建立独立的时序与数据顺序证明

**目标：** 在 daemon 使用 RX/TX 数据面前，先用 simulation 证明 BRAM byte address、同步读延迟、抽取和 commit 状态机。

**Files:**
- Create: `rf_overlay/rtl/tb/rf_sample_reader_tb.sv`
- Create: `rf_overlay/rtl/tb/rf_decimate_tb.sv`
- Create: `rf_overlay/rtl/tb/rf_sample_capture_tb.sv`
- Modify only if a failing TB proves a real RTL defect: `rf_overlay/rtl/rf_sample_reader.v`, `rf_overlay/rtl/rf_decimate.v`, `rf_overlay/rtl/rf_sample_capture.v`
- Test: `rf_overlay/bd/create_bd.tcl`, generated `.hwh` and netlist reports

**Interfaces under test:**

- `rf_sample_reader`: `ce`, `enable`, `length`, `wrap`, `bram_en`, byte `bram_addr`, `sample_i/q`, `pos`, `active`。
- `rf_decimate`: `ratio_sel=0..4` maps to D=1/2/4/8/16，`out_ce` 每个 group 恰好一次。
- `rf_sample_capture`: FCLK config stable → commit toggle → DATA_CLK arm → decimated samples → done，one-shot done 后 BRAM 不再覆盖。

- [ ] **Step 1: 写 reader TB 的失败断言。** BRAM model 只在 `bram_en` 时记录 byte address，并在下一个 DATA_CLK 返回 word；输入 `0x00100001`, `0x00200002`, `0x00300003`, `0x00400004`，断言输出第一个样本为 word 0 而不是 word 1，前四个地址为 `0,4,8,12`，后续每次增加 4。分别覆盖 len=1/2/4096、wrap=0/1、disable/re-enable 后回到 0、1R1T/2R2T `ce` 周期。
- [ ] **Step 2: 写 decimator TB 的失败断言。** 对固定有符号序列分别计算 Python/C golden 等价的算术均值，断言 D=1/2/4/8/16 的输出序列、负数右移和 group 清零；`ratio_sel=5..7` 按当前设计夹到 D=16，TB 必须显式记录这是 RTL 行为而非协议允许值。
- [ ] **Step 3: 写 capture TB 的失败断言。** 先写 `cfg_ratio/cfg_len/cfg_cont`，只翻转 `cfg_commit`，断言未 commit 前配置不生效；arm 后 one-shot 写满 `len` 个 decimated words，断言 `done=1`、`pos=len-1`、done 后读 BRAM 稳定；continuous 模式断言地址回绕且 `busy` 保持。
- [ ] **Step 4: 运行三组 TB，先确认红灯。** 使用 Vivado 2025.2 的 `xvlog`/`xelab`/`xsim -runall` 或项目既有仿真 wrapper；每组必须输出带名字的 `PASS`/`FAIL`，不能只靠波形观察。
- [ ] **Step 5: 只修真实 RTL 缺陷。** 如果失败，按 failure trace 定位是延迟、地址、CDC 或状态机问题；不得把 expected 改成当前错误输出。保留 `Enable_32bit_Address=true` 和 `word << 2`。
- [ ] **Step 6: 核对 BD 生成结果。** 运行 `vivado2025.cmd config check` 后，用 RF overlay 的 batch build 生成 `.hwh`，检查 `RF_AXI_CTRL_BASE=0x43000000`、SPI/GPIO/TX/RX BRAM 五个 aperture、port-B byte address、`rf_sample_capture` 到 RX BRAM 的实际连接；若生成的 `.hwh` 与 `platform_rf.h` 不一致，先停在本任务。

**完成门：** 三组 RTL TB 全绿；`.hwh`/综合网表证明 RX capture → RX BRAM 可达；未跑 bitstream 或板级测试前不得写数据面 PASS。

---

## Task 4: 实现 NDJSON、IQ binary frame 和 C daemon 状态机

**目标：** 在 fake hardware 上交付完整协议和安全状态机，先不连接真实板卡。

**Files:**
- Create: `rf_overlay/sw/board_app/rf_protocol.h`
- Create: `rf_overlay/sw/board_app/rf_protocol.c`
- Create: `rf_overlay/sw/board_app/rf_daemon.h`
- Create: `rf_overlay/sw/board_app/rf_daemon.c`
- Create: `rf_overlay/sw/board_app/test/test_rf_protocol.c`
- Create: `rf_overlay/sw/board_app/test/test_rf_state.c`
- Create: `rf_overlay/sw/board_app/test/test_rf_daemon.c`
- Modify: `rf_overlay/sw/board_app/Makefile`

**Protocol contract:**

- First line on a new connection is `{"id":1,"op":"protocol.hello","args":{"major":1,"minor":0}}`; incompatible major returns `VERSION_MISMATCH` and performs no hardware action。
- Every request with `id` gets exactly one response with the same `id`；无 `id` 仅用于异步事件。
- `tx.load` 的 JSON header 后立即读取 `bytes=samples*4` 个 payload；任何短读、超读、timeout 或越界使整帧作废，返回 `INVALID_ARGUMENT`/`TIMEOUT`，不得把部分样本写入 TX BRAM。
- RX 返回 JSON `type=iq_frame,direction=rx,format=s16_iq_le,samples,bytes,sample_rate_hz,ratio,timestamp_ns` 后紧跟 payload；payload 期间不插入 event。
- 最大 samples 是 `RF_BRAM_WORDS`；请求不能借 frame length 绕过 BRAM 边界。

**状态接口：**

```c
typedef enum {
    RF_OP_CONFIGURE, RF_OP_TX_START, RF_OP_RX_CAPTURE,
    RF_OP_DIGITAL_LOOPBACK, RF_OP_PHYSICAL_LOOPBACK
} rf_operation_t;

typedef struct {
    rf_state_t state;
    bool pending;
    bool lease_held;
} rf_session_t;

typedef struct {
    int code;
    const char *message;
} rf_error_t;

int rf_state_can_start(rf_state_t state, rf_operation_t op);
int rf_state_begin(rf_session_t *session, rf_operation_t op);
int rf_state_finish(rf_session_t *session, rf_hw_status_t status);
int rf_state_safe_stop(rf_session_t *session, rf_error_t *error);
```

状态必须覆盖 `OFFLINE → INITIALIZING → READY → TX_ACTIVE/RX_CAPTURING/DIGITAL_LOOPBACK/PHYSICAL_LOOPBACK → STOPPING → READY/FAULT`。普通 TX/RX 互斥；pending 时所有冲突命令返回 `BUSY`。第二个 TCP 客户端返回 `BUSY`，不抢租约。

- [ ] **Step 1: 写 protocol parser 的失败测试。** 覆盖合法 hello、缺 id、未知 op、缺 args、非法 JSON、超过 line limit、错误码序列化、`bytes != samples*4`、`format`/`direction` 不匹配。测试断言 parser 不执行任何硬件 callback。
- [ ] **Step 2: 写 state machine 的失败测试。** 断言 OFFLINE 不能 `tx.start`，TX_ACTIVE 不能 `rx.capture`，pending 期间返回 `BUSY`，stop 回读失败进入 `FAULT`，physical start 缺 cable confirmation 或 attenuation 非 40000 返回 `INVALID_ARGUMENT`，disconnect 只调用 safe stop/max attenuation。
- [ ] **Step 3: 写 socket framing 的失败测试。** 用 `socketpair()` 分段写 JSON 和 payload：短读、payload 超时、客户端在 payload 中断开、第二客户端竞争、RX frame 后 event 插入都必须可检测；测试禁止出现“部分写入后返回成功”。
- [ ] **Step 4: 运行新测试确认失败。**

```bash
make -C rf_overlay/sw/board_app host-test-protocol
make -C rf_overlay/sw/board_app host-test-daemon
```

Expected: 新 symbols/targets 不存在，不能依赖现有 CLI 输出当作协议测试。

- [ ] **Step 5: 实现受限 parser 和 serializer。** 不引入可执行表达式、任意寄存器写或通用 JSON object 依赖；只接受协议声明的字段和有限整数/布尔/字符串。错误码固定为 `INVALID_ARGUMENT`、`INVALID_STATE`、`BUSY`、`HARDWARE_IO`、`SPI_ERROR`、`TIMEOUT`、`OVERFLOW`、`UNSUPPORTED`、`VERSION_MISMATCH`。
- [ ] **Step 6: 实现单租约 daemon。** listener 使用配置端口和允许的管理网地址；一次只服务一个控制 client。请求分派到 core，硬件调用在 pending 锁内完成；不得在 payload 读取期间发送 event。daemon 退出或 socket 断开时，只执行“若 TX active 则停发 → 设置最大衰减 → 回读确认”，不自动重新初始化或配置。
- [ ] **Step 7: 实现所有第一版 op。** `device.info`、`state.get`、`rf.init`、`rf.configure`、`tx.load`、`tx.start/stop`、`rx.capture`、`loopback.digital.start`、`loopback.physical.start`、`loopback.stop`、`status.subscribe`。GUI API 不暴露 `regwrite`；raw SPI 仍只在受控 CLI。
- [ ] **Step 8: 运行 C daemon 全门。**

```bash
make -C rf_overlay/sw/board_app clean
make -C rf_overlay/sw/board_app host-test
make -C rf_overlay/sw/board_app host-test-protocol
make -C rf_overlay/sw/board_app host-test-daemon
make -C rf_overlay/sw/board_app vendor-integrity
```

Expected: fake hardware 上所有协议、状态、frame、lease、disconnect safety 测试通过；输出明确标注 `REAL BOARD: NOT RUN`。

**完成门：** 不接板即可证明协议 framing 和状态门禁；不存在未验证的“写入即成功”路径。

---

## Task 5: 增加 daemon 可执行文件、overlay manifest 和安全 launcher

**目标：** 让板端启动流程可审计、可阻止错误 overlay/所有权/时钟组合，并把 bit/hwh/daemon 绑定起来。

**Files:**
- Create: `rf_overlay/sw/board_app/rf_daemon_main.c`
- Create: `rf_overlay/sw/rf_overlay_launcher.py`
- Create: `rf_overlay/sw/rf-daemon.service`
- Create: `rf_overlay/sw/rf_deploy.py`
- Create: `scripts/rf_release_manifest.py`
- Create: `scripts/rf_verify_release.py`
- Modify: `rf_overlay/sw/board_app/Makefile`
- Modify: `rf_overlay/build.tcl`
- Test: `rf_overlay/sw/board_app/test/test_vendor_integrity.py`

**Manifest contract:**

```json
{
  "product": "ac820-ad9361-rf",
  "protocol_major": 1,
  "daemon_version": "RF_DAEMON_VERSION",
  "bit_sha256": "64 lowercase hexadecimal characters",
  "hwh_sha256": "64 lowercase hexadecimal characters",
  "vivado_version": "2025.2",
  "wns_ns": 0.0,
  "whs_ns": 0.0,
  "generated_at": "ISO-8601 timestamp"
}
```

实际实现必须写入真实的 64 位十六进制摘要、版本、时间戳和 timing 数值；生成器在任一字段缺失时失败而不是填 0。

- [ ] **Step 1: 写 manifest/launcher 的失败测试。** 用临时目录生成 bit/hwh/manifest，覆盖 bit/hwh hash 不匹配、daemon version 不匹配、Vivado 版本不匹配、缺少 timing 字段、`pynq-status` active、发现另一个 RF/PYNQ overlay 使用者、FCLK divisor 不符合预期。测试断言 launcher 返回非零且不调用 `Overlay()`。
- [ ] **Step 2: 运行失败测试。**

```bash
python -m pytest rf_overlay/sw/tests/test_rf_release.py -q
```

Expected: 新测试/模块不存在；不能先把失败测试改成宽松判据。

- [ ] **Step 3: 修改 build 输出 metadata。** `rf_overlay/build.tcl` 保留 synth/impl/bitstream/timing 的失败语义；构建完成后输出 bit/hwh 到同一 `out` 目录，记录 Vivado version、WNS、WHS 和同次构建标识。由 `scripts/rf_release_manifest.py` 计算 SHA-256，禁止用 mtime 或“文件存在”证明同次构建。
- [ ] **Step 4: 实现 launcher 所有权检查。** 启动前检查 systemd `pynq-status` 状态和相关进程；不杀进程、不调用 `Overlay()` 抢占已使用的 FPGA manager。检查 `.bit/.hwh/manifest` hash/version，检查 P18=2.5V 的部署前记录，检查 FCLK0/FCLK1 实际 SLCR 分频器；不得调用 `Clocks.fclk1_mhz` 修改 FCLK1。
- [ ] **Step 5: 实现 systemd unit 和 deploy helper。** unit 的 `ExecStart`, `WorkingDirectory`, manifest 路径和 overlay 路径只出现一份有效配置；daemon 失败时不自动回退旧目录。`rf_deploy.py` 只通过 `pynq_ssh.py put/run`，远端 sudo 使用 `echo $PYNQ_PASSWORD | sudo -S`；脚本中不得出现 `ssh`、`scp` 或 `subprocess` 裸网络命令。
- [ ] **Step 6: 添加 daemon version check。** `rf_daemon_main` 在打开硬件前读取 manifest 并比较 `RF_DAEMON_VERSION`/protocol major；失败退出，不写寄存器、不打开 RF SPI。启动默认 TX disabled/max attenuation，不能自动发射。
- [ ] **Step 7: 运行静态部署门。**

```bash
python scripts/rf_release_manifest.py --help
python scripts/rf_verify_release.py --help
python -m pytest rf_overlay/sw/tests/test_rf_release.py -q
make -C rf_overlay/sw/board_app vendor-integrity
```

Expected: fake manifest 的负控全部失败；未运行真实 launcher 前不得报告板端服务 PASS。

**完成门：** daemon、bit、hwh 和 manifest 能互相证明版本一致；launcher 遇到所有权/版本/时钟问题 fail-closed。

---

## Task 6: 实现独立 Python `RfClient` 和 RF GUI 状态模型

**目标：** 提供不依赖 ACM2108 的主机协议客户端和可测试的 UI 模型。

**Files:**
- Create: `rf_overlay/sw/rf_client_protocol.py`
- Create: `rf_host/__init__.py`
- Create: `rf_host/client.py`
- Create: `rf_host/iq.py`
- Create: `rf_host/model.py`
- Create: `rf_host/tests/test_protocol.py`
- Create: `rf_host/tests/test_client.py`
- Create: `rf_host/tests/test_model.py`
- Do not modify: `host/main.py`, `host/siggen/*`

**Interfaces:**

```python
from dataclasses import dataclass

@dataclass
class RfDraft:
    lo_hz: int
    sample_rate_hz: int
    attenuation_mdB: int

@dataclass
class RfObserved:
    state: str
    readback: dict

@dataclass
class MetricStatus:
    supported: bool
    reason: str
    value: float | None

class RfClient:
    def connect(self) -> None:
        raise NotImplementedError
    def close(self) -> None:
        raise NotImplementedError
    def request(self, op: str, args: dict) -> dict:
        raise NotImplementedError
    def tx_load(self, samples: Sequence[tuple[int, int]], sample_rate_hz: int) -> dict:
        raise NotImplementedError
    def rx_capture(self, *, ratio: int, samples: int, continuous: bool = False) -> tuple[dict, list[tuple[int, int]]]:
        raise NotImplementedError
    def subscribe(self, enabled: bool = True) -> dict:
        raise NotImplementedError
```

`RfClient` 必须区分 response、event 和 `iq_frame`；请求 ID 单调递增并且不能被事件占用。`tx_load` 在发送 header 前校验所有 samples；`rx_capture` 要严格读取 header 声明的 payload 长度。网络断开清理租约状态，不伪造成功结果。

`RfModel` 只存 draft/observed：

```python
class RfModel:
    draft: RfDraft
    observed: RfObserved
    def apply(self, client: RfClient) -> dict:
        raise NotImplementedError
    def accept_readback(self, result: dict) -> None:
        raise NotImplementedError
    def metric(self, name: str) -> MetricStatus:
        raise NotImplementedError
```

`MetricStatus` 对尚未实现的 BER/EVM/CRC/解调返回 `supported=False, reason="该指标未在 RF v1 实现"`，不能以 `0` 伪装。

- [ ] **Step 1: 写 Python 协议和 client 的失败测试。** fake socket 分片返回 JSON、payload、event，测试请求 ID 匹配、事件不影响 response、短帧/长帧/超时/非法范围、第二 client `BUSY`、断线后重连状态清空。
- [ ] **Step 2: 运行失败测试。**

```bash
python -m pytest rf_host/tests/test_protocol.py rf_host/tests/test_client.py -q
```

Expected: 新包不存在或 tests fail；不能通过修改测试 fixture 省略 binary payload。

- [ ] **Step 3: 实现 `rf_host/iq.py` 和协议编解码。** 使用 `struct.pack('<hh', i, q)`；有效范围严格为 `-2048..2047`；样本数/bytes 乘法做边界检查；RX metadata 原样保留 ratio、actual_samples、sample_rate_hz、timestamp_ns。
- [ ] **Step 4: 实现 `RfClient`。** `connect()` 完成 hello/version negotiation；`request()` 只返回同 id 的 response；`tx_load()`/`rx_capture()` 复用同一 socket framing；禁止发送任意寄存器操作；错误对象保留 `code`、`message` 和 daemon 返回的 detail。
- [ ] **Step 5: 写并实现 model 的失败测试。** 覆盖 configure 前 draft 与 observed 分离、硬件回读不一致进入 fault、BUSY 不覆盖 draft、断线不显示 READY、unsupported 指标含原因。
- [ ] **Step 6: 运行 Python 离线门。**

```bash
python -m pytest rf_host/tests/test_protocol.py rf_host/tests/test_client.py rf_host/tests/test_model.py -q
```

Expected: 所有 client/model tests 通过；未连接 daemon 的测试只使用 fake server，不能称为真实 RF PASS。

**完成门：** RF client 可以独立与 fake daemon 完成 TX/RX frame 闭环，且 ACM2108 `host/` 目录没有被导入或修改。

---

## Task 7: 实现独立 PySide6 RF GUI

**目标：** 在不改变 `host/main.py` 的情况下提供 RF 控制和原始 IQ 观察界面。

**Files:**
- Create: `rf_host/main.py`
- Create: `rf_host/ui/main_window.py`
- Create: `rf_host/ui/widgets.py`
- Create: `rf_host/tests/test_ui.py`
- Modify: `rf_host/model.py` only for view-model behavior found by UI tests

**UI contract:**

- 连接页：host/port、protocol/daemon/bitstream identity、CONNECT/DISCONNECT。
- RF config 页：explicit attenuation、LO、sample rate、bandwidth、PHY mode；apply 只能显示 daemon 的 readback。
- TX/RX 页：已知 IQ 生成、`tx.load/start/stop`、一次性 RX capture、实际 metadata；禁止把采集数据当作解调结果。
- Loopback 页：digital 和 physical 两个分离按钮；physical 必须有线缆/端口确认和固定 `40000 mdB` 提示，用户未确认则不发请求。
- 状态栏：READY/TX_ACTIVE/RX_CAPTURING/FAULT、BUSY、错误码、hardware readback；BER/EVM/CRC/解调显示 `unsupported` 及原因。
- 时域/频域绘图只显示接收到的原始 IQ 或明确标注的合成 TX IQ；不以合成数据证明 RF 路径。

- [ ] **Step 1: 写离屏 UI 失败测试。** 使用 fake `RfClient`，断言未连接时不能 apply/start，BUSY 显示 BUSY，FAULT 锁住 TX，physical 未确认不调用 client，确认后调用参数含 `attenuation_mdB=40000`，unsupported label 不显示 0。
- [ ] **Step 2: 运行失败测试。**

```bash
python -m pytest rf_host/tests/test_ui.py -q
```

Expected: UI 模块不存在或断言失败。

- [ ] **Step 3: 实现最小主窗口和 widgets。** GUI 只引用 `RfClient` 和 `RfModel`；不得直接 import `platform_rf`, `ctypes`, `/dev/mem`, vendor API 或 `rf_main`。长操作在 worker/thread 中运行，UI 只依据 response 更新 observed。
- [ ] **Step 4: 实现 raw IQ views。** 频谱转换标注 sample rate、capture ratio、actual samples、timestamp；窗口为空或 metadata 不完整时显示缺失状态，不填默认 0。
- [ ] **Step 5: 运行离屏 GUI 门和独立入口 smoke test。**

```bash
python -m pytest rf_host/tests -q
python rf_host/main.py --help
```

Expected: RF tests 全绿；入口可导入/显示 help；`python host/main.py` 的 ACM2108 入口不受本任务影响。若当前环境无 display，使用 Qt offscreen，并把真实桌面交互标为 `NOT RUN`。

**完成门：** RF GUI 与 ACM2108 GUI 入口、状态模型和协议完全隔离；物理安全确认是不可绕过的 UI 门。

---

## Task 8: 构建 RF overlay 并完成只读 release 证明

**目标：** 生成与 daemon 绑定的 RF bit/hwh，证明 TX/RX 数据路径和时序约束来自本次构建。

**Files:**
- Modify: `rf_overlay/build.tcl`
- Modify only if Task 3 evidence requires: `rf_overlay/bd/create_bd.tcl`, `rf_overlay/rtl/*.v`
- Create/use: `scripts/rf_release_manifest.py`, `scripts/rf_verify_release.py`
- Test: `rf_overlay/out/rf_overlay.bit`, `rf_overlay/out/rf_overlay.hwh`, timing/netlist reports

- [ ] **Step 1: 构建前运行配置检查。**

```bash
vivado2025.cmd config check
vivado2025.cmd config show
```

确认使用 Vivado 2025.2 的真实 bin 路径；不得从安装根目录猜路径，也不得使用旧 `cli-anything-vivado`。

- [ ] **Step 2: 运行 RTL TB 和 host-test。**

```bash
make -C rf_overlay/sw/board_app host-test
# 使用项目当前 Vivado 2025.2 xvlog/xelab/xsim wrapper 运行 rf_overlay/rtl/tb/*.sv
```

任意 TB、C test、vendor integrity 失败都阻止 build。

- [ ] **Step 3: 运行 RF overlay build。**

```bash
vivado -mode batch -source rf_overlay/build.tcl
```

构建必须完整结束并输出 bitstream、`.hwh`、WNS/WHS、Vivado version；不能用被中断留下的 bit 文件继续。命令使用 `vivado2025.cmd config show` 返回的真实 `Vivado/bin/vivado.bat`，不要调用 PATH 中未知版本的 `vivado`。

- [ ] **Step 4: 生成并核对 manifest。**

```bash
python scripts/rf_release_manifest.py \
  --bit rf_overlay/out/rf_overlay.bit \
  --hwh rf_overlay/out/rf_overlay.hwh \
  --daemon-version <RF_DAEMON_VERSION> \
  --wns <build-report-value> --whs <build-report-value> \
  --vivado 2025.2 \
  --output rf_overlay/out/rf_overlay.manifest.json
python scripts/rf_verify_release.py --manifest rf_overlay/out/rf_overlay.manifest.json
```

命令必须重新计算 bit/hwh SHA-256，检查同次构建标识、daemon/protocol version、时序字段和所有 aperture；不能只比较 mtime。

- [ ] **Step 5: 只读核对 generated `.hwh` 和 netlist。** 确认 `axi_ctrl`、SPI、GPIO、TX BRAM、RX BRAM 的实际地址仍是 `0x43000000/0x43010000/0x43020000/0x43030000/0x43040000`；确认 RX capture 写 port-B、PS 只读 port-A，确认没有 BlackBox/空 BRAM。出现地址/网表不一致时停止，不下载板卡。

**完成门：** 只有本次完整 build 的 bit/hwh/manifest/timing/netlist 证据可以进入部署；本任务本身不宣称板级 RF PASS。

---

## Task 9: 通过 `pynq_ssh.py` 部署并验证常驻 daemon 生命周期

**目标：** 在不抢占 PYNQ 共享 Overlay 所有权的前提下，让 daemon 安全启动、连接和停止。

**Files:**
- Modify: `rf_overlay/sw/rf-daemon.service`
- Modify: `rf_overlay/sw/rf_overlay_launcher.py`
- Modify: `rf_overlay/sw/rf_deploy.py`
- Test: `rf_overlay/sw/rf_board_e2e.py`
- Modify: `docs/rf/boardtest.md`

- [ ] **Step 1: 只读检查板上所有权和物理条件。** 使用项目指定 helper 执行等价检查：`pynq-status` 是否 active、进程 cwd/父子关系、当前 overlay 目录、`.bit/.hwh` manifest、FCLK0/FCLK1 SLCR 实值、P18=2.5V 记录。发现正在运行的 `pynq-status` 或残留 Overlay 使用者时只报告 BLOCKED，不杀进程、不调用 `Overlay()`。
- [ ] **Step 2: 通过 helper 部署。** `rf_deploy.py` 只能调用 `pynq_ssh.py put/run`；远端安装 unit、daemon、launcher、manifest 和 bit/hwh；systemd 三处路径指向同一 deployment directory；执行 `daemon-reload` 后再启动。
- [ ] **Step 3: 验证启动负控。** manifest hash/version 不匹配、旧 daemon、FCLK 错误、overlay owner 冲突时 unit 必须失败退出且不写 RF 寄存器；日志不得出现“回退旧目录”或“自动重置另一个 overlay”。
- [ ] **Step 4: 验证 `device.info`/hello。** 只在 launcher 成功后连接，确认 protocol version、daemon version、bit/hwh SHA-256、PRODUCT_ID 和 capabilities；PRODUCT_ID 成功只记控制面证据，不写 TX/RX 数据面 PASS。
- [ ] **Step 5: 记录结果。** `docs/rf/boardtest.md` 新增带日期、命令、manifest hash、板卡状态、失败原因和 `NOT RUN` 项的表格；不覆盖历史 loopback 结果，不把 fake/host-test 合并到硬件结果。

**完成门：** daemon 可安全启动/停止并能返回 device.info；所有权冲突 fail-closed；部署路径由 helper 证明，没有裸 SSH/SCP。

---

## Task 10: 分层执行 TX-only、RX-only 和数字 loopback 板级验收

**目标：** 先证明原始 IQ 数据面，再证明片内数字环回；每一层失败都阻止物理环回。

**Files:**
- Modify: `rf_overlay/sw/rf_board_e2e.py`
- Modify: `docs/rf/boardtest.md`
- Test: running board daemon through `RfClient`

**板级顺序和门禁：**

- [ ] **Step 1: `rf.init`。** 请求必须带 `attenuation_mdB`；首次 bring-up 使用显式安全值，记录 reset、PRODUCT_ID、PHY mode、sampling rate、LO、attenuation、DATA_CLK status 和实际 readback。任何关键 readback mismatch 为 FAIL，不能继续。
- [ ] **Step 2: configure。** 在 READY 执行 LO/sample rate/bandwidth/PHY 配置；确认 `phy_mode` 只在 DATA_CLK 停止窗口设置；配置后重新读取关键寄存器。记录 `unsupported` 指标，不尝试算 BER/EVM。
- [ ] **Step 3: TX-only。** 上传 `N=1,64,128,256,4096` 的已知 I/Q（含负值、I-only、Q-only、IQ），确认 TX BRAM readback、`tx.start`、reader position advancing、`tx.stop` 后停止且 readback 正常。DDS tone 与 BRAM source 必须有可观测区分；只证明 PL source/AD9361 TX control，不以此证明空口 RF。
- [ ] **Step 4: RX-only。** 配置 one-shot `ratio=0` 和每个允许抽取比，arm 后等待 `done=1`，只在 done 后读 RX BRAM；确认实际 samples、signed extension、地址顺序和 `busy/done`。continuous capture 在未 stop 前只报告 active，不读取成品帧。
- [ ] **Step 5: digital loopback。** daemon 事务必须显式打开 0x3F5 bit0、配置 TX/RX、捕获、关闭并 readback；使用已知序列和具名对齐模型报告逐位结果、整数样点延迟或已知 I/Q 交叉。若出现历史坏态的高/低 6-bit 双射映射，只能在报告中明确标注模型和限制，不能用模型把任意数据判为 PASS；未加载 IDELAY 的 2R2T 判据写 `NOT RUN`。
- [ ] **Step 6: stop/断线。** 测试 `loopback.stop`、client disconnect、daemon restart；确认 TX 停止、最大衰减设置并回读，不能自动重配；若 stop readback 失败，状态必须是 FAULT。
- [ ] **Step 7: 保存分层证据。** 每个阶段单独输出 `PASS`/`FAIL`/`NOT RUN`、request id、daemon/bit/hwh hash、实际 samples、ratio、timestamp 和原始 capture 文件。`device.info`/SPI ID 不能被汇总成“RF data path PASS”。

**运行方式：** 板上命令、服务重启和文件传输均由 `rf_overlay/sw/rf_deploy.py` 间接调用 `pynq_ssh.py`；主机数据交互经 `RfClient`，不直接 SSH 执行控制命令。

**完成门：** TX-only、RX-only、digital loopback 各有独立证据；任一未做或未通过项目保持 `NOT RUN`/FAIL，不进入物理环回。

---

## Task 11: 在明确安全确认后执行物理 TX→RX loopback

**目标：** 只用适合模拟 RF 路径的判据证明物理环回，不把数字逐位结果套到经过 FIR/AGC/模拟链路的信号。

**Files:**
- Modify: `rf_overlay/sw/rf_board_e2e.py`
- Modify: `docs/rf/boardtest.md`
- Test: physical loopback only after Tasks 8–10 gates pass

- [ ] **Step 1: 记录物理连接确认。** 在测试记录中写明线缆、TX 端口、RX1A 端口、衰减器和 P18=2.5V；GUI/脚本的 `cable_confirmed` 必须为 true，否则 daemon 返回 `INVALID_ARGUMENT`。不能通过命令行隐含确认。
- [ ] **Step 2: 检查固定安全衰减。** `loopback.physical.start` 请求只接受 `attenuation_mdB == 40000`；daemon 在硬件调用前和关键回读后都确认 TX attenuation，任何其他值（包括缺省 0）直接拒绝。此门不能由客户端参数覆盖。
- [ ] **Step 3: 运行低风险物理判据。** 使用已知单音/IQ tone，记录 TX/RX LO、sample rate、频率跟随、RX 是否出现对应能量、停止后 RX/TX 状态；不得使用数字逐位比较、未经校准的绝对功率或未实现的 EVM/BER。
- [ ] **Step 4: 分离负控。** 在不改变 threshold 的前提下关闭线缆/关闭 physical loopback，确认判据能失败；确认数字 loopback 关闭后不能被物理结果替代。任何“负控恒过”都阻止报告 PASS。
- [ ] **Step 5: 执行安全 stop。** 结束时先 `loopback.stop`，回读 TX inactive、attenuation=40000 mdB 或更大、RX capture stopped；断开连接时只允许 daemon 的安全停发路径。
- [ ] **Step 6: 写入板级报告。** 物理结果单列为 `PHYSICAL RF LOOPBACK: PASS/FAIL/NOT RUN`，附线缆确认、40000 mdB 回读、原始采集、判据输入/输出和 daemon/overlay hash；任何未满足条件写 `NOT RUN`，不能以 host-test 或 digital loopback 代替。

**完成门：** 物理环回只有在显式 40000 mdB、线缆确认、前置分层门和真实 RX 证据全部满足时才可写 PASS。

---

## Task 12: 全量回归、自检和交付前审查

**目标：** 在不提交/不 push 的前提下，确认 RF 新增内容没有污染 ACM2108 主线，并把所有证据分层归档。

**Files:**
- Test all files in this plan
- Modify only: `docs/rf/boardtest.md` for final dated results

- [ ] **Step 1: 运行 C 离线全套。**

```bash
make -C rf_overlay/sw/board_app clean
make -C rf_overlay/sw/board_app host-test
make -C rf_overlay/sw/board_app host-test-iq
make -C rf_overlay/sw/board_app host-test-core
make -C rf_overlay/sw/board_app host-test-protocol
make -C rf_overlay/sw/board_app host-test-daemon
make -C rf_overlay/sw/board_app vendor-integrity
```

- [ ] **Step 2: 运行 Python RF 全套。**

```bash
python -m pytest rf_host/tests rf_overlay/sw/tests/test_rf_release.py -q
```

- [ ] **Step 3: 运行 RTL TB 和 RF overlay release verification。**

```bash
# 使用 Vivado 2025.2 的 xvlog/xelab/xsim wrapper 执行三组 rf_overlay/rtl/tb
python scripts/rf_verify_release.py --manifest rf_overlay/out/rf_overlay.manifest.json
```

若 bitstream 构建被中断、缺少 timing/hash/manifest 任一字段，交付状态为 BLOCKED，不拿磁盘上残留文件补证。

- [ ] **Step 4: 运行 ACM2108 非侵入回归。**

```bash
host/.venv/Scripts/python -m pytest host/tests/ -q
```

这只能证明 ACM2108 上位机回归未受影响，不能合并为 AD9361 PASS。若测试环境缺少依赖，报告准确的 `NOT RUN` 和原因。

- [ ] **Step 5: 做文件边界审查。**

```bash
git diff --name-only -- rf_overlay/sw/board_app/vendor host/main.py host/siggen
```

Expected: vendor、`host/main.py`、`host/siggen/` 无本计划引入的修改；若发现改动，先停止交付并恢复到安全的可审查状态，不覆盖用户已有工作。

- [ ] **Step 6: 做协议/类型/文档审查。** 对照规格逐项检查：hello/version、所有 op、错误码、frame 长度、signed extension、lease、pending、disconnect safety、physical 40000 mdB、unsupported metrics、NOT RUN 纪律、bit/hwh/daemon manifest。任何没有测试或真实证据的条目保持未完成。

- [ ] **Step 7: 交付前只报告事实。** 报告格式分为：C host-test、Python fake daemon、RTL simulation、overlay build/timing、daemon deployment/device.info、TX-only、RX-only、digital loopback、physical RF loopback、ACM2108 regression。每项给命令、日期、版本/hash、结果；未实测项写 `NOT RUN`。不创建 commit，不 push，除非用户另行明确要求。

---

## 依赖与执行顺序

```text
Task 1 (contracts/IQ/version)
    ├── Task 2 (shared RF core + CLI)
    │     └── Task 4 (protocol + daemon)
    │           ├── Task 5 (launcher/deploy)
    │           └── Task 6 (RfClient/model)
    │                 └── Task 7 (GUI)
    └── Task 3 (RTL TB + BD evidence)
          └── Task 8 (overlay build/release)
Task 5 + Task 7 + Task 8 → Task 9 (board daemon deployment)
Task 9 → Task 10 (TX/RX/digital loopback)
Task 10 → Task 11 (physical loopback)
All tasks → Task 12 (full regression/report)
```

可并行但不得跨未定义接口实施的工作只有：Task 3 的 RTL TB 与 Task 6 的 Python fake-client 测试；Task 2 必须先确定 `rf_core` 接口，Task 4/5 才能使用它。任何板级任务都不能与 overlay 所有权检查并行。

## 计划自检结果

- **Spec coverage:** 规格第 1–2 节由 Tasks 5–7 和 Global Constraints 覆盖；第 3–4 节由 Task 4/6 覆盖；第 5 节由 Task 4/5/10/11 覆盖；第 6 节由 Task 2/3/10/11 覆盖；第 7 节由 Tasks 1/3/5/6/8/10–12 覆盖；第 8 节由文件边界和所有 Global Constraints 覆盖。
- **Placeholder scan:** 计划没有未完成标记或“稍后补测试”步骤；所有未测状态均要求输出 `NOT RUN`，不是占位实现。
- **Type/interface consistency:** C 的 `rf_iq_*`、`rf_core_*`、`rf_state_*` 先在 Task 1/2 定义，Task 4 使用它们；Python `RfClient`/`RfModel` 在 Task 6 定义，Task 7 只消费它们；manifest 字段由 Task 5 定义并由 Task 8/9 验证；TX/RX register names 与当前 `platform_rf.h` 保持一致。

## 执行方式

计划完成并保存后有两种执行方式：

1. **Subagent-Driven（推荐）**：按任务逐个派发 fresh subagent，每个任务完成后检查 diff 和测试，再进入下一任务。
2. **Inline Execution**：在本会话中按依赖顺序执行，每个任务完成后运行对应测试并设置检查点。

当前只完成计划编写，不执行上述实现、构建、下载、部署、板级 loopback、commit 或 push。
