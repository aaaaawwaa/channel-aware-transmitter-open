"""`awg_ctl` 的回读路径测试 —— 文本解析 + **真寄存器**解码。

两件事在这里被钉住:

1. **文本回读解析**(`parse_*`): 串口客户端只能通过 GET 看设备, 所以回包必须能解回结构体。
   这组断言跑 **P0 金标全量**(`docs/golden/command_replies.json` 的 230 条 GET 回包)——
   解析器对基线里的每一条都要成功, 否则面板在某个状态上会瞎。
2. **真寄存器回读**(`read_registers`): 面板自己持有 `AwgCtl`, 所以它**不必**读影子状态。
   这一条是本改版的核心价值 —— `GET` 读影子状态, 写进去再读出来必然一致, 那种校验是同义
   反复, 抓不到"写没生效"; 从 GPIO/BRAM 解出来才能抓到。

**为什么这组测试值钱**: 本工程有两次真实的"写入静默失效"前科(BD 修订落后导致 AXI BRAM
写入不生效、`proc_sys_reset` 把 AXI 外设按在复位里), 两次 GET 都照常回正确值。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import awg_ctl as A  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[3] / "docs" / "golden" / "command_replies.json"


def _golden_cases():
    if not GOLDEN.exists():
        pytest.skip("金标不在(%s)" % GOLDEN)
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def _ctl():
    return A.AwgCtl(A.RecordingBackend())


# ---------------- 文本回读解析: 对 P0 金标全量 ----------------

def test_every_golden_get_ch_line_parses():
    cases = [c for c in _golden_cases()
             if c["cmd"].startswith("GET CH") and not c["cmd"].endswith("CHAN")]
    assert cases, "金标里应当有 GET CHx 用例"
    for c in cases:
        got = A.parse_ch_line(c["reply"])
        assert got is not None, (c["cmd"], c["reply"])


def test_every_golden_get_digmod_line_parses():
    cases = [c for c in _golden_cases() if c["cmd"] == "GET DIGMOD"]
    assert cases
    for c in cases:
        got = A.parse_digmod_line(c["reply"])
        assert got is not None, c["reply"]


def test_every_golden_get_chan_line_parses():
    cases = [c for c in _golden_cases()
             if c["cmd"].startswith("GET CH") and c["cmd"].endswith("CHAN")]
    assert cases
    for c in cases:
        got = A.parse_chan_line(c["reply"])
        assert got is not None, c["reply"]


def test_parsed_golden_values_agree_with_the_replayed_state():
    """解析结果必须与"重放金标后的状态"一致 —— 不只是"能解析"。

    ⚠ 金标是**一次连续会话**的录制(`meta` 里有端口/位流/ELF 哈希), 不是各自独立的场景。
    实测: 按 `scenario` 分组、每组用干净控制器重放**会失败** —— 因为录制时的状态是跨场景
    延续的(某个场景的 `GET CH1` 报的 FREQ/AMPL 是更早的命令留下的)。所以只能**整份按序**
    重放进**同一个**控制器。按序重放时 741 条命令逐条一致。
    """
    ctl = _ctl()
    checked = 0
    for c in _golden_cases():
        cmd, reply = c["cmd"], c["reply"]
        if cmd.startswith("SET "):
            assert ctl.handle(cmd) == reply, (cmd, reply)
        elif cmd.startswith("GET CH1"):
            if cmd.endswith("CHAN"):
                got = A.parse_chan_line(reply)
                assert got["noise_pct"] == (ctl.chan[0].word >> 1) & 0x7F, (cmd, reply)
                assert got["enabled"] == bool(ctl.chan[0].word & A.CHAN_EN), (cmd, reply)
                assert got["taps"][0]["gain_q8"] == A.sign9(ctl.chan[0].tap[0] & 0x1FF)
            else:
                got = A.parse_ch_line(reply)
                s = ctl.ch[0]
                assert got["wave"] == A.WAVE_NAME[s.wave], (cmd, reply)
                assert got["freq_hz"] == s.freq_hz, (cmd, reply)
                assert got["ampl_pct"] == s.ampl_pct, (cmd, reply)
                assert got["duty_pct"] == s.pulse_duty, (cmd, reply)
                assert got["mode"] == A.MODE_NAME[s.mode], (cmd, reply)
            checked += 1
    assert checked >= 40, "应当核对到几十条 GET CH1 用例, 实际 %d" % checked


def test_parsers_round_trip_against_the_formatters():
    """格式化 → 解析 必须回到同一组值(方向相反的两半要对称)。"""
    ctl = _ctl()
    for cmd in ("SET CH1 WAVE TRIANGLE", "SET CH1 FREQ 250000", "SET CH1 AMPL 80",
                "SET CH1 DUTY 30", "SET CH1 MODE FM", "SET CH1 NOISE 25",
                "SET CH1 TAP 1 -100 20", "SET CH1 TAP 2 255 511", "SET CH1 CHAN ON"):
        assert ctl.handle(cmd) == "OK", cmd
    ch = A.parse_ch_line(ctl.handle("GET CH1"))
    assert (ch["wave"], ch["freq_hz"], ch["ampl_pct"], ch["duty_pct"], ch["mode"]) == \
           ("TRIANGLE", 250000, 80, 30, "FM")
    cn = A.parse_chan_line(ctl.handle("GET CH1 CHAN"))
    assert cn["enabled"] and cn["noise_pct"] == 25
    assert cn["taps"][1] == {"gain_q8": -100, "delay_samples": 20}
    assert cn["taps"][2] == {"gain_q8": 255, "delay_samples": 511}


def test_parsers_reject_malformed_input_instead_of_guessing():
    """缺字段/非法枚举/越界一律拒绝 —— 给默认值会把"解析失败"伪装成"设备就是这个值"。"""
    assert A.parse_ch_line("") is None
    assert A.parse_ch_line("garbage") is None
    assert A.parse_ch_line("CH1 WAVE BOGUS FREQ 1000 AMPL 50") is None
    assert A.parse_ch_line("CH1 WAVE SINE FREQ 1 AMPL 50") is None          # 频率越界
    assert A.parse_ch_line("CH1 WAVE SINE FREQ 1000 AMPL 500") is None      # 幅度越界
    assert A.parse_digmod_line("DIGMOD MODE OFF SOURCE PRBS7") is None      # 缺 SYMRATE
    assert A.parse_digmod_line(
        "DIGMOD MODE OFF SOURCE PRBS7 FILTER RRC ROLLOFF 0.35 SEED 999 SYMRATE 100") is None
    assert A.parse_digmod_line(
        "DIGMOD MODE BOGUS SOURCE PRBS7 FILTER RRC ROLLOFF 0.35 SEED 127 SYMRATE 100") is None
    assert A.parse_chan_line("CH1 CHAN ON NOISE 200 TAP0 1 1 TAP1 1 1 TAP2 1 1 TAP3 1 1") is None
    assert A.parse_chan_line("CH1 CHAN ON NOISE 1 TAP0 999 1 TAP1 1 1 TAP2 1 1 TAP3 1 1") is None
    assert A.parse_chan_line("CH1 CHAN ON NOISE 1 TAP0 1 1") is None        # 缺 TAP


def test_the_channel_line_is_parsed_positionally_not_as_key_value_pairs():
    """`TAP0 255 3` 里的延迟是个裸数字, 没有自己的键名 —— 按 key/value 配对会解析失败。"""
    line = ("CH1 CHAN OFF NOISE 7 TONE 0 0 IMPULSE 0 0 0 "
            "TAP0 255 3 TAP1 0 0 TAP2 0 0 TAP3 0 0")
    got = A.parse_chan_line(line)
    assert got is not None, "这一行必须能解析"
    assert got["taps"][0] == {"gain_q8": 255, "delay_samples": 3}


def test_get_ch_mode_is_not_the_digmod_mode():
    """⚠ `GET CHx` 的 MODE 是 AM/FM/PM/SWEEP(通道调制), 与 DIGMOD 的模式同名但无关。

    面板要的"设备实际模式"必须从 `GET DIGMOD` 取 —— 混用会让界面显示错的运行状态。
    """
    ctl = _ctl()
    ctl.handle("SET CH1 MODE FM")           # 通道调制模式
    assert ctl.handle("GET DIGMOD").split()[2] == "OFF"     # DIGMOD 仍关着
    ch = A.parse_ch_line(ctl.handle("GET CH1"))
    assert ch["mode"] == "FM"
    dm = A.parse_digmod_line(ctl.handle("GET DIGMOD"))
    assert dm["mode"] == "OFF" and dm["running"] is False


# ---------------- 真寄存器解码: pack_* 的逆 ----------------

def test_decode_word_inverts_pack_word():
    s = A.ChState()
    s.freq_hz, s.ampl_pct, s.wave = 250000, 80, 2
    assert A.decode_word(A.pack_word(s)) == (250000, 80, 2)


def test_decode_tap_word_inverts_chan_tap_word_including_negatives():
    for gain in (-256, -100, -1, 0, 1, 254, 255):
        for delay in (0, 3, 511):
            w = A.chan_tap_word(gain, delay)
            assert A.decode_tap_word(w) == (gain, delay), (gain, delay)


def test_decode_word_f_inverts_pack_word_f():
    ch1, ch2, d = A.ChState(), A.ChState(), A.DigmodState()
    ch1.pulse_duty, ch2.pulse_duty, ch2.phaselink = 30, 70, 1
    d.mode, d.filter, d.rolloff, d.seed = 4, 1, 1, 42
    f = A.decode_word_f(A.pack_word_f(ch1, ch2, d))
    assert f["phaselink"] == 1
    assert f["duty"] == [30, 70]
    assert (f["mode"], f["filter"], f["rolloff"], f["seed"]) == (4, 1, 1, 42)
    assert A.decode_rolloff(f["rolloff"]) == "0.2"


def test_decode_chan_ctrl_inverts_the_control_word():
    assert A.decode_chan_ctrl(A.CHAN_EN | (25 << 1)) == (True, 25)
    assert A.decode_chan_ctrl(0) == (False, 0)


def test_symrate_round_trip_is_within_one_hz():
    """硬件存的是截断的相位步进, 反解恒偏小 1 Hz —— 这是真实的量化, 不是 bug。"""
    for v in (10, 1000, 10000, 100000, 123456, 500000, 999999, 1000000):
        assert abs(A.decode_symstep(A.symrate_to_phase_step(v)) - v) <= 1, v


# ---------------- 真寄存器回读: 端到端 ----------------

def test_read_registers_reflects_what_the_commands_wrote():
    ctl = _ctl()
    for cmd in ("SET CH1 WAVE TRIANGLE", "SET CH1 FREQ 250000", "SET CH1 AMPL 80",
                "SET CH1 DUTY 30", "SET CH2 WAVE SAW", "SET CH2 FREQ 5000",
                "SET CH2 DUTY 70", "SET DIGMOD FILTER RRC", "SET DIGMOD ROLLOFF 0.2",
                "SET DIGMOD SEED 42", "SET DIGMOD SYMRATE 123456",
                "SET CH1 TAP 0 255 3", "SET CH1 TAP 1 -100 20",
                "SET CH1 NOISE 25", "SET CH1 CHAN ON"):
        assert ctl.handle(cmd) == "OK", cmd
    r = ctl.read_registers()
    assert r["awg"][0]["wave"] == "TRIANGLE" and r["awg"][0]["freq_hz"] == 250000
    assert r["awg"][0]["ampl_pct"] == 80 and r["awg"][0]["duty_pct"] == 30
    assert r["awg"][1]["wave"] == "SAW" and r["awg"][1]["freq_hz"] == 5000
    assert r["awg"][1]["duty_pct"] == 70
    assert r["dig"]["filter"] == "RRC" and r["dig"]["rolloff"] == "0.2"
    assert r["dig"]["seed"] == 42
    assert abs(r["dig"]["symrate_hz"] - 123456) <= 1
    assert r["chan"][0]["enabled"] is True and r["chan"][0]["noise_pct"] == 25
    assert r["chan"][0]["taps"][0] == {"gain_q8": 255, "delay_samples": 3}
    assert r["chan"][0]["taps"][1] == {"gain_q8": -100, "delay_samples": 20}


def test_read_registers_catches_a_write_that_silently_did_not_land():
    """**这组测试是本改版的核心价值。**

    后端的写静默失效(真实前科: BD 修订落后 / AXI 外设被按在复位里), 此时:
      - `handle("GET ...")` 读**影子状态**, 照常回正确值 -> 影子校验会**漏报**;
      - `read_registers()` 读**真寄存器**, 回的是没写进去的值 -> **正确报错**。
    """
    class DeafBackend(A.RecordingBackend):
        def write_gpio(self, instance, channel, value):
            pass                                  # 写不进去

    ctl = A.AwgCtl(DeafBackend())
    ctl.handle("SET CH1 FREQ 250000")
    # 影子状态认为成功了
    assert "FREQ 250000" in ctl.handle("GET CH1")
    # 真寄存器说没写进去 —— 这正是要抓的
    assert ctl.read_registers()["awg"][0]["freq_hz"] != 250000


def test_read_registers_catches_a_bram_write_that_did_not_land():
    """BRAM(抽头表)走的是另一条路径, 同样要能抓到。"""
    class DeafBram(A.RecordingBackend):
        def write_bram_word(self, word_index, value):
            pass

    ctl = A.AwgCtl(DeafBram())
    ctl.handle("SET CH1 TAP 0 200 7")
    assert "TAP0 200 7" in ctl.handle("GET CH1 CHAN")       # 影子说成功了
    assert ctl.read_registers()["chan"][0]["taps"][0] != {"gain_q8": 200, "delay_samples": 7}


def test_read_registers_agrees_with_get_when_writes_work():
    """正常路径下两条回读必须一致 —— 否则面板与串口会各说各话。"""
    ctl = _ctl()
    for cmd in ("SET CH1 WAVE SQUARE", "SET CH1 FREQ 12345", "SET CH1 AMPL 77",
                "SET CH1 DUTY 15", "SET CH1 NOISE 33", "SET CH1 CHAN ON",
                "SET CH1 TAP 3 -50 100"):
        assert ctl.handle(cmd) == "OK", cmd
    reg = ctl.read_registers()
    txt = A.parse_ch_line(ctl.handle("GET CH1"))
    chn = A.parse_chan_line(ctl.handle("GET CH1 CHAN"))
    assert reg["awg"][0]["wave"] == txt["wave"]
    assert reg["awg"][0]["freq_hz"] == txt["freq_hz"]
    assert reg["awg"][0]["ampl_pct"] == txt["ampl_pct"]
    assert reg["awg"][0]["duty_pct"] == txt["duty_pct"]
    assert reg["chan"][0]["enabled"] == chn["enabled"]
    assert reg["chan"][0]["noise_pct"] == chn["noise_pct"]
    assert reg["chan"][0]["taps"] == chn["taps"]


def test_read_registers_output_is_shaped_like_a_ui_config():
    """面板要把它直接当 `ui_model` 的配置用 —— 字段必须齐。"""
    import ui_model as M
    cfg = _ctl().read_registers()
    assert set(cfg) == {"awg", "dig", "chan"}
    for c in cfg["awg"]:
        assert set(c) == {"wave", "freq_hz", "ampl_pct", "duty_pct"}
    assert set(cfg["dig"]) == {"mode", "source", "symrate_hz", "filter", "rolloff", "seed"}
    for c in cfg["chan"]:
        assert set(c) == {"enabled", "noise_pct", "taps"}
        for t in c["taps"]:
            assert set(t) == {"gain_q8", "delay_samples"}
    # 默认状态下应当能通过面板的校验
    assert M.validate(cfg)[0] == M.OK


def test_a_backend_without_read_support_raises_rather_than_degrading_silently():
    """没有读路径的后端必须**抛**, 不能悄悄退回影子状态 —— 那会让校验重新变成同义反复。"""
    class WriteOnly(object):
        def write_gpio(self, instance, channel, value):
            pass

        def write_bram_word(self, word_index, value):
            pass

    ctl = A.AwgCtl(WriteOnly())
    with pytest.raises(AttributeError):
        ctl.read_registers()