"""`ui_model` 的离线测试 —— 面板事务模型。

移植自参考设计 `docs/reference/ui_model.c` 的语义, 所以这里按它声称覆盖的场景逐条钉住:
连接门禁、过期状态、应用等待(在途不可并发)、运行锁定、范围校验、抽头提交、
回读不符、停止恢复、错误状态。

**另一类断言同样重要**: 生成的命令必须真的被 `awg_ctl` 接受(它是协议唯一真源)。
所以下面有一组测试把每条计划喂给真的 `AwgCtl`, 逐条要求回 `OK` 或合法的 GET 行 ——
否则界面会生成一串协议根本不认的命令, 而纯逻辑测试全绿也看不出来。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import awg_ctl as A  # noqa: E402
import ui_model as M  # noqa: E402


def app(connected=True, fresh=True, mode="OFF"):
    a = M.UiApp()
    a.set_observed(M.default_config(), mode, connected=connected)
    if not fresh:
        a.fresh = False
    return a


# ---------------- 校验 ----------------

def test_validate_accepts_the_defaults():
    assert M.validate(M.default_config()) == (M.OK, "")


def test_validate_rejects_out_of_range_fields():
    cases = [
        ("awg", 0, "freq_hz", 9, "频率"),
        ("awg", 0, "freq_hz", 1000001, "频率"),
        ("awg", 0, "ampl_pct", 101, "幅度"),
        ("awg", 0, "duty_pct", -1, "占空比"),
        ("chan", 0, "noise_pct", 101, "噪声"),
    ]
    for kind, ci, key, bad, why in cases:
        cfg = M.default_config()
        cfg[kind][ci][key] = bad
        res, msg = M.validate(cfg)
        assert res == M.INVALID and why in msg, (kind, key, bad, msg)


def test_validate_rejects_bad_digmod_fields():
    for key, bad in (("mode", "QAM4"), ("filter", "XYZ"), ("rolloff", "0.9"),
                     ("seed", 0), ("seed", 128), ("symrate_hz", 9),
                     ("symrate_hz", 1000001), ("source", "BOGUS")):
        cfg = M.default_config()
        cfg["dig"][key] = bad
        assert M.validate(cfg)[0] == M.INVALID, (key, bad)


def test_validate_rejects_bad_taps():
    cfg = M.default_config()
    cfg["chan"][0]["taps"][2]["gain_q8"] = 256
    assert M.validate(cfg)[0] == M.INVALID
    cfg = M.default_config()
    cfg["chan"][0]["taps"][2]["delay_samples"] = 512
    assert M.validate(cfg)[0] == M.INVALID
    cfg = M.default_config()
    cfg["chan"][1]["taps"][0]["gain_q8"] = -256      # 下界含
    cfg["chan"][1]["taps"][0]["delay_samples"] = 511  # 上界含
    assert M.validate(cfg)[0] == M.OK


# ---------------- 门禁: 连接 / 过期 / 在途 ----------------

def test_begin_refuses_when_disconnected():
    a = app(connected=False)
    assert a.begin(M.APPLY_AWG)[0] == M.DISCONNECTED


def test_begin_refuses_when_state_is_stale():
    a = app(fresh=False)
    for action in (M.APPLY_AWG, M.APPLY_DIGITAL, M.APPLY_CHANNEL, M.START_DIGITAL):
        assert a.begin(action)[0] == M.STALE, action


def test_stop_is_allowed_even_when_state_is_stale():
    """`SET DIGMOD MODE OFF` 幂等, 所以过期也要能关 —— "关不掉"比"状态旧"更危险。"""
    a = app(fresh=False)
    res, plan = a.begin(M.STOP_DIGITAL)
    assert res == M.OK
    assert plan.commands[0].strip() == "SET DIGMOD MODE OFF"


def test_begin_refuses_while_a_transaction_is_in_flight():
    a = app()
    assert a.begin(M.APPLY_AWG)[0] == M.OK
    assert a.begin(M.APPLY_AWG)[0] == M.BUSY, "在途时禁止并发应用"


def test_begin_refuses_invalid_draft():
    a = app()
    a.draft["awg"][0]["freq_hz"] = 5
    res, plan = a.begin(M.APPLY_AWG)
    assert res != M.OK and plan is None
    assert "范围" in res


# ---------------- 运行锁定 ----------------

def test_running_locks_mode_and_awg_edits():
    a = app(mode="QPSK")
    assert a.running
    for action in (M.APPLY_AWG, M.APPLY_DIGITAL, M.START_DIGITAL):
        assert a.begin(action)[0] == M.RUNNING, action


def test_channel_edits_are_allowed_while_running():
    """信道参数可以在数字调制运行时编辑(AGENT_GUIDE 必须保留的行为 3)。"""
    a = app(mode="QPSK")
    assert a.begin(M.APPLY_CHANNEL)[0] == M.OK


def test_stop_is_allowed_while_running():
    a = app(mode="QPSK")
    assert a.begin(M.STOP_DIGITAL)[0] == M.OK


def test_user_source_is_unsupported_until_hardware_is_ready():
    a = app()
    a.draft["dig"]["source"] = "USER"
    assert a.begin(M.START_DIGITAL)[0] == M.UNSUPPORTED


# ---------------- 命令计划 ----------------

def test_awg_plan_has_the_expected_commands():
    a = app()
    res, plan = a.begin(M.APPLY_AWG, channel=1)
    assert res == M.OK
    assert [c.strip() for c in plan.commands] == [
        "SET CH2 WAVE SINE", "SET CH2 FREQ 100000", "SET CH2 AMPL 50",
        "SET CH2 DUTY 50", "GET CH2"]


def test_digital_plan_puts_mode_last_and_only_when_starting():
    a = app()
    _res, apply_plan = a.begin(M.APPLY_DIGITAL)
    assert not any("MODE " in c for c in apply_plan.commands), "应用参数不该启动"
    a.pending = False
    _res, start_plan = a.begin(M.START_DIGITAL)
    modes = [c for c in start_plan.commands if "MODE " in c]
    # 本契约是"命令内容与位置"；行尾(EOL)不属于它（产品当前发 \n，旧断言写死 \r\n）。
    assert [m.rstrip("\r\n") for m in modes] == ["SET DIGMOD MODE QPSK"], "启动事务最后才发 MODE"
    assert start_plan.commands.index(modes[0]) == len(start_plan.commands) - 2


def test_channel_plan_commits_taps_exactly_once():
    """COMMIT 是**拨动 epoch 位**, 发两次等于没提交 —— 所以只能发一次。"""
    a = app()
    _res, plan = a.begin(M.APPLY_CHANNEL)
    commits = [c for c in plan.commands if "COMMIT" in c]
    assert len(commits) == 1
    # 4 个 TAP 必须在 COMMIT 之前
    assert plan.commands.index(commits[0]) == 4


def test_plan_freezes_the_draft():
    """计划必须冻结: 事务在途时用户还能改草稿, 计划不能跟着变。"""
    a = app()
    _res, plan = a.begin(M.APPLY_AWG)
    frozen = list(plan.commands)
    a.draft["awg"][0]["freq_hz"] = 999999
    assert plan.commands == frozen
    assert plan.target["awg"][0]["freq_hz"] == 100000


# ---------------- 生成的命令必须被真协议接受 ----------------

def test_every_generated_command_is_accepted_by_the_real_protocol():
    """把每条计划喂给真的 `AwgCtl` —— 它才是协议唯一真源。

    没有这组断言的话, 界面可以生成一串协议不认的命令而纯逻辑测试全绿。

    ⚠ 每个动作要用**干净的** `AwgCtl`: `START_DIGITAL` 会把设备置成 RUNNING, 之后
    `SET DIGMOD SOURCE` 会被固件拒成 `ERR DIGMOD RUNNING`(那是**正确的**门控, 不是
    计划的问题)。共用一台设备会把这条门控误读成计划有错。
    """
    for action in (M.APPLY_AWG, M.APPLY_DIGITAL, M.APPLY_CHANNEL,
                   M.START_DIGITAL, M.STOP_DIGITAL):
        for ci in (0, 1):
            ctl = A.AwgCtl()
            a = app()
            res, plan = a.begin(action, channel=ci)
            assert res == M.OK, (action, ci, res)
            for cmd in plan.commands:
                reply = ctl.handle(cmd.strip())
                assert reply is not None, (action, cmd)
                assert not reply.startswith("ERR"), (action, cmd, reply)


def test_generated_get_lines_are_parseable_by_the_protocol():
    """GET 行要真的回出对应状态行(不是错误)。"""
    ctl = A.AwgCtl()
    a = app()
    _res, plan = a.begin(M.APPLY_CHANNEL)
    gets = [c.strip() for c in plan.commands if c.startswith("GET")]
    assert gets == ["GET CH1 CHAN"]
    assert ctl.handle(gets[0]).startswith("CH1 CHAN ")


# ---------------- 回读校验 ----------------

def _snapshot(cfg, mode="OFF", connected=True, fresh=True):
    return {"config": M.clone(cfg), "actual_mode": mode,
            "connected": connected, "fresh": fresh}


def test_finish_reports_ok_when_the_readback_matches():
    a = app()
    a.draft["awg"][0]["freq_hz"] = 250000
    _res, plan = a.begin(M.APPLY_AWG)
    res = a.finish(_snapshot(plan.target))
    assert res == M.OK
    assert a.last_result == M.OK
    assert a.pending is False and a.in_flight is None


def test_finish_reports_mismatch_and_does_not_pretend_to_roll_back():
    """回读不符时**照实报告** —— 设备可能已经被改了一部分, 不能假装回滚成功。"""
    a = app()
    a.draft["awg"][0]["freq_hz"] = 250000
    _res, plan = a.begin(M.APPLY_AWG)
    wrong = M.clone(plan.target)
    wrong["awg"][0]["freq_hz"] = 1000          # 设备没接受
    res = a.finish(_snapshot(wrong))
    assert res == M.MISMATCH
    assert a.last_message == M.RESULT_TEXT[M.MISMATCH]
    # 不能假装回滚: observed 就是设备报回来的值, draft 也没被偷偷改回去
    assert a.observed["awg"][0]["freq_hz"] == 1000
    assert a.draft["awg"][0]["freq_hz"] == 250000


def test_finish_rejects_a_stale_or_disconnected_snapshot():
    a = app()
    _res, plan = a.begin(M.APPLY_AWG)
    assert a.finish(_snapshot(plan.target, connected=False)) == M.IO_ERROR
    assert a.pending is False and a.fresh is False

    a = app()
    _res, plan = a.begin(M.APPLY_AWG)
    assert a.finish(_snapshot(plan.target, fresh=False)) == M.IO_ERROR
    assert a.fresh is False


def test_finish_without_a_transaction_is_invalid():
    a = app()
    assert a.finish(_snapshot(M.default_config())) == M.INVALID


def test_fail_invalidates_and_does_not_retry():
    a = app()
    _res, plan = a.begin(M.APPLY_AWG)
    a.fail()
    assert a.pending is False and a.in_flight is None
    assert a.fresh is False, "失败后必须标成过期, 让界面重新读取"
    assert a.last_result == M.IO_ERROR


def test_start_then_stop_round_trip():
    a = app()
    _res, plan = a.begin(M.START_DIGITAL)
    running = M.clone(plan.target)
    assert a.finish(_snapshot(running, mode="QPSK")) == M.OK
    assert a.running

    _res, plan = a.begin(M.STOP_DIGITAL)
    assert a.finish(_snapshot(running, mode="OFF")) == M.OK
    assert not a.running


def test_only_the_fields_this_transaction_touched_are_compared():
    """只改频率却因为别的字段没同步而报不符, 是没用的校验。"""
    a = app()
    a.draft["awg"][0]["freq_hz"] = 250000
    _res, plan = a.begin(M.APPLY_AWG)
    readback = M.clone(plan.target)
    readback["chan"][0]["noise_pct"] = 42        # 与本事务无关的字段不同
    assert a.finish(_snapshot(readback)) == M.OK


# ---------------- QAM4 归一化 ----------------

def test_qam4_readback_normalizes_to_qpsk():
    """固件把 QAM4 与 QPSK 当作两种模式, 参考设计只有 5 个槽 —— 读回时归一。"""
    assert M.normalize_digmod_mode("QAM4") == "QPSK"
    assert M.normalize_digmod_mode("QPSK") == "QPSK"
    assert M.normalize_digmod_mode("QAM16") == "QAM16"
    assert M.normalize_digmod_mode("BOGUS") is None
    assert "QAM4" not in M.MODES, "面板不该露出协议参考设计里没有的槽"


def test_panel_never_emits_qam4():
    a = app()
    for mode in M.MODES:
        if mode == "OFF":
            continue
        a = app()
        a.draft["dig"]["mode"] = mode
        _res, plan = a.begin(M.START_DIGITAL)
        assert "QAM4" not in " ".join(plan.commands), mode


# ---------------- draft / observed 分离 ----------------

def test_editing_the_draft_does_not_touch_the_device_view():
    a = app()
    a.draft["awg"][0]["freq_hz"] = 555000
    assert a.observed["awg"][0]["freq_hz"] == 100000
    assert a.dirty, "草稿与回读不同, 就是待应用"


def test_dirty_clears_after_a_successful_apply():
    a = app()
    a.draft["awg"][0]["freq_hz"] = 555000
    assert a.dirty
    _res, plan = a.begin(M.APPLY_AWG)
    a.finish(_snapshot(plan.target))
    assert not a.dirty


def test_a_fresh_readback_resyncs_the_draft_when_no_transaction_is_pending():
    """串口侧改了参数, 面板要跟上 —— 否则用户一点应用就把设备推回旧值。"""
    a = app()
    other = M.default_config()
    other["awg"][0]["freq_hz"] = 777000
    a.set_observed(other, "OFF")
    assert a.draft["awg"][0]["freq_hz"] == 777000


def test_a_readback_arriving_mid_transaction_does_not_overwrite_the_draft():
    a = app()
    a.draft["awg"][0]["freq_hz"] = 555000
    a.begin(M.APPLY_AWG)
    other = M.default_config()
    other["awg"][0]["freq_hz"] = 111000
    a.set_observed(other, "OFF")
    assert a.draft["awg"][0]["freq_hz"] == 555000, "在途时不许后台覆盖用户草稿"


def test_a_readback_never_clobbers_the_selected_mode():
    """`dig.mode` 是**选择**(下次跑哪个), 不是设备状态 —— 回读不许动它。

    设备在 MODE OFF 时 `actual_mode` 恒是 OFF; 若把回读的 mode 同步进草稿, 用户的选择
    就被抹成 OFF, 于是底栏点"启动数字调制"发出去的 START 计划里是 `SET DIGMOD MODE OFF`
    —— 板上现象是"点了启动, 结果 STALE, 什么也没发生"(验收脚本抓到)。
    参考设计把两者分得很清楚(`ui_model.h`: `mode /* selected mode; OFF is actual
    runtime state */` 且 `draft /* never overwritten by telemetry polling */`)。
    """
    a = app()
    assert a.draft["dig"]["mode"] == "QPSK"
    a.set_observed(M.default_config(), "OFF")       # 设备在 OFF
    assert a.draft["dig"]["mode"] == "QPSK", "回读把用户选的模式抹成了 OFF"

    # 而且要能真的启动起来: START 计划的 MODE 行必须是选的那个, 不是 OFF
    _res, plan = a.begin(M.START_DIGITAL)
    modes = [c for c in plan.commands if "MODE " in c]
    assert [m.rstrip("\r\n") for m in modes] == ["SET DIGMOD MODE QPSK"], modes


def test_a_readback_of_a_running_device_still_keeps_the_selection():
    """设备正在跑某个模式时, 回读的 mode 也不该改写用户的**选择**(两者本就可能不同)。"""
    a = app()
    a.draft["dig"]["mode"] = "QAM16"                # 用户选了 QAM16
    a.set_observed(M.default_config(), "QPSK")      # 设备在跑 QPSK
    assert a.draft["dig"]["mode"] == "QAM16"
    assert a.actual_mode == "QPSK"


def test_the_selected_mode_does_not_make_the_panel_look_dirty():
    """开机同步之后不该显示"有待应用参数" —— 用户什么都没改。

    `draft != observed` 会把 `dig.mode` 的**必然差异**算成脏: 草稿里是用户的选择(QPSK),
    而设备回读里 `dig.mode` 取自寄存器、停播时恒是 OFF(`read_registers` 从 gpio 解)。
    于是底栏一开机就挂着"有待应用参数"(板上实测)。
    """
    a = M.UiApp()
    assert not a.dirty, "刚建出来的 app 不该是脏的"
    # 设备回读: 停播, 所以 dig.mode 是 OFF —— 与草稿的选择 QPSK 不同, 但那不是"脏"
    dev = M.default_config()
    dev["dig"]["mode"] = "OFF"
    a.set_observed(dev, "OFF")
    assert not a.dirty, "回读同步之后不该是脏的(模式选择不算差异)"
    # 但真的改了别的字段, 仍然要脏
    a.draft["dig"]["seed"] = 42
    assert a.dirty, "改了 seed 却不算脏"
    a.draft["dig"]["seed"] = 127
    a.draft["awg"][0]["freq_hz"] = 250000
    assert a.dirty, "改了频率却不算脏"


# ---------------- 其它 ----------------

def test_result_text_is_chinese_and_covers_every_code():
    for code in (M.OK, M.INVALID, M.RUNNING, M.DISCONNECTED, M.STALE, M.BUSY,
                 M.UNSUPPORTED, M.OVERFLOW, M.MISMATCH, M.IO_ERROR):
        assert code in M.RESULT_TEXT and M.RESULT_TEXT[code]


def test_pages_include_the_daemon_status_page():
    """守护进程的状态页(网络/写卡/关机)在参考设计里没有, 本工程保留为独立入口。
    数据页(DDR 本地文件队列 + 环形播放)同样是自己加的。"""
    assert M.PAGE_STATUS in M.PAGES
    assert M.PAGE_DDR in M.PAGES
    assert len(M.PAGES) == 7


def test_log_records_the_commands_that_were_planned():
    a = app()
    a.begin(M.APPLY_AWG)
    assert any("SET CH1 WAVE" in line for line in a.log)


def test_clone_is_deep_enough():
    a = M.default_config()
    b = M.clone(a)
    b["chan"][0]["taps"][0]["gain_q8"] = 1
    b["awg"][1]["wave"] = "SAW"
    assert a["chan"][0]["taps"][0]["gain_q8"] == 255
    assert a["awg"][1]["wave"] == "SINE"

# ---------------- 数据页的纯逻辑 ----------------

def test_ddr_window_slices_and_clamps_the_page():
    files = [("f%d.bin" % i, i) for i in range(9)]
    assert M.ddr_pages(9) == 3
    start, win = M.ddr_window(files, 0)
    assert start == 0 and len(win) == M.DDR_PAGE_ROWS
    start, win = M.ddr_window(files, 2)
    assert start == 2 * M.DDR_PAGE_ROWS and len(win) == 1
    # 越界页码夹回最后一页 —— 删了文件之后页码可能越界, 夹回去比显示空白好
    start, win = M.ddr_window(files, 99)
    assert start == 2 * M.DDR_PAGE_ROWS


def test_ddr_pages_is_never_zero():
    """0 页会让页码显示成 "1/0"。"""
    assert M.ddr_pages(0) == 1


def _ddr_app(present=1, active=0, b0=0, b1=0, queue=None, connected=True,
             mode="QPSK"):
    a = M.UiApp()
    a.connected = connected
    # 起播要求数字调制开着(`DdrPp.start()` 的门禁), 所以能起播的默认场景必须给个非 OFF 模式。
    a.actual_mode = mode
    a.pp = {"present": present, "active": active, "bank0_len": b0, "bank1_len": b1}
    a.ddr_queue = list(queue or [])
    return a


def test_ddr_start_needs_the_ring_thread():
    a = _ddr_app(present=0, b0=4096)
    assert M.ddr_start_block(a) == "DDR 不可用"


def test_ddr_start_needs_a_connection():
    a = _ddr_app(connected=False, b0=4096)
    assert M.ddr_start_block(a) == "设备未连接"


def test_ddr_start_refuses_while_playing():
    a = _ddr_app(active=1, b0=4096)
    assert M.ddr_start_block(a) == "已在播放"


def test_ddr_start_needs_something_to_play():
    """队列非空**不算**可播 —— 装填要求 MODE OFF、起播要求非 OFF, 两者不可能同时成立。

    所以"队列里有文件"只说明**能装填**, 不说明**能起播**。这条判据与直觉相反, 曾经写成
    "队列非空也算", 结果板上是"点了启动, 状态直接变成 ERROR"(`PP_START_FAILED`)。
    """
    assert M.ddr_start_block(_ddr_app()) == "请先装填一个文件"
    assert M.ddr_start_block(_ddr_app(queue=["a.bin"])) == "请先装填一个文件", \
        "队列里有文件不等于 DDR 里有数据"
    assert M.ddr_start_block(_ddr_app(b0=512)) is None
    assert M.ddr_start_block(_ddr_app(b1=512)) is None


def test_ddr_start_requires_the_digital_mode_to_be_on():
    """MODE OFF 时不许起播 —— 这一条**由硬件门禁反推**: `DdrPp.start()` 在
    `digmod_mode == 0` 时直接把状态置成 `ERROR / PP_START_FAILED`。

    它与「装填要求 MODE OFF」是一对: 装填完必须打开调制才点得动启动。少了这条门禁,
    面板会让用户点下去, 然后屏幕上出现一个 ERROR —— 正是本页最该避免的观感。
    """
    assert M.ddr_start_block(_ddr_app(b0=512, mode="OFF")) == "请先打开数字调制"
    assert M.ddr_start_block(_ddr_app(b0=512, mode="QPSK")) is None


def test_ddr_load_requires_mode_off():
    """装填是本页**唯一**被 MODE 挡住的动作 —— 这条门禁来自 `DdrPp.feed_file`。"""
    a = _ddr_app(present=1, queue=["a.bin"])
    a.connected = True
    a.actual_mode = "OFF"
    assert M.ddr_load_block(a) is None
    a.actual_mode = "QPSK"
    assert M.ddr_load_block(a) == "请先关闭数字调制"
    a.actual_mode = "OFF"
    a.pp["active"] = 1
    assert M.ddr_load_block(a) == "播放中不能装填"


def test_ddr_load_needs_a_file():
    a = _ddr_app(present=1, connected=True)
    a.actual_mode = "OFF"
    assert M.ddr_load_block(a) == "请先入队一个文件"
    a.ddr_queue = ["a.bin"]
    assert M.ddr_load_block(a) is None


# ---------------- RX 采集契约 ----------------

def test_rx_output_fs_and_config_contract():
    assert M.rx_output_fs(M.RX_INPUT_FS_HZ, 1) == 60000000
    assert M.rx_output_fs(M.RX_INPUT_FS_HZ, 16) == 3750000
    assert M.rx_output_fs(M.RX_INPUT_FS_HZ, 3) is None
    assert M.validate_rx_config({"input_fs_hz": 60000000, "decim": 4,
                                 "rx_len": 4096}) == (M.OK, "")


def test_rx_config_rejects_wrong_rate_decim_and_length():
    for key, value in (("input_fs_hz", 120000000), ("decim", 3),
                       ("rx_len", 0), ("rx_len", 4097), ("rx_len", 1.5)):
        config = {"input_fs_hz": 60000000, "decim": 1, "rx_len": 4096}
        config[key] = value
        result, reason = M.validate_rx_config(config)
        assert result == M.INVALID and reason


def test_rx_capture_gate_requires_real_capability_and_is_mutually_exclusive():
    a = app()
    assert M.rx_capture_block(a) == M.UNSUPPORTED
    a.set_capabilities({"capture": True})
    assert M.rx_capture_block(a) is None
    a.actual_mode = "QPSK"
    assert M.rx_capture_block(a) == M.RUNNING
    a.actual_mode = "OFF"
    a.pending = True
    assert M.rx_capture_block(a) == M.BUSY


def test_rx_snapshot_marks_overflow_stale_expired_and_synthetic_unusable():
    for field in ("overflow", "stale", "expired", "synthetic"):
        snap = M.RxSnapshot(state="DONE", valid=True, actual_len=16, rx_len=16,
                            **{field: True})
        assert not snap.usable
    assert M.RxSnapshot(state="DONE", valid=True, actual_len=16, rx_len=16).usable


def test_rx_frames_export_only_crc_pass_real_payloads():
    assert M.RxFrame(crc="PASS", valid_payload=True).exportable
    assert not M.RxFrame(crc="FAIL", valid_payload=True).exportable
    assert not M.RxFrame(crc="PASS", valid_payload=True, synthetic=True).exportable


def test_rx_recommendation_never_changes_draft_or_opens_auto():
    a = app()
    before = M.clone(a.draft)
    a.set_recommendation({"mode": "QPSK", "confidence": 0.9,
                          "stale": False, "action": {"symrate_hz": 200000}})
    assert a.draft == before
    assert M.rx_auto_block(a) == M.UNSUPPORTED


def test_ddr_load_needs_the_ring_thread():
    a = _ddr_app(present=0, queue=["a.bin"], connected=True)
    a.actual_mode = "OFF"
    assert M.ddr_load_block(a) == "DDR 不可用"
    a = _ddr_app(present=1, queue=["a.bin"], connected=False)
    a.actual_mode = "OFF"
    assert M.ddr_load_block(a) == "设备未连接"
def test_rf_status_is_separate_from_capture_capabilities():
    app = M.UiApp()
    app.set_rf_status({
        "online": True, "stale": False, "error": None,
        "received_at_monotonic": 123.5, "mode": "status_only",
        "daemon_state": "OFFLINE", "core": False,
        "pl_status": {
            "io_ok": True, "sampled_at_monotonic_ns": 123456,
            "tx": {"source_selected": True, "length_words": 1024,
                   "position_words": 33},
            "rx": {"armed": False, "done": False, "position_words": 0},
        },
    })
    status = app.rf_status
    assert status["online"] is True
    assert status["daemon_state"] == "OFFLINE"
    assert status["pl_status"]["tx"]["length_words"] == 1024
    assert status["pl_status"]["rx"]["position_words"] == 0
    assert app.capabilities.capture is False
    assert app.capabilities.model is False
    assert app.caps["ad9361"] is False
    assert app.caps["receiver"] is False
    assert app.caps["model"] is False
    assert app.rx_snapshot.state == "UNAVAILABLE"


def test_rf_status_expiry_clears_registers_but_keeps_stale_and_error():
    app = M.UiApp()
    app.set_rf_status({
        "online": True, "stale": True, "error": "poll expired",
        "mode": "status_only", "core": False,
        "pl_status": {
            "io_ok": True, "tx": {"source_selected": True,
                                  "length_words": 512, "position_words": 9},
            "rx": {"armed": True, "done": True, "position_words": 8},
        },
    })
    status = app.rf_status
    assert status["stale"] is True
    assert status["error"] == "poll expired"
    assert status["pl_status"]["tx"]["length_words"] is None
    assert status["pl_status"]["rx"]["position_words"] is None
    assert status["pl_status"]["io_ok"] is False

# ---------------- RF 控制面(Task 5/6) ----------------
#
# 判据来源: docs/superpowers/plans/2026-09-29-merged-consumer-phase2.md 的 Task 5/6。
# 这一层是**纯逻辑**: 它只根据不可变快照算出"面板允许暴露哪些动作", 不碰 socket。
# 真正的发送在 `rf_status_worker.RfStatusWorker.control`。

def _rf_control_snapshot(mode="control", armed=False, ops=("control.arm",),
                         core=True, daemon_state="OFFLINE", pending=False, **kw):
    """构造一份控制面快照。默认: 可控制模式、未武装、只广告 arm。"""
    snapshot = {
        "online": True, "stale": False, "error": None,
        "received_at_monotonic": 1.0, "mode": mode, "core": core,
        "daemon_state": daemon_state, "armed": armed,
        "control_ops": list(ops), "pending": pending, "pending_op": None,
        "lease_held": True,
        "pl_status": {
            "io_ok": True, "sampled_at_monotonic_ns": 1,
            "tx": {"source_selected": None, "length_words": None,
                   "position_words": None},
            "rx": {"armed": None, "done": None, "position_words": None},
        },
    }
    snapshot.update(kw)
    return snapshot


# daemon 武装后真正会广告的控制 op 集合(见 rf_daemon.c::control_op_enabled)。
ARMED_OPS = ("control.disarm", "rf.init", "rf.configure", "tx.stop")


def _control_app(mode="control", armed=False, ops=("control.arm",), **kw):
    a = M.UiApp()
    a.set_rf_status(_rf_control_snapshot(mode=mode, armed=armed, ops=ops, **kw))
    return a


def test_rf_control_no_advertised_op_exposes_no_action_at_all():
    """判据 1(修订版): **快照没有广告任何控制 op** 时, 一个动作都不暴露。

    原文是 "mode=status_only 时不暴露任何控制动作"。那段文本让控制面在真机上
    **按构造不可达**: 生产 daemon 终身发 mode="status_only"(武装后也一样), 可用性它用
    control_ops 表达。2026-09-30 实测面板控制面因此完全不可用, 这是**判据写错了**,
    不是实现错了; 修订理由与证据见交付记录 §41。
    保留的不变量: 面板没有的动作**不存在**, 不是"禁用"。
    """
    a = _control_app(mode="status_only", armed=False, ops=(), core=False)
    assert a.rf_control["surface"] == "status_only"
    assert a.rf_control["state"] == "read_only"
    assert a.rf_control["actions"] == ()
    assert a.rf_control["stop_reachable"] is False
    assert a.rf_control_block_reason("arm") is not None
    assert a.rf_control_block_reason("stop") is not None
    # 同一条规则与 mode 的名字无关: mode="control" 但什么都没广告, 同样空。
    b = _control_app(mode="control", armed=True, ops=())
    assert b.rf_control["surface"] == "status_only"
    assert b.rf_control["actions"] == ()


def test_rf_control_armed_status_only_snapshot_reaches_the_advertised_ops():
    """真机形状: daemon 武装前后都发 mode="status_only", 武装后 core=True 并广告
    ARMED_OPS。面板必须按**广告的能力**放开, 否则生产环境永远只能只读。

    这是 2026-09-30 修掉的 "武装即面板失联" 的模型侧一半。
    """
    a = _control_app(mode="status_only", armed=True, core=True, ops=ARMED_OPS)
    assert a.rf_control["surface"] == "control"
    assert a.rf_control["state"] == "armed"
    assert set(a.rf_control["actions"]) == {"init", "stop", "disarm"}
    assert a.rf_control["stop_reachable"] is True
    # 同一条规则在未武装时只放开 arm(未武装的 daemon 只广告 control.arm)。
    b = _control_app(mode="status_only", armed=False, core=False,
                     ops=("control.arm",))
    assert b.rf_control["surface"] == "control"
    assert b.rf_control["actions"] == ("arm",)
    assert b.rf_control_block_reason("init") is not None


def test_rf_control_unarmed_control_mode_exposes_only_arm():
    """判据 2: mode=control 未武装 => 只有 arm, 没有 init/configure/stop/disarm。"""
    a = _control_app(mode="control", armed=False, ops=("control.arm",))
    assert a.rf_control["surface"] == "control"
    assert a.rf_control["state"] == "read_only"
    assert a.rf_control["actions"] == ("arm",)
    for action in ("init", "configure", "stop", "disarm"):
        assert a.rf_control_block_reason(action) is not None, action


def test_rf_control_fails_closed_when_arm_is_not_advertised():
    """控制模式但 daemon 没广告 control.arm 时 fail-closed: 连 arm 都不暴露。"""
    a = _control_app(mode="control", armed=False, ops=())
    assert a.rf_control["actions"] == ()
    assert a.rf_control_block_reason("arm") is not None


def test_rf_control_unknown_mode_is_read_only_and_never_control():
    """判据 3: 不可识别的 mode 一律未知/只读, 绝不当作 control。"""
    for mode in ("full", "control_test", "", "CONTROL", None):
        a = _control_app(mode=mode, armed=True, ops=ARMED_OPS)
        assert a.rf_control["surface"] == "unknown", mode
        assert a.rf_control["state"] == "read_only", mode
        assert a.rf_control["actions"] == (), mode
        assert a.rf_control_block_reason("arm") is not None, mode
        assert a.rf_control_block_reason("stop") is not None, mode


def test_rf_control_confirmation_is_required_for_arm_init_configure_only():
    """判据 6: arm/init/configure 必须显式确认; stop/disarm 永远不需要。"""
    assert M.RF_CONTROL_CONFIRM == ("arm", "init", "configure", "start")
    a = _control_app(mode="control", armed=False, ops=("control.arm",))
    # 确认之前不让执行。
    assert a.rf_control_block_reason("arm") is not None
    accepted, needs_confirm, _msg = a.rf_control_prompt("arm")
    assert accepted is True and needs_confirm is True
    assert a.rf_control["pending_action"] == "arm"
    # 确认之后放行。
    assert a.rf_control_block_reason("arm") is None
    a.rf_control_cancel()
    assert a.rf_control["pending_action"] is None
    assert a.rf_control_block_reason("arm") is not None
    # stop 不需要确认, 一点即放行。
    armed = _control_app(mode="control", armed=True, ops=ARMED_OPS)
    assert armed.rf_control_block_reason("stop") is None
    accepted, needs_confirm, _msg = armed.rf_control_prompt("stop")
    assert accepted is True and needs_confirm is False
    assert armed.rf_control["pending_action"] is None


def test_rf_control_state_machine_follows_what_the_daemon_permits():
    """判据 4: read_only -> armed -> initialized -> configured -> stopped, 只允许
    daemon 真的允许的转移:
      * arm     只在未武装时 (control.arm);
      * init    只在武装后, 且必须早于 configure (rf.init);
      * configure 只在武装后; 重复 configure 是 daemon 明确允许的幂等操作;
      * stop    在任意非只读状态都可发 (tx.stop);
      * disarm  回到未武装 (control.disarm)。
    """
    a = _control_app(mode="control", armed=False, ops=("control.arm",))
    assert a.rf_control["state"] == "read_only"
    for action in ("init", "configure", "stop", "disarm"):
        assert a.rf_control_block_reason(action) is not None, action

    # init/configure carry operator parameters and the panel invents none, so the state
    # machine can only be driven once they are set.  These are exactly the fields the
    # daemon requires (rf_daemon.c), named here so this criterion exercises the real
    # path instead of a panel that could never send anything.
    for name, value in (("lo_hz", 90000000), ("sample_rate_hz", 60000000),
                        ("rx_bandwidth_hz", 30000000), ("tx_bandwidth_hz", 30000000),
                        ("attenuation_mdB", 45000), ("phy_mode_1r1t", 0),
                        ("idelay_en", 0), ("idelay_tap", 0)):
        assert a.set_rf_parameter(name, value) is None, name

    # arm: read_only -> armed
    a.rf_control_prompt("arm")
    assert a.rf_control_block_reason("arm") is None
    a.set_rf_control_result("arm", {"ok": True, "code": None, "message": ""})
    assert a.rf_control["state"] == "armed"

    # daemon 的下一份快照广告 init/configure/stop/disarm。没 init 之前不能 configure。
    a.set_rf_status(_rf_control_snapshot(mode="control", armed=True, ops=ARMED_OPS))
    assert a.rf_control["state"] == "armed"
    assert a.rf_control["actions"] == ("init", "stop", "disarm")
    assert a.rf_control_block_reason("configure") is not None
    assert a.rf_control_block_reason("stop") is None

    # init: armed -> initialized
    a.rf_control_prompt("init")
    a.set_rf_control_result("init", {"ok": True, "code": None, "message": ""})
    assert a.rf_control["state"] == "initialized"
    a.set_rf_status(_rf_control_snapshot(mode="control", armed=True, ops=ARMED_OPS))
    assert a.rf_control["actions"] == ("configure", "stop", "disarm")

    # configure: initialized -> configured
    a.rf_control_prompt("configure")
    a.set_rf_control_result("configure", {"ok": True, "code": None, "message": ""})
    assert a.rf_control["state"] == "configured"

    # 幂等 configure: configured -> configured (daemon 明确允许)。
    a.set_rf_status(_rf_control_snapshot(mode="control", armed=True, ops=ARMED_OPS))
    assert "configure" in a.rf_control["actions"]
    a.rf_control_prompt("configure")
    assert a.rf_control_block_reason("configure") is None
    a.set_rf_control_result("configure", {"ok": True, "code": None, "message": ""})
    assert a.rf_control["state"] == "configured"

    # stop: configured -> stopped (stop 不需要确认)。
    assert a.rf_control_block_reason("stop") is None
    a.set_rf_control_result("stop", {"ok": True, "code": None, "message": ""})
    assert a.rf_control["state"] == "stopped"
    a.set_rf_status(_rf_control_snapshot(mode="control", armed=True, ops=ARMED_OPS))
    assert a.rf_control["state"] == "stopped"
    assert a.rf_control["actions"] == ("configure", "stop", "disarm")
    # stopped 之后同一配置仍能再次 configure(安全状态不是终点)。
    assert "configure" in a.rf_control["actions"]

    # disarm: any armed -> read_only
    assert a.rf_control_block_reason("disarm") is None
    a.set_rf_control_result("disarm", {"ok": True, "code": None, "message": ""})
    assert a.rf_control["state"] == "read_only"
    # disarm 后的真实快照: 未武装, 只广告 arm => 回到判据 2 的形状。
    a.set_rf_status(_rf_control_snapshot(mode="control", armed=False,
                                         ops=("control.arm",)))
    assert a.rf_control["actions"] == ("arm",)


def test_rf_control_stop_is_reachable_in_every_non_read_only_state_even_mid_operation():
    """判据 6: 非只读状态下 stop 始终可达 —— 包括操作进行中(挂 pending, 排队)。"""
    a = _control_app(mode="control", armed=False, ops=("control.arm",))
    assert "stop" not in a.rf_control["actions"], "只读状态不该有 stop"
    a.rf_control_prompt("arm")
    a.set_rf_control_result("arm", {"ok": True, "code": None, "message": ""})
    for expected, advance in (("armed", "init"), ("initialized", "configure"),
                              ("configured", None)):
        a.set_rf_status(_rf_control_snapshot(mode="control", armed=True,
                                             ops=ARMED_OPS, pending=True))
        assert a.rf_control["state"] == expected
        assert "stop" in a.rf_control["actions"], expected
        assert a.rf_control_block_reason("stop") is None, expected
        if advance is None:
            break
        a.rf_control_prompt(advance)
        a.set_rf_control_result(advance, {"ok": True, "code": None, "message": ""})
    a.set_rf_control_result("stop", {"ok": True, "code": None, "message": ""})
    a.set_rf_status(_rf_control_snapshot(mode="control", armed=True,
                                         ops=ARMED_OPS, pending=True))
    assert a.rf_control["state"] == "stopped"
    assert a.rf_control_block_reason("stop") is None


def test_rf_control_failure_keeps_state_and_records_the_daemon_code():
    """判据 6: 失败不推进状态机, 且把 daemon 的码原样记下(不是泛化消息)。"""
    a = _control_app(mode="control", armed=False, ops=("control.arm",))
    assert a.rf_control["last_code"] is None
    a.rf_control_prompt("arm")
    a.set_rf_control_result("arm", {"ok": False, "code": "PERMISSION",
                                    "message": "control token rejected"})
    assert a.rf_control["state"] == "read_only", "失败不该推进状态机"
    assert a.rf_control["last_action"] == "arm"
    assert a.rf_control["last_code"] == "PERMISSION"
    assert "PERMISSION" in a.rf_control["last_error"]
    assert "token rejected" in a.rf_control["last_error"]
    # 失败之后仍可重试(状态没被污染)。
    assert a.rf_control_block_reason("arm") is not None  # 需要重新确认
    a.rf_control_prompt("arm")
    assert a.rf_control_block_reason("arm") is None


def test_rf_control_token_is_a_path_hint_and_never_a_built_in_value():
    """判据 5: 面板只带操作员 provision 的**路径提示**, 绝不内置令牌/默认值。"""
    assert M.RF_CONTROL_TOKEN_PATH == "/etc/rf-control.token"
    a = M.UiApp()
    assert a.rf_control["token_path_hint"] == "/etc/rf-control.token"
    # 快照里若夹带令牌, 也不许进面板状态(只保留固定字段)。
    a.set_rf_status(_rf_control_snapshot(mode="control", armed=False,
                                         ops=("control.arm",), token="<SET_LOCALLY>"))
    assert "s3cret" not in repr(a.rf_control)
    assert "s3cret" not in repr(a.rf_status)
    assert a.rf_control["token_path_hint"] == M.RF_CONTROL_TOKEN_PATH
# ---- control-parameter contract (2026-09-30) -------------------------------------------
#
# "The panel never invents a number that configures real TX hardware" only means something
# if the contract it must satisfy is pinned.  These criteria pin the one the daemon
# enforces (rf_daemon.c), so the parameter-entry UI that does not exist yet has something
# exact to hit -- and so a future edit cannot quietly relax it.


def test_rf_panel_requires_explicit_mode_and_delay_beyond_daemon_minimum():
    assert M.RF_SAFE_ATTENUATION_MDB == 89750
    assert M.RF_ACTION_PARAMS["init"] == ("lo_hz", "sample_rate_hz", "attenuation_mdB", "phy_mode_1r1t")
    assert M.RF_ACTION_PARAMS["configure"] == (
        "lo_hz", "sample_rate_hz", "rx_bandwidth_hz", "tx_bandwidth_hz",
        "attenuation_mdB", "phy_mode_1r1t", "idelay_en", "idelay_tap")
    assert M.RF_ACTION_OPTIONAL_PARAMS["configure"] == ()
    # attenuation is required by BOTH and defaulted by NEITHER: the daemon says
    # "rf.init requires an explicit attenuation_mdB; omitting it ...".
    for action in ("init", "configure"):
        assert "attenuation_mdB" in M.RF_ACTION_PARAMS[action]


def test_rf_parameter_validator_rejects_what_the_daemon_would_refuse():
    assert M.rf_parameter_error("attenuation_mdB", 89750) is None
    assert M.rf_parameter_error("attenuation_mdB", 89751) is not None
    assert M.rf_parameter_error("attenuation_mdB", 0) is None
    assert M.rf_parameter_error("attenuation_mdB", -1) is not None
    valid_values = {
        "lo_hz": 80000000,
        "sample_rate_hz": 60000000,
        "rx_bandwidth_hz": 1000000,
        "tx_bandwidth_hz": 1000000,
    }
    for name in ("lo_hz", "sample_rate_hz", "rx_bandwidth_hz", "tx_bandwidth_hz"):
        assert M.rf_parameter_error(name, 0) is not None, name
        assert M.rf_parameter_error(name, -1) is not None, name
        assert M.rf_parameter_error(name, valid_values[name]) is None, name
    assert M.rf_parameter_error("lo_hz", 69999999) is not None
    assert M.rf_parameter_error("sample_rate_hz", 61440001) is not None
    assert M.rf_parameter_error("idelay_tap", 0) is None
    assert M.rf_parameter_error("idelay_tap", 31) is None
    assert M.rf_parameter_error("idelay_tap", 32) is not None
    assert M.rf_parameter_error("idelay_en", 1) is None
    assert M.rf_parameter_error("idelay_en", 2) is not None
    # A bool is NOT an integer here: True travels as 1 and would silently mean
    # "tap 1" or "1 mdB".
    assert M.rf_parameter_error("attenuation_mdB", True) is not None
    assert M.rf_parameter_error("idelay_tap", True) is not None
    assert M.rf_parameter_error("no_such_parameter", 1) is not None


def test_a_rejected_parameter_is_never_recorded():
    """The rejected value is exactly the one a later send could pick up, so a refusal
    must leave no trace in the store."""
    a = M.UiApp()
    assert a.set_rf_parameter("attenuation_mdB", 90000) is not None      # above ceiling
    assert a.set_rf_parameter("attenuation_mdB", True) is not None       # bool, not 1
    assert a.set_rf_parameter("lo_hz", 90000000) is None
    assert a.set_rf_parameter("sample_rate_hz", 60000000) is None
    # The two refusals left nothing behind: init is still short of attenuation, and an
    # INCOMPLETE plan hands back no arguments at all -- a partial set is not something a
    # later send should be able to pick up.
    args, why = a.rf_parameter_plan("init")
    assert args == {}, "an incomplete plan must not hand back a partial argument set"
    assert why is not None and "attenuation_mdB" in why, why


def test_rf_parameter_plan_names_the_missing_fields_and_never_invents_them():
    a = _control_app(mode="control", armed=True, ops=ARMED_OPS)
    args, why = a.rf_parameter_plan("init")
    assert args == {}
    for name in ("lo_hz", "sample_rate_hz", "attenuation_mdB"):
        assert name in why, (name, why)
    # The prompt refuses for that reason instead of asking for a confirmation it cannot
    # honour, and nothing is left pending.
    accepted, needs_confirm, message = a.rf_control_prompt("init")
    assert accepted is False and needs_confirm is False and message == why
    assert a.rf_control["pending_action"] is None
    # Once the operator supplies them the action becomes sendable, and only the fields
    # the action requires travel -- idelay_tap is configure-only and must not ride on init.
    for name, value in (("lo_hz", 80000000), ("sample_rate_hz", 60000000),
                        ("attenuation_mdB", 40000), ("phy_mode_1r1t", 0), ("idelay_tap", 5)):
        assert a.set_rf_parameter(name, value) is None, name
    args, why = a.rf_parameter_plan("init")
    assert why is None
    assert args == {"lo_hz": 80000000, "sample_rate_hz": 60000000,
                    "attenuation_mdB": 40000, "phy_mode_1r1t": 0}
    accepted, needs_confirm, _message = a.rf_control_prompt("init")
    assert accepted is True and needs_confirm is True
    assert a.rf_control_block_reason("init") is None


def test_configure_needs_both_bandwidths_even_when_init_is_ready():
    a = _control_app(mode="control", armed=True, ops=ARMED_OPS)
    for name, value in (("lo_hz", 80000000), ("sample_rate_hz", 60000000),
                        ("attenuation_mdB", 40000), ("phy_mode_1r1t", 0)):
        assert a.set_rf_parameter(name, value) is None
    assert a.rf_parameter_plan("init")[1] is None
    args, why = a.rf_parameter_plan("configure")
    assert args == {} and "rx_bandwidth_hz" in why and "tx_bandwidth_hz" in why
    assert a.set_rf_parameter("rx_bandwidth_hz", 30000000) is None
    assert a.set_rf_parameter("tx_bandwidth_hz", 30000000) is None
    assert "idelay_en" in a.rf_parameter_plan("configure")[1]
    assert a.set_rf_parameter("idelay_en", 0) is None
    assert a.set_rf_parameter("idelay_tap", 0) is None
    args, why = a.rf_parameter_plan("configure")
    assert why is None
    assert args == {"lo_hz": 80000000, "sample_rate_hz": 60000000,
                    "attenuation_mdB": 40000, "rx_bandwidth_hz": 30000000,
                    "tx_bandwidth_hz": 30000000, "phy_mode_1r1t": 0,
                    "idelay_en": 0, "idelay_tap": 0}
