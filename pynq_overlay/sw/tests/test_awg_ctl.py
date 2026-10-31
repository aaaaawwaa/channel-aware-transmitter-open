"""`awg_ctl` 的离线验收(P1)。

主判据是**重放 P0 金标**: `docs/golden/command_replies.json` 是拿真固件采下来的
(命令 → 原始回包), 按**原有顺序**喂进同一个 `AwgCtl`, 每一条的回包必须逐字节相同。
顺序不能打乱 —— 状态是累积的(而且 `normalize()` 的结果依赖历史)。

其余是定点数学与寄存器映射的单测: 这些地方离线只有"手算 + 位域定义"能验,
端到端那一层留给板上的 `scope_verify.py`。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import awg_ctl as a  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[3] / "docs" / "golden" / "command_replies.json"


def _fresh():
    return a.AwgCtl()


def test_golden_plies_back_byte_for_byte():
    """P0 采的 731 条金标: 全部逐字节复现。"""
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    ctl = _fresh()                       # 一个实例走完全程: 状态累积与真机一致
    bad = []
    for case in doc["cases"]:
        got = ctl.handle(case["cmd"])
        got = "" if got is None else got
        if got != case["reply"]:
            bad.append((case["scenario"], case["cmd"], case["reply"], got))
    assert not bad, "有 %d 条回包对不上, 前 8 条:\n%s" % (
        len(bad), "\n".join("  [%s] %s\n    期望 %r\n    实得 %r" % b for b in bad[:8]))


def test_golden_covers_every_config_family():
    """金标确实覆盖了要移植的各族 —— 免得"全过"其实是因为没测。"""
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cmds = [c["cmd"] for c in doc["cases"]]
    for family in ("SET CH1 WAVE", "SET CH1 DUTY", "SET CH1 FREQ", "SET CH1 AMPL",
                   "SET CH1 MODE", "SET CH1 MODWAVE", "SET CH1 MODFREQ",
                   "SET CH1 AMDEPTH", "SET CH1 FMDEV", "SET CH1 PMDEV",
                   "SET CH1 BASE", "SET CH1 HARM1", "SET CH2 PHASE",
                   "SET CH2 PHASELINK", "SET CH1 NOISE", "SET CH1 TAP",
                   "SET CH1 CHAN", "SET IQIMP", "SET DIGMOD", "GET CH1",
                   "GET CH1 CHAN", "GET IQIMP", "GET DIGMOD"):
        assert any(c.startswith(family) for c in cmds), "金标没覆盖 %s" % family


# ---------------- 打包 / 派生 ----------------

def test_pack_word_matches_the_documented_bit_layout():
    s = a.ChState()
    s.freq_hz, s.ampl_pct, s.wave = 100000, 80, a.WAVE_NAME.index("SQUARE")
    assert a.pack_word(s) == (100000 | (80 << 20) | (1 << 28))


def test_pack_word_a_and_b_carry_the_harmonics():
    s = a.ChState()
    s.base_pct, s.h_ord[0], s.h_ampl[0] = 60, 3, 30
    s.h_ord[1], s.h_ampl[1] = 2, 15
    assert a.pack_word_a(s) == ((60 << 22) | (15 << 15) | (2 << 11) | (30 << 4) | 3)
    s.h_ord[2], s.h_ampl[2], s.h_ord[3], s.h_ampl[3] = 5, 7, 8, 9
    assert a.pack_word_b(s) == ((9 << 15) | (8 << 11) | (7 << 4) | 5)


def test_pack_word_c_and_d_carry_modulation():
    s = a.ChState()
    s.am_depth, s.mfreq_01, s.mwave, s.mode = 60, 1000, 2, 1
    assert a.pack_word_c(s) == ((60 << 24) | (1000 << 5) | (2 << 3) | 1)
    s.pm_dev, s.fm_dev = 300, 5000
    assert a.pack_word_d(s) == ((300 << 19) | 5000)


def test_angle_to_phase_is_a_full_turn_of_32_bits():
    assert a.angle_to_phase(18000) == 1 << 31
    assert a.angle_to_phase(36000) == 0            # 一整周 = 回绕到 0
    assert a.angle_to_phase(9000) == 1 << 30


def test_symrate_to_phase_step_truncates_like_the_firmware():
    assert a.symrate_to_phase_step(50000000) == 0
    assert a.symrate_to_phase_step(100000) == 8589934  # (100000<<32)//50e6 截断


def test_normalize_truncates_integer_wise():
    s = a.ChState()
    s.base_pct, s.h_ampl = 100, [30, 0, 0, 0]
    a.normalize(s)
    assert (s.base_pct, s.h_ampl[0]) == (76, 23), "100×100/130 与 30×100/130 都截断"
    s2 = a.ChState()
    s2.base_pct, s2.h_ampl = 50, [0, 0, 0, 0]
    a.normalize(s2)
    assert (s2.base_pct, s2.h_ampl) == (50, [0, 0, 0, 0]), "≤100 时不动"


def test_base_is_renormalized_as_soon_as_harmonics_change():
    # 归一化是**立即**发生的(每次 BASE / HARMn 之后), 所以读数会"变小"。
    # 长历史下的值(金标里那个 70)依赖一整串场景, 由重放测试覆盖; 这里验机制:
    ctl = _fresh()
    assert ctl.handle("SET CH1 BASE 100") == "OK"
    assert "BASE 100" in ctl.handle("GET CH1")
    assert ctl.handle("SET CH1 HARM1 AMPL 30") == "OK"
    line = ctl.handle("GET CH1")
    assert "BASE 76" in line and "H1 0 23" in line, line   # 100×100/130, 30×100/130 截断


# ---------------- 解析边界(与固件逐条对齐) ----------------

def test_parse_helpers_keep_the_firmware_limits():
    assert a.parse_u32("2000000") == 2000000
    assert a.parse_u32("2000001") is None, "通用上限就是 2000000"
    assert a.parse_long("-1000000") == -1000000
    assert a.parse_long("-1000001") is None
    assert a.parse_u32("") is None and a.parse_u32("1a") is None
    assert a.parse_long("-") is None
    assert a.parse_u32_limit("25000000", 25000000) == 25000000


def test_nine_tokens_is_a_bad_value():
    ctl = _fresh()
    assert ctl.handle("SET CH1 FREQ 1000 1 2 3 4 5") == "ERR BAD VALUE"


def test_empty_line_is_silent_and_unknown_verb_is_an_error():
    ctl = _fresh()
    assert ctl.handle("   ") is None
    assert ctl.handle("FOO BAR") == "ERR UNKNOWN CMD"


def test_lowercase_is_accepted():
    ctl = _fresh()
    assert ctl.handle("set ch1 freq 2000") == "OK"
    assert "FREQ 2000" in ctl.handle("get ch1")


def test_state_families_report_unsupported_in_p1():
    ctl = _fresh()
    for cmd in ("SET DDR CLEAR", "SET USER CLEAR", "SET PLAY START", "SET DMA TEST"):
        assert ctl.handle(cmd) == "ERR UNKNOWN CMD", cmd


# ---------------- I/Q 定点(BRAM 里的系数) ----------------

def test_sincos_table_is_the_real_one():
    assert len(a._SINCOS_COS) == 1024 and len(a._SINCOS_SIN) == 1024
    assert a._SINCOS_COS[0] == 32767 and abs(a._SINCOS_SIN[0]) <= 1
    assert a._SINCOS_SIN[256] == 32767             # 1/4 周期


def test_identity_iq_matrix_keeps_diagonal_minus_one_lsb():
    # phase=0, gain=16384(Q14 的 1.0) -> a=16384, b=c=0, d=**16383**
    # d 少 1 LSB 是 Q1.15 的往返损失(32767×32767 右移 15 再折半), 不是写错:
    # 别"顺手修"成 16384, 那会和固件/PL 分叉。
    coef_ab, coef_cd = a.iq_coeffs(0, 0, 16384)
    assert coef_ab == (0 << 16) | 16384
    assert coef_cd == (16383 << 16) | 0


def test_iq_to_q214_shifts_right_not_left():
    # 方向写反会让单位阵变成 32767(=2.0) —— 固件里就是这样抓出来的
    assert a.iq_to_q214(32767) == 16384
    assert a.iq_to_q214(-32768) == -16384


# ---------------- 寄存器 / BRAM 映射 ----------------

def test_default_state_writes_the_same_words_as_the_firmware():
    ctl = _fresh()
    g = ctl.backend.gpio
    assert g[(a.GPIO_WORD, 1)] == a.pack_word(ctl.ch[0])
    assert g[(a.GPIO_WORD, 2)] == a.pack_word(ctl.ch[1])
    assert g[(a.GPIO_F, 2)] == a.pack_word_f(ctl.ch[0], ctl.ch[1], ctl.digmod)
    assert g[(a.GPIO_SYMSTEP, 1)] == a.symrate_to_phase_step(10000)
    assert g[(a.GPIO_CHAN, 1)] == 0 and g[(a.GPIO_CHAN, 2)] == 0, "默认两通道都是关"


def test_noise_and_tap_land_in_the_control_word_and_tap_words():
    ctl = _fresh()
    ctl.handle("SET CH1 NOISE 30")
    assert ctl.backend.gpio[(a.GPIO_CHAN, 1)] == (30 << 1)
    assert ctl.handle("SET CH1 TAP 0 255 50") == "OK"
    assert ctl.backend.bram[0] == ((255 & 0x1FF) | (50 << 9))
    assert ctl.handle("SET CH1 TAP 1 -256 511") == "OK"
    assert ctl.backend.bram[1] == ((-256 & 0x1FF) | (511 << 9))
    ctl.handle("SET CH1 TAP CLEAR")
    assert [ctl.backend.bram[k] for k in range(4)] == [0, 0, 0, 0]


def test_second_channel_uses_the_upper_tap_words():
    ctl = _fresh()
    ctl.handle("SET CH2 TAP 3 100 7")
    assert ctl.backend.bram[4 + 3] == ((100 & 0x1FF) | (7 << 9))
    # CH1 的抽头字是 0..3, 一个都不该被碰到(构造函数写的是 IQ/tone 那几个字)
    assert not set(range(4)) & set(ctl.backend.bram), "只该写 CH2 那 4 个字"


# ---------------- gpio7 ch1: 用户源控制字的所有权 ----------------

def test_user_ctrl_word_matches_the_firmware_bit_layout():
    """`main.c:565-574` 的位域逐位镜像。"""
    ctl = _fresh()
    assert ctl.user_ctrl_word() == 0, "默认: 源 PRBS7、bank0、长度 0"
    ctl.digmod.source = 1
    assert ctl.user_ctrl_word() == a.USER_CTRL_SOURCE
    ctl.user_active_bank = 1
    assert ctl.user_ctrl_word() == a.USER_CTRL_SOURCE | a.USER_CTRL_BANK
    ctl.user_loop = 1
    ctl.user_commit_toggle = 1
    assert ctl.user_ctrl_word() == (a.USER_CTRL_SOURCE | a.USER_CTRL_BANK
                                    | a.USER_CTRL_LOOP | a.USER_CTRL_COMMIT)


def test_user_ctrl_word_encodes_the_bram_length_but_never_a_megabyte():
    """length 字段是 **BRAM USERBUF** 的长度, 且满 512 B 时编码成 0(`main.c:568`)。

    ⚠ 这条不是记账洁癖: `awg_top` 的 `user_length` 取自 ch1, 而 DDR PP 的长度走 ch2。
    往 ch1 塞 1 MiB 会让 BRAM 路径按 1 MiB 节流, 消费链永远排不完(板上实测 ERROR 24)。
    """
    ctl = _fresh()
    ctl.user_bit_length = 64 * 8                      # 64 B
    assert ctl.user_ctrl_word() == (64 << a.USER_CTRL_LENGTH_SHIFT)
    ctl.user_bit_length = a.USER_BANK_BYTES * 8       # 满 bank -> 编码 0
    assert ctl.user_ctrl_word() == 0, "满 512 B 要编码成 0(固件就是这么发的)"
    ctl.user_bit_length = 1000000 * 8
    assert (ctl.user_ctrl_word() >> a.USER_CTRL_LENGTH_SHIFT) == 1000000, \
        "非满 bank 的长度原样左移, 不加掩码"


def test_write_user_ctrl_writes_gpio7_ch1_and_leaves_the_other_registers_alone():
    ctl = _fresh()
    ctl.backend.gpio.clear()
    ctl.write_user_ctrl_for_pp(1)
    assert ctl.backend.gpio == {(a.GPIO_USER_CTRL, 1):
                                a.USER_CTRL_SOURCE | a.USER_CTRL_BANK}, \
        "ch1 只写 SOURCE|BANK —— 带上 length 就会毁掉 BRAM 路径"
    assert ctl.backend.gpio.get((a.GPIO_USER_CTRL, 2)) is None, "ch2(DDR 长度)不归它管"


def test_a_set_during_playback_does_not_close_the_user_source():
    """播放中来一条 `SET` 必须**不关掉** USER 源 —— 否则正在播的流当场断。

    `SET` 走 `write_all()`, 它按**影子** `digmod.source` 重写 ch1。`DdrPp.start()` 若绕过
    `AwgCtl` 直接写 GPIO, 影子就还是 PRBS7 —— 那条 `SET` 会把源切回去, DMA 排出的数据
    没人取, 状态机按超时判死(ERROR 24, 看起来像"环形坏了")。这条就是那个修复的回归网。
    """
    ctl = _fresh()
    ctl.write_user_ctrl_for_pp(0)                      # 模拟 DdrPp.start(0)
    assert ctl.backend.gpio[(a.GPIO_USER_CTRL, 1)] & a.USER_CTRL_SOURCE
    assert ctl.handle("SET CH1 FREQ 1000") == "OK"     # 播放中随手一条 SET
    assert ctl.backend.gpio[(a.GPIO_USER_CTRL, 1)] & a.USER_CTRL_SOURCE, \
        "SET 把 USER 源关掉了 —— 影子 source 没跟着 start 一起置起"


def test_read_registers_reports_the_source_from_the_register_not_the_shadow():
    """`GET DIGMOD` 的 SOURCE 必须**从 gpio7 ch1 解出来**。

    影子读出来必然等于自己写的, 是同义反复 —— 而"寄存器写没生效"正是本项目的高发故障
    (BD 修订落后曾让 AXI BRAM 写入静默失效)。所以拿寄存器直接证明: 手改寄存器, 影子不动,
    回读必须跟着寄存器变。
    """
    ctl = _fresh()
    assert ctl.read_registers()["dig"]["source"] == "PRBS7"
    ctl.write_user_ctrl_for_pp(1)
    assert ctl.read_registers()["dig"]["source"] == "USER"
    # 绕过影子直接改寄存器(模拟"写没生效"的反面: 硬件里有, 影子没有)
    ctl.backend.gpio[(a.GPIO_USER_CTRL, 1)] = 0
    assert ctl.read_registers()["dig"]["source"] == "PRBS7", \
        "回读在拿影子糊弄 —— 必须解寄存器"


# ---------------- GET DDR PP: 真状态由环形线程推来 ----------------

def test_ddr_pp_line_falls_back_to_the_baseline_when_nothing_was_pushed():
    """没推过时回 P0 基线金标 —— `awg_bridge` 独立起(`make_ctl`)走的就是这条路。"""
    ctl = _fresh()
    assert ctl.handle("GET DDR PP") == (
        "DDR PP ACTIVE 0 ACTIVE_BANK NONE NEXT_BANK NONE STATE IDLE ERROR 00 "
        "BANK0 VALID 0 LENGTH 0 CRC 0000 BANK1 VALID 0 LENGTH 0 CRC 0000")


def test_ddr_pp_line_reports_the_pushed_snapshot():
    """环形在播时 `GET DDR PP` 必须回**真**值, 而不是恒零桩。

    格式串与 `awg_ddr_pp.status_line()` / 固件 `main.c:1977` 逐字一致; `LENGTH` 是
    **字节数**(快照里已是 `bit_length // 8`)。
    """
    ctl = _fresh()
    ctl.set_pp_status({
        "active": 1, "state": "DRAIN", "error_code": 0,
        "active_bank": "1", "next_bank": "0",
        "bank0_len": 4096, "bank1_len": 2048,
        "bank0_valid": 1, "bank1_valid": 1, "bank0_crc": 0x1234, "bank1_crc": 0xABCD,
    })
    assert ctl.handle("GET DDR PP") == (
        "DDR PP ACTIVE 1 ACTIVE_BANK 1 NEXT_BANK 0 STATE DRAIN ERROR 00 "
        "BANK0 VALID 1 LENGTH 4096 CRC 1234 BANK1 VALID 1 LENGTH 2048 CRC ABCD")


def test_pushing_an_empty_snapshot_returns_to_the_baseline():
    """线程没了(或快照被清)要能退回基线, 不能把上一帧的"还在播"留在那里。"""
    ctl = _fresh()
    ctl.set_pp_status({"active": 1, "state": "RUNNING", "active_bank": "0"})
    assert "ACTIVE 1" in ctl.handle("GET DDR PP")
    ctl.set_pp_status(None)
    assert "ACTIVE 0" in ctl.handle("GET DDR PP")


# ---------------- GET PLAY / GET USERBUF: PL 侧字段从只读状态 GPIO 读 ----------------

def test_play_line_is_the_baseline_when_the_status_gpio_reads_zero():
    """静止态(位流刚下、播放 FSM 在 IDLE)必须与 P0 基线金标**逐字节**一致。

    这条就是"金标重放不受影响"的机制: `RecordingBackend` 没写过的键读回 0, 而 0 解出来
    恰好是 IDLE/全 0。
    """
    ctl = _fresh()
    assert ctl.handle("GET PLAY") == (
        "PLAY STATE IDLE FRAME_DONE 0 UNDERFLOW 0 TRIGGER_SEEN 0 "
        "STOP_PENDING 0 ERROR_VALID 0 REPEAT_REMAINING 0 ERROR 00")


def test_play_line_decodes_the_real_status_word():
    """八个字段全部从 `axi_gpio_10` ch1 解出(位域见 `awg_top.v:421`)。

    位序(从低到高): state[2:0], frame_done, underflow, trigger_seen, stop_pending,
    error_valid, repeat_remaining[15:0], error_code[7:0]。
    """
    ctl = _fresh()
    word = (3 | (1 << 3) | (1 << 5) | (1 << 7) | (7 << 8) | (0x24 << 24))
    ctl.backend.write_gpio(a.GPIO_PLAY_STATUS, 1, word)
    assert ctl.handle("GET PLAY") == (
        "PLAY STATE RUNNING FRAME_DONE 1 UNDERFLOW 0 TRIGGER_SEEN 1 "
        "STOP_PENDING 0 ERROR_VALID 1 REPEAT_REMAINING 7 ERROR 24")


def test_play_line_rejects_an_out_of_range_state_like_the_firmware():
    """状态索引 ≥7 回 `ERR PLAY STATUS`(`main.c:622` `play_state_valid`), 不猜名字。"""
    ctl = _fresh()
    ctl.backend.write_gpio(a.GPIO_PLAY_STATUS, 1, 7)      # 7 是第一个越界值
    assert ctl.handle("GET PLAY") == "ERR PLAY STATUS"


def test_userbuf_line_is_the_baseline_when_nothing_was_uploaded():
    """没上传过时与 P0 基线金标逐字节一致(软件账本静止 + PL 位读 0)。"""
    ctl = _fresh()
    assert ctl.handle("GET USERBUF") == (
        "USERBUF VALID 0 LENGTH 0 CRC 0000 BANK 0 UPLOAD IDLE LOOP OFF "
        "COMMIT_SEEN 0 READY 0 DONE 0 UNDERFLOW 0")


def test_userbuf_line_reads_the_pl_bits_and_the_software_ledger():
    """`READY/DONE/UNDERFLOW/BANK` 从 `axi_gpio_8` ch1 读; 其余来自软件账本。

    ⚠ 两者**必须**分开取真源: PL 那四个位是固件 `read_user_status` 读硬件的,
    而 `VALID`/`LENGTH` 是 PS RAM 里的账本 —— 把它也改成从 PL 取会让
    `SET DIGMOD SOURCE USER` 的门禁(读影子)与回包说两套话。
    """
    ctl = _fresh()
    ctl.user_valid = True
    ctl.user_bit_length = 48
    ctl.user_loop = 1
    ctl.user_commit_seen = 1
    ctl.backend.write_gpio(a.GPIO_USER_STATUS, 1,
                           a.USER_STATUS_READY | a.USER_STATUS_BANK)
    assert ctl.handle("GET USERBUF") == (
        "USERBUF VALID 1 LENGTH 48 CRC 0000 BANK 1 UPLOAD IDLE LOOP ON "
        "COMMIT_SEEN 1 READY 1 DONE 0 UNDERFLOW 0")


def test_userbuf_done_and_underflow_come_from_the_hardware_not_the_ledger():
    """`DONE`/`UNDERFLOW` 只有 PL 会置 —— 软件账本里根本没有这两个字段。

    回归网: 若哪天有人把它们"顺手"改成从影子取, 这条会立刻失败(影子恒 0)。
    """
    ctl = _fresh()
    ctl.backend.write_gpio(a.GPIO_USER_STATUS, 1,
                           a.USER_STATUS_DONE | a.USER_STATUS_UNDERFLOW)
    line = ctl.handle("GET USERBUF")
    assert "DONE 1" in line and "UNDERFLOW 1" in line, line


def test_tone_and_impulse_go_to_words_12_15_and_toggle_the_switch_bits():
    ctl = _fresh()
    ctl.handle("SET CH1 CHAN TONE 1000000 20")
    assert ctl.chan[0].word & a.CHAN_TONE_SW
    assert ctl.backend.bram[12] == a.pack_tone(1000000, 20)
    ctl.handle("SET CH2 CHAN IMPULSE 1000 8 -100")
    assert ctl.chan[1].word & a.CHAN_IMP_SW
    assert ctl.backend.bram[15] == a.pack_impulse(1000, 8, -100)
    ctl.handle("SET CH1 CHAN TONE 0 0")
    assert not ctl.chan[0].word & a.CHAN_TONE_SW, "幅度 0 等于关"


def test_commit_toggles_the_epoch_bit_and_iq_borrows_ch1s():
    ctl = _fresh()
    ctl.handle("SET CH1 CHAN COMMIT")
    assert ctl.chan[0].word & a.CHAN_COMMIT_EPOCH
    ctl.handle("SET CH1 CHAN COMMIT")
    assert not ctl.chan[0].word & a.CHAN_COMMIT_EPOCH, "是拨动, 不是置位"
    ctl.handle("SET IQIMP COMMIT")
    assert ctl.chan[0].word & a.CHAN_COMMIT_EPOCH, "IQIMP 复用 CH1 的 epoch 位"


def test_iq_ext_words_are_written_for_the_identity_matrix():
    ctl = _fresh()
    ctl.handle("SET IQIMP ON")
    ctl.handle("SET IQIMP PHASE 0")
    ctl.handle("SET IQIMP GAIN 16384")
    ctl.handle("SET IQIMP PHASEERR 0")
    assert ctl.backend.bram[a.IQ_WORD_BASE] == (0 << 16) | 16384
    assert ctl.backend.bram[a.IQ_WORD_BASE + 1] == (16383 << 16) | 0
    assert ctl.backend.bram[a.IQ_WORD_BASE + 2] == 0


def test_iq_fword_wraps_at_50mhz():
    assert a.iq_fword(0) == 0
    assert a.iq_fword(1000000) == 85899346
    assert a.iq_fword(-1000000) == (-85899346) & 0xFFFFFFFF
    assert a.iq_fword(50000000) == 0, "满速 = 一整周, 32 位字回绕到 0"


def test_phase_difference_only_exists_on_ch2():
    ctl = _fresh()
    assert ctl.handle("SET CH1 PHASE 9000") == "ERR BAD CHANNEL"
    assert ctl.handle("SET CH2 PHASE 9000") == "OK"
    assert ctl.backend.gpio[(a.GPIO_E, 1)] == a.angle_to_phase(9000)


def test_phaselink_bit_reaches_the_link_word():
    ctl = _fresh()
    ctl.handle("SET CH2 PHASELINK ON")
    assert ctl.backend.gpio[(a.GPIO_F, 2)] & 1
