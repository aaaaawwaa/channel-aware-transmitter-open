"""Connection + channel state model + command flow.
UI-independent; injectable fake worker for unit testing.
"""
from collections import deque
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .protocol import (set_cmd, get_cmd, parse_response, parse_status,
                       base_cmd, phase_cmd, phaselink_cmd, harm_cmd, mod_cmd,
                       digmod_cmd, digmod_symrate_cmd, digmod_filter_cmd,
                       digmod_rolloff_cmd, digmod_seed_cmd, digmod_reset_cmd,
                       digmod_source_cmd, user_clear_cmd, user_chunk_cmd,
                       user_commit_cmd, user_loop_cmd, get_digmod_cmd,
                       parse_digmod_status, parse_userbuf_status, crc16_ccitt,
                       ddr_clear_cmd, ddr_chunk_cmd, ddr_commit_cmd, ddr_abort_cmd,
                       ddr_start_cmd, ddr_stop_cmd, get_ddr_cmd, parse_ddr_status,
                       ddr_pp_clear_cmd, ddr_pp_chunk_cmd, ddr_pp_commit_cmd,
                       ddr_pp_start_cmd, ddr_pp_stop_cmd, get_ddr_pp_cmd,
                       parse_ddr_pp_status, DDR_PP_BANK_BYTES,
                       DDR_PP_MAX_BITS,
                       DDR_BUFFER_BYTES,
                       play_mode_cmd, play_repeat_cmd, play_trigger_cmd,
                       play_arm_cmd, play_start_cmd, play_stop_cmd, play_clear_cmd,
                       get_play_cmd, parse_play_status,
                       chan_enable_cmd, chan_noise_cmd, chan_tap_cmd,
                       chan_commit_cmd, parse_chan_status)
from .udp_ddr_pp import DEFAULT_HOST, DEFAULT_PORT, UdpDdrPpClient, UdpDdrPpWorker

TIMEOUT_MS = 1000


@dataclass
class ChannelState:
    wave: str = "SINE"
    freq_hz: int = 1000
    ampl_pct: int = 50
    pulse_duty: int = 50
    base_pct: int = 100
    phase_0_01: int = 0
    phaselink: bool = False
    harms: list = field(default_factory=lambda: [(0, 0)] * 4)
    mode: str = "OFF"
    mwave: str = "SINE"
    mfreq_01: int = 10
    am_depth: int = 100
    fm_dev: int = 0
    pm_dev: int = 0


@dataclass
class DigitalModState:
    mode: str = "OFF"
    source: str = "PRBS7"
    filter: str = "RECT"
    rolloff: str = "0.35"
    symrate_hz: int = 10_000
    seed: int = 127
    running: bool = False


@dataclass
class _Transaction:
    command: str
    response: str
    ch: Optional[int] = None
    field: Optional[str] = None
    value: object = None
    initializing: bool = False


@dataclass
class _UserUpload:
    data: bytes
    bit_length: int
    loop: bool
    chunk_index: int = 0


@dataclass
class _DdrUpload:
    data: bytes
    bit_length: int
    chunk_index: int = 0


@dataclass
class _DdrPpUpload:
    bank: int
    data: bytes
    bit_length: int
    chunk_index: int = 0


_DIGMOD_FIELDS = {
    "digmod", "digmod_source", "digmod_symrate", "digmod_filter", "digmod_rolloff",
    "digmod_seed", "digmod_reset",
}


class Controller(QObject):
    connected = Signal()
    disconnected = Signal(str)
    state_changed = Signal(int)
    digmod_changed = Signal(str)
    user_upload_progress = Signal(int, int)
    userbuf_changed = Signal(dict)
    ddr_upload_progress = Signal(int, int)
    ddr_changed = Signal(dict)
    ddr_pp_upload_progress = Signal(int, int)
    ddr_pp_changed = Signal(dict)
    ddr_pp_upload_finished = Signal(bool)
    play_changed = Signal(dict)
    play_capability_changed = Signal(bool)
    chan_status_changed = Signal(int, dict)
    log = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.state = {0: ChannelState(), 1: ChannelState()}
        self.digmod = DigitalModState()
        self.userbuf = None
        self.ddr = None
        self.ddr_pp = None
        self.play = {
            "state": "IDLE",
            "frame_done": False,
            "underflow": False,
            "trigger_seen": False,
            "stop_pending": False,
            "error_valid": False,
            "repeat_remaining": 0,
            "error_code": 0,
        }
        self.play_supported = False
        self._user_upload = None
        self._ddr_upload = None
        self._ddr_pp_upload = None
        self._ddr_pp_udp_worker = None
        self.udp_pp_host = DEFAULT_HOST
        self.udp_pp_port = DEFAULT_PORT
        self.udp_pp_client_factory = UdpDdrPpClient
        self.udp_pp_worker_factory = UdpDdrPpWorker
        self._queue = deque()
        self._active = None
        self._pending = None
        self._ready = False
        self._initializing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    @property
    def ready(self) -> bool:
        return self._ready

    def _clear_transactions(self) -> None:
        self._queue.clear()
        self._active = None
        self._pending = None
        self._timer.stop()

    def _sync_pending_view(self) -> None:
        tx = self._active
        if tx is None:
            self._pending = None
        elif tx.response == "ACK":
            self._pending = (tx.ch, tx.field, tx.value)
        elif tx.response == "CHAN":
            self._pending = (tx.ch, "chan_status", None)
        elif tx.response.startswith("CH"):
            self._pending = (tx.ch, "status", None)
        elif tx.response == "DIGMOD":
            self._pending = (None, "digmod_status", None)
        elif tx.response == "PLAY":
            self._pending = (None, "play_status", None)
        elif tx.response == "DDR":
            self._pending = (None, "ddr_status", None)
        elif tx.response == "DDR_PP":
            self._pending = (None, "ddr_pp_status", None)
        else:
            self._pending = (None, "userbuf_status", None)

    def _enqueue(self, tx: _Transaction, front: bool = False) -> None:
        if front:
            self._queue.appendleft(tx)
        else:
            self._queue.append(tx)
        self._pump()

    def _pump(self) -> None:
        if self.worker is None or self._active is not None or not self._queue:
            return
        self._active = self._queue.popleft()
        self._sync_pending_view()
        try:
            self.worker.send(self._active.command + "\r\n")
        except Exception as exc:
            self._on_lost(str(exc))
            return
        self._timer.start(TIMEOUT_MS)
        self.log.emit(f">> {self._active.command}", "info")

    def _new_ack(self, ch, field, value, command) -> _Transaction:
        return _Transaction(command, "ACK", ch, field, value)

    def _new_status(self, kind, ch=None, initializing=False):
        if kind == "DIGMOD":
            command = get_digmod_cmd()
        elif kind == "USERBUF":
            command = "GET USERBUF"
        elif kind == "PLAY":
            command = get_play_cmd()
        elif kind == "DDR":
            command = get_ddr_cmd()
        elif kind == "DDR_PP":
            command = get_ddr_pp_cmd()
        else:
            command = get_cmd(ch + 1)
        return _Transaction(command, kind, ch, initializing=initializing)

    def _new_chan_status(self, channel) -> _Transaction:
        if isinstance(channel, bool) or not isinstance(channel, int) or channel not in (1, 2):
            raise ValueError(f"bad channel: {channel!r}")
        return _Transaction(f"GET CH{channel} CHAN", "CHAN", channel - 1)

    # ---- connection ----
    def connect_serial(self, port: str, baud: int = 115200) -> Optional[str]:
        from .transport import SerialWorker
        self._clear_transactions()
        self._ready = False
        self._initializing = True
        self.play_supported = False
        self.play_capability_changed.emit(False)
        self.worker = SerialWorker(port, baud, self)
        err = self.worker.open()
        if err:
            self.worker = None
            self._initializing = False
            return err
        self.worker.line_received.connect(self._on_line)
        self.worker.connection_lost.connect(self._on_lost)
        self.worker.start()
        self._enqueue(self._new_status("CH1", 0, initializing=True))
        self._enqueue(self._new_status("CH2", 1, initializing=True))
        self._enqueue(self._new_status("DIGMOD", initializing=True))
        self._enqueue(self._new_status("DDR_PP"))
        self.log.emit(f"connecting {port} @ {baud}", "info")
        return None

    def disconnect(self) -> None:
        worker = self.worker
        self.worker = None
        self._ready = False
        self._initializing = False
        self._clear_transactions()
        self._clear_user_upload()
        self._clear_ddr_upload()
        self._clear_ddr_pp_upload()
        self._clear_ddr_pp_udp_upload()
        if worker is not None:
            worker.stop()
        self.play_capability_changed.emit(False)
        self.disconnected.emit("disconnected")

    # ---- command flow ----
    def set_field(self, ch: int, field: str, value) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        field_l = field.lower()
        if field_l == "base":
            try:
                cmd = base_cmd(ch + 1, value)
            except ValueError as e:
                self.log.emit(str(e), "error")
                return
            self._enqueue(self._new_ack(ch, field_l, value, cmd))
            return
        if field_l == "phase":
            try:
                cmd = phase_cmd(ch + 1, value)
            except ValueError as e:
                self.log.emit(str(e), "error")
                return
            self._enqueue(self._new_ack(ch, field_l, value, cmd))
            return
        if field_l == "phaselink":
            try:
                cmd = phaselink_cmd(ch + 1, value)
            except ValueError as e:
                self.log.emit(str(e), "error")
                return
            self._enqueue(self._new_ack(ch, field_l, value, cmd))
            return
        if (field_l in ("h1_order", "h2_order", "h3_order", "h4_order") or
                field_l in ("h1_ampl", "h2_ampl", "h3_ampl", "h4_ampl")):
            try:
                n = int(field_l[1])
                kind = "ORDER" if field_l.endswith("order") else "AMPL"
                cmd = harm_cmd(ch + 1, n, kind, value)
            except ValueError as e:
                self.log.emit(str(e), "error")
                return
            self._enqueue(self._new_ack(ch, field_l, value, cmd))
            return
        if field_l in ("mode", "mwave", "modwave", "modfreq", "amdepth", "fmdev", "pmdev"):
            try:
                cmd = mod_cmd(ch + 1, field_l, value)
            except ValueError as e:
                self.log.emit(str(e), "error")
                return
            self._enqueue(self._new_ack(ch, field_l, value, cmd))
            return
        try:
            cmd = set_cmd(ch + 1, field, value)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._enqueue(self._new_ack(ch, field, value, cmd))

    def _send(self, ch: int, field: str, value, cmd: str) -> None:
        self._enqueue(self._new_ack(ch, field, value, cmd))

    def request_status(self) -> None:
        if self.worker is None:
            return
        self._enqueue(self._new_status("CH1", 0))
        self._enqueue(self._new_status("CH2", 1))

    def apply_channel_impairment(self, channel, enabled, noise, taps) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            commands = [chan_enable_cmd(channel, bool(enabled)),
                        chan_noise_cmd(channel, noise)]
            for index, (gain, delay) in enumerate(taps):
                commands.append(chan_tap_cmd(channel, index, gain, delay))
            commands.append(chan_commit_cmd(channel))
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        for cmd in commands:
            self._send(channel - 1, "chan", None, cmd)

    def request_channel_status(self, channel) -> None:
        if self.worker is None:
            return
        self._enqueue(self._new_chan_status(channel))

    def _set_digmod_command(self, field, value, cmd) -> None:
        self._enqueue(self._new_ack(None, field, value, cmd))

    def set_digmod(self, mode: str) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = digmod_cmd(mode)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_digmod_command("digmod", mode, cmd)

    def set_digmod_filter(self, filter_name: str) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = digmod_filter_cmd(filter_name)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_digmod_command("digmod_filter", filter_name, cmd)

    def set_digmod_rolloff(self, rolloff: str) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = digmod_rolloff_cmd(rolloff)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_digmod_command("digmod_rolloff", rolloff, cmd)

    def set_digmod_symrate(self, symrate_hz: int) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = digmod_symrate_cmd(symrate_hz)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_digmod_command("digmod_symrate", symrate_hz, cmd)

    def set_digmod_seed(self, seed: int) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = digmod_seed_cmd(seed)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_digmod_command("digmod_seed", seed, cmd)

    def set_digmod_reset(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_digmod_command("digmod_reset", None, digmod_reset_cmd())

    def set_digmod_source(self, source: str) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = digmod_source_cmd(source)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_digmod_command("digmod_source", str(source).upper(), cmd)

    def set_user_loop(self, on: bool) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = user_loop_cmd(on)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._enqueue(self._new_ack(None, "user_loop", on, cmd))

    def upload_user_data(self, data, bit_length=None, loop: bool = False) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        if self._active is not None or self._queue or self._user_upload is not None:
            self.log.emit("controller busy", "error")
            return
        if isinstance(data, (str, PathLike)):
            try:
                data = Path(data).read_bytes()
            except OSError:
                self.log.emit("user data file unreadable", "error")
                return
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self.log.emit("user data must be bytes-like", "error")
            return
        payload = bytes(data)
        if not 1 <= len(payload) <= 512:
            self.log.emit("user data length out of range", "error")
            return
        if bit_length is None:
            bit_length = len(payload) * 8
        if (isinstance(bit_length, bool) or not isinstance(bit_length, int) or
                bit_length != len(payload) * 8):
            self.log.emit("user bit length must match data length", "error")
            return
        if not isinstance(loop, bool):
            self.log.emit("user loop must be a bool", "error")
            return
        self._user_upload = _UserUpload(payload, bit_length, loop)
        self.user_upload_progress.emit(0, len(payload))
        self._enqueue(self._new_ack(None, "user_clear", None, user_clear_cmd()))

    def _enqueue_next_user_chunk(self) -> None:
        upload = self._user_upload
        if upload is None:
            return
        offset = upload.chunk_index * 20
        if offset >= len(upload.data):
            crc = crc16_ccitt(upload.data)
            self._enqueue(self._new_ack(None, "user_commit", upload.bit_length, user_commit_cmd(upload.bit_length, crc)))
            return
        chunk = upload.data[offset:offset + 20]
        self._enqueue(self._new_ack(None, "user_chunk", (offset, len(chunk)), user_chunk_cmd(offset, chunk)))

    def upload_ddr_data(self, data, bit_length=None) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        if self._active is not None or self._queue or self._user_upload is not None or self._ddr_upload is not None:
            self.log.emit("controller busy", "error")
            return
        if isinstance(data, (str, PathLike)):
            try:
                data = Path(data).read_bytes()
            except OSError:
                self.log.emit("DDR data file unreadable", "error")
                return
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self.log.emit("DDR data must be bytes-like", "error")
            return
        payload = bytes(data)
        if not 1 <= len(payload) <= DDR_BUFFER_BYTES:
            self.log.emit("DDR data length out of range", "error")
            return
        if bit_length is None:
            bit_length = len(payload) * 8
        if (isinstance(bit_length, bool) or not isinstance(bit_length, int) or
                bit_length != len(payload) * 8):
            self.log.emit("DDR bit length must match data length", "error")
            return
        self._ddr_upload = _DdrUpload(payload, bit_length)
        self.ddr_upload_progress.emit(0, len(payload))
        self._enqueue(self._new_ack(None, "ddr_clear", None, ddr_clear_cmd()))

    def _enqueue_next_ddr_chunk(self) -> None:
        upload = self._ddr_upload
        if upload is None:
            return
        offset = upload.chunk_index * 20
        if offset >= len(upload.data):
            crc = crc16_ccitt(upload.data)
            self._enqueue(self._new_ack(
                None, "ddr_commit", upload.bit_length,
                ddr_commit_cmd(upload.bit_length, crc)))
            return
        chunk = upload.data[offset:offset + 20]
        self._enqueue(self._new_ack(
            None, "ddr_chunk", (offset, len(chunk)), ddr_chunk_cmd(offset, chunk)))

    def _enqueue_next_ddr_pp_chunk(self) -> None:
        upload = self._ddr_pp_upload
        if upload is None:
            return
        offset = upload.chunk_index * 20
        if offset >= len(upload.data):
            crc = crc16_ccitt(upload.data)
            self._enqueue(self._new_ack(
                None, "ddr_pp_commit", upload.bit_length,
                ddr_pp_commit_cmd(upload.bank, upload.bit_length, crc)))
            return
        chunk = upload.data[offset:offset + 20]
        self._enqueue(self._new_ack(
            None, "ddr_pp_chunk", (upload.bank, offset, len(chunk)),
            ddr_pp_chunk_cmd(upload.bank, offset, chunk)))

    def _validated_ddr_pp_payload(self, bank, data, bit_length):
        """Shared bank/payload/bit-length validation; returns None after logging."""
        if isinstance(data, (str, PathLike)):
            try:
                data = Path(data).read_bytes()
            except OSError:
                self.log.emit("DDR PP data file unreadable", "error")
                return None
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self.log.emit("DDR PP data must be bytes-like", "error")
            return None
        payload = bytes(data)
        if not 4 <= len(payload) <= DDR_PP_BANK_BYTES or len(payload) % 4:
            self.log.emit("DDR PP data length out of range", "error")
            return None
        if bit_length is None:
            bit_length = len(payload) * 8
        if (isinstance(bit_length, bool) or not isinstance(bit_length, int) or
                bit_length < 32 or bit_length > DDR_PP_MAX_BITS or
                bit_length % 32 or bit_length != len(payload) * 8):
            self.log.emit("DDR PP bit length out of range", "error")
            return None
        if isinstance(bank, bool) or bank not in (0, 1):
            self.log.emit("DDR PP bank must be 0 or 1", "error")
            return None
        return bank, payload, bit_length

    def upload_ddr_pp_data(self, bank: int, data, bit_length=None) -> None:
        """Upload one DDR PP bank over the UDP data plane (UART stays control)."""
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        if (self._user_upload is not None or self._ddr_upload is not None or
                self._ddr_pp_upload is not None or
                self._ddr_pp_udp_worker is not None):
            self.log.emit("controller busy", "error")
            return
        validated = self._validated_ddr_pp_payload(bank, data, bit_length)
        if validated is None:
            return
        bank, payload, _bit_length = validated
        client = self.udp_pp_client_factory(self.udp_pp_host, self.udp_pp_port)
        worker = self.udp_pp_worker_factory(client, bank, payload)
        worker.progress.connect(self.ddr_pp_upload_progress)
        worker.completed.connect(
            lambda result, bank=bank: self._on_ddr_pp_udp_completed(bank, result))
        worker.failed.connect(
            lambda message, bank=bank: self._on_ddr_pp_udp_failed(bank, message))
        self._ddr_pp_udp_worker = worker
        self.ddr_pp_upload_progress.emit(0, len(payload))
        self.log.emit(f"DDR PP bank {bank} UDP upload started", "info")
        worker.start()

    def upload_ddr_pp_data_serial(self, bank: int, data, bit_length=None) -> None:
        """Explicit UART stop-and-wait fallback for DDR PP bank upload."""
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        if (self._active is not None or self._queue or self._user_upload is not None or
                self._ddr_upload is not None or self._ddr_pp_upload is not None or
                self._ddr_pp_udp_worker is not None):
            self.log.emit("controller busy", "error")
            return
        validated = self._validated_ddr_pp_payload(bank, data, bit_length)
        if validated is None:
            return
        bank, payload, bit_length = validated
        self._ddr_pp_upload = _DdrPpUpload(bank, payload, bit_length)
        self.ddr_pp_upload_progress.emit(0, len(payload))
        self._enqueue(self._new_ack(None, "ddr_pp_clear", None, ddr_pp_clear_cmd()))

    def _on_ddr_pp_udp_completed(self, bank: int, result) -> None:
        self._clear_ddr_pp_udp_upload()
        self.log.emit(
            "DDR PP bank %d UDP upload: %d bytes in %.2fs (%.2f MB/s, %d retransmits)" %
            (bank, result.bytes_sent, result.elapsed_s, result.effective_mb_s,
             result.retransmits), "ok")
        self.ddr_pp_upload_finished.emit(True)
        if self.worker is not None:
            self._enqueue(self._new_status("DDR_PP"))

    def _on_ddr_pp_udp_failed(self, bank: int, message: str) -> None:
        self._clear_ddr_pp_udp_upload()
        self.log.emit(f"DDR PP bank {bank} UDP upload failed: {message}", "error")
        self.ddr_pp_upload_finished.emit(False)

    def request_ddr_pp_status(self) -> None:
        if self.worker is not None:
            self._enqueue(self._new_status("DDR_PP"))

    def ddr_pp_start(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_play_command("ddr_pp_start", None, ddr_pp_start_cmd())

    def ddr_pp_stop(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_play_command("ddr_pp_stop", None, ddr_pp_stop_cmd())

    def request_ddr_status(self) -> None:
        if self.worker is not None:
            self._enqueue(self._new_status("DDR"))

    def ddr_start(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_play_command("ddr_start", None, ddr_start_cmd())

    def ddr_stop(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_play_command("ddr_stop", None, ddr_stop_cmd())

    def ddr_abort(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._enqueue(self._new_ack(None, "ddr_abort", None, ddr_abort_cmd()))

    def request_digmod_status(self) -> None:
        if self.worker is not None:
            self._enqueue(self._new_status("DIGMOD"))

    def request_userbuf_status(self) -> None:
        if self.worker is not None:
            self._enqueue(self._new_status("USERBUF"))

    def _set_play_command(self, field, value, cmd) -> None:
        self._enqueue(self._new_ack(None, field, value, cmd))

    def set_play_mode(self, mode: str) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = play_mode_cmd(mode)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_play_command("play_mode", str(mode).upper(), cmd)

    def set_play_repeat(self, repeat: int) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = play_repeat_cmd(repeat)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_play_command("play_repeat", repeat, cmd)

    def set_play_trigger(self, trigger: str) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = play_trigger_cmd(trigger)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._set_play_command("play_trigger", str(trigger).upper(), cmd)

    def play_arm(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_play_command("play_arm", None, play_arm_cmd())

    def play_start(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_play_command("play_start", None, play_start_cmd())

    def play_stop(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_play_command("play_stop", None, play_stop_cmd())

    def play_clear(self) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        self._set_play_command("play_clear", None, play_clear_cmd())

    def request_play_status(self) -> None:
        if self.worker is not None:
            self._enqueue(self._new_status("PLAY"))

    def _clear_user_upload(self) -> None:
        self._user_upload = None

    def _clear_ddr_upload(self) -> None:
        self._ddr_upload = None

    def _clear_ddr_pp_upload(self) -> None:
        self._ddr_pp_upload = None

    def _clear_ddr_pp_udp_upload(self) -> None:
        worker = self._ddr_pp_udp_worker
        self._ddr_pp_udp_worker = None
        if worker is not None:
            worker.stop()

    # ---- response handling ----
    @staticmethod
    def _complete_channel_status(d) -> bool:
        return (d["base_pct"] is not None and len(d["harms"]) == 4 and
                all(d.get(k) is not None for k in (
                    "mode", "mwave", "mfreq_01", "am_depth", "fm_dev",
                    "pm_dev", "phase_0_01", "phaselink")))

    def _apply_channel_status(self, d) -> None:
        st = self.state[d["ch"] - 1]
        st.wave, st.freq_hz, st.ampl_pct = d["wave"], d["freq_hz"], d["ampl_pct"]
        st.pulse_duty = d["pulse_duty"]
        if d["base_pct"] is not None:
            st.base_pct = d["base_pct"]
            st.harms = d["harms"]
        for key in ("phase_0_01", "phaselink", "mode", "mwave", "mfreq_01",
                    "am_depth", "fm_dev", "pm_dev"):
            if d.get(key) is not None:
                setattr(st, key, d[key])
        self.state_changed.emit(d["ch"] - 1)

    def _apply_digmod_status(self, line) -> None:
        mode, source, filter_name, rolloff, seed, symrate_hz, running = parse_digmod_status(line)
        self.digmod.mode = mode
        self.digmod.source = source
        self.digmod.filter = filter_name
        self.digmod.rolloff = rolloff
        self.digmod.seed = seed
        self.digmod.symrate_hz = symrate_hz
        self.digmod.running = running
        self.digmod_changed.emit(mode)

    def _apply_userbuf_status(self, line) -> None:
        status = parse_userbuf_status(line)
        self.userbuf = status
        self.userbuf_changed.emit(status)

    def _apply_ddr_status(self, line) -> None:
        status = parse_ddr_status(line)
        self.ddr = status
        self.ddr_changed.emit(status)

    def _apply_ddr_pp_status(self, line) -> None:
        status = parse_ddr_pp_status(line)
        self.ddr_pp = status
        self.ddr_pp_changed.emit(status)

    def _apply_play_status(self, line) -> None:
        status = parse_play_status(line)
        self.play = status
        self.play_changed.emit(status)

    def _finish_active(self) -> Optional[_Transaction]:
        tx = self._active
        self._active = None
        self._sync_pending_view()
        self._timer.stop()
        return tx

    def _finish_success(self, tx: _Transaction, line: str) -> None:
        if tx.response == "ACK":
            if tx.field == "ddr_clear":
                self._enqueue_next_ddr_chunk()
            elif tx.field == "ddr_pp_clear":
                self._enqueue_next_ddr_pp_chunk()
            elif tx.field == "ddr_chunk":
                upload = self._ddr_upload
                if upload is not None:
                    upload.chunk_index += 1
                    self.ddr_upload_progress.emit(
                        min(upload.chunk_index * 20, len(upload.data)), len(upload.data))
                    self._enqueue_next_ddr_chunk()
            elif tx.field == "ddr_commit":
                self._enqueue(self._new_status("DDR"))
            elif tx.field == "ddr_pp_chunk":
                upload = self._ddr_pp_upload
                if upload is not None:
                    upload.chunk_index += 1
                    self.ddr_pp_upload_progress.emit(
                        min(upload.chunk_index * 20, len(upload.data)), len(upload.data))
                    self._enqueue_next_ddr_pp_chunk()
            elif tx.field == "ddr_pp_commit":
                self._enqueue(self._new_status("DDR_PP"))
            elif tx.field in ("ddr_start", "ddr_stop", "ddr_abort"):
                self._enqueue(self._new_status("DDR"), front=True)
            elif tx.field in ("ddr_pp_start", "ddr_pp_stop"):
                self._enqueue(self._new_status("DDR_PP"), front=True)
            elif tx.field == "user_clear":
                self._enqueue_next_user_chunk()
            elif tx.field == "user_chunk":
                upload = self._user_upload
                if upload is not None:
                    upload.chunk_index += 1
                    self.user_upload_progress.emit(
                        min(upload.chunk_index * 20, len(upload.data)), len(upload.data))
                    self._enqueue_next_user_chunk()
            elif tx.field == "user_commit":
                upload = self._user_upload
                if upload is not None and upload.loop:
                    self._enqueue(self._new_ack(None, "user_loop", True,
                                                user_loop_cmd(True)))
                else:
                    self._enqueue(self._new_status("USERBUF"))
            elif tx.field == "user_loop":
                self._enqueue(self._new_status("USERBUF"))
            elif tx.field in _DIGMOD_FIELDS:
                self._enqueue(self._new_status("DIGMOD"), front=True)
            elif tx.field.startswith("play_"):
                self._enqueue(self._new_status("PLAY"), front=True)
            elif tx.field == "base" or tx.field.endswith("order") or tx.field.endswith("ampl"):
                self._enqueue(self._new_status("CH1" if tx.ch == 0 else "CH2", tx.ch), front=True)
            else:
                st = self.state[tx.ch]
                if tx.field == "wave":
                    st.wave = str(tx.value).upper()
                elif tx.field == "freq":
                    st.freq_hz = int(tx.value)
                elif tx.field == "ampl":
                    st.ampl_pct = int(tx.value)
                elif tx.field == "duty":
                    st.pulse_duty = int(tx.value)
                elif tx.field == "phase":
                    st.phase_0_01 = int(tx.value)
                elif tx.field == "phaselink":
                    st.phaselink = bool(tx.value)
                elif tx.field == "mode":
                    st.mode = str(tx.value).upper()
                elif tx.field == "mwave":
                    st.mwave = str(tx.value).upper()
                elif tx.field in ("modfreq", "amdepth", "fmdev", "pmdev"):
                    attr = {"modfreq": "mfreq_01", "amdepth": "am_depth",
                            "fmdev": "fm_dev", "pmdev": "pm_dev"}[tx.field]
                    setattr(st, attr, int(tx.value))
                self.state_changed.emit(tx.ch)
            self.log.emit(f"<< {line}", "ok")
            return
        if tx.initializing and tx.response == "DIGMOD":
            self._enqueue(self._new_status("PLAY", initializing=True), front=True)
        elif tx.initializing and tx.response == "PLAY":
            self.play_supported = True
            self.play_capability_changed.emit(True)
            self._initializing = False
            self._ready = True
            self.log.emit("hardware state synchronized", "ok")
            self.connected.emit()
        if tx.response == "USERBUF":
            self._clear_user_upload()
        if tx.response == "DDR":
            self._clear_ddr_upload()
        if tx.response == "DDR_PP":
            self._clear_ddr_pp_upload()
        self.log.emit(f"<< {line}", "ok")

    def _abort_initialization(self, reason: str) -> None:
        worker = self.worker
        self.worker = None
        self._ready = False
        self._initializing = False
        self._clear_transactions()
        if worker is not None:
            worker.stop()
        self.log.emit(f"initialization failed: {reason}", "error")
        self.disconnected.emit(reason)

    def _finish_failure(self, tx: _Transaction, reason: str) -> None:
        if tx.field in _DIGMOD_FIELDS:
            self.digmod_changed.emit(self.digmod.mode)
        if tx.field in ("play_mode", "play_repeat", "play_trigger", "play_arm",
                        "play_start", "play_stop", "play_clear") or tx.response == "PLAY":
            self.play_changed.emit(self.play)
        if tx.field in ("user_clear", "user_chunk", "user_commit", "user_loop") or tx.response == "USERBUF":
            self._clear_user_upload()
        if tx.field in ("ddr_clear", "ddr_chunk", "ddr_commit", "ddr_abort") or tx.response == "DDR":
            self._clear_ddr_upload()
        if (tx.field in ("ddr_pp_clear", "ddr_pp_chunk", "ddr_pp_commit") or
                tx.response == "DDR_PP"):
            self._clear_ddr_pp_upload()
        if tx.initializing:
            self._abort_initialization(reason)
        else:
            self._pump()

    def _on_line(self, line: str) -> None:
        line = line.strip()
        if line == "AC820-AWG bridge ready":
            self.log.emit(f"<< {line}", "info")
            return

        tx = self._active
        if line.startswith("CH1 CHAN ") or line.startswith("CH2 CHAN "):
            channel = 1 if line.startswith("CH1 CHAN ") else 2
            try:
                parsed = parse_chan_status(line)
            except (ValueError, IndexError):
                self.log.emit(f"protocol parse error: {line}", "error")
                if tx is not None and tx.response == "CHAN" and tx.ch == channel - 1:
                    self._finish_failure(self._finish_active(), "protocol parse error")
                return
            self.chan_status_changed.emit(channel - 1, parsed)
            if tx is not None and tx.response == "CHAN" and tx.ch == channel - 1:
                self._finish_success(self._finish_active(), line)
                self._pump()
            else:
                self.log.emit(f"<< {line}", "ok")
            return

        if line.startswith("CH1 ") or line.startswith("CH2 "):
            try:
                d = parse_status(line)
            except (ValueError, IndexError):
                self.log.emit(f"protocol parse error: {line}", "error")
                if tx is not None and tx.response == line[:3] and tx.initializing:
                    self._finish_failure(self._finish_active(), "protocol parse error")
                return
            self._apply_channel_status(d)
            kind = f"CH{d['ch']}"
            if tx is not None and tx.response == kind and tx.ch == d["ch"] - 1:
                if tx.initializing and not self._complete_channel_status(d):
                    self._finish_active()
                    self._finish_failure(tx, "protocol parse error")
                    return
                self._finish_success(self._finish_active(), line)
                self._pump()
            else:
                self.log.emit(f"<< {line}", "ok")
            return

        if line.startswith("DIGMOD "):
            try:
                self._apply_digmod_status(line)
            except (ValueError, IndexError):
                self.log.emit(f"protocol parse error: {line}", "error")
                if tx is not None and tx.response == "DIGMOD":
                    failed = self._finish_active()
                    self._finish_failure(failed, "protocol parse error")
                return
            if tx is not None and tx.response == "DIGMOD":
                if tx.initializing and len(line.split()) != 15:
                    failed = self._finish_active()
                    self._finish_failure(failed, "protocol parse error")
                    return
                self._finish_success(self._finish_active(), line)
                self._pump()
            else:
                self.log.emit(f"<< {line}", "ok")
            return

        if line.startswith("USERBUF "):
            try:
                self._apply_userbuf_status(line)
            except (ValueError, IndexError):
                self.log.emit(f"protocol parse error: {line}", "error")
                if tx is not None and tx.response == "USERBUF":
                    failed = self._finish_active()
                    self._finish_failure(failed, "protocol parse error")
                return
            if tx is not None and tx.response == "USERBUF":
                self._finish_success(self._finish_active(), line)
                self._pump()
            else:
                self.log.emit(f"<< {line}", "ok")
            return

        if line.startswith("DDR PP "):
            try:
                self._apply_ddr_pp_status(line)
            except (ValueError, IndexError):
                self.log.emit(f"protocol parse error: {line}", "error")
                if tx is not None and tx.response == "DDR_PP":
                    failed = self._finish_active()
                    self._finish_failure(failed, "protocol parse error")
                return
            if tx is not None and tx.response == "DDR_PP":
                self._finish_success(self._finish_active(), line)
                self._pump()
            else:
                self.log.emit(f"<< {line}", "ok")
            return

        if line.startswith("DDR "):
            try:
                self._apply_ddr_status(line)
            except (ValueError, IndexError):
                self.log.emit(f"protocol parse error: {line}", "error")
                if tx is not None and tx.response == "DDR":
                    failed = self._finish_active()
                    self._finish_failure(failed, "protocol parse error")
                return
            if tx is not None and tx.response == "DDR":
                self._finish_success(self._finish_active(), line)
                self._pump()
            else:
                self.log.emit(f"<< {line}", "ok")
            return

        if line.startswith("PLAY "):
            try:
                self._apply_play_status(line)
            except (ValueError, IndexError):
                self.log.emit(f"protocol parse error: {line}", "error")
                if tx is not None and tx.response == "PLAY":
                    failed = self._finish_active()
                    self._finish_failure(failed, "protocol parse error")
                return
            if tx is not None and tx.response == "PLAY":
                self._finish_success(self._finish_active(), line)
                self._pump()
            else:
                self.log.emit(f"<< {line}", "ok")
            return

        ok, text = parse_response(line)
        if line == "OK":
            if tx is None:
                self.log.emit("<< OK", "info")
            elif tx.response != "ACK":
                failed = self._finish_active()
                self.log.emit("protocol parse error: unexpected OK", "error")
                self._finish_failure(failed, "protocol parse error")
            else:
                self._finish_success(self._finish_active(), line)
            self._pump()
            return
        if line.startswith("ERR "):
            if tx is None:
                self.log.emit(f"device error: {text}", "error")
            elif (tx.initializing and tx.response == "PLAY" and
                  text == "PLAY UNSUPPORTED"):
                self._finish_active()
                self.play_supported = False
                self.play_capability_changed.emit(False)
                self._initializing = False
                self._ready = True
                self.log.emit("PLAY unsupported; legacy hardware state synchronized", "info")
                self.connected.emit()
                self._pump()
            else:
                failed = self._finish_active()
                self.log.emit(f"device error: {text}", "error")
                self._finish_failure(failed, "device error")
            return
        self.log.emit(f"unexpected response: {line}", "info")

    def _on_timeout(self) -> None:
        if self._active is None:
            return
        failed = self._finish_active()
        self.log.emit("timeout: no response", "error")
        self._finish_failure(failed, "timeout")

    def _on_lost(self, msg: str) -> None:
        worker = self.worker
        self.worker = None
        self._ready = False
        self._initializing = False
        self._clear_transactions()
        self._clear_user_upload()
        self._clear_ddr_upload()
        self._clear_ddr_pp_upload()
        self._clear_ddr_pp_udp_upload()
        if worker is not None:
            worker.stop()
        self.log.emit(f"connection lost: {msg}", "error")
        self.disconnected.emit(msg)
