# GEM0 UDP DDR PP Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable PS GEM0 and a bare-metal lwIP Raw UDP data plane that preloads one or both stopped DDR PP banks at at least 3 MB/s, while preserving UART as the control, log, and recovery plane.

**Architecture:** GEM0 connects directly to the board RTL8211F; the PS application owns a single lwIP raw-UDP receiver and writes validated packets into an inactive 1 MiB DDR PP staging bank. A new UDP module owns framing, socket/lwIP polling, single-session timeout, and replies, while `sdk/src/main.c` remains the authority for PP bank metadata, DMA ownership, cache maintenance, UART commands, and the non-blocking service loop. The host uses a dedicated UDP codec/client/worker rather than `SerialWorker`; UART remains responsible for control, status, playback, logs, and recovery.

**Tech Stack:** Vivado 2025.2 PS7, Zynq-7020 GEM0/RGMII/MDIO, RTL8211F, Vitis 2025.2 standalone BSP with `lwip213` Raw API, C, Python 3, PySide6, pytest, pyserial.

**Spec:** `docs/superpowers/specs/2026-09-08-ethernet-ddr-upload-design.md`

## Global Constraints

- Only enable PS GEM0 and MDIO in `scripts/ps7_props.tcl`; do not add AXI Ethernet, PL Ethernet IP, or a new PL clock domain.
- Preserve the known RTL8211F hardware-reset topology: `CONFIG.PCW_ENET0_RESET_ENABLE` and `CONFIG.PCW_ENET_RESET_ENABLE` remain `0` until a schematic proves a PS-MIO PHY-reset route.
- Preserve UART1 on MIO48/49 at 115200 as the control, log, diagnostics, and recovery channel; `host/siggen/transport.py:11-114` remains serial-only.
- Use the fixed M0 direct-link configuration: host `192.0.2.10/24`, board `192.0.2.10/24`, UDP port `40100`. Do not freeze another address without a successful M0 collision check.
- First release accepts `BEGIN` only while ordinary DDR upload, DMA playback, DDR PP playback, manual playback, and digital modulation playback are all inactive.
- First release supports only stopped-state preload of one or both 1 MiB PP banks. It does not overwrite an inactive bank during playback and does not create a continuous ring/refill protocol.
- A UDP `DATA` payload is 4 to 1400 bytes, is four-byte aligned, and never relies on IP fragmentation. PP transfer lengths are four-byte aligned and at most `0x00100000` bytes.
- Use network-byte-order fields and IEEE CRC-32 for the UDP protocol. Continue accepting existing UART PP `CLEAR`/`CHUNK`/`COMMIT`/`START`/`STOP` commands and their CRC-16 semantics.
- A UDP session may invalidate only its target bank. A bad header, session mismatch, invalid range, data CRC mismatch, timeout, or `ABORT` must not make a bank valid or alter the other bank.
- CPU-written UDP payload must be cache-flushed before a successful PP commit exposes it to MM2S DMA. Do not add cache invalidation to this CPU-write path.
- After PS7 changes, run `python scripts/build_bitstream.py --force` and `python scripts/check_bitstream.py`; regenerate the BSP/application from that HDF, then apply the existing OCM-linker patch before building the ELF.
- Treat `sdk/ws*/` as generated output. Source of truth is `sdk/src/`; do not commit a generated HDF, BSP, workspace, ELF, or bitstream.

---

## Wire Contract

All datagrams start with this 32-byte, packed, network-byte-order header. Implement it with explicit byte reads/writes, not casts of an unaligned C struct.

```text
0       4       magic       ASCII "ADPP"
4       1       version     1
5       1       type        ECHO=1, BEGIN=2, DATA=3, STATUS=4, COMMIT=5,
                            ABORT=6, REPLY=0x80
6       2       bank        0 or 1 for a transaction; 0xffff for ECHO
8       4       session_id non-zero random host value; zero for ECHO
12      4       sequence    host DATA sequence; copied into each reply
16      4       offset      DATA offset, otherwise zero
20      4       total_bytes full payload length for BEGIN/DATA/COMMIT
24      4       payload_len trailing payload length
28      4       crc32       BEGIN/COMMIT full-payload CRC; DATA payload CRC
```

`ECHO` has no payload and receives `REPLY` with a 20-byte status payload. `BEGIN`, `DATA`, `STATUS`, `COMMIT`, and `ABORT` are transaction messages. Sequence values are fixed: ECHO and BEGIN use `0`; DATA uses the monotonically assigned packet index `0..N-1`; STATUS uses `0xfffffffe`; COMMIT uses `0xffffffff`; and ABORT uses `0xfffffffd`. The device accepts DATA in any arrival order. A successful `REPLY` copies the request `bank`, `session_id`, and `sequence`, sets `offset` to zero, sets `total_bytes` to the active transaction length (zero for ECHO), and carries this status payload in network order. Its `crc32` is zero for ECHO and rejected requests, otherwise the active transaction's declared full-payload CRC32; after successful COMMIT it is the device-computed full-payload CRC32. `payload_len` is always 20:

```text
0       4       state                 IDLE=0, RECEIVING=1, COMMITTED=2,
                                      ABORTED=3, REJECTED=4
4       4       error                 NONE=0, HEADER=1, SESSION=2, BUSY=3,
                                      RANGE=4, CRC=5, INCOMPLETE=6,
                                      TIMEOUT=7, CONFLICT=8
8       4       received_bytes        distinct accepted bytes in the target bank
12      4       first_missing_offset  first missing 4-byte word, or total_bytes
16      4       first_missing_length  contiguous missing-byte count from that offset
```

`BEGIN` and `COMMIT` require an empty payload and duplicate the same non-zero total length and full-payload CRC. `DATA` requires `total_bytes` and the transaction session to match the accepted `BEGIN`; `crc32` covers only the trailing packet payload. A duplicate `DATA` is accepted only when its already-received words equal the DDR contents. A conflicting overlap is rejected without changing DDR. `STATUS` asks for the same status payload and `ABORT` clears only the matching UDP staging transaction.

## File Map

| Path | Responsibility |
| --- | --- |
| `scripts/ps7_props.tcl:90-108,269-319,429-459` | Enable the existing GEM0 RGMII/MDIO MIO mapping without touching UART1 or PHY reset. |
| `host/tests/test_gem0_ps7.py` | Guard the reviewed PS7 GEM0/MDIO/no-reset configuration. |
| `host/siggen/udp_ddr_pp.py` | UDP header codec, response decoder, bounded-window uploader, and independent `QThread` worker. |
| `host/tests/test_udp_ddr_pp.py` | Pure host codec/client tests using a deterministic fake UDP socket. |
| `host/siggen/controller.py:110-137,492-542,661-745` | Start/observe the UDP worker and retain UART commands for status/start/stop/recovery. |
| `host/tests/test_udp_ddr_pp_controller.py` | Verify that default PP payload upload uses the UDP worker and no payload travels through `SerialWorker`. |
| `sdk/src/udp_ddr_pp.h` | Narrow firmware boundary between lwIP/UDP session handling and PP bank ownership. |
| `sdk/src/udp_ddr_pp.c` | GEM0/lwIP Raw API setup, UDP parser/replies, timeout, and polling. |
| `sdk/src/main.c:58-69,101-141,300-330,641-645,1260-1399,1464-1700,2219-2356` | PP UDP staging callbacks, cache flush, UART ownership checks, `GET NET`, initialization, and non-blocking polling. |
| `host/tests/test_udp_ddr_pp_sdk.py` | Source-level guard for protocol constants, main-loop scheduling, staging isolation, CRC32, and cache direction. |
| `sdk/ws2025/awg_bridge/src/CMakeLists.txt:12-18` | Generated application copy: link standalone app with `lwip213` after every fresh generation. |
| `scripts/udp_ddr_pp_gate.py` | M0–M3 board-gate runner and machine-readable performance/integrity report. |
| `host/tests/test_udp_ddr_pp_gate.py` | Gate argument, packet-sequence, and result-parsing tests without board hardware. |
| `README.md:111-140` | Reproducible GEM/lwIP build, network configuration, UDP loader, and M0–M3 validation instructions. |

### Task 1: Lock the GEM0 PS7 configuration and derived-HDF gate

**Files:**
- Create: `host/tests/test_gem0_ps7.py`
- Modify: `scripts/ps7_props.tcl:90-108,126-148,269-319,429-459`
- Modify: `README.md:98-127`

**Interfaces:**
- Produces a PS7 handoff with GEM0 on RGMII MIO16–27 and MDIO/MDC MIO52–53.
- Produces `XPAR_XEMACPS_0_BASEADDR` in the regenerated standalone BSP, consumed by `sdk/src/udp_ddr_pp.c`.
- Does not alter `XPAR_XUARTPS_0_BASEADDR` or UART1 MIO48–49.

- [ ] **Step 1: Write the failing configuration guard**

Create `host/tests/test_gem0_ps7.py` with assertions that read `scripts/ps7_props.tcl` as text and require exactly these reviewed values:

```python
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
PS7 = (ROOT / "scripts" / "ps7_props.tcl").read_text(encoding="utf-8")


def _mio_tree(name):
    match = re.search(rf"CONFIG\.{name} \{{([^}}]+)\}}", PS7)
    assert match, name
    return match.group(1).split("#")


def test_gem0_uses_board_rgmii_and_mdio_mios_without_ps_reset():
    required = (
        "CONFIG.PCW_ENET0_PERIPHERAL_ENABLE {1}",
        "CONFIG.PCW_EN_ENET0 {1}",
        "CONFIG.PCW_ENET0_ENET0_IO {MIO 16 .. 27}",
        "CONFIG.PCW_ENET0_GRP_MDIO_ENABLE {1}",
        "CONFIG.PCW_ENET0_GRP_MDIO_IO {MIO 52 .. 53}",
        "CONFIG.PCW_ENET0_RESET_ENABLE {0}",
        "CONFIG.PCW_ENET_RESET_ENABLE {0}",
        "CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1}",
        "CONFIG.PCW_UART1_UART1_IO {MIO 48 .. 49}",
    )
    for setting in required:
        assert setting in PS7


def test_gem0_mio_tree_preserves_board_rgmii_mdio_and_uart1_ownership():
    peripherals = _mio_tree("PCW_MIO_TREE_PERIPHERALS")
    signals = _mio_tree("PCW_MIO_TREE_SIGNALS")
    assert peripherals[16:28] == ["Ethernet 0"] * 12
    assert signals[16:28] == [
        "tx_clk", "txd[0]", "txd[1]", "txd[2]", "txd[3]", "tx_en",
        "rx_clk", "rxd[0]", "rxd[1]", "rxd[2]", "rxd[3]", "rx_dv",
    ]
    assert peripherals[52:54] == ["Ethernet 0", "Ethernet 0"]
    assert signals[52:54] == ["mdc", "mdio"]
    assert peripherals[48:50] == ["UART 1", "UART 1"]
    assert signals[48:50] == ["tx", "rx"]
```

The property assertions lock the selected MIO groups; the indexed MIO-tree assertion makes a later Vivado property export fail if it returns any RGMII/MDIO pin to GPIO or relocates UART1.

- [ ] **Step 2: Run the guard and confirm it fails against disabled GEM0**

Run:

```bash
python -m pytest host/tests/test_gem0_ps7.py -v
```

Expected: failure because GEM0/MDIO are disabled and the MIO tree currently assigns those pins as GPIO.

- [ ] **Step 3: Enable only the reviewed PS7 peripherals and MIO groups**

In `scripts/ps7_props.tcl`, set these properties in the existing list, preserving all current addresses, IO PLL settings, DDR properties, and UART1 entries:

```tcl
CONFIG.PCW_ENET0_PERIPHERAL_ENABLE {1} \
CONFIG.PCW_EN_ENET0 {1} \
CONFIG.PCW_ENET0_ENET0_IO {MIO 16 .. 27} \
CONFIG.PCW_ENET0_GRP_MDIO_ENABLE {1} \
CONFIG.PCW_ENET0_GRP_MDIO_IO {MIO 52 .. 53} \
CONFIG.PCW_ENET0_RESET_ENABLE {0} \
CONFIG.PCW_ENET_RESET_ENABLE {0} \
```

Replace the generated `PCW_MIO_TREE_PERIPHERALS` entries at MIO16–27 and MIO52–53 with `Ethernet 0`, and replace the matching `PCW_MIO_TREE_SIGNALS` slice exactly with:

```text
MIO16..27: tx_clk, txd[0], txd[1], txd[2], txd[3], tx_en,
           rx_clk, rxd[0], rxd[1], rxd[2], rxd[3], rx_dv
MIO52..53: mdc, mdio
MIO48..49: UART 1 / tx, UART 1 / rx (unchanged)
```

Do not enable `PCW_ENET0_RESET_ENABLE`, global `PCW_ENET_RESET_ENABLE`, EMIO Ethernet, ENET1, an F2P Ethernet interrupt, or any PL IP.

- [ ] **Step 4: Re-run the configuration guard**

Run:

```bash
python -m pytest host/tests/test_gem0_ps7.py -v
```

Expected: PASS.

- [ ] **Step 5: Rebuild and inspect the actual derived hardware before firmware work**

Run:

```bash
python scripts/build_bitstream.py --force
python scripts/check_bitstream.py
```

Expected: both exit with code 0; the generated HDF is new and the bitstream check recognizes `scripts/ps7_props.tcl` in its source hash. Do not create or reuse an SDK application at this stage: Task 4 creates the fresh lwIP BSP from this HDF and verifies the generated `xparameters.h` before compiling firmware.

- [ ] **Step 6: Document the hardware-only rebuild boundary**

Add a concise README note directly after the PS-side build paragraph stating that a GEM0/MDIO property change requires the forced bitstream/HDF rebuild and a new BSP, while the RTL/DMA data path does not gain Ethernet IP.

- [ ] **Step 7: Commit the reviewed configuration gate**

```bash
git add scripts/ps7_props.tcl host/tests/test_gem0_ps7.py README.md
git commit -m "feat: enable GEM0 board MIO routing"
```

### Task 2: Specify and test the host UDP wire codec

**Files:**
- Create: `host/siggen/udp_ddr_pp.py`
- Create: `host/tests/test_udp_ddr_pp.py`
- Do not modify: `host/siggen/__init__.py`

**Interfaces:**
- Produces `UDP_DDR_PP_MAGIC`, `UDP_DDR_PP_VERSION`, `MessageType`, `ReplyState`, `ReplyError`, `Packet`, `ReplyStatus`, `encode_packet()`, and `decode_packet()`.
- Consumes byte strings only; it has no Qt, serial, controller, or socket dependency in its codec functions.
- Later tasks consume `UdpDdrPpClient`, `UdpDdrPpWorker`, and the exact 32-byte header format from this module.

- [ ] **Step 1: Write golden-vector codec tests**

Create tests that assert the exact byte order, field validation, and reply-status decoding. Use this golden vector for a `DATA` packet:

```python
from host.siggen.udp_ddr_pp import (
    HEADER_SIZE, MAX_PAYLOAD_BYTES, MessageType, Packet, ReplyError,
    ReplyState, decode_packet, decode_reply_status, encode_packet,
)


def test_data_packet_is_a_32_byte_network_order_header_followed_by_payload():
    payload = b"\x10\x20\x30\x40"
    packet = Packet(
        type=MessageType.DATA, bank=1, session_id=0x10203040,
        sequence=7, offset=0x00001000, total_bytes=0x00010000,
        crc32=0xE08AB900, payload=payload,
    )
    wire = encode_packet(packet)
    assert HEADER_SIZE == 32
    assert wire.hex() == (
        "414450500103000110203040000000070000100000010000"
        "00000004e08ab90010203040"
    )
    assert decode_packet(wire) == packet


def test_codec_rejects_non_fragment_safe_or_malformed_data_packets():
    packet = Packet(MessageType.DATA, 0, 1, 0, 0, 4, 0x2144DF1C, b"\0\0\0\0")
    assert len(encode_packet(packet)) == HEADER_SIZE + 4
    for bad in (b"", b"ADPP", encode_packet(packet)[:-1]):
        try:
            decode_packet(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed packet was accepted")
    assert MAX_PAYLOAD_BYTES == 1400


def test_reply_status_is_network_order_and_exposes_missing_range():
    status = decode_reply_status(bytes.fromhex(
        "00000001000000000000057800000af000000578"
    ))
    assert status.state is ReplyState.RECEIVING
    assert status.error is ReplyError.NONE
    assert status.received_bytes == 1400
    assert status.first_missing_offset == 2800
    assert status.first_missing_length == 1400
```

Add parameterized rejection tests for wrong magic/version, unknown type, non-zero `payload_len` mismatch, invalid bank, zero session for a transaction, unaligned DATA offset/length, a `DATA` payload over 1400 bytes, non-empty BEGIN/COMMIT/ABORT, and a BEGIN/COMMIT with zero or non-four-byte-aligned `total_bytes`.

- [ ] **Step 2: Run the host codec tests and confirm import failure**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp.py -v
```

Expected: collection fails because `host.siggen.udp_ddr_pp` does not exist.

- [ ] **Step 3: Implement the codec with one canonical packet model**

In `host/siggen/udp_ddr_pp.py`, define the fixed header once and use it for both encode and decode:

```python
import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

UDP_DDR_PP_MAGIC = b"ADPP"
UDP_DDR_PP_VERSION = 1
HEADER = struct.Struct("!4sBBHIIIIII")
HEADER_SIZE = HEADER.size
MAX_PAYLOAD_BYTES = 1400
BANK_COUNT = 2
BANK_BYTES = 1 << 20


class MessageType(IntEnum):
    ECHO = 1
    BEGIN = 2
    DATA = 3
    STATUS = 4
    COMMIT = 5
    ABORT = 6
    REPLY = 0x80


@dataclass(frozen=True)
class Packet:
    type: MessageType
    bank: int
    session_id: int
    sequence: int
    offset: int
    total_bytes: int
    crc32: int
    payload: bytes


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF
```

`encode_packet()` must derive `payload_len` from `len(packet.payload)` and reject inconsistent semantic fields before `HEADER.pack`. `decode_packet()` must reject a datagram whose byte count is not exactly `HEADER_SIZE + payload_len`, then validate all message-specific invariants before constructing `Packet`. Do not use pickle, JSON, Python object serialization, or host-native-endian packing.

- [ ] **Step 4: Run the codec tests**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit the protocol contract**

```bash
git add host/siggen/udp_ddr_pp.py host/tests/test_udp_ddr_pp.py
git commit -m "feat: define UDP DDR PP wire protocol"
```

### Task 3: Implement the independent bounded-window host uploader

**Files:**
- Modify: `host/siggen/udp_ddr_pp.py`
- Modify: `host/tests/test_udp_ddr_pp.py`
- Create: `host/tests/test_udp_ddr_pp_controller.py`
- Modify: `host/siggen/controller.py:110-137,492-542,661-745,814-840,1005`
- Do not modify: `host/siggen/transport.py:11-114`

**Interfaces:**
- Produces `UdpDdrPpClient(host, port, *, timeout_s=0.15, window_size=8, socket_factory=socket.socket)` with `echo()` and `upload(bank, payload, progress=None) -> UploadResult`; `UploadResult` has `bytes_sent`, `elapsed_s`, `effective_mb_s`, `retransmits`, `payload_crc32`, `device_crc32`, and `device_error` fields.
- Produces `UdpDdrPpWorker(QThread)` with `progress(int, int)`, `completed(UploadResult)`, and `failed(str)` signals.
- `SignalGeneratorController.upload_ddr_pp_data(bank, data, bit_length=None)` starts the UDP worker by default. `upload_ddr_pp_data_serial(...)` retains the existing UART stop-and-wait implementation for explicit recovery use.

- [ ] **Step 1: Write fake-socket sliding-window tests**

Add a `FakeUdpSocket` whose `sendto()` records encoded requests and whose `recvfrom()` returns queued replies. Test all of these observable behaviors:

```python
def test_upload_opens_session_then_retransmits_only_reported_missing_packet():
    client, sock = make_client_with_fake_socket(window_size=3)
    payload = bytes(range(256)) * 12  # 3072 bytes, three DATA packets at 1400/1400/272
    result = client.upload(0, payload)
    sent = [decode_packet(data) for data, _ in sock.sent]
    data = [packet for packet in sent if packet.type is MessageType.DATA]
    assert [packet.sequence for packet in data[:3]] == [0, 1, 2]
    assert [packet.sequence for packet in data[3:]] == [1]
    assert result.bytes_sent == len(payload)
    assert result.payload_crc32 == crc32(payload)
    assert result.retransmits == 1


def test_upload_aborts_session_when_begin_data_or_commit_fails():
    client, sock = make_client_with_rejected_reply(ReplyError.BUSY)
    with pytest.raises(UdpDdrPpUploadError, match="BUSY"):
        client.upload(1, bytes(4))
    assert decode_packet(sock.sent[-1][0]).type is MessageType.ABORT
```

Also test: no more than `window_size` unacknowledged packets; out-of-order replies do not advance completion incorrectly; a timeout retransmits only packets inside the reported missing range; duplicate successful replies do not double-count progress; COMMIT only follows status with `received_bytes == len(payload)` and an empty missing range; `UploadResult` records wall time/effective MB/s/retransmits/final CRC/device error; all socket operations target `(host, port)`.

- [ ] **Step 2: Add controller isolation tests before implementation**

Build on the existing fake controller fixture pattern in `host/tests/test_stage5_m0_controller.py`. Assert that default upload creates/starts `UdpDdrPpWorker`, receives its progress/completion/failure signals, and sends no `SET DDR PP CLEAR`, `CHUNK`, or `COMMIT` line through `ctrl.worker.sent`. After success, assert the controller enqueues only `GET DDR PP\r\n` on UART to refresh the authoritative playback status. Add one explicit serial-fallback test proving `upload_ddr_pp_data_serial()` retains the current `CLEAR` → sequential `CHUNK` → `COMMIT` sequence.

- [ ] **Step 3: Run the new host tests and confirm they fail**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp.py host/tests/test_udp_ddr_pp_controller.py -v
```

Expected: failure because the client/worker and UDP controller path do not exist.

- [ ] **Step 4: Implement the client transaction state machine**

Implement `UdpDdrPpClient` with this exact request sequence:

```text
BEGIN -> DATA sliding window -> STATUS -> COMMIT
                                      \-> ABORT on terminal error
```

Generate the non-zero session ID with `secrets.randbits(32) or 1`. Divide the payload into aligned chunks no larger than 1400 bytes. The last chunk remains four-byte aligned because the public client rejects a payload whose length is not a multiple of four. Keep an `in_flight: dict[int, Packet]` keyed by sequence; do not issue a new DATA packet while `len(in_flight) == window_size`. Use each reply's `first_missing_offset`/`first_missing_length` to identify the packet offsets that require retransmission. Treat a reply with mismatched session, bank, or sequence as unrelated network traffic and ignore it. Treat any non-`NONE` device error as terminal, send one best-effort ABORT, and raise `UdpDdrPpUploadError`.

`echo()` sends an ECHO packet with bank `0xffff`, session zero, sequence zero, and no payload. It returns the decoded status only when a matching `REPLY` arrives before the timeout.

- [ ] **Step 5: Implement the worker and controller handoff**

Keep socket work out of the Qt GUI thread:

```python
class UdpDdrPpWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            result = self._client.upload(
                self._bank, self._payload,
                progress=lambda sent, total: self.progress.emit(sent, total),
            )
        except UdpDdrPpUploadError as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)
```

In `SignalGeneratorController`, store the active UDP worker separately from `_ddr_pp_upload`, reject a second UDP upload while the first worker is running, map worker signals to the existing `ddr_pp_upload_progress` signal, and request `GET DDR PP` through the serial queue only after `completed`. On failure, clear only the UDP upload state and emit the existing controller error signal; do not send UART PP payload commands. Retain `SerialWorker` unchanged and keep existing UART PP command parsing/status/start/stop paths unchanged.

- [ ] **Step 6: Run focused and existing PP host regressions**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp.py host/tests/test_udp_ddr_pp_controller.py host/tests/test_stage5_m0_protocol.py host/tests/test_stage5_m0_controller.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit the host data-plane migration**

```bash
git add host/siggen/udp_ddr_pp.py host/siggen/controller.py host/tests/test_udp_ddr_pp.py host/tests/test_udp_ddr_pp_controller.py
git commit -m "feat: upload DDR PP payloads over UDP"
```

### Task 4: Add the firmware lwIP/UDP module with an M0 echo-only stop gate

**Files:**
- Create: `sdk/src/udp_ddr_pp.h`
- Create: `sdk/src/udp_ddr_pp.c`
- Create: `host/tests/test_udp_ddr_pp_sdk.py`
- Modify: `sdk/src/main.c:58-69,2219-2356`
- Modify generated application copy after fresh HDF generation: `sdk/ws2025/awg_bridge/src/CMakeLists.txt:12-18`

**Interfaces:**
- Produces `int udp_ddr_pp_init(const udp_ddr_pp_ops_t *ops)`, `void udp_ddr_pp_poll(void)`, `int udp_ddr_pp_is_initialized(void)`, `int udp_ddr_pp_link_is_up(void)`, and `const char *udp_ddr_pp_ipv4_string(void)`.
- In this task `ops` is null and only ECHO is accepted; no received datagram may write DDR.
- Later task replaces the null callback table with PP staging callbacks without changing the public network initialization/poll API.

- [ ] **Step 1: Write the failing firmware-source gate for M0**

Create `host/tests/test_udp_ddr_pp_sdk.py` with focused source-level assertions:

```python
from pathlib import Path

ROOT = Path(__file__).parents[2]
MAIN = (ROOT / "sdk" / "src" / "main.c").read_text(encoding="utf-8")
UDP_C = (ROOT / "sdk" / "src" / "udp_ddr_pp.c").read_text(encoding="utf-8")
UDP_H = (ROOT / "sdk" / "src" / "udp_ddr_pp.h").read_text(encoding="utf-8")


def test_firmware_uses_ps_gem0_lwip_raw_udp_and_fixed_m0_endpoint():
    for token in (
        '#include "lwip/udp.h"', '#include "netif/xadapter.h"',
        "XPAR_XEMACPS_0_BASEADDR", "xemac_add", "xemacif_input",
        "sys_check_timeouts", "0x02, 0xac, 0x82, 0x00, 0x00, 0x02",
        "192, 168, 2, 2", "40100", "UDP_DDR_PP_TYPE_ECHO",
    ):
        assert token in UDP_C


def test_main_services_network_without_replacing_uart_or_pp_service():
    loop = MAIN[MAIN.index("while (1)"):]
    assert "ddr_pp_service();" in loop
    assert "ddr_service();" in loop
    assert "udp_ddr_pp_poll();" in loop
    assert loop.index("ddr_pp_service();") < loop.index("udp_ddr_pp_poll();")
    assert "XUartPs_IsReceiveData" in loop
    assert "udp_ddr_pp_init" in MAIN
    assert "udp_ddr_pp_ops_t" in UDP_H
```

- [ ] **Step 2: Run the M0 source gate and confirm it fails**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp_sdk.py -v
```

Expected: collection fails because the UDP firmware files do not exist.

- [ ] **Step 3: Define the narrow firmware boundary**

Create `sdk/src/udp_ddr_pp.h` with the following public types. Keep all addresses and PP metadata out of this header.

```c
#ifndef UDP_DDR_PP_H
#define UDP_DDR_PP_H

#include "xil_types.h"

enum udp_ddr_pp_state {
    UDP_DDR_PP_STATE_IDLE = 0,
    UDP_DDR_PP_STATE_RECEIVING = 1,
    UDP_DDR_PP_STATE_COMMITTED = 2,
    UDP_DDR_PP_STATE_ABORTED = 3,
    UDP_DDR_PP_STATE_REJECTED = 4,
};

enum udp_ddr_pp_error {
    UDP_DDR_PP_ERROR_NONE = 0,
    UDP_DDR_PP_ERROR_HEADER = 1,
    UDP_DDR_PP_ERROR_SESSION = 2,
    UDP_DDR_PP_ERROR_BUSY = 3,
    UDP_DDR_PP_ERROR_RANGE = 4,
    UDP_DDR_PP_ERROR_CRC = 5,
    UDP_DDR_PP_ERROR_INCOMPLETE = 6,
    UDP_DDR_PP_ERROR_TIMEOUT = 7,
    UDP_DDR_PP_ERROR_CONFLICT = 8,
};

typedef struct {
    u32 received_bytes;
    u32 first_missing_offset;
    u32 first_missing_length;
    u32 state;
    u32 error;
} udp_ddr_pp_status_t;

typedef struct {
    int (*begin)(u16 bank, u32 total_bytes, u32 payload_crc32);
    int (*write)(u16 bank, u32 offset, const u8 *data, u16 length);
    int (*commit)(u16 bank, u32 total_bytes, u32 payload_crc32);
    void (*abort)(u16 bank);
    void (*status)(u16 bank, udp_ddr_pp_status_t *status);
} udp_ddr_pp_ops_t;

int udp_ddr_pp_init(const udp_ddr_pp_ops_t *ops);
void udp_ddr_pp_poll(void);
int udp_ddr_pp_is_initialized(void);
int udp_ddr_pp_link_is_up(void);
const char *udp_ddr_pp_ipv4_string(void);

#endif
```

- [ ] **Step 4: Implement a Raw-API GEM0 bring-up that cannot access DDR**

In `sdk/src/udp_ddr_pp.c`, include the standalone/lwIP headers `xparameters.h`, `xil_printf.h`, `lwip/init.h`, `lwip/ip4_addr.h`, `lwip/timeouts.h`, `lwip/udp.h`, `netif/xadapter.h`, and `netif/xemacif.h`. Define the board's locally administered GEM MAC once as `static unsigned char gem0_mac[6] = {0x02, 0xac, 0x82, 0x00, 0x00, 0x02};`; do not derive it from a PHY register or reuse the host MAC. Create one static `struct netif`, call `lwip_init()`, then add GEM0 with the Xilinx Raw-API signature:

```c
if (!xemac_add(&gem0_netif, &ipaddr, &netmask, &gw, gem0_mac,
               XPAR_XEMACPS_0_BASEADDR))
    return -1;
```

Use these fixed network values:

```c
IP4_ADDR(&ipaddr, 192, 168, 2, 2);
IP4_ADDR(&netmask, 255, 255, 255, 0);
IP4_ADDR(&gw, 192, 168, 2, 10);
```

Set the netif default/up, create a UDP PCB bound to port `40100`, and register a raw UDP receive callback. The callback must use `pbuf_copy_partial` into a fixed `u8 datagram[32 + 1400]` buffer, reject every oversized or malformed pbuf, decode explicit big-endian fields, and only answer ECHO in this task. It must call `pbuf_free` exactly once along every path. It must never dereference DDR PP addresses, call an operation callback, or contain `Xil_DCache` calls during M0.

`udp_ddr_pp_poll()` must call `xemacif_input(&gem0_netif);` followed by `sys_check_timeouts();`, with no delay/sleep function. `udp_ddr_pp_link_is_up()` must return `netif_is_link_up(&gem0_netif)` only after successful initialization. Print one UART log line at initialization and a one-time link transition line in this exact family:

```text
NET GEM0 INIT OK IP 192.0.2.10 UDP 40100
NET GEM0 LINK UP
NET GEM0 LINK DOWN
```

- [ ] **Step 5: Wire initialization and polling without starving existing services**

In `sdk/src/main.c`, include `udp_ddr_pp.h`; after UART/GPIO/DMA initialization call:

```c
if (udp_ddr_pp_init(0) != 0)
    xil_printf("NET GEM0 INIT FAIL\r\n");
```

In the existing `while (1)` loop at `sdk/src/main.c:2324-2356`, retain the two pre-existing service calls and insert one non-blocking network poll between them and the UART FIFO-drain loop:

```c
#if DDR_HW && DMA_HW
    ddr_pp_service();
    ddr_service();
#endif
    udp_ddr_pp_poll();
    while (XUartPs_IsReceiveData(Uart.Config.BaseAddress)) {
```

Do not replace UART polling with an interrupt or block waiting for Ethernet data.

- [ ] **Step 6: Build against the fresh lwIP BSP and run static tests**

Create a fresh generated application from the Task 1 HDF (the destination workspace must not contain a stale `awg_bridge` application):

```bash
cli-anything-vivado sdk app new awg_bridge --hw awg/host_bd.hdf --src sdk/src --workspace sdk/ws2025 --proc ps7_cortexa9_0
```

Before building the generated copy, inspect `sdk/ws2025/awg_bridge_platform/ps7_cortexa9_0/standalone_ps7_cortexa9_0/bsp/include/xparameters.h` and require both:

```c
#define XPAR_XEMACPS_0_BASEADDR 0xE000B000U
#define XPAR_XUARTPS_0_BASEADDR 0xE0001000U
```

In the Vitis 2025.2 platform for the same `standalone_ps7_cortexa9_0` domain, create the `lwip213` software-library component with the Raw API and XEmacPs/GEM0 transport, with no RTOS or socket API. Confirm the component output is named `lwip213`, then add it to the generated application dependency list:

```cmake
collect(PROJECT_LIB_DEPS xilstandalone;xiltimer;lwip213)
```

Copy `sdk/src/main.c`, `sdk/src/udp_ddr_pp.c`, and `sdk/src/udp_ddr_pp.h` to the regenerated application `src/`; run:

```bash
python scripts/patch_vitis_linker.py sdk/ws2025/awg_bridge
cli-anything-vivado sdk build awg_bridge --workspace sdk/ws2025
python -m pytest host/tests/test_udp_ddr_pp_sdk.py host/tests/test_gem0_ps7.py -v
```

Expected: application links with `lwip213`; static tests pass. Keep the design on Raw lwIP; do not replace it with sockets or an RTOS.

- [ ] **Step 7: Run M0 on the board before adding DDR callbacks**

Program the validated bitstream and run the fresh ELF. Configure the host adapter as `192.0.2.10/24`. Verify in this order:

```bash
bash scripts/program_board.sh
cli-anything-vivado sdk run --elf sdk/ws2025/awg_bridge/build/awg_bridge.elf --hw-platform sdk/ws2025/awg_bridge_platform/hw/sdt
ping 192.0.2.10
```

Use a temporary one-shot Python REPL command that imports `UdpDdrPpClient("192.0.2.10", 40100)` and calls `echo()`. Require a matching UDP `REPLY`, an ARP neighbor for `192.0.2.10`, UART output containing `NET GEM0 INIT OK`, and an observed `NET GEM0 LINK UP`. Do not proceed to any DDR packet, write callback, or DMA test if any M0 check fails; diagnose MIO routing, PHY reset topology, MDIO, PS initialization, or lwIP/BSP configuration first.

- [ ] **Step 8: Commit the M0-only network foundation**

```bash
git add sdk/src/udp_ddr_pp.h sdk/src/udp_ddr_pp.c sdk/src/main.c host/tests/test_udp_ddr_pp_sdk.py
git commit -m "feat: initialize GEM0 UDP echo service"
```

### Task 5: Implement isolated UDP staging, integrity, and cache ownership

**Files:**
- Modify: `sdk/src/main.c:101-141,300-330,641-645,1260-1399,1618-1700,2219-2356`
- Modify: `sdk/src/udp_ddr_pp.c`
- Modify: `sdk/src/udp_ddr_pp.h`
- Modify: `host/tests/test_udp_ddr_pp_sdk.py`
- Modify: `host/tests/test_stage5_m0_sdk.py`

**Interfaces:**
- `main.c` supplies the `udp_ddr_pp_ops_t` callbacks to `udp_ddr_pp_init(&ops)`.
- `udp_ddr_pp.c` validates wire/session state then calls callbacks; it never knows DDR addresses, DMA register values, or PP-valid arrays.
- Existing UART PP commands continue to use CRC-16 and become explicitly owned by `DDR_PP_STAGE_UART`; UDP uses `DDR_PP_STAGE_UDP` and CRC-32.

- [ ] **Step 1: Add failing staging-isolation tests**

Extend `host/tests/test_udp_ddr_pp_sdk.py` with assertions for the exact safety boundaries:

```python
def test_udp_staging_is_owned_per_bank_and_never_uses_uart_cpu_sync_helper():
    for token in (
        "DDR_PP_STAGE_UART", "DDR_PP_STAGE_UDP", "ddr_pp_stage_owner[2]",
        "ddr_pp_stage_expected_bytes[2]", "ddr_pp_stage_received_bytes[2]",
        "ddr_pp_payload_crc32[2]", "ddr_pp_udp_begin", "ddr_pp_udp_write",
        "ddr_pp_udp_commit", "ddr_pp_udp_abort", "ddr_pp_flush_cpu_write_range",
    ):
        assert token in MAIN
    udp_commit = MAIN[MAIN.index("static int ddr_pp_udp_commit"):]
    assert "Xil_DCacheFlushRange" in udp_commit
    assert "Xil_DCacheInvalidateRange" not in udp_commit


def test_udp_decoder_rejects_bad_session_range_crc_and_timeout_before_commit():
    for token in (
        "UDP_DDR_PP_ERROR_SESSION", "UDP_DDR_PP_ERROR_RANGE",
        "UDP_DDR_PP_ERROR_CRC", "UDP_DDR_PP_ERROR_TIMEOUT",
        "udp_ddr_pp_crc32", "UDP_DDR_PP_SESSION_TIMEOUT",
        "UDP_DDR_PP_TYPE_BEGIN", "UDP_DDR_PP_TYPE_DATA",
        "UDP_DDR_PP_TYPE_COMMIT", "UDP_DDR_PP_TYPE_ABORT",
    ):
        assert token in UDP_C
```

Extend `host/tests/test_stage5_m0_sdk.py` so its UART PP command/lifecycle assertions still pass, and add a check that `ddr_pp_stage_owner` protects `SET DDR PP CLEAR`, `CHUNK`, and `COMMIT` from a UDP-owned stage.

- [ ] **Step 2: Run the staging tests and confirm failure**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp_sdk.py host/tests/test_stage5_m0_sdk.py -v
```

Expected: failure because the PP staging owner, UDP callbacks, CRC32, and CPU-write-only cache flush do not exist.

- [ ] **Step 3: Make PP staging ownership explicit in `main.c`**

Near the existing PP state at `sdk/src/main.c:300-330`, add only the metadata needed for a non-contiguous UDP stage:

```c
enum ddr_pp_stage_owner {
    DDR_PP_STAGE_NONE = 0,
    DDR_PP_STAGE_UART,
    DDR_PP_STAGE_UDP,
};

static u8 ddr_pp_stage_owner[2];
static u32 ddr_pp_stage_expected_bytes[2];
static u32 ddr_pp_stage_received_bytes[2];
static u32 ddr_pp_payload_crc32[2];
```

Keep the existing `ddr_pp_stage_seen`, `ddr_pp_stage_bytes`, `ddr_pp_bit_length`, `ddr_pp_crc16`, and `ddr_pp_valid` arrays. Add helpers that clear one target bank's stage metadata, count distinct received four-byte words, calculate the first missing contiguous range, and verify all expected words. Do not clear or rewrite the other bank's bitmap, length, CRC, or validity in any per-bank UDP helper.

For existing UART `SET DDR PP CLEAR`, reject the command if either bank has `DDR_PP_STAGE_UDP`; otherwise set both owners to `DDR_PP_STAGE_UART` as it opens their existing sequential stages. Require `DDR_PP_STAGE_UART` in UART `CHUNK` and `COMMIT`. This prevents a UART recovery command from mixing metadata with a live UDP transaction.

- [ ] **Step 4: Add CPU-write-only flush and CRC32 helpers**

Keep `ddr_sync_range()` unchanged for its existing UART/DDRx use. Add a separate helper:

```c
static void ddr_pp_flush_cpu_write_range(UINTPTR addr, u32 length)
{
    Xil_DCacheFlushRange(addr, length);
}
```

Implement one IEEE CRC-32 helper using the reflected polynomial `0xedb88320u`, initial value `0xffffffffu`, and final xor `0xffffffffu`. Use it for UDP DATA validation in `udp_ddr_pp.c` and full-bank commit validation in `main.c`. Do not use CRC-16 as a substitute; continue populating `ddr_pp_crc16[bank]` at successful UDP commit so legacy UART status remains meaningful.

- [ ] **Step 5: Implement the PP callbacks in `main.c`**

`ddr_pp_udp_begin(bank, total_bytes, payload_crc32)` must reject all of: invalid bank, zero/non-four-byte-aligned/oversized total, zero CRC is allowed only when it is the actual IEEE CRC32 value, an open UART stage, ordinary DDR upload, active DMA, active PP playback, active manual playback, nonzero digital modulation mode, or `play_blocks_config()`. On success, clear only the target bank's staging bitmap/metadata, set its owner to `DDR_PP_STAGE_UDP`, set expected length/full CRC32, and set `ddr_pp_valid[bank] = 0` before accepting data.

`ddr_pp_udp_write(bank, offset, data, length)` must require UDP ownership; four-byte alignment; `1 <= length <= 1400`; `length % 4 == 0`; and `offset + length <= expected_bytes`. First compare every previously received four-byte word with its existing DDR bytes. If any differs, return the conflict error without writing any new byte. Then write unreceived words, mark their bitmap bits, and increment `ddr_pp_stage_received_bytes[bank]` exactly once per new word. This makes duplicate valid DATA packets idempotent and conflicting overlaps harmless.

`ddr_pp_udp_commit(bank, total_bytes, payload_crc32)` must require UDP ownership, the BEGIN length/CRC to match, `received_bytes == total_bytes`, and a full bitmap. Calculate full-bank CRC32 at `ddr_pp_bank_addr(bank)` and reject mismatch. On success: flush the target range with `ddr_pp_flush_cpu_write_range`; populate the target bit-length/CRC16/CRC32 metadata; close its stage and set its owner to `DDR_PP_STAGE_NONE`; then set `ddr_pp_valid[bank] = 1` as the final PP metadata mutation. A failed commit leaves the target invalid and the other bank unchanged.

`ddr_pp_udp_abort(bank)` clears only target UDP staging metadata and ownership. It does not erase DDR memory and it never restores or changes the other bank.

`ddr_pp_udp_status(bank, status)` reports the dynamic distinct-byte count and first missing range. It must not claim `COMMITTED` unless the target valid metadata was set by a successful UDP commit.

- [ ] **Step 6: Turn the Raw UDP receiver into the transaction engine**

Add message-specific validation to `udp_ddr_pp.c`:

```text
ECHO:    no payload, bank=0xffff, session=0; reply without callbacks.
BEGIN:   no payload, bank 0/1, nonzero session, aligned total; call begin once.
DATA:    matching active session/bank/total, aligned offset/length, <=1400,
         CRC32(payload) matches header; call write, then reply with status.
STATUS:  matching session/bank, no payload; reply with status.
COMMIT:  matching session/bank/total/full CRC, no payload; call commit and reply.
ABORT:   matching session/bank, no payload; call abort, reply ABORTED, close session.
```

Maintain exactly one active session in `udp_ddr_pp.c`; accept duplicate matching BEGIN as a status reply without clearing collected data. Store its last-activity tick. In `udp_ddr_pp_poll()`, after `xemacif_input`, abort the active bank and clear the session when elapsed ticks exceed `UDP_DDR_PP_SESSION_TIMEOUT` (five seconds). Reject a new BEGIN while another UDP session is open with `BUSY`; never route a foreign session's packet to `main.c` callbacks. Build replies with explicit big-endian writes and `udp_sendto` to the sender address/port.

- [ ] **Step 7: Expose network state on UART and initialize real callbacks**

Add a UART `GET NET` branch beside existing `GET DDR`/`GET DDR PP` handling. Its stable format is:

```text
NET GEM0 INIT <0|1> LINK <UP|DOWN> IP 192.0.2.10 UDP 40100
```

At startup, pass the real static callback table rather than null:

```c
static const udp_ddr_pp_ops_t ddr_pp_udp_ops = {
    ddr_pp_udp_begin,
    ddr_pp_udp_write,
    ddr_pp_udp_commit,
    ddr_pp_udp_abort,
    ddr_pp_udp_status,
};
```

Call `udp_ddr_pp_init(&ddr_pp_udp_ops)` exactly once after DDR/DMA state initialization but before the main loop. Keep all `SET DDR PP ...`, `GET DDR PP`, `SET DDR ...`, `GET DDR`, and UART receive behavior available.

- [ ] **Step 8: Run firmware/static and complete host regressions**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp_sdk.py host/tests/test_stage5_m0_sdk.py host/tests/test_stage5_m0_protocol.py host/tests/test_stage5_m0_controller.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit the safe DDR receiver**

```bash
git add sdk/src/main.c sdk/src/udp_ddr_pp.c sdk/src/udp_ddr_pp.h host/tests/test_udp_ddr_pp_sdk.py host/tests/test_stage5_m0_sdk.py
git commit -m "feat: stage DDR PP banks from UDP"
```

### Task 6: Recreate, link, and load the complete GEM/lwIP application

**Files:**
- Verify canonical source files: `sdk/src/main.c`, `sdk/src/udp_ddr_pp.c`, `sdk/src/udp_ddr_pp.h`
- Modify generated copy only after fresh generation: `sdk/ws2025/awg_bridge/src/CMakeLists.txt`
- Modify generated copy only after fresh generation: `sdk/ws2025/awg_bridge/src/main.c`, `udp_ddr_pp.c`, `udp_ddr_pp.h`
- Do not commit: `sdk/ws2025/**`

**Interfaces:**
- Consumes the Task 1 HDF and the Task 5 source API.
- Produces a fresh ELF with `.text` in OCM and `.data`, `.bss`, heap, and stack in DDR, linked with raw lwIP/GEM support.

- [ ] **Step 1: Write an HDF/BSP presence check before loading hardware**

Add to `host/tests/test_udp_ddr_pp_sdk.py` a test that treats generated files as local verification inputs (not git source) and skips only when the fresh workspace is absent. When present, assert:

```python
assert "XPAR_XEMACPS_0_BASEADDR" in xparameters_text
assert "lwip213" in cmake_text
assert "ps7_ram_0_memory_0" in linker_text
assert ".data" in linker_text and "ps7_ddr_0_memory_0" in linker_text
```

The test must fail, not skip, when `sdk/ws2025/awg_bridge` exists but contains an old HDF/BSP without GEM0 or lwIP.

- [ ] **Step 2: Run the check and confirm the pre-GEM workspace fails or is absent**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp_sdk.py -v
```

Expected: either a documented skip because no workspace exists, or failure because the old generated BSP lacks GEM0/lwIP.

- [ ] **Step 3: Generate from the forced-rebuild HDF and apply source changes**

Replace the generated application `src/main.c`, `src/udp_ddr_pp.c`, and `src/udp_ddr_pp.h` with the canonical Task 5 files, preserve `lwip213` in the generated `CMakeLists.txt`, then rerun the established linker patch and complete application build:

```bash
python scripts/patch_vitis_linker.py sdk/ws2025/awg_bridge
cli-anything-vivado sdk build awg_bridge --workspace sdk/ws2025
```

Inspect the build link output: it must link `lwip213`, and no undefined `xemac_add`, `xemacif_input`, `udp_new`, or `udp_sendto` symbol may remain.

- [ ] **Step 4: Verify OCM/DDR placement and load the ELF**

Check the generated `lscript.ld` after the patch: `.text` targets `ps7_ram_0_memory_0`, while `.data`, `.bss`, heap, and stack target `ps7_ddr_0_memory_0`. Do not move lwIP buffers, PP bitmaps, or network data structures into OCM merely to satisfy the text-placement policy.

Run:

```bash
bash scripts/program_board.sh
cli-anything-vivado sdk run --elf sdk/ws2025/awg_bridge/build/awg_bridge.elf --hw-platform sdk/ws2025/awg_bridge_platform/hw/sdt
```

On UART, require the existing bridge banner plus `NET GEM0 INIT OK IP 192.0.2.10 UDP 40100`. Query `GET NET` and require `INIT 1` before any upload gate.

- [ ] **Step 5: Run the source and generated-workspace checks**

Run:

```bash
python -m pytest host/tests/test_gem0_ps7.py host/tests/test_udp_ddr_pp_sdk.py -v
python scripts/check_bitstream.py
```

Expected: all checks pass.

- [ ] **Step 6: Commit only source/configuration changes**

```bash
git status --short
git add sdk/src/main.c sdk/src/udp_ddr_pp.c sdk/src/udp_ddr_pp.h host/tests/test_udp_ddr_pp_sdk.py
git commit -m "build: link bridge with standalone lwip"
```

Do not add `sdk/ws2025/`, `awg/`, `.Xil/`, generated HDF, ELF, bitstream, or build logs.

### Task 7: Create automated M0–M2 UDP board gates

**Files:**
- Create: `scripts/udp_ddr_pp_gate.py`
- Create: `host/tests/test_udp_ddr_pp_gate.py`
- Modify: `host/siggen/udp_ddr_pp.py`
- Modify: `README.md:129-140`

**Interfaces:**
- Produces `python scripts/udp_ddr_pp_gate.py m0|m1|m2 --host 192.0.2.10 --port 40100 [--serial COM11]`.
- Consumes `UdpDdrPpClient` and raw `encode_packet()` where deliberately malformed/test-order packets are required.
- Prints one final `M0 PASS`, `M1 PASS`, or `M2 PASS` line only after every assertion for that gate succeeds.

- [ ] **Step 1: Write offline gate tests**

Create a fake-client/fake-clock test suite that verifies the command parser and exact acceptance criteria:

```python
def test_m0_requires_arp_echo_and_uart_net_status():
    result = run_m0(fake_client, fake_arp, fake_serial)
    assert result["arp"] == "192.0.2.10"
    assert result["echo"].error is ReplyError.NONE
    assert result["net"]["init"] == 1
    assert result["net"]["link"] == "UP"


def test_m2_reports_effective_rate_and_retransmits():
    report = make_m2_report(bytes_sent=1 << 20, elapsed_s=0.2,
                            retransmits=3, payload_crc32=0x12345678,
                            device_error=0)
    assert report["effective_mb_s"] == pytest.approx(5.0)
    assert report["passes_rate_gate"] is True
```

Test M1's generated packet schedule includes: a duplicate DATA, an out-of-order first pair, one deliberately withheld packet followed by `STATUS`, one bad per-packet CRC and one out-of-range offset created by mutating bytes 16–19 or 28–31 of an otherwise valid `bytearray(encode_packet(packet))`, `ABORT`, and a timeout created by advancing the fake clock past five seconds. This preserves the production codec's CRC/range validation while giving the gate precise malformed datagrams. Assert that a saved valid-bank status snapshot is unchanged after each negative case.

- [ ] **Step 2: Run the gate tests and confirm import failure**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp_gate.py -v
```

Expected: collection fails because `scripts.udp_ddr_pp_gate` does not exist.

- [ ] **Step 3: Implement M0 as a strict network-only stop gate**

`m0` must:

1. send ECHO and require a matching reply;
2. inspect the host neighbor table for `192.0.2.10` using a platform-specific, read-only command (`arp -a 192.0.2.10` on Windows, `ip neigh show 192.0.2.10` on Linux) and require a non-empty hardware address;
3. when `--serial` is supplied, send `GET NET` and require `INIT 1`, `LINK UP`, `IP 192.0.2.10`, and `UDP 40100`;
4. print the parsed facts then `M0 PASS`.

M0 must not send BEGIN, DATA, COMMIT, ABORT, or PP playback commands.

- [ ] **Step 4: Implement M1 integrity/negative-path coverage**

`m1` creates a deterministic 64 KiB payload with:

```python
payload = bytes((index * 29 + 17) & 0xFF for index in range(64 * 1024))
```

For the positive transaction, send BEGIN, DATA out of order, one byte-identical duplicate, STATUS after intentionally withholding one packet, then the missing packet and COMMIT. Require the reply final status to be `COMMITTED`, zero device error, `received_bytes == 65536`, and the same IEEE CRC32 on host and device reply. For each negative case, snapshot the non-target valid-bank metadata via UART `GET DDR PP` if `--serial` is supplied, then verify it is byte-for-byte unchanged after the rejected request/ABORT/timeout. Print the target bank, host CRC32, device CRC32, retransmits, and every negative case outcome before `M1 PASS`.

- [ ] **Step 5: Implement M2 performance measurement**

`m2` uploads exactly one 1 MiB deterministic bank through `UdpDdrPpClient.upload()`. Record monotonic start/end time around the transaction and print:

```text
M2 BYTES 1048576 ELAPSED_S <seconds> EFFECTIVE_MB_S <rate>
M2 RETRANSMITS <count> HOST_CRC32 <8hex> DEVICE_CRC32 <8hex> ERROR <code>
```

Fail unless successful commit status reports the expected bytes/CRC, error zero, and `effective_mb_s >= 3.0`. Do not average retries or count UDP headers in the effective payload rate.

- [ ] **Step 6: Run host tests and M0–M2 on hardware**

Run offline first:

```bash
python -m pytest host/tests/test_udp_ddr_pp.py host/tests/test_udp_ddr_pp_gate.py -v
```

Then, with the fresh bitstream/ELF loaded and host NIC set to `192.0.2.10/24`, run sequentially:

```bash
python scripts/udp_ddr_pp_gate.py m0 --host 192.0.2.10 --port 40100 --serial COM11
python scripts/udp_ddr_pp_gate.py m1 --host 192.0.2.10 --port 40100 --serial COM11 --bank 0
python scripts/udp_ddr_pp_gate.py m2 --host 192.0.2.10 --port 40100 --bank 1
```

Do not start M1 until M0 passes; do not start M2 until M1 passes. Archive the printed M2 metrics with the board test record.

- [ ] **Step 7: Document and commit the gate runner**

Add these exact invocation patterns and their stop conditions to README. Then commit:

```bash
git add scripts/udp_ddr_pp_gate.py host/tests/test_udp_ddr_pp_gate.py host/siggen/udp_ddr_pp.py README.md
git commit -m "test: add UDP DDR PP validation gates"
```

### Task 8: Run M3 playback regression and preserve UART recovery

**Files:**
- Modify: `scripts/udp_ddr_pp_gate.py`
- Modify: `host/tests/test_udp_ddr_pp_gate.py`
- Modify: `README.md:129-140`
- Verify only: `scripts/test_bridge.py`, `scripts/ddr_pp_timeline_gate.py`, `scripts/scope_verify.py`

**Interfaces:**
- Produces `udp_ddr_pp_gate.py m3` that uploads/validates both banks over UDP, then drives existing PP playback exclusively through the UART command interface.
- Consumes existing `GET DDR PP`, `SET DDR PP START`, `SET DDR PP STOP`, `scripts/test_bridge.py`, and `scripts/ddr_pp_timeline_gate.py` behavior without changing their wire grammar.

- [ ] **Step 1: Write the M3 host gate test**

Add a fake serial session test asserting that M3, after two successful UDP uploads, sends this control sequence and no UART payload chunks:

```python
assert serial.sent == [
    "GET DDR PP\r\n",
    "SET DDR PP START\r\n",
    "GET DDR PP\r\n",
    "GET DDR PP\r\n",
]
assert not any("CHUNK" in line or "COMMIT" in line for line in serial.sent)
```

Feed status lines for bank 0 then bank 1 with `STATE RUNNING`/`DRAIN`, followed by `ACTIVE 0 STATE IDLE ERROR 00`; require the gate to reject any nonzero error, missing bank transition, or final non-IDLE state.

- [ ] **Step 2: Run the test and confirm M3 is absent**

Run:

```bash
python -m pytest host/tests/test_udp_ddr_pp_gate.py -v
```

Expected: M3 test fails because the command is not implemented.

- [ ] **Step 3: Implement M3 using the existing playback control plane**

Have M3 upload deterministic full 1 MiB payloads to bank 0 and bank 1 using the UDP client and validate both commit replies. Query UART `GET DDR PP` and require both banks valid with expected length/CRC metadata. Send UART `SET DDR PP START`, poll UART `GET DDR PP`, and require playback to observe bank 0 and then bank 1 with no error before the final IDLE state. On timeout/error, send `SET DDR PP STOP` once and fail the gate.

Do not add a UDP playback-start message, do not write either bank while playback is active, and do not permit M3 to turn into an active-bank reload test.

- [ ] **Step 4: Run full board regression in dependency order**

After M0–M2 remain green, run:

```bash
python scripts/udp_ddr_pp_gate.py m3 --host 192.0.2.10 --port 40100 --serial COM11
python scripts/test_bridge.py COM11
python scripts/ddr_pp_timeline_gate.py COM11
python scripts/scope_verify.py COM11
```

Expected: M3 sees both banks and no PP/DMA/FIFO error; bridge protocol regression passes; DDR PP timeline gate passes; scope regression passes. Treat a PP/DMA/FIFO failure as a blocker even if the UDP transfer CRC was correct.

- [ ] **Step 5: Run the complete host suite**

Run:

```bash
python -m pytest host/tests/ -v
```

Expected: all host tests pass, including the existing serial PP, ordinary DDR, DMA, BRAM USER, PRBS7, and protocol tests.

- [ ] **Step 6: Finish documentation and commit**

Document the M3 command, report fields, explicit UART recovery behavior, and the non-goal of playback-time reload. Then commit:

```bash
git add scripts/udp_ddr_pp_gate.py host/tests/test_udp_ddr_pp_gate.py README.md
git commit -m "test: regress dual-bank playback after UDP upload"
```

## Final Acceptance Checklist

- [ ] Task 1 has a checked-in PS7 configuration guard; forced bitstream/HDF rebuild and bitstream check pass.
- [ ] Fresh BSP exposes both GEM0 and UART1, links Raw lwIP, and preserves OCM `.text` plus DDR data/heap/stack placement.
- [ ] M0 proves ARP, link, UART `GET NET`, and UDP ECHO before any DDR receiver is exercised.
- [ ] M1 proves 64 KiB integrity, duplicate/out-of-order/loss recovery, bad CRC/range rejection, ABORT, timeout, and unchanged non-target bank metadata.
- [ ] M2 transfers 1 MiB at at least 3 MB/s and records elapsed time, effective MB/s, retransmits, CRC32, and device error.
- [ ] M3 plays two UDP-committed banks through the unchanged DMA/FIFO path without an underflow, DMA error, or PP error, and all UART/DMA/BRAM/PRBS/scope regressions pass.
- [ ] No implementation permits upload during playback or a playback-time non-active-bank reload.
- [ ] `SerialWorker` remains serial-only; UART remains usable for `GET NET`, status, start/stop, diagnostics, and recovery.
- [ ] `sdk/ws*/`, generated hardware, bitstreams, and ELF files are absent from staged changes.

## Plan Self-Review

**Spec coverage:** Tasks 1 and 6 cover GEM0/MDIO configuration, derived-HDF rebuild, BSP/lwIP inclusion, and OCM/DDR linker placement. Tasks 2–3 define the fixed network-order protocol and bounded host window without using `SerialWorker`. Tasks 4–5 cover Raw lwIP initialization, M0 echo, packet validation, duplicate/idempotent writes, per-bank ownership, CRC32, timeout/abort, and cache flush direction. Tasks 7–8 provide all M0–M3 hardware stop gates and retain UART playback/control regression. The plan expressly excludes PL Ethernet and runtime bank refill.

**Placeholder scan:** The plan defines concrete header fields, packet values, functions, files, tests, commands, response formats, thresholds, and commits. No task delegates unspecified validation or leaves an implementation decision unnamed.

**Type consistency:** Host `Packet` uses the same ten header fields as firmware byte codec; both use 32-byte header, 1400-byte payload maximum, two 1 MiB banks, big-endian encoding, and IEEE CRC32. Firmware `udp_ddr_pp_ops_t` owns only staging callbacks; the network module remains independent of DDR/DMA details. The host worker is independent from `SerialWorker`, while controller UART status refresh uses established commands.
