import pytest

from siggen.protocol import (
    set_cmd, get_cmd, parse_response, parse_status,
    parse_freq_input, base_cmd, phase_cmd, phaselink_cmd,
    harm_cmd, mod_cmd, parse_modfreq_input,
    digmod_cmd, digmod_symrate_cmd, digmod_filter_cmd, digmod_rolloff_cmd,
    digmod_seed_cmd, digmod_reset_cmd,
    DIGMOD_ROLLOFFS,
    get_digmod_cmd,
    parse_digmod_status,
    crc8_atm, crc16_ccitt, encode_hex, decode_hex,
    user_clear_cmd, user_chunk_cmd, user_commit_cmd,
    user_abort_cmd, user_loop_cmd, digmod_source_cmd,
    parse_userbuf_status,
    play_mode_cmd, play_repeat_cmd, play_trigger_cmd,
    play_arm_cmd, play_start_cmd, play_stop_cmd, play_clear_cmd,
    get_play_cmd, parse_play_status,
    ddr_clear_cmd, ddr_chunk_cmd, ddr_commit_cmd, ddr_abort_cmd,
    ddr_start_cmd, ddr_stop_cmd, get_ddr_cmd, parse_ddr_status,
    DDR_BUFFER_BYTES, DDR_CHUNK_MAX_BYTES,
    PLAY_STATES,
    chan_commit_cmd, chan_enable_cmd, chan_noise_cmd,
    chan_tap_cmd, chan_tap_clear_cmd, parse_chan_status,
    CHAN_IMP_AMPL_MAX, CHAN_IMP_AMPL_MIN,
    chan_impulse_cmd, chan_tone_cmd,
)


def test_set_wave():
    assert set_cmd(1, "wave", "PULSE") == "SET CH1 WAVE PULSE"
    assert set_cmd(2, "wave", "noise") == "SET CH2 WAVE NOISE"
    assert set_cmd(1, "duty", 25) == "SET CH1 DUTY 25"
    assert set_cmd(2, "DUTY", 100) == "SET CH2 DUTY 100"


def test_set_freq():
    assert set_cmd(1, "freq", 100000) == "SET CH1 FREQ 100000"
    assert set_cmd(1, "freq", 10) == "SET CH1 FREQ 10"
    assert set_cmd(1, "freq", 1000000) == "SET CH1 FREQ 1000000"


def test_set_ampl():
    assert set_cmd(1, "ampl", 80) == "SET CH1 AMPL 80"
    assert set_cmd(1, "ampl", 0) == "SET CH1 AMPL 0"
    assert set_cmd(1, "ampl", 100) == "SET CH1 AMPL 100"


def test_set_validation():
    with pytest.raises(ValueError):
        set_cmd(3, "wave", "SINE")              # 坏通道
    with pytest.raises(ValueError):
        set_cmd(1, "wave", "BOGUS")             # 坏波形
    with pytest.raises(ValueError):
        set_cmd(1, "freq", 9)                   # 低于下限
    with pytest.raises(ValueError):
        set_cmd(1, "freq", 1000001)             # 高于上限
    with pytest.raises(ValueError):
        set_cmd(1, "ampl", -1)
    with pytest.raises(ValueError):
        set_cmd(1, "ampl", 101)
    with pytest.raises(ValueError):
        set_cmd(1, "duty", -1)
    with pytest.raises(ValueError):
        set_cmd(1, "duty", 101)
    with pytest.raises(ValueError):
        set_cmd(1, "foo", 1)                    # 坏字段


def test_get_cmd():
    assert get_cmd(1) == "GET CH1"
    assert get_cmd(2) == "GET CH2"
    with pytest.raises(ValueError):
        get_cmd(0)


def test_parse_response():
    assert parse_response("OK") == (True, "OK")
    assert parse_response("OK\r\n") == (True, "OK")
    ok, msg = parse_response("ERR FREQ OUT OF RANGE")
    assert ok is False and msg == "FREQ OUT OF RANGE"
    ok, msg = parse_response("ERR BAD VALUE")
    assert ok is False and msg == "BAD VALUE"
    ok, line = parse_response("CH1 WAVE SINE FREQ 100000 AMPL 80")
    assert ok is True
    ok, msg = parse_response("garbage")
    assert ok is False and "garbage" in msg


def test_parse_status():
    d = parse_status("CH1 WAVE SINE FREQ 100000 AMPL 80")
    assert d == {"ch": 1, "wave": "SINE", "freq_hz": 100000, "ampl_pct": 80,
                 "pulse_duty": 50,
                 "base_pct": None, "harms": [],
                 "phase_0_01": None, "phaselink": None,
                 "mode": None, "mwave": None, "mfreq_01": None,
                 "am_depth": None, "fm_dev": None, "pm_dev": None}
    d = parse_status("CH2 WAVE SAW FREQ 2500 AMPL 20")
    assert d == {"ch": 2, "wave": "SAW", "freq_hz": 2500, "ampl_pct": 20,
                 "pulse_duty": 50,
                 "base_pct": None, "harms": [],
                 "phase_0_01": None, "phaselink": None,
                 "mode": None, "mwave": None, "mfreq_01": None,
                 "am_depth": None, "fm_dev": None, "pm_dev": None}


def test_parse_status_pulse_duty():
    d = parse_status("CH1 WAVE PULSE FREQ 1000 AMPL 80 DUTY 25")
    assert d["wave"] == "PULSE" and d["pulse_duty"] == 25


    assert parse_freq_input("100000") == 100000
    assert parse_freq_input("100k") == 100000
    assert parse_freq_input("250kHz") == 250000
    assert parse_freq_input("0.5M") == 500000
    assert parse_freq_input(" 100k ") == 100000
    with pytest.raises(ValueError):
        parse_freq_input("abc")
    with pytest.raises(ValueError):
        parse_freq_input("1.5M")          # 超 1MHz 上限
    with pytest.raises(ValueError):
        parse_freq_input("5")             # 低于 10Hz


def test_base_cmd():
    assert base_cmd(1, 100) == "SET CH1 BASE 100"
    assert base_cmd(2, 0) == "SET CH2 BASE 0"
    with pytest.raises(ValueError):
        base_cmd(1, 101)
    with pytest.raises(ValueError):
        base_cmd(3, 50)


def test_phase_cmd():
    assert phase_cmd(2, 0) == "SET CH2 PHASE 0"
    assert phase_cmd(2, 36000) == "SET CH2 PHASE 36000"
    with pytest.raises(ValueError):
        phase_cmd(1, 0)


@pytest.mark.parametrize("ch, value", [
    (3, 0), (1.0, 0), ("1", 0), (None, 0), (True, 0),
    (2, -1), (2, 36001), (2, 1.5), (2, True), (2, None), (2, "1"),
])
def test_phase_cmd_rejects_bad_channel_or_non_integer(ch, value):
    with pytest.raises(ValueError):
        phase_cmd(ch, value)


def test_phaselink_cmd():
    assert phaselink_cmd(2, True) == "SET CH2 PHASELINK ON"
    assert phaselink_cmd(2, False) == "SET CH2 PHASELINK OFF"
    with pytest.raises(ValueError):
        phaselink_cmd(1, True)


@pytest.mark.parametrize("on", ["OFF", 1, None])
def test_phaselink_cmd_rejects_non_bool(on):
    with pytest.raises(ValueError):
        phaselink_cmd(2, on)


def test_harm_cmd():
    assert harm_cmd(1, 2, "ORDER", 3) == "SET CH1 HARM2 ORDER 3"
    assert harm_cmd(1, 2, "AMPL", 30) == "SET CH1 HARM2 AMPL 30"
    assert harm_cmd(1, 1, "order", 0) == "SET CH1 HARM1 ORDER 0"
    with pytest.raises(ValueError):
        harm_cmd(1, 5, "ORDER", 2)        # n 超 4
    with pytest.raises(ValueError):
        harm_cmd(1, 1, "ORDER", 1)        # 次数 1 非法
    with pytest.raises(ValueError):
        harm_cmd(1, 1, "ORDER", 9)
    with pytest.raises(ValueError):
        harm_cmd(1, 1, "AMPL", 101)
    with pytest.raises(ValueError):
        harm_cmd(1, 1, "FOO", 2)


def test_parse_status_extended():
    d = parse_status("CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 H2 5 8 H3 0 0 H4 0 0 "
                     "PHASE 12345 PHASELINK ON")
    assert d["base_pct"] == 68
    assert d["harms"] == [(3, 20), (5, 8), (0, 0), (0, 0)]
    assert d["phase_0_01"] == 12345 and d["phaselink"] is True
    d_off = parse_status("CH2 WAVE SAW FREQ 2500 AMPL 20 PHASE 36000 PHASELINK OFF")
    assert d_off["phase_0_01"] == 36000 and d_off["phaselink"] is False


def test_parse_status_legacy():
    # 旧格式(无 BASE/HARM)仍可解析
    d = parse_status("CH1 WAVE SINE FREQ 100000 AMPL 80")
    assert d["base_pct"] is None and d["harms"] == []
    assert d["phase_0_01"] is None and d["phaselink"] is None


def test_mod_cmd():
    assert mod_cmd(1, "MODE", "FM") == "SET CH1 MODE FM"
    assert mod_cmd(1, "mode", "sweep") == "SET CH1 MODE SWEEP"
    assert mod_cmd(2, "MODWAVE", "TRIANGLE") == "SET CH2 MODWAVE TRIANGLE"
    assert mod_cmd(1, "mwave", "sine") == "SET CH1 MODWAVE SINE"   # 内部词汇别名
    assert mod_cmd(1, "MODFREQ", 100) == "SET CH1 MODFREQ 100"
    assert mod_cmd(1, "MODFREQ", 500000) == "SET CH1 MODFREQ 500000"
    assert mod_cmd(1, "AMDEPTH", 80) == "SET CH1 AMDEPTH 80"
    assert mod_cmd(1, "AMDEPTH", 100) == "SET CH1 AMDEPTH 100"
    assert mod_cmd(1, "AMDEPTH", 0) == "SET CH1 AMDEPTH 0"
    assert mod_cmd(1, "FMDEV", 10000) == "SET CH1 FMDEV 10000"
    assert mod_cmd(1, "FMDEV", 500000) == "SET CH1 FMDEV 500000"
    assert mod_cmd(1, "PMDEV", 64) == "SET CH1 PMDEV 64"
    assert mod_cmd(1, "PMDEV", 511) == "SET CH1 PMDEV 511"
    with pytest.raises(ValueError):
        mod_cmd(1, "MODE", "FOO")
    with pytest.raises(ValueError):
        mod_cmd(1, "MODWAVE", "BOGUS")
    with pytest.raises(ValueError):
        mod_cmd(1, "MODWAVE", "PULSE")
    with pytest.raises(ValueError):
        mod_cmd(1, "MODFREQ", 0)
    with pytest.raises(ValueError):
        mod_cmd(1, "MODFREQ", 500001)
    with pytest.raises(ValueError):
        mod_cmd(1, "AMDEPTH", 101)
    with pytest.raises(ValueError):
        mod_cmd(1, "FMDEV", 500001)
    with pytest.raises(ValueError):
        mod_cmd(1, "PMDEV", 512)
    with pytest.raises(ValueError):
        mod_cmd(3, "MODE", "AM")
    with pytest.raises(ValueError):
        mod_cmd(1, "FOO", 1)


def test_parse_modfreq_input():
    assert parse_modfreq_input("1") == 10        # 1.0Hz
    assert parse_modfreq_input("1.5") == 15
    assert parse_modfreq_input("10k") == 100000  # 10kHz
    assert parse_modfreq_input("10K") == 100000  # 后缀大小写不敏感
    assert parse_modfreq_input("0.001M") == 10000  # 1kHz
    assert parse_modfreq_input(" 1.5 ") == 15      # 空白容忍
    assert parse_modfreq_input("0.1") == 1       # 0.1Hz 下限
    assert parse_modfreq_input("50k") == 500000  # 50kHz 上限
    assert parse_modfreq_input("1kHz") == 10000  # 1kHz = 1000Hz -> 0.1Hz 单位
    assert parse_modfreq_input("10hz") == 100    # 10Hz -> 0.1Hz 单位
    with pytest.raises(ValueError):
        parse_modfreq_input("0.05")
    with pytest.raises(ValueError):
        parse_modfreq_input("abc")
    with pytest.raises(ValueError):
        parse_modfreq_input("51k")


def test_parse_status_mod():
    d = parse_status("CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 "
                     "H2 0 0 H3 0 0 H4 0 0 MODE FM MODWAVE SQUARE MODFREQ 100 "
                     "AMDEPTH 80 FMDEV 10000 PMDEV 64")
    assert d["mode"] == "FM" and d["mwave"] == "SQUARE"
    assert d["mfreq_01"] == 100 and d["am_depth"] == 80
    assert d["fm_dev"] == 10000 and d["pm_dev"] == 64
    d2 = parse_status("CH1 WAVE SINE FREQ 1000 AMPL 50")
    assert d2["mode"] is None and d2["fm_dev"] is None   # 旧格式兼容


def test_digmod_seed_commands_validate_and_build():
    assert digmod_seed_cmd(1) == "SET DIGMOD SEED 1"
    assert digmod_seed_cmd(127) == "SET DIGMOD SEED 127"
    assert digmod_reset_cmd() == "SET DIGMOD RESET"
    for bad in (0, 128, -1, "5", True, 5.0):
        with pytest.raises(ValueError):
            digmod_seed_cmd(bad)


def test_parse_digmod_status_accepts_seed():
    assert parse_digmod_status(
        "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.5 "
        "SEED 5 SYMRATE 100000 STATE RUNNING"
    ) == ("QPSK", "PRBS7", "RRC", "0.5", 5, 100000, True)


@pytest.mark.parametrize("bad", ["QAM8", "8qam", "", None, 5])
def test_digmod_cmd_rejects_invalid(bad):
    with pytest.raises(ValueError):
        digmod_cmd(bad)


def test_digmod_symrate_cmd_builds_and_validates():
    assert digmod_symrate_cmd(100000) == "SET DIGMOD SYMRATE 100000"
    for bad in (9, 1_000_001, "100k", True):
        with pytest.raises(ValueError):
            digmod_symrate_cmd(bad)


def test_digmod_filter_command_and_new_get_status():
    from siggen.protocol import DIGMOD_FILTERS

    assert DIGMOD_FILTERS == ("RECT", "RRC")
    assert digmod_filter_cmd("rrc") == "SET DIGMOD FILTER RRC"
    assert parse_digmod_status(
        "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC SYMRATE 100000 STATE RUNNING"
    ) == ("QPSK", "PRBS7", "RRC", "0.35", 127, 100000, True)


@pytest.mark.parametrize("rolloff", ["0.2", "0.35", "0.5"])
def test_digmod_rolloff_command_builds_all_supported_values(rolloff):
    assert DIGMOD_ROLLOFFS == ("0.2", "0.35", "0.5")
    assert digmod_rolloff_cmd(rolloff) == f"SET DIGMOD ROLLOFF {rolloff}"


def test_digmod_rolloff_command_uses_string_value():
    assert digmod_rolloff_cmd(0.35) == "SET DIGMOD ROLLOFF 0.35"


@pytest.mark.parametrize("value", [" 0.2", "0.2 ", "0.50", "0.3", "", None, True, 5])
def test_digmod_rolloff_command_rejects_noncanonical_values(value):
    with pytest.raises(ValueError):
        digmod_rolloff_cmd(value)


def test_parse_digmod_status_accepts_explicit_rolloff():
    assert parse_digmod_status(
        "DIGMOD MODE QAM64 SOURCE PRBS7 FILTER RRC ROLLOFF 0.5 "
        "SYMRATE 100000 STATE RUNNING"
    ) == ("QAM64", "PRBS7", "RRC", "0.5", 127, 100000, True)


def test_old_get_status_defaults_filter_to_rect_and_rolloff():
    assert parse_digmod_status(
        "DIGMOD MODE OFF SOURCE PRBS7 SYMRATE 10000 STATE STOPPED"
    ) == ("OFF", "PRBS7", "RECT", "0.35", 127, 10000, False)


@pytest.mark.parametrize("value", ["TRIANGLE", "", None, True])
def test_digmod_filter_command_rejects_unknown_values(value):
    with pytest.raises(ValueError):
        digmod_filter_cmd(value)


@pytest.mark.parametrize("mode", ["OFF", "BPSK", "QPSK", "QAM4", "QAM16", "QAM64"])
def test_parse_digmod_status_accepts_all(mode):
    running = mode != "OFF"
    state = "RUNNING" if running else "STOPPED"
    line = f"DIGMOD MODE {mode} SOURCE PRBS7 SYMRATE 100000 STATE {state}"
    assert parse_digmod_status(line) == (mode, "PRBS7", "RECT", "0.35", 127, 100000, running)


@pytest.mark.parametrize(
    "bad",
    ["DIGMOD MODE", "DIGMOD MODE QAM8", "CH1 WAVE SINE", "DIGMOD FOO OFF",
     "DIGMOD MODE QPSK SOURCE CSV SYMRATE 100000 STATE RUNNING",
     "DIGMOD MODE OFF SOURCE PRBS7 SYMRATE 100000 STATE RUNNING",
     "DIGMOD MODE QPSK SOURCE PRBS7 SYMRATE 9 STATE RUNNING",
     "DIGMOD MODE QPSK SOURCE PRBS7 FILTER BAND SYMRATE 100000 STATE RUNNING",
     "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC SYMRATE 100000 STATE RUNNING EXTRA",
     "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROUND 0.35 SYMRATE 100000 STATE RUNNING",
     "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.50 SYMRATE 100000 STATE RUNNING",
     "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.35 RATE 100000 STATE RUNNING",
     "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.35 SYMRATE 9 STATE RUNNING",
     "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.35 SYMRATE 100000 STATE STOPPED",
     "DIGMOD MODE OFF SOURCE PRBS7 FILTER RECT ROLLOFF 0.35 SYMRATE 100000 STATE RUNNING",
     "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC ROLLOFF 0.35 SYMRATE 100000 STATE RUNNING EXTRA"],
)
def test_parse_digmod_status_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_digmod_status(bad)


def test_user_crc_vectors_and_hex_codec():
    assert crc8_atm(b"123456789") == 0xF4
    assert crc16_ccitt(b"123456789") == 0x29B1
    assert encode_hex(bytes([0x00, 0x01, 0xA5, 0xFF])) == "0001A5FF"
    assert decode_hex("00 01 a5 ff") == b"\x00\x01\xA5\xFF"
    with pytest.raises(ValueError):
        decode_hex("ABC")





def test_user_codecs_reject_non_bytes_values():
    for codec in (crc8_atm, crc16_ccitt, encode_hex):
        with pytest.raises(ValueError):
            codec(1)


def test_user_commands_validate_and_build():
    payload = bytes(range(20))
    assert user_clear_cmd() == "SET USER CLEAR"
    assert user_chunk_cmd(0, payload) == (
        "SET USER CHUNK 0 000102030405060708090A0B0C0D0E0F10111213 27"
    )
    assert user_commit_cmd(160, 0x29B1) == "SET USER COMMIT 160 29B1"
    assert user_abort_cmd() == "SET USER ABORT"
    assert user_loop_cmd(True) == "SET USER LOOP ON"
    assert user_loop_cmd(False) == "SET USER LOOP OFF"
    assert digmod_source_cmd("user") == "SET DIGMOD SOURCE USER"


@pytest.mark.parametrize("offset", [-1, 512])
def test_user_chunk_rejects_invalid_offset(offset):
    with pytest.raises(ValueError):
        user_chunk_cmd(offset, b"x")


@pytest.mark.parametrize("payload", [b"", bytes(range(21)), "00"])
def test_user_chunk_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        user_chunk_cmd(0, payload)


@pytest.mark.parametrize("bit_length", [0, 7, 4097, 16 * 8 + 1])
def test_user_commit_rejects_invalid_bit_length(bit_length):
    with pytest.raises(ValueError):
        user_commit_cmd(bit_length, 0)


def test_parse_userbuf_status():
    status = parse_userbuf_status(
        "USERBUF VALID 1 LENGTH 16 CRC 29B1 BANK 1 UPLOAD IDLE LOOP ON "
        "COMMIT_SEEN 1 READY 1 DONE 0 UNDERFLOW 0"
    )
    assert status == {
        "valid": True, "bit_length": 16, "crc16": 0x29B1, "bank": 1,
        "upload": "IDLE", "loop": True, "commit_seen": True,
        "ready": True, "done": False, "underflow": False,
    }








def test_parse_digmod_status_accepts_user_source():
    assert parse_digmod_status(
        "DIGMOD MODE QPSK SOURCE USER FILTER RECT ROLLOFF 0.35 "
        "SEED 127 SYMRATE 100000 STATE RUNNING"
    ) == ("QPSK", "USER", "RECT", "0.35", 127, 100000, True)





def test_parse_response_accepts_userbuf_status_line():
    ok, text = parse_response("USERBUF VALID 1 LENGTH 8 CRC 29B1 BANK 0 UPLOAD IDLE "
                              "LOOP OFF COMMIT_SEEN 1 READY 1 DONE 0 UNDERFLOW 0")
    assert ok is True
    assert text.startswith("USERBUF ")


def test_parse_response_accepts_digmod_status_line():
    ok, text = parse_response("DIGMOD MODE QPSK")
    assert ok is True
    assert text == "DIGMOD MODE QPSK"


def test_play_commands_build_and_validate():
    assert play_mode_cmd("manual") == "SET PLAY MODE MANUAL"
    assert play_repeat_cmd(0) == "SET PLAY REPEAT 0"
    assert play_repeat_cmd(65535) == "SET PLAY REPEAT 65535"
    assert play_trigger_cmd("external") == "SET PLAY TRIGGER EXTERNAL"
    assert play_arm_cmd() == "SET PLAY ARM"
    assert play_start_cmd() == "SET PLAY START"
    assert play_stop_cmd() == "SET PLAY STOP"
    assert play_clear_cmd() == "SET PLAY CLEAR"
    assert get_play_cmd() == "GET PLAY"
    for func, value in ((play_mode_cmd, "bad"), (play_trigger_cmd, "bad")):
        with pytest.raises(ValueError):
            func(value)
    for value in (-1, 65536, True, "1"):
        with pytest.raises(ValueError):
            play_repeat_cmd(value)


def test_parse_play_status():
    line = ("PLAY STATE RUNNING FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 1 "
            "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 2 ERROR 00")
    status = parse_play_status(line)
    assert status == {
        "state": "RUNNING", "frame_done": False, "underflow": False,
        "trigger_seen": True, "stop_pending": False, "error_valid": False,
        "repeat_remaining": 2, "error_code": 0,
    }
    assert PLAY_STATES[3] == "RUNNING"
    with pytest.raises(ValueError):
        parse_play_status(line.replace("ERROR 00", "ERROR GG"))
    with pytest.raises(ValueError):
        parse_play_status(line.replace("REPEAT_REMAINING 2", "REPEAT_REMAINING 65536"))


def test_ddr_commands_build_with_independent_namespace():
    payload = bytes(range(DDR_CHUNK_MAX_BYTES))
    assert DDR_BUFFER_BYTES == 1 << 20
    assert ddr_clear_cmd() == "SET DDR CLEAR"
    assert ddr_chunk_cmd(32, payload) == (
        "SET DDR CHUNK 32 000102030405060708090A0B0C0D0E0F10111213 27"
    )
    assert ddr_commit_cmd(160, 0x29B1) == "SET DDR COMMIT 160 29B1"
    assert ddr_abort_cmd() == "SET DDR ABORT"
    assert ddr_start_cmd() == "SET DDR START"
    assert ddr_stop_cmd() == "SET DDR STOP"
    assert get_ddr_cmd() == "GET DDR"


@pytest.mark.parametrize("offset", [-1, DDR_BUFFER_BYTES])
def test_ddr_chunk_rejects_invalid_offset(offset):
    with pytest.raises(ValueError):
        ddr_chunk_cmd(offset, b"x")


@pytest.mark.parametrize("payload", [b"", bytes(range(DDR_CHUNK_MAX_BYTES + 1)), "00"])
def test_ddr_chunk_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        ddr_chunk_cmd(0, payload)


def test_ddr_chunk_rejects_crossing_buffer_end():
    with pytest.raises(ValueError):
        ddr_chunk_cmd(DDR_BUFFER_BYTES - 1, b"ab")


@pytest.mark.parametrize("bit_length", [0, 7, DDR_BUFFER_BYTES * 8 + 1])
def test_ddr_commit_rejects_invalid_bit_length(bit_length):
    with pytest.raises(ValueError):
        ddr_commit_cmd(bit_length, 0)


def test_parse_ddr_status():
    status = parse_ddr_status(
        "DDR VALID 1 BUFFER 00300000 MAX 1048576 LENGTH 160 CRC 29B1 "
        "UPLOAD IDLE ACTIVE 0 DMA READY ERROR 00"
    )
    assert status == {
        "valid": True, "buffer": 0x00300000, "max_bytes": 1048576,
        "bit_length": 160, "crc16": 0x29B1, "upload": "IDLE",
        "active": False, "dma_state": "READY", "error_code": 0,
    }
    with pytest.raises(ValueError):
        parse_ddr_status("DDR VALID 1 BUFFER 00300000 MAX 1048576 LENGTH 7 CRC 0000 "
                         "UPLOAD IDLE ACTIVE 0 DMA READY ERROR 00")


def test_parse_ddr_status_accepts_dma_drain_state():
    status = parse_ddr_status(
        "DDR VALID 1 BUFFER 00300000 MAX 1048576 LENGTH 160 CRC 29B1 "
        "UPLOAD IDLE ACTIVE 1 DMA DRAIN ERROR 00"
    )
    assert status["active"] is True
    assert status["dma_state"] == "DRAIN"


def test_chan_command_builders():
    assert chan_enable_cmd(1, True) == "SET CH1 CHAN ON"
    assert chan_enable_cmd(2, False) == "SET CH2 CHAN OFF"
    assert chan_noise_cmd(1, 40) == "SET CH1 NOISE 40"
    assert chan_tap_cmd(2, 3, -256, 511) == "SET CH2 TAP 3 -256 511"
    assert chan_tap_clear_cmd(1) == "SET CH1 TAP CLEAR"
    assert chan_commit_cmd(2) == "SET CH2 CHAN COMMIT"


@pytest.mark.parametrize("bad", [(-257, 0), (256, 0), (0, -1), (0, 512)])
def test_chan_tap_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        chan_tap_cmd(1, 0, *bad)


def test_chan_noise_rejects_out_of_range():
    with pytest.raises(ValueError):
        chan_noise_cmd(1, 101)


@pytest.mark.parametrize("level", [-1, 101, True, "40", None])
def test_chan_noise_rejects_bad_level(level):
    with pytest.raises(ValueError):
        chan_noise_cmd(1, level)


@pytest.mark.parametrize("index", [-1, 4, True, "0"])
def test_chan_tap_rejects_bad_index(index):
    with pytest.raises(ValueError):
        chan_tap_cmd(1, index, 0, 0)


@pytest.mark.parametrize("ch", [0, 3, True, "1", None])
def test_chan_builders_reject_bad_channel(ch):
    with pytest.raises(ValueError):
        chan_enable_cmd(ch, True)
    with pytest.raises(ValueError):
        chan_noise_cmd(ch, 50)
    with pytest.raises(ValueError):
        chan_tap_cmd(ch, 0, 0, 0)
    with pytest.raises(ValueError):
        chan_tap_clear_cmd(ch)
    with pytest.raises(ValueError):
        chan_commit_cmd(ch)


@pytest.mark.parametrize("on", ["ON", 1, None])
def test_chan_enable_rejects_non_bool(on):
    with pytest.raises(ValueError):
        chan_enable_cmd(1, on)


def test_parse_chan_status():
    line = "CH1 CHAN ON NOISE 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 3"
    parsed = parse_chan_status(line)
    assert parsed["enabled"] is True and parsed["noise"] == 40
    assert parsed["taps"] == [(255, 0), (-128, 2), (0, 0), (0, 3)]


def test_parse_chan_status_off():
    line = "CH2 CHAN OFF NOISE 0 TAP0 0 0 TAP1 0 0 TAP2 0 0 TAP3 0 0"
    parsed = parse_chan_status(line)
    assert parsed["enabled"] is False and parsed["noise"] == 0
    assert parsed["taps"] == [(0, 0)] * 4


@pytest.mark.parametrize("bad", [
    # 12 tokens (missing TAP3)
    "CH1 CHAN ON NOISE 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0",
    # extra token
    "CH1 CHAN ON NOISE 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 3 EXTRA",
    # bad channel token
    "CH3 CHAN ON NOISE 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 3",
    # bad CHAN keyword
    "CH1 CHANNEL ON NOISE 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 3",
    # bad ON/OFF token
    "CH1 CHAN MAYBE NOISE 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 3",
    # bad NOISE keyword
    "CH1 CHAN ON NOISY 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 3",
    # out-of-range noise
    "CH1 CHAN ON NOISE 101 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 3",
    # wrong tap keyword
    "CH1 CHAN ON NOISE 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP4 0 3",
    # non-integer tap value
    "CH1 CHAN ON NOISE 40 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 x 3",
])
def test_parse_chan_status_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_chan_status(bad)


def test_chan_tone_cmd_builds_expected_line():
    assert chan_tone_cmd(1, 1000000, 20) == "SET CH1 CHAN TONE 1000000 20"
    assert chan_tone_cmd(2, 0, 0) == "SET CH2 CHAN TONE 0 0"


def test_chan_tone_cmd_rejects_out_of_range():
    with pytest.raises(ValueError):
        chan_tone_cmd(1, -1, 10)
    with pytest.raises(ValueError):
        chan_tone_cmd(1, 25_000_001, 10)
    with pytest.raises(ValueError):
        chan_tone_cmd(1, 1000, 101)


def test_chan_impulse_cmd_builds_expected_line():
    assert chan_impulse_cmd(1, 1000, 8, 30) == "SET CH1 CHAN IMPULSE 1000 8 30"
    assert chan_impulse_cmd(2, 1, 1, CHAN_IMP_AMPL_MIN) == "SET CH2 CHAN IMPULSE 1 1 -100"


def test_chan_impulse_cmd_rejects_out_of_range():
    with pytest.raises(ValueError):
        chan_impulse_cmd(1, 0, 1, 10)
    with pytest.raises(ValueError):
        chan_impulse_cmd(1, 65536, 1, 10)
    with pytest.raises(ValueError):
        chan_impulse_cmd(1, 10, 256, 10)
    with pytest.raises(ValueError):
        chan_impulse_cmd(1, 10, 1, CHAN_IMP_AMPL_MAX + 1)


def test_parse_chan_status_reads_new_24_token_line():
    line = ("CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 30 "
            "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0")
    status = parse_chan_status(line)
    assert status["enabled"] is True
    assert status["noise"] == 30
    assert status["tone"] == (1000000, 20)
    assert status["impulse"] == (1000, 8, 30)
    assert status["taps"] == [(255, 0), (0, 0), (0, 0), (0, 0)]


def test_parse_chan_status_accepts_legacy_17_token_line():
    line = "CH1 CHAN OFF NOISE 0 TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0"
    status = parse_chan_status(line)
    assert status["tone"] == (0, 0)
    assert status["impulse"] is None
    assert status["noise"] == 0


def test_parse_chan_status_accepts_fresh_board_idle_line():
    """上电/复位后 chan_imp 全 0, GET 发 IMPULSE 0 0 0 —— 必须能解析。

    这一条是板上联调的前提: 解析失败会让 controller 记 protocol parse error
    且不发 chan_status_changed, GUI 状态栏对所有通道恒显示 "--"。
    """
    line = ("CH1 CHAN OFF NOISE 0 TONE 0 0 IMPULSE 0 0 0 "
            "TAP0 0 0 TAP1 0 0 TAP2 0 0 TAP3 0 0")
    status = parse_chan_status(line)
    assert status["enabled"] is False
    assert status["impulse"] is None      # 全 0 == 从未配置, 与 17-token 形式一致
    assert status["tone"] == (0, 0)
    assert status["taps"] == [(0, 0)] * 4


@pytest.mark.parametrize("bad", [
    # TONE keyword misplaced (off-by-one habit: "TONE" written where the freq belongs)
    "CH1 CHAN ON NOISE 30 1000000 TONE 20 IMPULSE 1000 8 30 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0",
    # tone amplitude out of range
    "CH1 CHAN ON NOISE 30 TONE 1000000 101 IMPULSE 1000 8 30 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0",
    # impulse period 0 with non-zero width/ampl -> corrupt, not the all-zero sentinel
    "CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 0 8 30 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0",
    # impulse amplitude out of range
    "CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 101 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0",
    # only 23 tokens
    "CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 30 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0",
])
def test_parse_chan_status_rejects_malformed_24_token(bad):
    with pytest.raises(ValueError):
        parse_chan_status(bad)


from siggen.protocol import (          # noqa: E402  (B 组: 复数 I/Q 损伤协议层)
    IQIMP_FREQOFF_MAX,
    IQIMP_FREQOFF_MIN,
    IQIMP_GAIN_MAX,
    IQIMP_GAIN_MIN,
    IQIMP_PHASEERR_MAX,
    IQIMP_PHASE_MAX,
    get_iqimp_cmd,
    iqimp_commit_cmd,
    iqimp_freqoff_cmd,
    iqimp_gain_cmd,
    iqimp_off_cmd,
    iqimp_on_cmd,
    iqimp_phase_cmd,
    iqimp_phaseerr_cmd,
    parse_iqimp_status,
)


def test_iqimp_switch_and_commit_cmds():
    assert iqimp_on_cmd() == "SET IQIMP ON"
    assert iqimp_off_cmd() == "SET IQIMP OFF"
    assert iqimp_commit_cmd() == "SET IQIMP COMMIT"
    assert get_iqimp_cmd() == "GET IQIMP"


def test_iqimp_param_cmds_build_expected_lines():
    assert iqimp_phase_cmd(0) == "SET IQIMP PHASE 0"
    assert iqimp_phase_cmd(-18000) == "SET IQIMP PHASE -18000"
    assert iqimp_freqoff_cmd(1000000) == "SET IQIMP FREQOFF 1000000"
    assert iqimp_gain_cmd(16384) == "SET IQIMP GAIN 16384"
    assert iqimp_phaseerr_cmd(-250) == "SET IQIMP PHASEERR -250"


@pytest.mark.parametrize("bad", [
    lambda: iqimp_phase_cmd(IQIMP_PHASE_MAX + 1),
    lambda: iqimp_phase_cmd(-IQIMP_PHASE_MAX - 1),
    lambda: iqimp_phaseerr_cmd(IQIMP_PHASEERR_MAX + 1),
    lambda: iqimp_freqoff_cmd(IQIMP_FREQOFF_MAX + 1),
    lambda: iqimp_freqoff_cmd(IQIMP_FREQOFF_MIN - 1),
    lambda: iqimp_gain_cmd(IQIMP_GAIN_MAX + 1),
    lambda: iqimp_gain_cmd(IQIMP_GAIN_MIN - 1),
    # bool 是 int 的子类: 不拦的话 True 会变成 "1" 悄悄下发
    lambda: iqimp_phase_cmd(True),
    lambda: iqimp_gain_cmd("16384"),
])
def test_iqimp_param_cmds_reject_out_of_range(bad):
    with pytest.raises(ValueError):
        bad()


def test_parse_iqimp_status_reads_all_five_fields():
    status = parse_iqimp_status(
        "IQIMP ON PHASE -1234 FREQOFF 250000 GAIN 16384 PHASEERR 300")
    assert status == {"enabled": True, "phase": -1234, "freqoff": 250000,
                      "gain": 16384, "phaseerr": 300}


def test_parse_iqimp_status_defaults_line():
    # 上电初值: 关闭 / 全零 / 单位增益 —— 必须能解析, 否则 GUI 状态栏恒显 "--"
    status = parse_iqimp_status("IQIMP OFF PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0")
    assert status["enabled"] is False
    assert (status["phase"], status["freqoff"], status["phaseerr"]) == (0, 0, 0)
    assert status["gain"] == 16384


@pytest.mark.parametrize("bad", [
    # 关键字写错位(把 FREQOFF 写成 PHASE 的位置)
    "IQIMP ON PHASE 0 250000 GAIN 16384 PHASEERR 0",
    # 少一个 token
    "IQIMP ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR",
    # 多一个 token
    "IQIMP ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0 1",
    # 相位越界
    "IQIMP ON PHASE 18001 FREQOFF 0 GAIN 16384 PHASEERR 0",
    # 频偏越过协议上限(硬件能到 25MHz, 协议收窄到 1MHz)
    "IQIMP ON PHASE 0 FREQOFF 1000001 GAIN 16384 PHASEERR 0",
    # 增益是 Q2.14 原始码, 16384 = 1.0; 32768 表示不了
    "IQIMP ON PHASE 0 FREQOFF 0 GAIN 32768 PHASEERR 0",
    # 开关不是 ON/OFF
    "IQIMP ENABLE PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0",
    # 家族名不对
    "IQIMPS ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0",
])
def test_parse_iqimp_status_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_iqimp_status(bad)
