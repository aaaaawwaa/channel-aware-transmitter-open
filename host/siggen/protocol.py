"""AC820 AWG 串口协议(上位机侧)。

ASCII 行协议,CR/LF 结尾,大小写不敏感,响应全大写:
  SET CH1|CH2 WAVE SINE|SQUARE|TRIANGLE|SAW|PULSE|NOISE -> OK
  SET CH1|CH2 DUTY <0..100>                    -> OK (PULSE 高电平占空比)
  SET CH1|CH2 FREQ <10..1000000>              -> OK
  SET CH1|CH2 AMPL <0..100>                   -> OK
  SET CH1|CH2 BASE <0..100>                   -> OK
  SET CH1|CH2 HARM<n> ORDER <0,2..8>          -> OK  (n=1..4, 0=禁用)
  SET CH1|CH2 HARM<n> AMPL <0..100>           -> OK
  SET CH1|CH2 MODE OFF|AM|FM|PM|SWEEP         -> OK
  SET CH1|CH2 MODWAVE SINE|SQUARE|TRIANGLE|SAW -> OK
  SET CH1|CH2 MODFREQ <1..500000>             -> OK  (0.1Hz 单位)
  SET CH1|CH2 AMDEPTH <0..100>                -> OK
  SET CH1|CH2 FMDEV <0..500000>               -> OK
  SET CH1|CH2 PMDEV <0..511>                  -> OK
  SET DIGMOD MODE OFF|BPSK|QPSK|QAM4|QAM16|QAM64 -> OK
  SET DIGMOD SOURCE PRBS7|USER                 -> OK
  SET DIGMOD FILTER RECT|RRC                   -> OK (仅 STOPPED; RRC 支持全部模式)
  SET DIGMOD SYMRATE <10..1000000>             -> OK (仅 STOPPED)
  SET DIGMOD SEED <1..127>                    -> OK (仅 STOPPED)
  SET DIGMOD RESET                             -> OK (仅 STOPPED)
  GET DIGMOD                                  -> DIGMOD MODE .. SOURCE PRBS7 FILTER .. ROLLOFF ..
                                                 SEED .. SYMRATE .. STATE ..
                                                 [BASE .. H1 o a H2 o a .. MODE
                                                 .. MODWAVE .. MODFREQ .. AMDEPTH
                                                 .. FMDEV .. PMDEV .. PHASE ..
                                                 PHASELINK ON|OFF]
  SET DDR PP CLEAR                            -> OK
  SET DDR PP CHUNK <bank> <offset> <hex> <crc8> -> OK
  SET DDR PP COMMIT <bank> <bit_length> <crc16> -> OK
  SET DDR PP START|STOP                       -> OK
  GET DDR PP                                  -> DDR PP ACTIVE .. ACTIVE_BANK ..
                                                 NEXT_BANK .. STATE .. ERROR ..
                                                 BANK0 VALID .. LENGTH .. CRC ..
                                                 BANK1 VALID .. LENGTH .. CRC ..
  SET CH1|CH2 CHAN ON|OFF                     -> OK
  SET CH1|CH2 NOISE <0..100>                  -> OK
  SET CH1|CH2 TAP <0..3> <-256..255> <0..511> -> OK
  SET CH1|CH2 TAP CLEAR                       -> OK
  SET CH1|CH2 CHAN COMMIT                     -> OK
  PHASE/PHASELINK 指令仅允许 CH2; PHASE 单位为 0.01°,范围 0..36000。
错误响应: ERR <msg> (msg 见桥程序 main.c)。
"""

import re

WAVES = ("SINE", "SQUARE", "TRIANGLE", "SAW", "PULSE", "NOISE")
FREQ_MIN, FREQ_MAX = 10, 1_000_000
AMPL_MIN, AMPL_MAX = 0, 100
DUTY_MIN, DUTY_MAX = 0, 100
MODES = ("OFF", "AM", "FM", "PM", "SWEEP")
MODWAVES = WAVES[:4]
MODFREQ_MIN, MODFREQ_MAX = 1, 500_000      # 0.1Hz 单位
FMDEV_MAX = 500_000
PMDEV_MAX = 511
PHASE_MAX = 36000
DIGMODES = ("OFF", "BPSK", "QPSK", "QAM4", "QAM16", "QAM64")
DIGMOD_SOURCES = ("PRBS7", "USER")
DIGMOD_FILTERS = ("RECT", "RRC")
DIGMOD_ROLLOFFS = ("0.2", "0.35", "0.5")
DIGMOD_SYMRATE_MIN, DIGMOD_SYMRATE_MAX = 10, 1_000_000
DIGMOD_SEED_MIN, DIGMOD_SEED_MAX = 1, 127
USER_BANK_BYTES = 512
USER_MAX_BITS = USER_BANK_BYTES * 8
USER_CHUNK_MAX_BYTES = 20
DDR_BUFFER_BYTES = 1 << 20
DDR_MAX_BITS = DDR_BUFFER_BYTES * 8
DDR_CHUNK_MAX_BYTES = 20
DDR_PP_BANK_COUNT = 2
DDR_PP_BANK_BYTES = DDR_BUFFER_BYTES
DDR_PP_MAX_BITS = DDR_PP_BANK_BYTES * 8
DDR_PP_CHUNK_MAX_BYTES = DDR_CHUNK_MAX_BYTES
DDR_PP_STATES = ("IDLE", "RUNNING", "DRAIN", "ERROR")
PLAY_MODES = ("LEGACY", "MANUAL")
PLAY_TRIGGERS = ("SOFTWARE", "EXTERNAL")
PLAY_STATES = ("IDLE", "ARMED", "STARTING", "RUNNING", "DRAIN", "DONE", "ERROR")
CHAN_TAP_COUNT = 4
CHAN_TAP_GAIN_MIN, CHAN_TAP_GAIN_MAX = -256, 255
CHAN_TAP_DELAY_MIN, CHAN_TAP_DELAY_MAX = 0, 511
CHAN_NOISE_MIN, CHAN_NOISE_MAX = 0, 100


def set_cmd(ch: int, field: str, value) -> str:
    """构造 SET 命令;非法参数抛 ValueError。"""
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    field = field.upper()
    if field == "WAVE":
        wave = str(value).upper()
        if wave not in WAVES:
            raise ValueError(f"bad wave: {value}")
        return f"SET CH{ch} WAVE {wave}"
    if field == "DUTY":
        duty = int(value)
        if not DUTY_MIN <= duty <= DUTY_MAX:
            raise ValueError(f"duty out of range: {value}")
        return f"SET CH{ch} DUTY {duty}"
    if field == "FREQ":
        freq = int(value)
        if not FREQ_MIN <= freq <= FREQ_MAX:
            raise ValueError(f"freq out of range: {value}")
        return f"SET CH{ch} FREQ {freq}"
    if field == "AMPL":
        ampl = int(value)
        if not AMPL_MIN <= ampl <= AMPL_MAX:
            raise ValueError(f"ampl out of range: {value}")
        return f"SET CH{ch} AMPL {ampl}"
    raise ValueError(f"bad field: {field}")


def get_cmd(ch: int) -> str:
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    return f"GET CH{ch}"


def digmod_cmd(mode) -> str:
    """构造 SET DIGMOD MODE 命令;非法模式抛 ValueError。"""
    name = str(mode).upper()
    if name not in DIGMODES:
        raise ValueError(f"bad digital modulation mode: {mode}")
    return f"SET DIGMOD MODE {name}"


def _coerce_bytes(data) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("data must be bytes-like")
    return bytes(data)


def crc8_atm(data) -> int:
    payload = _coerce_bytes(data)
    crc = 0
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16_ccitt(data) -> int:
    payload = _coerce_bytes(data)
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode_hex(data) -> str:
    return _coerce_bytes(data).hex().upper()


def decode_hex(text: str) -> bytes:
    if not isinstance(text, str):
        raise ValueError("hex data must be text")
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError(f"bad hex data: {text!r}") from exc


def user_clear_cmd() -> str:
    return "SET USER CLEAR"


def user_chunk_cmd(offset: int, data) -> str:
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < USER_BANK_BYTES:
        raise ValueError(f"user chunk offset out of range: {offset}")
    payload = _coerce_bytes(data)
    if not 1 <= len(payload) <= USER_CHUNK_MAX_BYTES:
        raise ValueError("user chunk length out of range")
    return f"SET USER CHUNK {offset} {encode_hex(payload)} {crc8_atm(payload):02X}"


def user_commit_cmd(bit_length: int, crc16: int) -> str:
    if (isinstance(bit_length, bool) or not isinstance(bit_length, int) or
            not 8 <= bit_length <= USER_MAX_BITS or bit_length % 8):
        raise ValueError(f"user bit length out of range: {bit_length}")
    if isinstance(crc16, bool) or not isinstance(crc16, int) or not 0 <= crc16 <= 0xFFFF:
        raise ValueError(f"user CRC16 out of range: {crc16}")
    return f"SET USER COMMIT {bit_length} {crc16:04X}"


def user_abort_cmd() -> str:
    return "SET USER ABORT"


def user_loop_cmd(on: bool) -> str:
    if not isinstance(on, bool):
        raise ValueError(f"user loop must be a bool: {on}")
    return f"SET USER LOOP {'ON' if on else 'OFF'}"


def ddr_clear_cmd() -> str:
    return "SET DDR CLEAR"


def ddr_chunk_cmd(offset: int, data) -> str:
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < DDR_BUFFER_BYTES:
        raise ValueError(f"DDR chunk offset out of range: {offset}")
    payload = _coerce_bytes(data)
    if not 1 <= len(payload) <= DDR_CHUNK_MAX_BYTES:
        raise ValueError("DDR chunk length out of range")
    if offset + len(payload) > DDR_BUFFER_BYTES:
        raise ValueError("DDR chunk exceeds buffer")
    return f"SET DDR CHUNK {offset} {encode_hex(payload)} {crc8_atm(payload):02X}"


def ddr_commit_cmd(bit_length: int, crc16: int) -> str:
    if (isinstance(bit_length, bool) or not isinstance(bit_length, int) or
            not 8 <= bit_length <= DDR_MAX_BITS or bit_length % 8):
        raise ValueError(f"DDR bit length out of range: {bit_length}")
    if isinstance(crc16, bool) or not isinstance(crc16, int) or not 0 <= crc16 <= 0xFFFF:
        raise ValueError(f"DDR CRC16 out of range: {crc16}")
    return f"SET DDR COMMIT {bit_length} {crc16:04X}"


def ddr_abort_cmd() -> str:
    return "SET DDR ABORT"


def ddr_start_cmd() -> str:
    return "SET DDR START"


def ddr_stop_cmd() -> str:
    return "SET DDR STOP"


def get_ddr_cmd() -> str:
    return "GET DDR"


def _validate_ddr_pp_bank(bank: int) -> int:
    if isinstance(bank, bool) or not isinstance(bank, int) or bank not in range(DDR_PP_BANK_COUNT):
        raise ValueError(f"DDR PP bank out of range: {bank}")
    return bank


def ddr_pp_clear_cmd() -> str:
    return "SET DDR PP CLEAR"


def ddr_pp_chunk_cmd(bank: int, offset: int, data) -> str:
    _validate_ddr_pp_bank(bank)
    if (isinstance(offset, bool) or not isinstance(offset, int) or
            not 0 <= offset < DDR_PP_BANK_BYTES or offset % 4):
        raise ValueError(f"DDR PP chunk offset out of range: {offset}")
    payload = _coerce_bytes(data)
    if not 1 <= len(payload) <= DDR_PP_CHUNK_MAX_BYTES or len(payload) % 4:
        raise ValueError("DDR PP chunk length out of range")
    if offset + len(payload) > DDR_PP_BANK_BYTES:
        raise ValueError("DDR PP chunk exceeds bank")
    return f"SET DDR PP CHUNK {bank} {offset} {encode_hex(payload)} {crc8_atm(payload):02X}"


def ddr_pp_commit_cmd(bank: int, bit_length: int, crc16: int) -> str:
    _validate_ddr_pp_bank(bank)
    if (isinstance(bit_length, bool) or not isinstance(bit_length, int) or
            not 32 <= bit_length <= DDR_PP_MAX_BITS or bit_length % 32):
        raise ValueError(f"DDR PP bit length out of range: {bit_length}")
    if isinstance(crc16, bool) or not isinstance(crc16, int) or not 0 <= crc16 <= 0xFFFF:
        raise ValueError(f"DDR PP CRC16 out of range: {crc16}")
    return f"SET DDR PP COMMIT {bank} {bit_length} {crc16:04X}"


def ddr_pp_start_cmd() -> str:
    return "SET DDR PP START"


def ddr_pp_stop_cmd() -> str:
    return "SET DDR PP STOP"


def get_ddr_pp_cmd() -> str:
    return "GET DDR PP"


def _parse_ddr_pp_bank_selector(text: str):
    if text == "NONE":
        return None
    try:
        bank = int(text)
    except ValueError as exc:
        raise ValueError(f"bad DDR PP bank selector: {text!r}") from exc
    return _validate_ddr_pp_bank(bank)


def parse_ddr_pp_status(line: str) -> dict:
    parts = line.split()
    markers = ("ACTIVE", "ACTIVE_BANK", "NEXT_BANK", "STATE", "ERROR",
               "BANK0", "VALID", "LENGTH", "CRC", "BANK1", "VALID", "LENGTH", "CRC")
    positions = (2, 4, 6, 8, 10, 12, 13, 15, 17, 19, 20, 22, 24)
    if len(parts) != 26 or parts[0:2] != ["DDR", "PP"] or tuple(parts[index] for index in positions) != markers:
        raise ValueError(f"bad DDR PP status line: {line!r}")
    try:
        active = int(parts[3])
        active_bank = _parse_ddr_pp_bank_selector(parts[5])
        next_bank = _parse_ddr_pp_bank_selector(parts[7])
        state = parts[9]
        error_code = int(parts[11], 16)
        valid = [int(parts[14]), int(parts[21])]
        bit_length = [int(parts[16]), int(parts[23])]
        crc16 = [int(parts[18], 16), int(parts[25], 16)]
    except ValueError as exc:
        raise ValueError(f"bad DDR PP status line: {line!r}") from exc
    if (active not in (0, 1) or state not in DDR_PP_STATES or
            not 0 <= error_code <= 0xFF or
            any(value not in (0, 1) for value in valid) or
            any(not 0 <= value <= DDR_PP_MAX_BITS or value % 32 for value in bit_length) or
            any(not 0 <= value <= 0xFFFF for value in crc16)):
        raise ValueError(f"bad DDR PP status line: {line!r}")
    return {
        "active": bool(active),
        "active_bank": active_bank,
        "next_bank": next_bank,
        "state": state,
        "error_code": error_code,
        "bank0": {"valid": bool(valid[0]), "bit_length": bit_length[0], "crc16": crc16[0]},
        "bank1": {"valid": bool(valid[1]), "bit_length": bit_length[1], "crc16": crc16[1]},
    }


def parse_ddr_status(line: str) -> dict:
    parts = line.split()
    expected = ("VALID", "BUFFER", "MAX", "LENGTH", "CRC",
                "UPLOAD", "ACTIVE", "DMA", "ERROR")
    if len(parts) != 19 or parts[0] != "DDR" or tuple(parts[1::2]) != expected:
        raise ValueError(f"bad DDR status line: {line!r}")
    try:
        valid = int(parts[2])
        buffer_addr = int(parts[4], 16)
        max_bytes = int(parts[6])
        bit_length = int(parts[8])
        crc16 = int(parts[10], 16)
        upload = parts[12]
        active = int(parts[14])
        dma_state = parts[16]
        error_code = int(parts[18], 16)
    except ValueError as exc:
        raise ValueError(f"bad DDR status line: {line!r}") from exc
    if (valid not in (0, 1) or not 0 <= buffer_addr <= 0xFFFFFFFF or
            max_bytes != DDR_BUFFER_BYTES or not 0 <= bit_length <= DDR_MAX_BITS or
            (bit_length and bit_length % 8) or not 0 <= crc16 <= 0xFFFF or
            upload not in ("IDLE", "RECEIVING") or active not in (0, 1) or
            dma_state not in ("IDLE", "READY", "RUNNING", "DRAIN", "ERROR") or
            not 0 <= error_code <= 0xFF):
        raise ValueError(f"bad DDR status line: {line!r}")
    return {
        "valid": bool(valid), "buffer": buffer_addr, "max_bytes": max_bytes,
        "bit_length": bit_length, "crc16": crc16, "upload": upload,
        "active": bool(active), "dma_state": dma_state, "error_code": error_code,
    }


    name = str(source).upper()
    if name not in DIGMOD_SOURCES:
        raise ValueError(f"bad digital modulation source: {source}")
    return f"SET DIGMOD SOURCE {name}"


def digmod_source_cmd(source: str) -> str:
    name = str(source).upper()
    if name not in DIGMOD_SOURCES:
        raise ValueError(f"bad digital modulation source: {source}")
    return f"SET DIGMOD SOURCE {name}"


def play_mode_cmd(mode: str) -> str:
    name = str(mode).upper()
    if name not in PLAY_MODES:
        raise ValueError(f"bad play mode: {mode}")
    return f"SET PLAY MODE {name}"


def play_repeat_cmd(repeat: int) -> str:
    if isinstance(repeat, bool) or not isinstance(repeat, int) or not 0 <= repeat <= 0xFFFF:
        raise ValueError(f"play repeat out of range: {repeat}")
    return f"SET PLAY REPEAT {repeat}"


def play_trigger_cmd(trigger: str) -> str:
    name = str(trigger).upper()
    if name not in PLAY_TRIGGERS:
        raise ValueError(f"bad play trigger: {trigger}")
    return f"SET PLAY TRIGGER {name}"


def play_arm_cmd() -> str:
    return "SET PLAY ARM"


def play_start_cmd() -> str:
    return "SET PLAY START"


def play_stop_cmd() -> str:
    return "SET PLAY STOP"


def play_clear_cmd() -> str:
    return "SET PLAY CLEAR"


def get_play_cmd() -> str:
    return "GET PLAY"


def parse_play_status(line: str) -> dict:
    parts = line.split()
    keys = ("STATE", "FRAME_DONE", "UNDERFLOW", "TRIGGER_SEEN",
            "STOP_PENDING", "ERROR_VALID", "REPEAT_REMAINING", "ERROR")
    if len(parts) != 17 or parts[0] != "PLAY" or tuple(parts[1::2]) != keys:
        raise ValueError(f"bad play status line: {line!r}")
    try:
        state = parts[2]
        flags = [int(parts[index]) for index in (4, 6, 8, 10, 12)]
        repeat_remaining = int(parts[14])
        error_code = int(parts[16], 16)
    except ValueError as exc:
        raise ValueError(f"bad play status line: {line!r}") from exc
    if (state not in PLAY_STATES or any(value not in (0, 1) for value in flags)
            or not 0 <= repeat_remaining <= 0xFFFF or not 0 <= error_code <= 0xFF):
        raise ValueError(f"bad play status line: {line!r}")
    return {
        "state": state, "frame_done": bool(flags[0]), "underflow": bool(flags[1]),
        "trigger_seen": bool(flags[2]), "stop_pending": bool(flags[3]),
        "error_valid": bool(flags[4]), "repeat_remaining": repeat_remaining,
        "error_code": error_code,
    }


def parse_userbuf_status(line: str) -> dict:
    parts = line.split()
    expected = ("USERBUF", "VALID", "LENGTH", "CRC", "BANK", "UPLOAD",
                "LOOP", "COMMIT_SEEN", "READY", "DONE", "UNDERFLOW")
    if len(parts) != 21 or tuple(parts[1::2]) != expected[1:]:
        raise ValueError(f"bad userbuf status line: {line!r}")
    try:
        valid = int(parts[2])
        bit_length = int(parts[4])
        crc16 = int(parts[6], 16)
        bank = int(parts[8])
        upload = parts[10]
        loop = parts[12]
        commit_seen = int(parts[14])
        ready = int(parts[16])
        done = int(parts[18])
        underflow = int(parts[20])
    except ValueError as exc:
        raise ValueError(f"bad userbuf status line: {line!r}") from exc
    if (valid not in (0, 1) or not 0 <= bit_length <= USER_MAX_BITS or
            (bit_length and bit_length % 8) or not 0 <= crc16 <= 0xFFFF or
            bank not in (0, 1) or upload not in ("IDLE", "RECEIVING") or
            loop not in ("ON", "OFF") or
            any(value not in (0, 1) for value in (commit_seen, ready, done, underflow))):
        raise ValueError(f"bad userbuf status line: {line!r}")
    return {
        "valid": bool(valid), "bit_length": bit_length, "crc16": crc16,
        "bank": bank, "upload": upload, "loop": loop == "ON",
        "commit_seen": bool(commit_seen), "ready": bool(ready),
        "done": bool(done), "underflow": bool(underflow),
    }


def digmod_symrate_cmd(symrate_hz: int) -> str:
    if isinstance(symrate_hz, bool) or not isinstance(symrate_hz, int):
        raise ValueError("SYMRATE must be an integer")
    if not DIGMOD_SYMRATE_MIN <= symrate_hz <= DIGMOD_SYMRATE_MAX:
        raise ValueError("SYMRATE out of range")
    return f"SET DIGMOD SYMRATE {symrate_hz}"


def digmod_filter_cmd(filter_name) -> str:
    """构造 SET DIGMOD FILTER 命令;非法滤波器抛 ValueError。"""
    name = str(filter_name).upper()
    if name not in DIGMOD_FILTERS:
        raise ValueError(f"unsupported DIGMOD filter: {filter_name}")
    return f"SET DIGMOD FILTER {name}"


def digmod_rolloff_cmd(rolloff) -> str:
    """构造 SET DIGMOD ROLLOFF 命令;非法滚降因子抛 ValueError。"""
    name = str(rolloff)
    if name not in DIGMOD_ROLLOFFS:
        raise ValueError(f"unsupported DIGMOD rolloff: {rolloff}")
    return f"SET DIGMOD ROLLOFF {name}"


def digmod_seed_cmd(seed: int) -> str:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("SEED must be an integer")
    if not DIGMOD_SEED_MIN <= seed <= DIGMOD_SEED_MAX:
        raise ValueError("SEED out of range")
    return f"SET DIGMOD SEED {seed}"


def digmod_reset_cmd() -> str:
    return "SET DIGMOD RESET"


def get_digmod_cmd() -> str:
    return "GET DIGMOD"


def parse_digmod_status(line: str) -> tuple:
    """解析新旧 DIGMOD 状态行，返回 (mode, source, filter, rolloff, seed, symrate_hz, running)。"""
    parts = line.split()
    seed = 127
    if len(parts) == 9:
        mode, source, filter_name, rolloff = parts[2], parts[4], "RECT", "0.35"
        rate_key, rate_value, state_key, state_value = parts[5:9]
    elif len(parts) == 11:
        mode, source, filter_name, rolloff = parts[2], parts[4], parts[6], "0.35"
        if parts[5] != "FILTER":
            raise ValueError(f"bad digmod status line: {line!r}")
        rate_key, rate_value, state_key, state_value = parts[7:11]
    elif len(parts) == 13:
        mode, source, filter_name, rolloff = parts[2], parts[4], parts[6], parts[8]
        if parts[5] != "FILTER" or parts[7] != "ROLLOFF":
            raise ValueError(f"bad digmod status line: {line!r}")
        rate_key, rate_value, state_key, state_value = parts[9:13]
    elif len(parts) == 15:
        mode, source, filter_name, rolloff = parts[2], parts[4], parts[6], parts[8]
        if (parts[5] != "FILTER" or parts[7] != "ROLLOFF" or
                parts[9] != "SEED" or not parts[10].isdigit()):
            raise ValueError(f"bad digmod status line: {line!r}")
        seed = int(parts[10])
        rate_key, rate_value, state_key, state_value = parts[11:15]
    else:
        raise ValueError(f"bad digmod status line: {line!r}")
    if (parts[0] != "DIGMOD" or parts[1] != "MODE" or mode not in DIGMODES
            or parts[3] != "SOURCE" or source not in DIGMOD_SOURCES
            or filter_name not in DIGMOD_FILTERS or rolloff not in DIGMOD_ROLLOFFS
            or not DIGMOD_SEED_MIN <= seed <= DIGMOD_SEED_MAX
            or rate_key != "SYMRATE" or not rate_value.isdigit()
            or state_key != "STATE" or state_value not in ("RUNNING", "STOPPED")):
        raise ValueError(f"bad digmod status line: {line!r}")
    symrate_hz = int(rate_value)
    if not DIGMOD_SYMRATE_MIN <= symrate_hz <= DIGMOD_SYMRATE_MAX:
        raise ValueError(f"bad digmod symrate: {symrate_hz}")
    running = state_value == "RUNNING"
    if running != (mode != "OFF"):
        raise ValueError(f"inconsistent digmod state: {line!r}")
    return mode, source, filter_name, rolloff, seed, symrate_hz, running


def parse_response(line: str) -> tuple:
    """解析一行响应 -> (ok, text)。ok=True 表示 OK 或状态行。"""
    line = line.strip()
    if line == "OK":
        return True, "OK"
    if line.startswith("ERR "):
        return False, line[4:]
    if line.startswith("CH1 ") or line.startswith("CH2 "):
        return True, line
    if line.startswith("DIGMOD "):
        return True, line
    if line.startswith("USERBUF "):
        return True, line
    if line.startswith("DDR "):
        return True, line
    if line.startswith("PLAY "):
        return True, line
    return False, f"unparseable: {line!r}"


def parse_status(line: str) -> dict:
    """解析 'CHx WAVE .. FREQ .. AMPL .. [BASE .. H1 o a .. MODE .. MODWAVE ..]'。
    旧格式(无 BASE/HARM/调制字段)兼容:对应键为 None 或 []。"""
    parts = line.split()
    if len(parts) < 7 or parts[0] not in ("CH1", "CH2"):
        raise ValueError(f"bad status line: {line!r}")
    d = {"ch": int(parts[0][2]), "wave": parts[2],
         "freq_hz": int(parts[4]), "ampl_pct": int(parts[6]),
         "pulse_duty": 50,
         "base_pct": None, "harms": [],
         "phase_0_01": None, "phaselink": None,
         "mode": None, "mwave": None, "mfreq_01": None,
         "am_depth": None, "fm_dev": None, "pm_dev": None}
    i = 7
    while i < len(parts):
        key = parts[i]
        if key == "DUTY":
            d["pulse_duty"] = int(parts[i + 1])
            i += 2
        elif key == "BASE":
            d["base_pct"] = int(parts[i + 1])
            i += 2
        elif key in ("H1", "H2", "H3", "H4"):
            d["harms"].append((int(parts[i + 1]), int(parts[i + 2])))
            i += 3
        elif key == "MODE":
            d["mode"] = parts[i + 1]
            i += 2
        elif key == "MODWAVE":
            d["mwave"] = parts[i + 1]
            i += 2
        elif key == "MODFREQ":
            d["mfreq_01"] = int(parts[i + 1])
            i += 2
        elif key == "AMDEPTH":
            d["am_depth"] = int(parts[i + 1])
            i += 2
        elif key == "FMDEV":
            d["fm_dev"] = int(parts[i + 1])
            i += 2
        elif key == "PMDEV":
            d["pm_dev"] = int(parts[i + 1])
            i += 2
        elif key == "PHASE":
            d["phase_0_01"] = int(parts[i + 1])
            i += 2
        elif key == "PHASELINK":
            d["phaselink"] = parts[i + 1] == "ON"
            i += 2
        else:
            i += 1
    return d


_K_SUFFIX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([km]?)(?:hz)?\s*$", re.IGNORECASE)


def parse_freq_input(s: str) -> int:
    """解析频率输入:支持 100000 / 100k / 250kHz / 0.5M;范围 10..1MHz。"""
    m = _K_SUFFIX.match(s)
    if not m:
        raise ValueError(f"bad frequency input: {s!r}")
    v = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        v *= 1000
    elif unit == "m":
        v *= 1_000_000
    f = int(round(v))
    if not FREQ_MIN <= f <= FREQ_MAX:
        raise ValueError(f"freq out of range: {s!r}")
    return f


def base_cmd(ch: int, value) -> str:
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    v = int(value)
    if not 0 <= v <= 100:
        raise ValueError(f"base out of range: {value}")
    return f"SET CH{ch} BASE {v}"


def phase_cmd(ch: int, value) -> str:
    """构造 CH2 相位命令;通道和相位必须是整数。"""
    if isinstance(ch, bool) or not isinstance(ch, int) or ch != 2:
        raise ValueError(f"bad channel: {ch}")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"phase must be an integer: {value}")
    if not 0 <= value <= PHASE_MAX:
        raise ValueError(f"phase out of range: {value}")
    return f"SET CH2 PHASE {value}"


def phaselink_cmd(ch: int, on: bool) -> str:
    if isinstance(ch, bool) or not isinstance(ch, int) or ch != 2:
        raise ValueError(f"bad channel: {ch}")
    if not isinstance(on, bool):
        raise ValueError(f"phaselink must be a bool: {on}")
    return f"SET CH2 PHASELINK {'ON' if on else 'OFF'}"


def harm_cmd(ch: int, n: int, field: str, value) -> str:
    """构造 HARM 命令(规格形式 HARMn 拼接);n=1..4,field=ORDER|AMPL。"""
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    if not 1 <= n <= 4:
        raise ValueError(f"bad harmonic index: {n}")
    field = field.upper()
    v = int(value)
    if field == "ORDER":
        if v != 0 and not 2 <= v <= 8:
            raise ValueError(f"harm order out of range: {value}")
    elif field == "AMPL":
        if not 0 <= v <= 100:
            raise ValueError(f"harm ampl out of range: {value}")
    else:
        raise ValueError(f"bad field: {field}")
    return f"SET CH{ch} HARM{n} {field} {v}"


def mod_cmd(ch: int, field: str, value) -> str:
    """构造调制 SET 命令;非法参数抛 ValueError。
    field: MODE|MODWAVE(别名 MWAVE)|MODFREQ|AMDEPTH|FMDEV|PMDEV"""
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    field = field.upper()
    if field == "MODE":
        name = str(value).upper()
        if name not in MODES:
            raise ValueError(f"bad mod mode: {value}")
        return f"SET CH{ch} MODE {name}"
    if field in ("MODWAVE", "MWAVE"):   # MWAVE = 控制器内部词汇别名
        name = str(value).upper()
        if name not in MODWAVES:
            raise ValueError(f"bad mod wave: {value}")
        return f"SET CH{ch} MODWAVE {name}"
    if field == "MODFREQ":
        n = int(value)
        if not MODFREQ_MIN <= n <= MODFREQ_MAX:
            raise ValueError(f"modfreq out of range: {value}")
        return f"SET CH{ch} MODFREQ {n}"
    if field == "AMDEPTH":
        n = int(value)
        if not 0 <= n <= 100:
            raise ValueError(f"am depth out of range: {value}")
        return f"SET CH{ch} AMDEPTH {n}"
    if field == "FMDEV":
        n = int(value)
        if not 0 <= n <= FMDEV_MAX:
            raise ValueError(f"fm dev out of range: {value}")
        return f"SET CH{ch} FMDEV {n}"
    if field == "PMDEV":
        n = int(value)
        if not 0 <= n <= PMDEV_MAX:
            raise ValueError(f"pm dev out of range: {value}")
        return f"SET CH{ch} PMDEV {n}"
    raise ValueError(f"bad field: {field}")


_MODFREQ_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([km]?)(?:hz)?\s*$", re.IGNORECASE)


def parse_modfreq_input(s: str) -> int:
    """解析调制频率(Hz, 支持小数与 k/M 后缀) -> 0.1Hz 单位整数(1..500000)。"""
    m = _MODFREQ_RE.match(s)
    if not m:
        raise ValueError(f"bad mod freq input: {s!r}")
    v = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        v *= 1000
    elif unit == "m":
        v *= 1_000_000
    n = int(round(v * 10))
    if not MODFREQ_MIN <= n <= MODFREQ_MAX:
        raise ValueError(f"mod freq out of range: {s!r}")
    return n


def _channel_token(ch) -> str:
    if isinstance(ch, bool) or not isinstance(ch, int) or ch not in (1, 2):
        raise ValueError(f"bad channel: {ch!r}")
    return f"CH{ch}"


def chan_enable_cmd(ch, on: bool) -> str:
    """构造通道使能命令;非法参数抛 ValueError。"""
    if not isinstance(on, bool):
        raise ValueError(f"enable must be a bool: {on!r}")
    return f"SET {_channel_token(ch)} CHAN {'ON' if on else 'OFF'}"


def chan_noise_cmd(ch, level: int) -> str:
    """构造通道噪声命令;level 为 0..100 整数,否则抛 ValueError。"""
    if isinstance(level, bool) or not isinstance(level, int) or not CHAN_NOISE_MIN <= level <= CHAN_NOISE_MAX:
        raise ValueError(f"noise level out of range: {level!r}")
    return f"SET {_channel_token(ch)} NOISE {level}"


def chan_tap_cmd(ch, index: int, gain: int, delay: int) -> str:
    """构造通道抽头命令;index 0..3,gain -256..255,delay 0..511,否则抛 ValueError。"""
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < CHAN_TAP_COUNT:
        raise ValueError(f"tap index out of range: {index!r}")
    if isinstance(gain, bool) or not isinstance(gain, int) or not CHAN_TAP_GAIN_MIN <= gain <= CHAN_TAP_GAIN_MAX:
        raise ValueError(f"tap gain out of range: {gain!r}")
    if isinstance(delay, bool) or not isinstance(delay, int) or not CHAN_TAP_DELAY_MIN <= delay <= CHAN_TAP_DELAY_MAX:
        raise ValueError(f"tap delay out of range: {delay!r}")
    return f"SET {_channel_token(ch)} TAP {index} {gain} {delay}"


def chan_tap_clear_cmd(ch) -> str:
    """构造通道抽头清零命令。"""
    return f"SET {_channel_token(ch)} TAP CLEAR"


def chan_commit_cmd(ch) -> str:
    """构造通道配置提交命令。"""
    return f"SET {_channel_token(ch)} CHAN COMMIT"


CHAN_TONE_FREQ_MIN, CHAN_TONE_FREQ_MAX = 0, 25_000_000
CHAN_DAC_SAMPLE_RATE_HZ = 50_000_000


def chan_tone_cmd(ch, freq_hz: int, ampl: int) -> str:
    """构造窄带单音干扰命令;freq 0..25MHz, ampl 0..100,否则抛 ValueError。"""
    if (isinstance(freq_hz, bool) or not isinstance(freq_hz, int)
            or not CHAN_TONE_FREQ_MIN <= freq_hz <= CHAN_TONE_FREQ_MAX):
        raise ValueError(f"tone frequency out of range: {freq_hz!r}")
    if (isinstance(ampl, bool) or not isinstance(ampl, int)
            or not CHAN_NOISE_MIN <= ampl <= CHAN_NOISE_MAX):
        raise ValueError(f"tone amplitude out of range: {ampl!r}")
    return f"SET {_channel_token(ch)} CHAN TONE {freq_hz} {ampl}"


CHAN_IMP_PERIOD_MIN, CHAN_IMP_PERIOD_MAX = 1, 65535
CHAN_IMP_WIDTH_MIN, CHAN_IMP_WIDTH_MAX = 1, 255
CHAN_IMP_AMPL_MIN, CHAN_IMP_AMPL_MAX = -100, 100


def chan_impulse_cmd(ch, period: int, width: int, ampl: int) -> str:
    """构造周期冲激干扰命令;period 1..65535, width 1..255, ampl −100..100。"""
    if (isinstance(period, bool) or not isinstance(period, int)
            or not CHAN_IMP_PERIOD_MIN <= period <= CHAN_IMP_PERIOD_MAX):
        raise ValueError(f"impulse period out of range: {period!r}")
    if (isinstance(width, bool) or not isinstance(width, int)
            or not CHAN_IMP_WIDTH_MIN <= width <= CHAN_IMP_WIDTH_MAX):
        raise ValueError(f"impulse width out of range: {width!r}")
    if (isinstance(ampl, bool) or not isinstance(ampl, int)
            or not CHAN_IMP_AMPL_MIN <= ampl <= CHAN_IMP_AMPL_MAX):
        raise ValueError(f"impulse amplitude out of range: {ampl!r}")
    return f"SET {_channel_token(ch)} CHAN IMPULSE {period} {width} {ampl}"


def _parse_int(text):
    try:
        return int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bad integer token: {text!r}") from exc


def _parse_taps(parts):
    if len(parts) != 12:
        raise ValueError(f"expected 4 taps, got: {parts!r}")
    taps = []
    for k in range(CHAN_TAP_COUNT):
        base = k * 3
        if parts[base] != f"TAP{k}":
            raise ValueError(f"expected TAP{k}: {parts!r}")
        gain = _parse_int(parts[base + 1])
        delay = _parse_int(parts[base + 2])
        if not CHAN_TAP_GAIN_MIN <= gain <= CHAN_TAP_GAIN_MAX:
            raise ValueError(f"tap gain out of range: {gain!r}")
        if not CHAN_TAP_DELAY_MIN <= delay <= CHAN_TAP_DELAY_MAX:
            raise ValueError(f"tap delay out of range: {delay!r}")
        taps.append((gain, delay))
    return taps


def parse_chan_status(line: str) -> dict:
    """解析通道状态行（24 token 新格式 / 17 token 旧格式兼容）。"""
    parts = line.split()
    if len(parts) == 24:
        #  0    1     2      3      4      5    6     7      8        9      10     11
        # CHx CHAN ON|OFF NOISE <n> TONE <f> <a> IMPULSE <p> <w> <a> TAP0..TAP3(12 个)
        # ⚠ TONE 关键字在 parts[5]、参量在 [6]/[7]；IMPULSE 关键字在 parts[8]、参量在
        #   [9]/[10]/[11]；抽头从 parts[12] 起。整段从 5 起必须 +1，别照抄旧行的下标习惯。
        if (parts[0] not in ("CH1", "CH2") or parts[1] != "CHAN"
                or parts[2] not in ("ON", "OFF") or parts[3] != "NOISE"
                or parts[5] != "TONE" or parts[8] != "IMPULSE"):
            raise ValueError(f"bad chan status line: {line!r}")
        noise = _parse_int(parts[4])
        tone_freq = _parse_int(parts[6])
        tone_ampl = _parse_int(parts[7])
        imp_period = _parse_int(parts[9])
        imp_width = _parse_int(parts[10])
        imp_ampl = _parse_int(parts[11])
        taps = _parse_taps(parts[12:])
        tone = (tone_freq, tone_ampl)
        # ⚠ 固件初值是 chan_imp[2] = {0, 0}(main.c:307), 从未配置过冲激时 GET 发
        #   "IMPULSE 0 0 0"。period=0 不可能来自合法配置(固件要求 period>=1), 所以
        #   全 0 是"未配置"哨兵, 按旧 17-token 形式回报 impulse=None。
        #   **只认全 0**: 半 0 半非 0(如 "0 8 30")是损坏行, 仍按范围校验报错。
        #   不这样处理的话, 板子一上电/一复位, GET 的行就会被判非法 —— 实测
        #   host 侧 controller 会记 "protocol parse error" 且不发 chan_status_changed。
        impulse = None if (imp_period, imp_width, imp_ampl) == (0, 0, 0) \
            else (imp_period, imp_width, imp_ampl)
    elif len(parts) == 17:
        if (parts[0] not in ("CH1", "CH2") or parts[1] != "CHAN"
                or parts[2] not in ("ON", "OFF") or parts[3] != "NOISE"):
            raise ValueError(f"bad chan status line: {line!r}")
        noise = _parse_int(parts[4])
        taps = _parse_taps(parts[5:])
        tone = (0, 0)
        impulse = None
    else:
        raise ValueError(f"bad chan status line: {line!r}")
    if not CHAN_NOISE_MIN <= noise <= CHAN_NOISE_MAX:
        raise ValueError(f"noise out of range: {line!r}")
    if not CHAN_TONE_FREQ_MIN <= tone[0] <= CHAN_TONE_FREQ_MAX \
            or not CHAN_NOISE_MIN <= tone[1] <= CHAN_NOISE_MAX:
        raise ValueError(f"tone out of range: {line!r}")
    if impulse is not None and (
            not CHAN_IMP_PERIOD_MIN <= impulse[0] <= CHAN_IMP_PERIOD_MAX
            or not CHAN_IMP_WIDTH_MIN <= impulse[1] <= CHAN_IMP_WIDTH_MAX
            or not CHAN_IMP_AMPL_MIN <= impulse[2] <= CHAN_IMP_AMPL_MAX):
        raise ValueError(f"impulse out of range: {line!r}")
    return {"enabled": parts[2] == "ON", "noise": noise,
            "tone": tone, "impulse": impulse, "taps": taps}


# ---------- 复数 I/Q 损伤（顶层家族 IQIMP） ----------
# 该级是**通道对级单实例**, 所以不挂 CH1/CH2, 自成家族(与 SET/GET CHx 平级)。
# 静态三项(相位偏移 / I/Q 增益失配 / 正交相位误差)由主机合成为一个 Q2.14 矩阵,
# 硬件不知道用户设的是哪一项 —— 这是本结构的核心简化。
# 频偏硬件能到 ±25 MHz, 协议按实际用途收窄到 ±1 MHz(超出返回具名错误)。
IQIMP_PHASE_MIN, IQIMP_PHASE_MAX = -18000, 18000          # 0.01 度单位
IQIMP_FREQOFF_MIN, IQIMP_FREQOFF_MAX = -1_000_000, 1_000_000
IQIMP_GAIN_MIN, IQIMP_GAIN_MAX = -32768, 32767            # Q2.14 原始码, 16384 = 1.0
IQIMP_PHASEERR_MIN, IQIMP_PHASEERR_MAX = -18000, 18000


def _iqimp_checked(value, low, high, what):
    # bool 是 int 的子类: 不单独拦的话 True 会变成 "1" 悄悄下发
    if (isinstance(value, bool) or not isinstance(value, int)
            or not low <= value <= high):
        raise ValueError(f"{what} out of range: {value!r}")
    return value


def iqimp_on_cmd() -> str:
    """使能复数 I/Q 级(走 GPIO, 立即生效)。"""
    return "SET IQIMP ON"


def iqimp_off_cmd() -> str:
    """旁路复数 I/Q 级(逐位透传, 立即生效)。"""
    return "SET IQIMP OFF"


def iqimp_commit_cmd() -> str:
    """锁存 I/Q 数值参数(BRAM 字 8/9/10 由 chan_cfg 在 COMMIT 时排空)。"""
    return "SET IQIMP COMMIT"


def iqimp_phase_cmd(deg_x100: int) -> str:
    """整体相位偏移, 0.01 度单位, -18000..18000。"""
    _iqimp_checked(deg_x100, IQIMP_PHASE_MIN, IQIMP_PHASE_MAX, "iqimp phase")
    return f"SET IQIMP PHASE {deg_x100}"


def iqimp_freqoff_cmd(hz: int) -> str:
    """频率偏移(Hz), 协议限 ±1MHz。"""
    _iqimp_checked(hz, IQIMP_FREQOFF_MIN, IQIMP_FREQOFF_MAX, "iqimp freqoff")
    return f"SET IQIMP FREQOFF {hz}"


def iqimp_gain_cmd(q14: int) -> str:
    """I 轴增益, Q2.14 原始码(16384 = 1.0); 便于无损回读。"""
    _iqimp_checked(q14, IQIMP_GAIN_MIN, IQIMP_GAIN_MAX, "iqimp gain")
    return f"SET IQIMP GAIN {q14}"


def iqimp_phaseerr_cmd(deg_x100: int) -> str:
    """正交相位误差, 0.01 度单位, -18000..18000。"""
    _iqimp_checked(deg_x100, IQIMP_PHASEERR_MIN, IQIMP_PHASEERR_MAX,
                   "iqimp phase error")
    return f"SET IQIMP PHASEERR {deg_x100}"


def get_iqimp_cmd() -> str:
    """构造 I/Q 损伤状态查询命令。"""
    return "GET IQIMP"


def parse_iqimp_status(line: str) -> dict:
    """解析 `GET IQIMP` 的状态行。

    格式 10 个 token:
        IQIMP ON|OFF PHASE <deg_x100> FREQOFF <hz> GAIN <q14> PHASEERR <deg_x100>
    回读的是用户下发的语义值(固件影子数组), 与是否已 COMMIT 无关。
    """
    parts = line.split()
    #   0     1       2      3     4        5    6     7      8         9
    if (len(parts) != 10 or parts[0] != "IQIMP"
            or parts[1] not in ("ON", "OFF")
            or parts[2] != "PHASE" or parts[4] != "FREQOFF"
            or parts[6] != "GAIN" or parts[8] != "PHASEERR"):
        raise ValueError(f"bad iqimp status line: {line!r}")
    phase = _parse_int(parts[3])
    freqoff = _parse_int(parts[5])
    gain = _parse_int(parts[7])
    phaseerr = _parse_int(parts[9])
    _iqimp_checked(phase, IQIMP_PHASE_MIN, IQIMP_PHASE_MAX, "iqimp phase")
    _iqimp_checked(freqoff, IQIMP_FREQOFF_MIN, IQIMP_FREQOFF_MAX, "iqimp freqoff")
    _iqimp_checked(gain, IQIMP_GAIN_MIN, IQIMP_GAIN_MAX, "iqimp gain")
    _iqimp_checked(phaseerr, IQIMP_PHASEERR_MIN, IQIMP_PHASEERR_MAX,
                   "iqimp phase error")
    return {"enabled": parts[1] == "ON", "phase": phase, "freqoff": freqoff,
            "gain": gain, "phaseerr": phaseerr}
