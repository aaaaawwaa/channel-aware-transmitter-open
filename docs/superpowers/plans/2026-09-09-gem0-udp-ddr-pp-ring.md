# GEM0 UDP DDR PP 环形重装 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 PS 固件在 DDR PP 播放进行中允许主机通过现有 UDP 数据面把新内容写入非活动 bank，实现两 bank 无限交替（环形重装）。

**Architecture:** 固件新增 per-bank `ddr_pp_bank_fresh[2]`（自上次播放以来被 COMMIT 过），取代一次性 `ddr_pp_reload_used`；`ddr_pp_udp_begin` 在播放中放宽为“仅非活动 bank 可写”；`ddr_pp_service` 的 DRAIN 收尾改为三态判定（fresh→切换 / UDP staging 进行中→ERROR 0x26 / 否则→IDLE）。不动 DMA/PL；切换间隙由 AXIS FIFO 吸收。

**Tech Stack:** Zynq-7020 PS 裸机 C（`sdk/src/main.c`）、lwIP Raw UDP、Python 上位机（`host/`）、Vitis 2025.2 CLI（`vivado2025.cmd`）、pytest。

**Spec:** `docs/superpowers/specs/2026-09-09-gem0-udp-ddr-pp-ring-design.md`

## Global Constraints

- 工具链：Vitis 2025.2。构建/部署必须用工程根 `vivado2025.cmd`；不要用旧 `cli-anything-vivado`。
- 源码唯一入口 `sdk/src/main.c`；构建前必须同步到工作区 `sdk/ws2025_gem_fresh/awg_bridge/src/main.c`。
- 板卡：IP `192.0.2.10` UDP `40100`，串口 `COM11` @115200；主机网卡 `192.0.2.10/24`。
- 不改 `GET DDR PP` 的 wire 格式与字段名；不新增 UDP 消息；UART 命令集不变。
- 播放中仅 UDP 可写非活动 bank；UART 只在停播态 staging。
- 每个任务先写失败测试（RED），再实现（GREEN），最后提交。禁止先写实现。
- 所有 host 测试命令：`host/.venv/Scripts/python -m pytest <path> -v`。

---

### Task 1: 固件所有权模型 —— per-bank `fresh` 与 `begin` 放宽

**Files:**
- Modify: `sdk/src/main.c`（声明、`ddr_pp_clear_transfer`、`ddr_pp_start_transfer`、`ddr_pp_udp_commit`、UART `COMMIT`、`ddr_pp_udp_begin`）
- Test: `host/tests/test_stage6_ring_sdk.py`（新建）

**Interfaces:**
- Consumes: 现有 `ddr_pp_stage_owner[]`、`ddr_pp_valid[]`、`DDR_PP_STAGE_UART/UDP/NONE`、`ddr_pp_active`、`ddr_pp_active_bank`。
- Produces: `static u8 ddr_pp_bank_fresh[2];`（Task 2 的切换判定依赖它）；`ddr_pp_udp_begin()` 在 `ddr_pp_active` 时允许写非活动 bank 且不再因 `digmod_mode!=0`/`play_blocks_config()` 拒绝。

- [ ] **Step 1: 写失败测试**

创建 `host/tests/test_stage6_ring_sdk.py`：

```python
from pathlib import Path

ROOT = Path(__file__).parents[2]
SDK_MAIN = ROOT / "sdk" / "src" / "main.c"


def _text():
    return SDK_MAIN.read_text(encoding="utf-8")


def _block(text, start_marker, end_marker):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_firmware_declares_per_bank_fresh_flag():
    assert "static u8 ddr_pp_bank_fresh[2];" in _text()


def test_clear_and_start_and_commit_manage_fresh():
    text = _text()
    clear = _block(text, "static int ddr_pp_clear_transfer",
                   "static int ddr_pp_stop_transfer")
    assert "ddr_pp_bank_fresh[0] = 0" in clear
    assert "ddr_pp_bank_fresh[1] = 0" in clear
    start = _block(text, "static int ddr_pp_start_transfer",
                   "static void ddr_pp_service")
    assert "ddr_pp_bank_fresh[bank] = 0" in start
    udp_commit = _block(text, "static int ddr_pp_udp_commit",
                        "static void ddr_pp_udp_abort")
    assert "ddr_pp_bank_fresh[bank] = 1" in udp_commit


def test_uart_commit_sets_fresh():
    text = _text()
    block = _block(text, 'strcmp(tok[3], "COMMIT") == 0',
                   'strcmp(tok[3], "START") == 0')
    assert "ddr_pp_bank_fresh[bank] = 1" in block


def test_begin_allows_non_active_bank_while_playing():
    text = _text()
    begin = _block(text, "static int ddr_pp_udp_begin",
                   "static int ddr_pp_udp_write")
    # while playing: reject only the active bank, digmod/play no longer block
    assert "if (ddr_pp_active)" in begin
    assert "if (bank == ddr_pp_active_bank)" in begin
    assert "else if (digmod_mode != 0u || play_blocks_config())" in begin
    # stopped-state rule must still reject digital-mod-on
    assert "ddr_pp_bank_fresh[bank] = 0" in begin
```

- [ ] **Step 2: 运行测试确认失败**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_stage6_ring_sdk.py -v`
Expected: FAIL（`ddr_pp_bank_fresh` 不存在、`if (ddr_pp_active)` 分支不存在）

- [ ] **Step 3: 实现**

在 `sdk/src/main.c` 中：

(a) 在 `static u8 ddr_pp_next_bank;` 下一行加声明：

```c
static u8 ddr_pp_next_bank;
static u8 ddr_pp_bank_fresh[2];   /* committed since last played (ring refill) */
```

(b) `ddr_pp_clear_transfer()` 内，在 `ddr_pp_reload_used = 0;` 之后加：

```c
    ddr_pp_bank_fresh[0] = 0;
    ddr_pp_bank_fresh[1] = 0;
```

(c) `ddr_pp_start_transfer()` 内，在 `ddr_pp_active_bank = (u8)bank;` 之后加：

```c
    ddr_pp_bank_fresh[bank] = 0u;
```

(d) `ddr_pp_udp_commit()` 内，在 `ddr_pp_valid[bank] = 1;` 之后加：

```c
    ddr_pp_bank_fresh[bank] = 1u;
```

(e) UART `SET DDR PP COMMIT` 分支内，在 `ddr_pp_valid[bank] = 1;` 之后加：

```c
                ddr_pp_bank_fresh[bank] = 1u;
```

(f) `ddr_pp_udp_begin()`：把守卫块

```c
    /* First release stages only while no writer or player owns the DAC. */
    if (ddr_active || ddr_pp_active || ddr_uploading || digmod_mode != 0u ||
        play_blocks_config() ||
        ddr_pp_stage_owner[0] == DDR_PP_STAGE_UART ||
        ddr_pp_stage_owner[1] == DDR_PP_STAGE_UART)
        return UDP_DDR_PP_ERROR_BUSY;
    if (ddr_pp_stage_owner[bank] != DDR_PP_STAGE_NONE)
        return UDP_DDR_PP_ERROR_BUSY;
```

替换为：

```c
    /* While a bank plays, the other bank is the only legal refill target;
     * while stopped, keep the first-release rule (digital mod off, no play). */
    if (ddr_active || ddr_uploading ||
        ddr_pp_stage_owner[0] == DDR_PP_STAGE_UART ||
        ddr_pp_stage_owner[1] == DDR_PP_STAGE_UART)
        return UDP_DDR_PP_ERROR_BUSY;
    if (ddr_pp_active) {
        if (bank == ddr_pp_active_bank)
            return UDP_DDR_PP_ERROR_BUSY;
    } else if (digmod_mode != 0u || play_blocks_config()) {
        return UDP_DDR_PP_ERROR_BUSY;
    }
    if (ddr_pp_stage_owner[bank] != DDR_PP_STAGE_NONE)
        return UDP_DDR_PP_ERROR_BUSY;
```

并在该函数内 `ddr_pp_valid[bank] = 0;` 之后加：

```c
    ddr_pp_bank_fresh[bank] = 0u;
```

- [ ] **Step 4: 运行测试确认通过**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_stage6_ring_sdk.py -v`
Expected: PASS

- [ ] **Step 5: 回归现有 SDK 源码测试**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_stage4_m3_sdk.py host/tests/test_stage5_m0_sdk.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add sdk/src/main.c host/tests/test_stage6_ring_sdk.py
git commit -m "feat(fw): per-bank fresh flag and ring-refill begin relaxation"
```

---

### Task 2: 固件切换逻辑 —— 三态 DRAIN、NEXT_BANK、错误码 0x26

**Files:**
- Modify: `sdk/src/main.c`（`ddr_pp_service` 的 DRAIN 分支、`ddr_pp_start_transfer` 的 next_bank、`GET DDR PP` 的 NEXT_BANK、移除 `ddr_pp_reload_used`/`ddr_pp_next_bank`）
- Test: `host/tests/test_stage6_ring_sdk.py`（扩展）

**Interfaces:**
- Consumes: Task 1 的 `ddr_pp_bank_fresh[2]`；现有 `ddr_pp_stage_owner[]`、`ddr_pp_stop_transfer()`、`ddr_pp_start_transfer()`。
- Produces: DRAIN 三态收尾；错误码 `0x26`（ring underrun）；`GET DDR PP` 的 `NEXT_BANK` 改为按当前状态计算。

- [ ] **Step 1: 写失败测试**

在 `host/tests/test_stage6_ring_sdk.py` 追加：

```python
def test_drain_switches_on_fresh_else_underrun_else_idle():
    text = _text()
    service = _block(text, "static void ddr_pp_service",
                     "static void ddr_pp_flush_cpu_write_range")
    drain = service[service.index('strcmp(ddr_pp_state, "DRAIN") == 0'):
                    service.index('strcmp(ddr_pp_state, "RUNNING") == 0')]
    assert "ddr_pp_bank_fresh[other]" in drain
    assert "ddr_pp_stage_owner[other] == DDR_PP_STAGE_UDP" in drain
    assert "0x26u" in drain
    assert 'ddr_pp_state = "IDLE"' in drain


def test_reload_one_shot_flag_is_removed():
    text = _text()
    assert "ddr_pp_reload_used" not in text
    assert "ddr_pp_next_bank" not in text


def test_get_ddr_pp_next_bank_is_computed_from_fresh():
    text = _text()
    getter = _block(text, 'strcmp(tok[1], "DDR") == 0 && strcmp(tok[2], "PP") == 0',
                    'if (n == 2 && strcmp(tok[1], "DDR") == 0)')
    assert "ddr_pp_active_bank ^ 1u" in getter
    assert "ddr_pp_bank_fresh[" in getter
```

- [ ] **Step 2: 运行测试确认失败**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_stage6_ring_sdk.py -v`
Expected: 新增 3 个测试 FAIL（`0x26u`、`ddr_pp_active_bank ^ 1u` 不存在；`ddr_pp_reload_used` 仍存在）

- [ ] **Step 3: 实现**

(a) 删除 `static u8 ddr_pp_next_bank;` 与 `static u8 ddr_pp_reload_used;` 两行声明，以及所有对它们的赋值（`ddr_pp_clear_transfer` 的 `ddr_pp_reload_used = 0;`、`ddr_pp_stop_transfer`/`ddr_pp_start_transfer`/init 里的 `ddr_pp_next_bank = 0xffu;`）。

(b) `ddr_pp_start_transfer()` 中删除：

```c
    other = bank ^ 1u;
    ddr_pp_next_bank = (other < 2u && ddr_pp_valid[other]) ? (u8)other : 0xffu;
```

同时删除该函数里不再使用的局部变量 `u32 other;`。

(c) 把 `ddr_pp_service()` 的 DRAIN 分支

```c
    } else if (strcmp(ddr_pp_state, "DRAIN") == 0 && user_stream_done) {
        u32 next = ddr_pp_next_bank;
        u32 current = ddr_pp_active_bank;
        dma_pp_stream_ctrl(0, 0, current);
        DDR_PP_TIMELINE.DRAIN_TICKS = now - DDR_PP_TIMELINE.ioc_at;
        DDR_PP_TIMELINE.drain_at = now;
        if (!ddr_pp_reload_used && next < 2u && ddr_pp_valid[next]) {
            ddr_pp_reload_used = 1u;
            ddr_pp_active = 0;
            ddr_pp_active_bank = 0xffu;
            ddr_pp_next_bank = 0xffu;
            XAxiDma_Reset(&Dma);
            if (!dma_wait_reset()) {
                ddr_pp_state = "ERROR";
                ddr_pp_error_code = 0x21u;
                return;
            }
            if (!ddr_pp_start_transfer(next)) {
                ddr_pp_state = "ERROR";
                if (ddr_pp_error_code == 0u) ddr_pp_error_code = 0x22u;
                return;
            }
            ddr_pp_next_bank = 0xffu;
        } else {
            ddr_pp_active = 0;
            ddr_pp_active_bank = 0xffu;
            ddr_pp_next_bank = 0xffu;
            ddr_pp_state = "IDLE";
            ddr_pp_error_code = 0;
        }
    }
```

替换为：

```c
    } else if (strcmp(ddr_pp_state, "DRAIN") == 0 && user_stream_done) {
        u32 current = ddr_pp_active_bank;
        u32 other = current ^ 1u;
        dma_pp_stream_ctrl(0, 0, current);
        DDR_PP_TIMELINE.DRAIN_TICKS = now - DDR_PP_TIMELINE.ioc_at;
        DDR_PP_TIMELINE.drain_at = now;
        if (current < 2u && other < 2u && ddr_pp_bank_fresh[other]) {
            ddr_pp_active = 0;
            ddr_pp_active_bank = 0xffu;
            XAxiDma_Reset(&Dma);
            if (!dma_wait_reset()) {
                ddr_pp_state = "ERROR";
                ddr_pp_error_code = 0x21u;
                return;
            }
            if (!ddr_pp_start_transfer(other)) {
                ddr_pp_state = "ERROR";
                if (ddr_pp_error_code == 0u) ddr_pp_error_code = 0x22u;
                return;
            }
        } else if (current < 2u && other < 2u &&
                   ddr_pp_stage_owner[other] == DDR_PP_STAGE_UDP) {
            if (!ddr_pp_stop_transfer()) ddr_pp_error_code = 0x21u;
            else ddr_pp_error_code = 0x26u;
            ddr_pp_state = "ERROR";
        } else {
            ddr_pp_active = 0;
            ddr_pp_active_bank = 0xffu;
            ddr_pp_state = "IDLE";
            ddr_pp_error_code = 0;
        }
    }
```

(d) `GET DDR PP` 处理器：在 `sprintf` 之前计算 `NEXT_BANK`，并把 `ddr_pp_bank_name(ddr_pp_next_bank)` 换成 `ddr_pp_bank_name(next_bank)`：

```c
            u8 next_bank = 0xffu;
            if (ddr_pp_active && ddr_pp_active_bank < 2u &&
                ddr_pp_bank_fresh[ddr_pp_active_bank ^ 1u])
                next_bank = (u8)(ddr_pp_active_bank ^ 1u);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_stage6_ring_sdk.py -v`
Expected: PASS

- [ ] **Step 5: 全量 host 单测回归**

Run: `host/.venv/Scripts/python -m pytest host/tests/ -q`
Expected: PASS（含 M3 门禁离线测试：`NEXT_BANK` 语义变化不影响，因为 fake 仍按脚本返回）

- [ ] **Step 6: 提交**

```bash
git add sdk/src/main.c host/tests/test_stage6_ring_sdk.py
git commit -m "feat(fw): repeatable ring switch with underrun error 0x26"
```

---

### Task 3: M4 门禁 runner + 离线测试

**Files:**
- Modify: `scripts/udp_ddr_pp_gate.py`（新增 `make_ring_payloads`、`run_m4`、`main` 的 `m4` 分支）
- Test: `host/tests/test_udp_ddr_pp_gate.py`（追加 M4 用例）

**Interfaces:**
- Consumes: `UdpDdrPpClient.upload(bank, payload) -> UploadResult`；`M3Uart.cmd(text)`；现有 `_pp_status`、`_expect_ok`、`BANK_BYTES`、`GateError`。
- Produces: `run_m4(client, uart, rounds=3, poll_timeout_s=40.0) -> dict`，返回 `{"switch_order": [...], "final_state": str, "final_error": int, "rounds": int}`；`udp_ddr_pp_gate.py m4` 打印 `M4 PASS`。

- [ ] **Step 1: 写失败测试**

在 `host/tests/test_udp_ddr_pp_gate.py` 追加（复用现有 `FakeUart`、`FakeUdpClient`、`_pp_line`、`_fast_crc16`）：

```python
def test_m4_refills_non_active_bank_and_switches():
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_ring_payloads(0)
    crc = {0: _fast_crc16(p0), 1: _fast_crc16(p1)}
    bits = gate.BANK_BYTES * 8
    uart = FakeUart()
    # settle(metadata) -> START -> active 0 -> (gate uploads bank1) -> active 1 -> IDLE
    uart.ddrpp = [
        _pp_line(b0v=1, b0len=bits, b0crc=crc[0], b1v=1, b1len=bits, b1crc=crc[1]),
        _pp_line(active=1, abank="0", state="RUNNING", b0v=1, b1v=1),
        _pp_line(active=1, abank="1", state="RUNNING", b0v=1, b1v=1),
        _pp_line(),
    ]
    client = FakeUdpClient()

    result = gate.run_m4(client, uart, rounds=1)

    assert result["switch_order"] == ["1"]
    assert result["final_state"] == "IDLE"
    assert client.uploads[0][0] == 0 and client.uploads[1][0] == 1  # preload
    assert client.uploads[2][0] == 1                                # refill non-active
    assert "SET DDR PP START" in uart.sent


def test_m4_uses_distinct_payload_per_round():
    from scripts import udp_ddr_pp_gate as gate

    a = gate.make_ring_payloads(1)[0]
    b = gate.make_ring_payloads(2)[0]
    assert a != b


def test_m4_fails_on_playback_error():
    import pytest as _pytest
    from scripts import udp_ddr_pp_gate as gate

    p0, p1 = gate.make_ring_payloads(0)
    crc = {0: _fast_crc16(p0), 1: _fast_crc16(p1)}
    bits = gate.BANK_BYTES * 8
    uart = FakeUart()
    uart.ddrpp = [
        _pp_line(b0v=1, b0len=bits, b0crc=crc[0], b1v=1, b1len=bits, b1crc=crc[1]),
        _pp_line(active=1, abank="0", state="ERROR", error=0x26, b0v=1, b1v=1),
    ]
    with _pytest.raises(gate.GateError, match="ERROR"):
        gate.run_m4(FakeUdpClient(), uart, rounds=1)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_udp_ddr_pp_gate.py -k m4 -v`
Expected: FAIL（`make_ring_payloads` / `run_m4` 不存在）

- [ ] **Step 3: 实现**

在 `scripts/udp_ddr_pp_gate.py` 中：

```python
M4_ROUNDS = 3


def make_ring_payloads(seed):
    """Distinct deterministic 1 MiB payloads per round (CRC differs)."""
    return (
        bytes((i * 41 + 7 + seed) & 0xFF for i in range(BANK_BYTES)),
        bytes((i * 59 + 13 + seed) & 0xFF for i in range(BANK_BYTES)),
    )


def _ring_metadata_ok(status, payload_crc16):
    bits = BANK_BYTES * 8
    for bank in (0, 1):
        if not status["b%d_valid" % bank]:
            return False
        if status["b%d_length" % bank] != bits:
            return False
        if status["b%d_crc" % bank] != payload_crc16[bank]:
            return False
    return status["active"] == 0 and status["error"] == 0


def run_m4(client, uart, rounds=M4_ROUNDS, poll_timeout_s=M3_POLL_TIMEOUT_S):
    uart.cmd("SET DDR PP STOP")
    _expect_ok(uart, "SET DIGMOD MODE OFF")
    preload = {}
    base = make_ring_payloads(0)
    for bank in (0, 1):
        payload = base[bank]
        res = client.upload(bank, payload)
        if res.device_error is not ReplyError.NONE:
            raise GateError("M4 preload bank %d device error" % bank)
        if res.payload_crc32 != res.device_crc32 or res.bytes_sent != BANK_BYTES:
            raise GateError("M4 preload bank %d CRC mismatch" % bank)
        preload[bank] = payload
    status = _pp_status(uart)
    if not _ring_metadata_ok(status, {b: _crc16_ccitt_fast(preload[b]) for b in (0, 1)}):
        raise GateError("M4 preload metadata mismatch")
    _expect_ok(uart, "SET DIGMOD FILTER RECT")
    _expect_ok(uart, "SET DIGMOD ROLLOFF 0.35")
    _expect_ok(uart, "SET DIGMOD SYMRATE 1000000")
    _expect_ok(uart, "SET DIGMOD MODE QAM16")
    if uart.cmd("SET DDR PP START") != "OK":
        raise GateError("M4 SET DDR PP START rejected")
    switch_order = []
    for r in range(rounds):
        # wait until a bank is active and known
        active = _wait_active_bank(uart, poll_timeout_s)
        other = 1 - active
        payload = make_ring_payloads(rounds + 1 + r)[other]
        res = client.upload(other, payload)
        if res.device_error is not ReplyError.NONE:
            raise GateError("M4 refill bank %d device error" % other)
        if res.payload_crc32 != res.device_crc32:
            raise GateError("M4 refill bank %d CRC mismatch" % other)
        _wait_bank(uart, other, poll_timeout_s)
        switch_order.append(str(other))
    # stop refilling -> natural end
    final = _wait_idle(uart, poll_timeout_s)
    _expect_ok(uart, "SET DIGMOD MODE OFF")
    return {"switch_order": switch_order, "final_state": final["state"],
            "final_error": final["error"], "rounds": rounds}
```

`_wait_active_bank` / `_wait_bank` / `_wait_idle` 用小步轮询 `_pp_status(uart)`，命中 `status["error"] != 0` 抛 `GateError("M4 playback ERROR %02X" % ...)`，超时抛 `GateError("M4 timed out")`：

```python
def _wait_active_bank(uart, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = _pp_status(uart)
        if st["error"]:
            raise GateError("M4 playback ERROR %02X" % st["error"])
        if st["active"] and st["active_bank"] in ("0", "1"):
            return int(st["active_bank"])
        time.sleep(0.01)
    raise GateError("M4 timed out waiting for active bank")


def _wait_bank(uart, bank, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = _pp_status(uart)
        if st["error"]:
            raise GateError("M4 playback ERROR %02X" % st["error"])
        if st["active"] and st["active_bank"] == str(bank):
            return
        time.sleep(0.01)
    raise GateError("M4 timed out waiting for bank %d" % bank)


def _wait_idle(uart, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = _pp_status(uart)
        if st["error"]:
            raise GateError("M4 playback ERROR %02X" % st["error"])
        if not st["active"] and st["state"] == "IDLE":
            return st
        time.sleep(0.01)
    raise GateError("M4 timed out waiting for IDLE")
```

在 `main()` 的 `choices` 加 `"m4"`，并加分支：预装+环形运行后打印

```python
        print("M4 SWITCH ORDER %s STATE %s ERROR %02X" %
              (",".join(result["switch_order"]), result["final_state"],
               result["final_error"]))
        print("M4 PASS")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_udp_ddr_pp_gate.py -k m4 -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/udp_ddr_pp_gate.py host/tests/test_udp_ddr_pp_gate.py
git commit -m "feat(host): add M4 ring-refill board gate runner"
```

---

### Task 4: 构建/部署 + 板上 M4 验收 + 全回归 + 文档

**Files:**
- Modify: `README.md`（门禁段与已知缺陷段）
- Modify: `sdk/ws2025_gem_fresh/awg_bridge/src/main.c`（同步副本，非源码）

**Interfaces:**
- Consumes: Task 1–3 全部产出。
- Produces: 板上 `M4 PASS`；回归全绿；README 更新。

- [ ] **Step 1: 同步源码并重建 ELF**

```bash
cp sdk/src/main.c sdk/ws2025_gem_fresh/awg_bridge/src/main.c
./vivado2025.cmd app build awg_bridge --workspace sdk/ws2025_gem_fresh
```
Expected: `app.build: ok`

- [ ] **Step 2: 用 nm 复核固定 DDR 缓冲仍高于 `__bss_end`（防回归）**

```bash
NM="<LOCAL_PATH>2025.2/2025.2/Vitis/gnu/aarch32/nt/gcc-arm-none-eabi/bin/arm-none-eabi-nm.exe"
"$NM" -n sdk/ws2025_gem_fresh/awg_bridge/build/awg_bridge.elf | grep -wE "__bss_end|end"
```
Expected: `__bss_end` ≈ `0x005001c8`（未因新增 `ddr_pp_bank_fresh` 跨过 DMA/诊断缓冲）

- [ ] **Step 3: 部署**

```bash
./vivado2025.cmd deploy jtag --app awg_bridge --workspace sdk/ws2025_gem_fresh --bit awg/awg.runs/impl_1/host_bd_wrapper.bit
```
Expected: `deploy.jtag: ok`

- [ ] **Step 4: 板上 M4**

```bash
host/.venv/Scripts/python scripts/udp_ddr_pp_gate.py m4 --host 192.0.2.10 --port 40100 --serial COM11
```
Expected: `M4 SWITCH ORDER 1,0,1 STATE IDLE ERROR 00` 与 `M4 PASS`

- [ ] **Step 5: 负例（手动/脚本化）确认 underrun 0x26**

对非活动 bank `BEGIN` 但不 `COMMIT`，等切换点应见 `STATE ERROR ERROR 26`，随后 `SET DDR PP STOP` 恢复为 `IDLE`。若该步不方便脚本化，先用 `M4` runner 的负例测试覆盖离线路径，并在板上用 UART 手动确认一次，把输出贴进提交说明。

- [ ] **Step 6: 全回归**

```bash
host/.venv/Scripts/python scripts/udp_ddr_pp_gate.py m0 --host 192.0.2.10 --port 40100 --serial COM11
host/.venv/Scripts/python scripts/udp_ddr_pp_gate.py m1 --host 192.0.2.10 --port 40100 --serial COM11 --bank 0
host/.venv/Scripts/python scripts/udp_ddr_pp_gate.py m2 --host 192.0.2.10 --port 40100 --bank 1
host/.venv/Scripts/python scripts/udp_ddr_pp_gate.py m3 --host 192.0.2.10 --port 40100 --serial COM11
host/.venv/Scripts/python scripts/ddr_pp_timeline_gate.py COM11 --profile all
host/.venv/Scripts/python scripts/test_bridge.py COM11
host/.venv/Scripts/python scripts/ddr_gate.py COM11
host/.venv/Scripts/python -m pytest host/tests/ -q
```
Expected: 每个 gate 打印各自 `PASS`；timeline `--profile all` 仍以 `A→B→IDLE` 结束（有限语义不变）；host 单测全绿。

- [ ] **Step 7: 更新 README**

在门禁段（M0–M3 之后）加 `m4` 命令与语义说明：

```markdown
# 播放中经 UDP 重装非活动 bank（环形，切换 K≥3 次），验证 ACTIVE_BANK 0→1→0…
python scripts/udp_ddr_pp_gate.py m4 --host 192.0.2.10 --port 40100 --serial COM11
```

并说明：播放中仅非活动 bank 可写；切换时下一块未就绪报 `ERROR 26`；UART 仍只做控制/恢复。

- [ ] **Step 8: 提交**

```bash
git add README.md
git commit -m "docs: document M4 ring-refill gate and playback-time refill rules"
```

---

## Self-Review

**Spec coverage:**
- §1 所有权/fresh → Task 1（fresh 声明/生命周期）+ Task 2（切换用它）。✓
- §2 DRAIN 三态 / NEXT_BANK / 可重复切换 / 0x26 → Task 2。✓
- §3 begin 放宽 / COMMIT 置 fresh / UART 不变 / 0x26 / 上位机环形编排 → Task 1（begin、commit）+ Task 3（编排与 gate）。✓
- §4 错误与恢复（0x26 不自动 ABORT、ABORT 收尾、STOP、超时、FIFO 欠载）→ Task 2（0x26 路径）+ Task 4 Step 5（负例）。✓
- §5 测试与门禁（离线单测 + M4 + 回归）→ Task 1/2/3 的测试 + Task 4。✓
- 非目标（不改 DMA/PL、不新增消息、wire 不变）→ Global Constraints + Task 4 Step 2/6。✓

**Placeholder scan:** 无 TBD/TODO；每个代码步含实际代码或精确命令。

**Type consistency:** `ddr_pp_bank_fresh[]`、`run_m4`、`make_ring_payloads`、`M4_ROUNDS`、`_wait_active_bank/_wait_bank/_wait_idle`、`_ring_metadata_ok` 在各任务间命名一致；`run_m4` 返回键 `switch_order/final_state/final_error/rounds` 与测试断言一致。
