# 双通道相位联动(相位差可调)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现双通道相位联动:CH2 频率跟随 CH1、输出相位 = CH1 相位 + 用户设定相位差(0~360.00°),GUI 可调并预览叠加双通道。

**Architecture:** BD 加第 6 个 AXI GPIO(dual 32-bit → gpio10=相位差 32 位全周期、gpio11=联动使能 1 位);dds_core/harm_synth 加外部相位注入(主从模式);awg_top 在联动时强制 CH2 使用 CH1 的 fword 与相位累加器并抑制 CH2 调制;PS 桥新增 `SET CH2 PHASE/PHASELINK` 命令;GUI 加联动区并让预览叠加 CH2 曲线。

**Tech Stack:** Vivado 2018.3 (Verilog + AXI GPIO BD)、PS 裸机 C(sdk/src/main.c)、PySide6 上位机、DSOX1102A 示波器 USB-TMC。

**与设计文档的打包差异(已确认)**:设计文档草案 `gpio10 = {link_en[31], phase_off[30:0]}` 的 31 位相位字只能表示半周期(0~180°),改为 **`gpio10 = phase_off[31:0]`(32 位全周期相位差)、`gpio11 = {31'd0, link_en}`**。实施完成后同步修正设计文档。

---

## 文件结构

| 文件 | 责任 | 改动 |
|---|---|---|
| `scripts/create_bd.tcl` | BD 生成 | axi_gpio_5(dual 32-bit)+ 连接/MI/ACLK/ARESETN |
| `src/rtl/awg_top.v` | 顶层 | 加 gpio10/11 端口、联动 mux |
| `src/rtl/reg_bridge.v` | 寄存器拆包 | gpio10/11 2-FF 同步 + 拆 link_en/phase_off |
| `src/rtl/dds_core.v` | 波形 DDS | 外部相位注入 + phase_acc_out 输出 |
| `src/rtl/harm_synth.v` | 谐波合成 | 外部相位注入(acc0 主从) |
| `src/tb/awg_top_tb.v` | 顶层 TB | 端口同步 + 联动用例 |
| `src/tb/dds_core_tb.v` / `harm_synth_tb.v` / `reg_bridge_tb.v` | 模块 TB | 端口同步 + 新用例 |
| `sdk/src/main.c` | PS 桥 | PHASE/PHASELINK 命令、gpio10/11 打包 |
| `scripts/test_bridge.py` | 协议回归 | GET 断言更新 + 新用例 |
| `scripts/scope_verify.py` | 示波器闭环 | 相位差用例(过零时间差) |
| `host/siggen/protocol.py` | 上位机协议 | phase_cmd/phaselink_cmd/parse_status 扩展 |
| `host/siggen/controller.py` | 状态模型 | phase_0_01/phaselink 状态 + 命令流 |
| `host/siggen/ui/widgets.py` | 通道面板 | 联动区(复选框+滑块+置灰) |
| `host/siggen/ui/main_window.py` | 主窗口 | 接线 + set_state 传递 |
| `host/siggen/preview.py` | 预览 | make_plot_data phase_off + 双通道曲线 |
| `host/tests/*` | 上位机测试 | 联动/相位偏移/预览叠加用例 |
| `README.md` / `CLAUDE.md` / 设计文档 | 文档 | 协议、行为、经验 |

---

## Task 1: BD 加 axi_gpio_5 + reg_bridge 拆新字 + awg_top 端口扩展

**Files:**
- Modify: `scripts/create_bd.tcl`
- Modify: `src/rtl/awg_top.v`
- Modify: `src/rtl/reg_bridge.v`
- Modify: `src/tb/reg_bridge_tb.v`
- Modify: `src/tb/awg_top_tb.v`

- [ ] **Step 1: create_bd.tcl — 加 axi_gpio_5**

四处 foreach 循环从 5 个扩到 6 个,并在输出连接处加 gpio10/gpio11:

```tcl
# ---- 6x AXI GPIO (dual 32-bit all-output) ----
foreach ip_name {axi_gpio_0 axi_gpio_1 axi_gpio_2 axi_gpio_3 axi_gpio_4 axi_gpio_5} {
```

在 `connect_bd_net` 块末尾(gpio9 之后)追加:

```tcl
connect_bd_net [get_bd_pins axi_gpio_5/gpio_io_o]   [get_bd_pins awg_top_0/gpio10_in]
connect_bd_net [get_bd_pins axi_gpio_5/gpio2_io_o]  [get_bd_pins awg_top_0/gpio11_in]
```

AXI interconnect 从 5 改 6 MI:

```tcl
set_property -dict [list CONFIG.NUM_MI {6} CONFIG.NUM_SI {1} CONFIG.S00_HAS_REGSLICE {4}] $axiint
```

MI 映射与 ACLK/ARESETN 循环追加 `M05_AXI axi_gpio_5/S_AXI`、`M05_ACLK`、`M05_ARESETN`。

- [ ] **Step 2: awg_top.v — 加端口**

```verilog
    input  wire [31:0] gpio10_in, // AXI GPIO ch10 (PS FCLK0 domain) -> CH2 相位差(32 位全周期)
    input  wire [31:0] gpio11_in, // AXI GPIO ch11 (PS FCLK0 domain) -> 相位联动使能(bit0)
```

`reg_bridge u_reg` 实例化追加 `.gpio10_in(gpio10_in), .gpio11_in(gpio11_in)` 并声明 `wire ch_link_en; wire [31:0] ch_phase_off;`。

- [ ] **Step 3: reg_bridge.v — 拆新字**

端口声明追加 `input wire [31:0] gpio10_in, gpio11_in`、`output wire ch_link_en, output wire [31:0] ch_phase_off`。

在调制字同步块之后追加(同现有 2-FF 模式):

```verilog
    // ---- 通道关系字(字E: 相位差 32 位全周期; 字F: bit0 = 联动使能, 无哨兵: 全 0 = 关闭)----
    reg [31:0] s10, s11, r10, r11;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s10 <= 32'd0; s11 <= 32'd0;
            r10 <= 32'd0; r11 <= 32'd0;
        end else begin
            s10 <= gpio10_in; s11 <= gpio11_in;
            r10 <= s10;       r11 <= s11;
        end

    assign ch_phase_off = r10;
    assign ch_link_en   = r11[0];
```

- [ ] **Step 4: awg_top_tb.v / reg_bridge_tb.v — 端口同步**

两个 TB 的 `reg [31:0]` 声明加 `gpio10_in = 0, gpio11_in = 0;`,实例化端口追加 `.gpio10_in(gpio10_in), .gpio11_in(gpio11_in)`。

- [ ] **Step 5: reg_bridge_tb.v — 加拆包用例**

在 initial 块末尾(现有用例之后)追加:

```verilog
        // ---- 通道关系字: gpio10 = 相位差, gpio11 bit0 = 联动使能 ----
        gpio10_in = 32'h4000_0000;   // 90° (2^32/4)
        gpio11_in = 32'd1;
        #100;
        chk("phase_off unpack 0x40000000", uut.ch_phase_off == 32'h4000_0000);
        chk("link_en unpack bit0", uut.ch_link_en == 1'b1);
        gpio11_in = 32'd0;
        #100;
        chk("link_en clear -> 0", uut.ch_link_en == 1'b0);
```

- [ ] **Step 6: 仿真运行(端口接线错误会在此暴露)**

```bash
cd /d/Vivado_prj/signal_generator && xvlog -f scripts/filelist.f 2>/dev/null || \
  xvlog src/rtl/*.v src/tb/reg_bridge_tb.v src/tb/awg_top_tb.v && \
  xelab reg_bridge_tb -s reg_bridge_tb_sim -timescale 1ns/1ps && \
  xsim reg_bridge_tb_sim -R -runall 2>&1 | grep -E "PASS|FAIL"
```

预期:reg_bridge_tb 全部 PASS(含新用例);awg_top_tb 原有用例全 PASS(端口同步后)。若 xvlog 报端口不匹配,检查 TB 实例化与模块端口一一对应。

- [ ] **Step 7: 提交**

```bash
git add scripts/create_bd.tcl src/rtl/awg_top.v src/rtl/reg_bridge.v \
        src/tb/reg_bridge_tb.v src/tb/awg_top_tb.v
git commit -m "feat: BD 加 axi_gpio_5 + reg_bridge 拆相位差/联动字(相位联动硬件基础)"
```

---

## Task 2: dds_core/harm_synth 外部相位注入 + awg_top 联动 mux

**Files:**
- Modify: `src/rtl/dds_core.v`
- Modify: `src/rtl/harm_synth.v`
- Modify: `src/rtl/awg_top.v`
- Modify: `src/tb/dds_core_tb.v` / `src/tb/harm_synth_tb.v`(端口同步)
- Modify: `src/tb/awg_top_tb.v`(联动用例)

- [ ] **Step 1: dds_core.v — 外部相位注入**

端口改为:

```verilog
module dds_core (
    input  wire clk,          // 50 MHz
    input  wire rst_n,
    input  wire [31:0] fword,
    input  wire [1:0]  wave_sel,   // 00 sin / 01 sqr / 10 tri / 11 saw
    input  wire signed [8:0] pm_off,   // PM 相位偏移(地址 LSB, 0=直通)
    input  wire phase_ext_valid,       // 外部相位注入(相位联动主从): 不累加, 直接采样 phase_ext
    input  wire [31:0] phase_ext,      // 主通道相位 + 偏移(联动时由 awg_top 组合提供)
    output wire [31:0] phase_acc_out,  // 当前相位累加器(主相位源, 供 CH2 联动跟随)
    output reg  [7:0] sample
);
```

累加器改为:

```verilog
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            phase_acc  <= 32'd0;
            wave_sel_r <= 2'b00;
            pm_off_r   <= 9'sd0;
        end else begin
            if (phase_ext_valid)
                phase_acc <= phase_ext;
            else
                phase_acc  <= phase_acc + fword;
            wave_sel_r <= wave_sel;
            pm_off_r   <= pm_off;
        end

    assign phase_acc_out = phase_acc;
```

- [ ] **Step 2: harm_synth.v — 外部相位注入**

端口追加(在 pm_off 之后):

```verilog
    input  wire phase_ext_valid,   // 外部相位注入(相位联动主从)
    input  wire [31:0] phase_ext,  // 主通道相位 + 偏移(谐波与基波一起移相)
```

累加器改为:

```verilog
    reg [31:0] acc0;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) acc0 <= 32'd0;
        else if (phase_ext_valid) acc0 <= phase_ext;
        else        acc0 <= acc0 + fword;
```

- [ ] **Step 3: awg_top.v — 联动 mux**

在 mod_synth 实例化之后、dds_core/harm_synth 实例化之前插入:

```verilog
    // ---- 相位联动: link_en 时 CH2 频率跟随 CH1、相位 = CH1 相位 + 偏移、调制抑制 ----
    wire link_en = ch_link_en;
    wire [31:0] fword_inst2_used = link_en ? fword_inst1 : fword_inst2;
    wire signed [8:0] pm_off2_used  = link_en ? 9'sd0 : pm_off2;
    wire signed [16:0] am_gain2_used = link_en ? 17'sd12700 : am_gain2;
    // CH2 外部相位 = CH1 累加器 + 静态偏移(组合加法, 下游打拍采样; 全周期 mod 2^32 回绕)
    wire [31:0] phase_ext2 = phase_acc1 + ch_phase_off;
```

`u_dds1` 实例化加 `.phase_acc_out(phase_acc1)`;`u_dds2`/`u_harm2` 接线改为:

```verilog
    dds_core u_dds2 (
        .clk(sys_clk), .rst_n(rst_n), .fword(fword_inst2_used), .wave_sel(ch2_wave),
        .pm_off(pm_off2_used), .phase_ext_valid(link_en), .phase_ext(phase_ext2),
        .sample(s2_raw));
    harm_synth u_harm2 (
        .clk(sys_clk), .rst_n(rst_n), .fword(fword_inst2_used), .pm_off(pm_off2_used),
        .phase_ext_valid(link_en), .phase_ext(phase_ext2),
        .base(ch2_base), .h1_ord(ch2_h1_ord), .h2_ord(ch2_h2_ord),
        .h3_ord(ch2_h3_ord), .h4_ord(ch2_h4_ord),
        .h1_amp(ch2_h1_amp), .h2_amp(ch2_h2_amp),
        .h3_amp(ch2_h3_amp), .h4_amp(ch2_h4_amp),
        .sample(harm2));
```

`am_stage u_am2` 的 `.am_gain(am_gain2_used)`。声明 `wire [31:0] phase_acc1;` 与 `wire [31:0] phase_ext2;`。

- [ ] **Step 4: dds_core_tb.v / harm_synth_tb.v — 端口同步**

两个 TB 的输入声明加 `reg phase_ext_valid = 0; reg [31:0] phase_ext = 0;`,实例化追加 `.phase_ext_valid(phase_ext_valid), .phase_ext(phase_ext)`。dds_core_tb 的 uut 实例化加 `.phase_acc_out()`(悬空即可)。

- [ ] **Step 5: awg_top_tb.v — 联动用例**

端口同步(Step 4 同款)后,在 initial 块末尾(现有用例之后)追加:

```verilog
        // ---- 相位联动: CH1/CH2 同频 10kHz, link 开启, 相位差 90° (2^32/4) ----
        gpio0_in = pack(20'd10000, 8'd100, 2'd0);   // CH1: 10kHz SINE 100%
        gpio1_in = pack(20'd10000, 8'd100, 2'd0);   // CH2: 同频(联动后强制跟随)
        gpio6_in = 32'd0;  gpio7_in = 32'd0;  gpio8_in = 32'd0;  gpio9_in = 32'd0;
        gpio2_in = 32'd0;  gpio3_in = 32'd0;  gpio4_in = 32'd0;  gpio5_in = 32'd0;
        gpio10_in = 32'h4000_0000;   // 相位差 90°
        gpio11_in = 32'd1;           // link 开启
        #1000;
        begin : tlink
            integer i, ok, bad, bad2;
            ok = 0; bad = 0; bad2 = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                // CH2 相位恒 = CH1 相位 + 偏移(全周期 mod 2^32)
                if (uut.u_dds2.phase_acc === (uut.phase_acc1 + 32'h4000_0000)) ok = ok + 1;
                else bad = bad + 1;
                // 谐波累加器同步跟随
                if (uut.u_harm2.acc0 === (uut.phase_acc1 + 32'h4000_0000)) bad2 = bad2;
                else bad2 = bad2 + 1;
            end
            chk("link ch2 phase = ch1 + 90deg", ok > 4900 && bad == 0);
        end
        // 相位差实时改 180°
        gpio10_in = 32'h8000_0000;
        #100;
        begin : tlink2
            integer i, ok;
            ok = 0;
            for (i = 0; i < 2000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.u_dds2.phase_acc === (uut.phase_acc1 + 32'h8000_0000)) ok = ok + 1;
            end
            chk("link phase_off live update 180deg", ok > 1900);
        end
        // 关联动: CH2 恢复独立(频率不同, 相位自由累加)
        gpio11_in = 32'd0;
        gpio1_in = pack(20'd20000, 8'd100, 2'd0);   // CH2: 20kHz(不再跟随)
        #1000;
        begin : tunlink
            integer i, ok;
            ok = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.fword_inst2_used !== uut.fword_inst1) ok = ok + 1;
            end
            chk("unlink ch2 fword independent of ch1", ok > 4900);
        end
```

- [ ] **Step 6: 仿真运行**

```bash
xvlog src/rtl/*.v src/tb/dds_core_tb.v src/tb/harm_synth_tb.v src/tb/awg_top_tb.v && \
xelab awg_top_tb -s awg_top_tb_sim -timescale 1ns/1ps && \
xsim awg_top_tb_sim -R -runall 2>&1 | grep -E "PASS|FAIL"
```

预期:原有用例全 PASS + 新联动用例 PASS(link ch2 phase = ch1 + 90deg、link phase_off live update 180deg)。另跑 dds_core_tb、harm_synth_tb、reg_bridge_tb 确认无回归。

- [ ] **Step 7: 提交**

```bash
git add src/rtl/dds_core.v src/rtl/harm_synth.v src/rtl/awg_top.v \
        src/tb/dds_core_tb.v src/tb/harm_synth_tb.v src/tb/awg_top_tb.v
git commit -m "feat: 双通道相位联动 RTL(外部相位注入 + 主从 mux, 含 TB 用例)"
```

---

## Task 3: PS 桥协议(SET CH2 PHASE / PHASELINK)

**Files:**
- Modify: `sdk/src/main.c`

- [ ] **Step 1: 结构体与宏**

`ch_state_t` 追加两个字段:

```c
    u32 phase_0_01;  /* 相位差 0.01° 单位, 0..36000, 仅联动时生效 */
    u32 phaselink;   /* 0=off 1=on: CH2 跟随 CH1 */
```

宏与设备:

```c
#define GPIO6_DEVICE_ID XPAR_AXI_GPIO_5_DEVICE_ID
static XGpio Gpio6;
```

- [ ] **Step 2: 打包函数与 write_all**

```c
/* 相位差 0.01° -> 32 位相位字(全周期): x × 2^32 / 36000 */
static u32 angle_to_phase(u32 angle_0_01)
{
    return (u32)(((u64)angle_0_01 << 32) / 36000u);
}

static u32 pack_word_e(const ch_state_t *s)
{
    return angle_to_phase(s->phase_0_01);   /* gpio10: 32 位全周期相位差 */
}

static u32 pack_word_f(const ch_state_t *s)
{
    return s->phaselink;                     /* gpio11: bit0 = 联动使能 */
}
```

`write_all()` 末尾追加(注意是 CH2 的字段,`ch[1]`):

```c
    XGpio_DiscreteWrite(&Gpio6, 1, pack_word_e(&ch[1]));    /* gpio10: CH2 相位差 */
    XGpio_DiscreteWrite(&Gpio6, 2, pack_word_f(&ch[1]));    /* gpio11: 联动使能 */
```

- [ ] **Step 3: main() 初始化 Gpio6**

```c
    XGpio_Initialize(&Gpio6, GPIO6_DEVICE_ID);
    XGpio_SetDataDirection(&Gpio6, 1, 0x0);
    XGpio_SetDataDirection(&Gpio6, 2, 0x0);
```

`ch[0]` 初始化加 `phase_0_01 = 0; phaselink = 0;`。

- [ ] **Step 4: do_line 新命令**

在 `SET` 分支、`PMDEV` 处理之后插入:

```c
        } else if (strcmp(tok[2], "PHASE") == 0) {
            u32 v;
            if (ci != 1) { uart_send("ERR BAD CHANNEL\r\n"); return; }   /* 仅 CH2(相对 CH1) */
            if (!parse_u32(tok[3], &v) || v > 36000) {
                uart_send("ERR PHASE OUT OF RANGE\r\n"); return;
            }
            ch[1].phase_0_01 = v;
        } else if (strcmp(tok[2], "PHASELINK") == 0) {
            if (ci != 1) { uart_send("ERR BAD CHANNEL\r\n"); return; }
            if (strcmp(tok[3], "ON") == 0)      ch[1].phaselink = 1;
            else if (strcmp(tok[3], "OFF") == 0) ch[1].phaselink = 0;
            else { uart_send("ERR BAD VALUE\r\n"); return; }
```

**注意**:PHASE/PHASELINK 分支在 `if (n < 4)` 检查之后(现有 SET 分支结构),`tok[3]` 一定存在;`write_all()` 在分支结束后统一调用(现有流程)。

- [ ] **Step 5: GET 回读追加字段**

GET 的 sprintf 追加(放在 PMDEV 之后):

```c
                     "PHASE %lu PHASELINK %s\r\n",
                ...,
                (unsigned long)ch[ci].phase_0_01,
                ch[ci].phaselink ? "ON" : "OFF");
```

(注意 sprintf 的格式串与参数顺序对应:PMDEV 之后追加 `PHASE %lu PHASELINK %s`。)

- [ ] **Step 6: 提交**

```bash
git add sdk/src/main.c
git commit -m "feat: 桥协议新增 SET CH2 PHASE/PHASELINK(相位联动命令与回读)"
```

---

## Task 4: 构建 + 下载 + 板级协议回归

**Files:**
- Modify: `scripts/test_bridge.py`
- Run: `build_bitstream.py` / `program_board.sh` / `cli-anything-vivado sdk`

- [ ] **Step 1: 重建 bitstream 并下载**

```bash
python scripts/build_bitstream.py      # create_bd.tcl 变化 -> 完整重建(BD+OOC+综合+实现)
bash scripts/program_board.sh          # 校验 + JTAG 下载
cli-anything-vivado sdk run            # 重建 SDK 驱动(新 XPAR_AXI_GPIO_5)+ 编译 + 跑 OCM
```

注意:SDK 重建会重新生成 xparameters.h,`XPAR_AXI_GPIO_5_DEVICE_ID` 必须存在,否则编译报错——若报未定义,检查 `sdk/ws` 的 BSP 是否重新生成(cli-anything-vivado sdk app new/build 全流程)。

- [ ] **Step 2: 先跑旧 test_bridge 确认基线**

```bash
python scripts/test_bridge.py COM11
```

预期:除 GET 断言(格式已加 PHASE/PHASELINK 字段)外全过。**这一步会看到 GET 用例 FAIL——这是预期,下一步更新断言。**

- [ ] **Step 3: test_bridge.py — 更新 GET 断言**

现有 7 处 `cmd("GET CH1" / "GET CH2", "CH1 WAVE ... PMDEV 0")` 断言,在 `PMDEV 0` 后追加 ` PHASE 0 PHASELINK OFF`。示例(全部 7 处同款):

```python
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
```

- [ ] **Step 4: test_bridge.py — 新增相位联动用例**

在调制越界用例之后、RESULT 打印之前插入:

```python
# ---- 相位联动(PHASE 仅 CH2 有效) ----
cmd("SET CH2 PHASE 9000", "OK")            # 90.00°
cmd("SET CH2 PHASELINK ON", "OK")
cmd("GET CH2", "CH2 WAVE SAW FREQ 2500 AMPL 20 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 9000 PHASELINK ON")
cmd("SET CH2 PHASE 0", "OK")
cmd("SET CH2 PHASE 36000", "OK")
cmd("SET CH2 PHASE 36001", "ERR PHASE OUT OF RANGE")
cmd("SET CH2 PHASE abc", "ERR PHASE OUT OF RANGE")   # parse_u32 失败 -> 范围错误
cmd("SET CH2 PHASELINK MAYBE", "ERR BAD VALUE")
cmd("SET CH1 PHASE 9000", "ERR BAD CHANNEL")         # PHASE 仅 CH2
cmd("SET CH1 PHASELINK ON", "ERR BAD CHANNEL")
# 错误后状态不变
cmd("GET CH2", "CH2 WAVE SAW FREQ 2500 AMPL 20 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 36000 PHASELINK ON")
cmd("SET CH2 PHASE 0", "OK")               # 复位
cmd("SET CH2 PHASELINK OFF", "OK")
```

注意:PHASE abc 的期望错误——parse_u32 返回 0 时现有代码 `ERR PHASE OUT OF RANGE`(与 MODFREQ abc 的 `ERR MODFREQ OUT OF RANGE` 风格一致)。

- [ ] **Step 5: 运行回归**

```bash
python scripts/test_bridge.py COM11
```

预期:全部 PASS(原有 76 项 + 新增 ~11 项)。

- [ ] **Step 6: 提交**

```bash
git add scripts/test_bridge.py
git commit -m "test: test_bridge 增加 PHASE/PHASELINK 用例并更新 GET 断言"
```

---

## Task 5: 示波器闭环相位差用例

**Files:**
- Modify: `scripts/scope_verify.py`

- [ ] **Step 1: 加相位差测量辅助函数**

在 `wav_fft()` 之后追加:

```python
def scope_wave(ch):
    """读取指定通道 NORM 波形(:STOP 冻结帧内, 与另一通道同一时刻采集)。"""
    inst.write(":WAV:SOUR " + ch)
    xinc = float(inst.query(":WAV:XINC?"))
    data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
    return xinc, np.array(data, dtype=np.uint8).astype(float)


def phase_from_zero_cross(xinc, y1, y2):
    """两通道同窗波形 -> 相位差(度, CH2 相对 CH1 超前为正, 0..360)。
    上升过零时间差 × 频率 × 360; 取所有过零对的中值(抗毛刺)。"""
    def zc(y):
        return np.where((y[:-1] < 128) & (y[1:] >= 128))[0]
    z1, z2 = zc(y1), zc(y2)
    if len(z1) < 2 or len(z2) < 2:
        return None
    # 对齐最近过零对(取中值对, 两通道过零数相同)
    n = min(len(z1), len(z2))
    d = (z2[:n] - z1[:n]) * xinc
    per = np.median(np.diff(z1)) * xinc      # CH1 周期
    if per <= 0:
        return None
    return (np.median(d % per)) / per * 360.0
```

- [ ] **Step 2: 加相位联动用例主块**

在 SWEEP 用例之后、`inst.close()` 之前追加:

```python
    # ---- 相位联动: CH1/CH2 同频 1kHz, 相位差 90°/180°, 频率跟随与解除 ----
    # 探头: CH1 接 DAC0 输出、CH2 接 DAC1 输出。:STOP 冻结后分读两通道波形
    # (同一冻结帧, 时间轴对齐), 上升过零时间差换算相位差。
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 100", "SET CH1 BASE 0",
                 "SET CH1 MODE OFF", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 10",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 0", "SET CH1 PMDEV 0",
                 "SET CH2 WAVE SINE", "SET CH2 FREQ 1000", "SET CH2 AMPL 100", "SET CH2 BASE 0",
                 "SET CH2 MODE OFF", "SET CH2 MODWAVE SINE", "SET CH2 MODFREQ 10",
                 "SET CH2 AMDEPTH 100", "SET CH2 FMDEV 0", "SET CH2 PMDEV 0",
                 "SET CH2 PHASE 0", "SET CH2 PHASELINK ON"])
    scope_acq(200e-6, 2.0)          # 1kHz: 200us/div = 2ms 窗 = 2 周期
    inst.write(":STOP")
    xinc, y1 = scope_wave("CHAN1")
    _, y2 = scope_wave("CHAN2")
    inst.write(":RUN")
    ph = phase_from_zero_cross(xinc, y1, y2)
    report("PHASELINK 0deg", ph is not None and abs(ph) < 5.0,
           "phase=%.1f deg (exp 0)" % (ph if ph is not None else -1))

    setup_board(["SET CH2 PHASE 9000"])
    scope_acq(200e-6, 2.0)
    inst.write(":STOP")
    xinc, y1 = scope_wave("CHAN1")
    _, y2 = scope_wave("CHAN2")
    inst.write(":RUN")
    ph = phase_from_zero_cross(xinc, y1, y2)
    report("PHASELINK 90deg", ph is not None and abs(ph - 90.0) < 5.0,
           "phase=%.1f deg (exp 90)" % (ph if ph is not None else -1))

    setup_board(["SET CH2 PHASE 18000"])
    scope_acq(200e-6, 2.0)
    inst.write(":STOP")
    xinc, y1 = scope_wave("CHAN1")
    _, y2 = scope_wave("CHAN2")
    inst.write(":RUN")
    ph = phase_from_zero_cross(xinc, y1, y2)
    report("PHASELINK 180deg", ph is not None and abs(ph - 180.0) < 5.0,
           "phase=%.1f deg (exp 180)" % (ph if ph is not None else -1))

    # 频率跟随: 改 CH1 频率 -> CH2 同步(相位差 90° 保持)
    setup_board(["SET CH1 FREQ 2000"])
    scope_acq(100e-6, 2.0)          # 2kHz: 100us/div = 1ms 窗 = 2 周期
    inst.write(":STOP")
    xinc, y1 = scope_wave("CHAN1")
    _, y2 = scope_wave("CHAN2")
    inst.write(":RUN")
    ph = phase_from_zero_cross(xinc, y1, y2)
    # 周期 = 0.5ms; 90° = 125us
    report("PHASELINK freq follow 2kHz", ph is not None and abs(ph - 90.0) < 5.0,
           "phase=%.1f deg (exp 90 @ 2kHz)" % (ph if ph is not None else -1))

    # 解除联动: CH2 独立(改 CH2 频率不跟随, 相位差无意义但不应报错)
    setup_board(["SET CH2 PHASELINK OFF", "SET CH2 FREQ 3000"])
    scope_acq(100e-6, 2.0)
    f, v = measure()
    report("PHASELINK off ch2 independent",
           f is not None and abs(f - 3000) <= 150,
           "ch2 freq=%.1fHz (exp 3000)" % (f or -1))
```

- [ ] **Step 3: 运行验证**

```bash
python scripts/scope_verify.py COM11
```

预期:原 14 项 + 新 5 项(PHASELINK 0/90/180deg、freq follow、off independent)全 PASS。若 90° 偏差超 5°,检查过零对对齐逻辑(两通道过零数可能差 1,`z2[:n]` 截断取中值)。

- [ ] **Step 4: 提交**

```bash
git add scripts/scope_verify.py
git commit -m "test: scope_verify 相位联动用例(0/90/180deg 过零换算 + 频率跟随/解除)"
```

---

## Task 6: 上位机协议层 + 控制器

**Files:**
- Modify: `host/siggen/protocol.py`
- Modify: `host/siggen/controller.py`
- Modify: `host/tests/test_protocol.py` / `host/tests/test_controller.py`

- [ ] **Step 1: protocol.py — 命令构造**

常量区追加:

```python
PHASE_MAX = 36000      # 0.01° 单位
```

文件末尾追加:

```python
def phase_cmd(ch: int, value) -> str:
    """相位差 0.01° 单位 0..36000(仅 CH2, 相对 CH1)。"""
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    v = int(value)
    if not 0 <= v <= PHASE_MAX:
        raise ValueError(f"phase out of range: {value}")
    return f"SET CH{ch} PHASE {v}"


def phaselink_cmd(ch: int, on: bool) -> str:
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    return f"SET CH{ch} PHASELINK {'ON' if on else 'OFF'}"
```

`parse_status` 的 while 循环追加两个字段解析:

```python
        elif key == "PHASE":
            d["phase_0_01"] = int(parts[i + 1])
            i += 2
        elif key == "PHASELINK":
            d["phaselink"] = parts[i + 1] == "ON"
            i += 2
```

`parse_status` 初始化 dict 追加 `"phase_0_01": None, "phaselink": None`。

- [ ] **Step 2: controller.py — 状态与命令流**

`ChannelState` 追加:

```python
    phase_0_01: int = 0        # 相位差 0.01° 单位(仅 CH2)
    phaselink: bool = False    # CH2 跟随 CH1
```

`set_field` 在调制字段分支前加:

```python
        if field_l == "phase":
            try:
                cmd = phase_cmd(ch + 1, value)
            except ValueError as e:
                self.log.emit(str(e), "error")
                return
            self._send(ch, field_l, value, cmd)
            return
        if field_l == "phaselink":
            cmd = phaselink_cmd(ch + 1, bool(value))
            self._send(ch, field_l, bool(value), cmd)
            return
```

(import 追加 `phase_cmd, phaselink_cmd`。)

`_on_line` 的 GET 处理循环追加:

```python
            for k in ("mode", "mwave", "mfreq_01", "am_depth", "fm_dev", "pm_dev",
                      "phase_0_01", "phaselink"):
```

`_on_line` 的 ack 处理追加(在 pmdev 分支后):

```python
                elif field == "phase":
                    st.phase_0_01 = int(value)
                elif field == "phaselink":
                    st.phaselink = bool(value)
```

- [ ] **Step 3: 测试**

`host/tests/test_protocol.py` 追加:

```python
def test_phase_cmd():
    from siggen.protocol import phase_cmd, phaselink_cmd
    assert phase_cmd(2, 9000) == "SET CH2 PHASE 9000"
    assert phase_cmd(2, 0) == "SET CH2 PHASE 0"
    assert phaselink_cmd(2, True) == "SET CH2 PHASELINK ON"
    assert phaselink_cmd(2, False) == "SET CH2 PHASELINK OFF"
    with pytest.raises(ValueError):
        phase_cmd(2, 36001)
    # 协议层不限制通道(与 set_cmd 一致), 桥负责拒绝 CH1
    assert phase_cmd(1, 9000) == "SET CH1 PHASE 9000"
```

`host/tests/test_controller.py` 追加(fake worker 风格与现有测试一致,见文件头部 fixture):

```python
def test_set_phase(ctrl):
    ctrl.set_field(1, "phase", 9000)
    assert ctrl.worker.sent[-1] == "SET CH2 PHASE 9000\r\n"


def test_set_phaselink(ctrl):
    ctrl.set_field(1, "phaselink", True)
    assert ctrl.worker.sent[-1] == "SET CH2 PHASELINK ON\r\n"
```

(若现有 fixture 名为 `ctrl` 且 worker 有 `sent` 列表——先读 test_controller.py 确认结构,按现有命名适配。)

- [ ] **Step 4: 运行测试**

```bash
cd host && python -m pytest tests/ -q
```

预期:全过(原 49 + 新增 ~4)。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/protocol.py host/siggen/controller.py host/tests/test_protocol.py host/tests/test_controller.py
git commit -m "feat: 上位机协议/控制器支持 PHASE/PHASELINK"
```

---

## Task 7: GUI 联动区(ChannelPanel + main_window)

**Files:**
- Modify: `host/siggen/ui/widgets.py`
- Modify: `host/siggen/ui/main_window.py`
- Modify: `host/tests/test_widgets.py`

- [ ] **Step 1: widgets.py — 联动区控件**

`ChannelPanel.__init__` 签名改为 `def __init__(self, title: str, link_controls: bool = False, parent=None)`,在"Vpp 显示与校准"区之后追加:

```python
        # ---- 相位联动区(仅 CH2 显示)----
        self.link_box = QVBoxLayout()
        link_row = QHBoxLayout()
        self.link_check = QCheckBox("跟随 CH1(相位联动)")
        self.link_check.toggled.connect(self._on_link_toggled)
        link_row.addWidget(self.link_check)
        link_row.addStretch(1)
        self.link_box.addLayout(link_row)
        ph_row = QHBoxLayout()
        ph_row.addWidget(QLabel("相位差"))
        self.phase_slider = QSlider(Qt.Horizontal)
        self.phase_slider.setRange(0, 36000)     # 0.01° 单位
        self.phase_slider.valueChanged.connect(
            lambda v: self._emit_phase(v))
        self.phase_label = QLabel("0.00°")
        ph_row.addWidget(self.phase_slider, 1)
        ph_row.addWidget(self.phase_label)
        self.link_box.addLayout(ph_row)
        self.link_box.setEnabled(link_controls)
        if link_controls:
            lay.addLayout(self.link_box)
```

import 追加 `QCheckBox`。

- [ ] **Step 2: widgets.py — 信号发射与置灰**

追加方法:

```python
    def _emit_phase(self, v):
        if self._updating:
            return
        self.phase_label.setText(f"{v / 100.0:.2f}°")
        self.param_changed.emit("phase", v)

    def _on_link_toggled(self, on):
        if self._updating:
            return
        self.param_changed.emit("phaselink", on)
        self._update_link_enabled()

    def _update_link_enabled(self):
        """联动开启: CH2 频率与调制控件禁用(硬件强制跟随/抑制); 关闭恢复。"""
        on = self.link_check.isChecked()
        for b in self.freq_btns:
            b.setEnabled(not on)
        self.freq_edit.setEnabled(not on)
        self._update_mod_enabled()      # 内部判断 link 状态
```

将频率按钮循环改为保存引用:

```python
        self.freq_btns = []
        for text, op in (("-10", -10), ("+10", 10), ("÷10", "div"), ("×10", "mul")):
            b = QPushButton(text)
            b.clicked.connect(lambda checked=False, o=op: self._step(o))
            self.freq_btns.append(b)
            freq_lay.addWidget(b)
```

`_update_mod_enabled` 改为:

```python
    def _update_mod_enabled(self):
        """OFF 模式或相位联动开启时调制参数置灰(与硬件一致)。"""
        on = self.mod_mode.currentIndex() != 0 and not self.link_check.isChecked()
        ...
```

`set_state` 签名追加 `link_on: bool = False, phase_0_01: int = 0`,末尾(现有 `self._updating = False` 之后)追加:

```python
        self.link_check.blockSignals(True)
        self.link_check.setChecked(link_on)
        self.link_check.blockSignals(False)
        self.phase_slider.blockSignals(True)
        self.phase_slider.setValue(phase_0_01)
        self.phase_slider.blockSignals(False)
        self.phase_label.setText(f"{phase_0_01 / 100.0:.2f}°")
        self._update_link_enabled()
```

- [ ] **Step 3: main_window.py — 接线与传递**

CH2 面板构造改:

```python
        self.ch2 = ChannelPanel("CH2 — DAC1", link_controls=True)
```

`_on_state` 的 set_state 调用追加:

```python
        panel.set_state(st.wave, st.freq_hz, st.ampl_pct, st.base_pct, st.harms,
                        st.mode, st.mwave, st.mfreq_01, st.am_depth,
                        st.fm_dev, st.pm_dev,
                        link_on=getattr(st, "phaselink", False),
                        phase_0_01=getattr(st, "phase_0_01", 0))
```

- [ ] **Step 4: 测试**

`host/tests/test_widgets.py` 追加:

```python
def test_link_disables_freq_and_mod(panel_link):
    got = []
    panel_link.param_changed.connect(lambda f, v: got.append((f, v)))
    assert panel_link.freq_btns[0].isEnabled()
    panel_link.link_check.setChecked(True)
    assert not panel_link.freq_btns[0].isEnabled()
    assert not panel_link.freq_edit.isEnabled()
    assert not panel_link.mod_mode.isEnabled()
    assert ("phaselink", True) in got
    panel_link.link_check.setChecked(False)
    assert panel_link.freq_btns[0].isEnabled()
    assert panel_link.mod_mode.isEnabled()


def test_phase_slider_emits(panel_link):
    got = []
    panel_link.param_changed.connect(lambda f, v: got.append((f, v)))
    panel_link.phase_slider.setValue(9000)
    assert ("phase", 9000) in got
    assert panel_link.phase_label.text() == "90.00°"


def test_set_state_link_no_echo(panel_link):
    got = []
    panel_link.param_changed.connect(lambda f, v: got.append((f, v)))
    panel_link.set_state("SINE", 1000, 50, 100, [(0, 0)] * 4,
                         "OFF", "SINE", 10, 100, 0, 0, link_on=True, phase_0_01=18000)
    assert got == []                       # 程序化更新零回灌
    assert panel_link.link_check.isChecked()
    assert panel_link.phase_label.text() == "180.00°"
    assert not panel_link.freq_edit.isEnabled()   # 联动时频率禁用
```

fixture 追加:

```python
@pytest.fixture
def panel_link():
    p = ChannelPanel("CH2", link_controls=True)
    p._updating = False
    return p
```

- [ ] **Step 5: 运行测试**

```bash
cd host && python -m pytest tests/ -q
```

预期:全过(原 53 + 新增 3)。

- [ ] **Step 6: 提交**

```bash
git add host/siggen/ui/widgets.py host/siggen/ui/main_window.py host/tests/test_widgets.py
git commit -m "feat: GUI 相位联动区(复选框+滑块+置灰联动, 仅 CH2 显示)"
```

---

## Task 8: 预览叠加双通道

**Files:**
- Modify: `host/siggen/preview.py`
- Modify: `host/siggen/ui/main_window.py`
- Modify: `host/tests/test_preview.py`

- [ ] **Step 1: preview.py — make_plot_data 加 phase_off**

签名加 `phase_off: float = 0.0`(周期单位,0~1):

```python
def make_plot_data(wave: str, freq_hz: int, ampl_pct: int,
                   base_pct: int = 100, harms=None, n: int = 10000, mod=None,
                   window_s: float = None, t_start: float = 0.0,
                   phase_off: float = 0.0):
```

三处相位计算加偏移:

```python
    if mod.get("mode", "OFF") == "OFF":
        t = np.linspace(t_start, t_start + periods / freq_hz, n, endpoint=False)
        phase = (t * freq_hz + phase_off) % 1.0
        ...
    if mode == "AM":
        gain = 1.0 + m * mod.get("am_depth", 0) / 100.0
        phase = (t_abs * freq_hz + phase_off) % 1.0
        ...
    elif mode in ("FM", "SWEEP"):
        ...
        theta0 = (t_start * freq_hz
                  + mod.get("fm_dev", 0) * _mod_int(...) / mfreq
                  + phase_off) % 1.0
        ...
    elif mode == "PM":
        off = m * mod.get("pm_dev", 0) / 256.0 + phase_off
        phase = (t_abs * freq_hz + off) % 1.0
```

- [ ] **Step 2: preview.py — WavePreview 双通道曲线**

`__init__` 加第二条曲线(在 `_mod_curve` 之后):

```python
        self._ch2_curve = self.plot([], [], pen=pg.mkPen("m", width=2), autoDownsample=True)
        legend.addItem(self._ch2_curve, "CH2")
```

`set_params` 签名加 `ch2=None`(ch2 dict: wave/freq_hz/ampl_pct/base_pct/harms/mod/phase_off),`_params` 元组扩为 7 元素:

```python
    def set_params(self, wave: str, freq_hz: int, ampl_pct: int,
                   base_pct: int = 100, harms=None, mod=None, ch2=None) -> None:
        self._params = (wave, freq_hz, ampl_pct, base_pct, harms, mod, ch2)
        self._regenerating = True
        try:
            t, y = make_plot_data(wave, freq_hz, ampl_pct, base_pct, harms, mod=mod)
            self._curve.setData(t, y)
            self._set_ch2_curve(ch2)          # 窗口对齐 CH1(两通道同窗口)
            ...
```

新增辅助方法(供 set_params/_reset_home/_regenerate 复用):

```python
    def _set_ch2_curve(self, ch2, window_s: float = None, t_start: float = 0.0):
        """画 CH2 曲线; ch2=None 或联动关闭时清除。窗口默认对齐 CH1(自适应)。"""
        if not ch2:
            self._ch2_curve.clear()
            return
        t2, y2 = make_plot_data(
            ch2["wave"], ch2["freq_hz"], ch2["ampl_pct"],
            ch2.get("base_pct", 100), ch2.get("harms"),
            mod=ch2.get("mod"), phase_off=ch2.get("phase_off", 0.0),
            window_s=window_s, t_start=t_start)
        self._ch2_curve.setData(t2, y2)
```

`_reset_home` 与 `_regenerate` 中:
- 解包改 7 元素:`wave, freq_hz, ampl_pct, base_pct, harms, mod, ch2 = self._params`
- `_reset_home`:`self._set_ch2_curve(ch2)`(自适应窗口,同 CH1)
- `_regenerate`:`self._set_ch2_curve(ch2, window_s=win, t_start=x0)`(与 CH1 同步延伸)

- [ ] **Step 3: main_window.py — 传 ch2 参数**

`_on_state` 的 set_params 调用改为:

```python
        st2 = self.ctrl.state[1]
        ch2 = None
        if getattr(st2, "phaselink", False):
            ch2 = {"wave": st2.wave, "freq_hz": st2.freq_hz, "ampl_pct": st2.ampl_pct,
                   "base_pct": st2.base_pct, "harms": st2.harms,
                   "phase_off": getattr(st2, "phase_0_01", 0) / 36000.0}
        self.status_panel.preview.set_params(
            st.wave, st.freq_hz, st.ampl_pct, st.base_pct, st.harms,
            mod={"mode": st.mode, "mwave": st.mwave,
                 "mfreq": st.mfreq_01 / 10.0, "am_depth": st.am_depth,
                 "fm_dev": st.fm_dev, "pm_dev": st.pm_dev},
            ch2=ch2)
```

- [ ] **Step 4: 测试**

`host/tests/test_preview.py` 追加:

```python
def test_make_plot_data_phase_off():
    from siggen.preview import make_plot_data
    # 无偏移: t=0 -> sin(0) = 0
    _, y0 = make_plot_data("SINE", 1000, 100, n=10000)
    assert abs(y0[0]) < 1e-6
    # 90° 相移: t=0 -> sin(90°) = +1
    _, y2 = make_plot_data("SINE", 1000, 100, n=10000, phase_off=0.25)
    assert abs(y2[0] - 1.0) < 1e-6


def test_make_plot_data_phase_off_fm():
    from siggen.preview import make_plot_data
    _, y = make_plot_data("SINE", 1000, 100, n=10000, phase_off=0.5,
                          mod={"mode": "FM", "mwave": "SINE", "mfreq": 100,
                               "am_depth": 0, "fm_dev": 0, "pm_dev": 0})
    assert abs(y[0]) < 1e-6          # 180° 相移: t=0 -> sin(180°) = 0
```

(第二个用例验证 FM 路径的 phase_off 注入;fm_dev=0 时 FM 恒频,相位偏移应等于 phase_off。)

- [ ] **Step 5: 运行测试**

```bash
cd host && python -m pytest tests/ -q
```

预期:全过(原 56 + 新增 2)。

- [ ] **Step 6: 提交**

```bash
git add host/siggen/preview.py host/siggen/ui/main_window.py host/tests/test_preview.py
git commit -m "feat: 预览叠加 CH2 曲线(相位差模式下, 缩放同步延伸)"
```

---

## Task 9: 文档 + 全量回归 + 设计文档修正

**Files:**
- Modify: `README.md` / `CLAUDE.md` / `docs/superpowers/specs/2026-08-23-dual-channel-phase-link-design.md`

- [ ] **Step 1: 设计文档修正打包描述**

把 `gpio10 = {link_en[31], phase_off[30:0]}` 改为 `gpio10 = phase_off[31:0]`(32 位全周期)、`gpio11 = {31'd0, link_en}`(理由:31 位相位字只能表示半周期)。

- [ ] **Step 2: README.md**

功能列表追加:

```
- 双通道相位联动: CH2 频率跟随 CH1、相位差 0~360.00° 可调(SET CH2 PHASE,
  0.01° 单位); 联动时 CH2 调制抑制、谐波整体移相; 预览叠加双通道曲线
```

协议区追加:

```
SET CH2 PHASE <0..36000> | SET CH2 PHASELINK ON|OFF   (仅 CH2, 相对 CH1)
```

- [ ] **Step 3: CLAUDE.md**

新增"相位联动经验(2026-08-23)"节:

```
## 相位联动经验(2026-08-23)
- 主从相位: CH2 相位 = CH1 累加器 + 静态偏移(组合加法 + 打拍采样), 全周期 mod 2^32
- 联动时 CH2 调制抑制(pm_off=0 / am_gain=12700 / fword 强制用 CH1)——调制使瞬时
  频率变化, 无法保持恒定相位差
- 谐波随基波整体移相: harm_synth acc0 同样外部注入, 保持 θk≡k·θ0
- 打包: gpio10 = 相位差 32 位全周期, gpio11 bit0 = 联动使能(31 位只够半周期!)
- 示波器验证: :STOP 冻结帧分读 CHAN1/CHAN2, 上升过零时间差 × 频率 → 相位差
```

- [ ] **Step 4: 全量回归**

```bash
cd host && python -m pytest tests/ -q          # 全过(原 49 + 新增)
python scripts/test_bridge.py COM11            # 全过(76 + ~11)
python scripts/scope_verify.py COM11           # 全过(14 + 5)
```

(板级两项需板卡+示波器在线。)

- [ ] **Step 5: 提交**

```bash
git add README.md CLAUDE.md docs/superpowers/specs/2026-08-23-dual-channel-phase-link-design.md
git commit -m "docs: 相位联动协议/经验文档 + 设计文档打包修正"
```

---

## 自审清单

1. **Spec 覆盖**:功能定义(跟随/相位差/调制抑制/谐波移相)→ Task 2/3;协议 → Task 3;GUI 联动区 → Task 7;预览叠加 → Task 8;验证四层 → Task 4/5/6;文档 → Task 9。✓
2. **占位符**:无 TBD;所有代码完整给出。✓
3. **类型一致性**:phase_0_01(0.01° 整数)贯穿协议/main.c/controller/widgets;phase_off(周期单位 float)仅预览内部;angle_to_phase 换算唯一入口。✓
4. **打包一致性**:gpio10=相位差(32 位)、gpio11=link_en——reg_bridge、main.c pack_word_e/f、Task 1 用例三者一致。✓
5. **行为细节**:PHASE 仅 CH2(桥拒绝 CH1,`ERR BAD CHANNEL`);phaselink 状态存 ch[1] 但 gpio 是全局关系字——write_all 用 ch[1] 打包。✓
