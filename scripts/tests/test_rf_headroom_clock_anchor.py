"""可信时钟锚（合同 §8）：板卡 RTC 不得用于判定授权窗口/接线新鲜度。

判据来源（本文件只写判据，不改判据）
------------------------------------
合同 §8：板端 RTC 不得用于排序事件或判定有效期；以「主机 UTC + board boot_id/uptime」
为准。实测背景（2026-10-15）：46 dB 单次派发被 G0 以
RUN_NOT_STARTED_PREFLIGHT / the authorization is not valid yet 拒绝、零消耗、nonce 未消耗、
无物理帧——根因就是授权窗口用的是板卡自身时钟（无电池 RTC，读到 2021）。

被测接口（由本文件与实现共同固定）
----------------------------------
* rf_headroom_research_run.RUNNER_VERSION == '1.0.3'（行为变更必须升版，运行包 receipt 随之绑定）
* CLOCK_ANCHOR_MAX_AGE_S == 120、CLOCK_ANCHOR_FUTURE_TOLERANCE_S == 60（写死，无开关）
* local_board_clock(boot_id_path=..., uptime_path=...) -> dict(boot_id=..., uptime_s=...)
  （板端入口自行读 /proc；读失败/空/非法一律抛错，绝不用 RTC 兜底）
* resolve_clock_anchor(host_utc, anchor_boot_id, anchor_uptime_s, *, board_clock_reader=...)
  -> ClockAnchor；anchor.now() == host_utc + (board_uptime - anchor_uptime)；
  任何失败 -> RunnerRefused(label=RUN_REFUSED_CLOCK_ANCHOR, gate=GATE_AUTHORIZATION, ...)
* CLI run 子命令：--host-utc / --anchor-boot-id / --anchor-uptime-s，默认 None，
  因此「缺锚」是具名拒绝而不是 argparse 退出；没有任何改容差/允许板卡时钟的开关。
* 拒绝发生在 G0、在任何板卡读取与 nonce 消费之前，终态 RUN_REFUSED_CLOCK_ANCHOR。

回归（本文件的核心，标 REGRESSION）
-----------------------------------
板卡时钟读到 2021 时：授权窗口/接线新鲜度必须按锚判定（否则「not valid yet」）；且进程
时钟一次都不许被读——把 R.utc_now 换成会失败的函数后，锚定运行必须照常完成、无锚
运行必须仍以具名拒绝结束。
"""
import datetime
import json
from pathlib import Path
import sys
import socket

import pytest

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / 'scripts' / 'tests'
for _extra in (str(REPO), str(TESTS)):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

import test_rf_headroom_research_run as base

R = base.R
sim = base.sim
Harness = base.Harness
SESSION = base.SESSION

ANCHOR_TIME = datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
ANCHOR_UTC = '2026-10-15T12:00:00+00:00'
ANCHOR_UPTIME_S = 1000.0
BOARD_BOOT_ID = 'd85fa469-aeb7-4ef9-860b-15389314ef30'
OTHER_BOOT_ID = '11111111-2222-3333-4444-555555555555'
# 板卡自身时钟：无电池 RTC 的真实读数（本轮板卡实测就是 2021）。
RTC_2021 = datetime.datetime(2021, 6, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
NEWLINE = chr(10)


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    """零板卡接触：这些用例里任何 socket 都必须失败。"""
    def forbidden(*args, **kwargs):
        raise AssertionError('this test tried to open a socket')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    return forbidden


def board_clock(boot_id=BOARD_BOOT_ID, uptime_s=ANCHOR_UPTIME_S):
    return lambda: dict(boot_id=boot_id, uptime_s=uptime_s)


def resolve(host_utc=ANCHOR_UTC, anchor_boot_id=BOARD_BOOT_ID,
            anchor_uptime_s=ANCHOR_UPTIME_S, reader=None, uptime_now=ANCHOR_UPTIME_S,
            boot_id_now=BOARD_BOOT_ID):
    probe = reader if reader is not None else board_clock(boot_id_now, uptime_now)
    return R.resolve_clock_anchor(host_utc, anchor_boot_id, anchor_uptime_s,
                                  board_clock_reader=probe)


def subparser_options(name):
    import argparse
    parser = R.build_parser()
    subparsers = [action for action in parser._actions
                  if isinstance(action, argparse._SubParsersAction)]
    choices = subparsers[0].choices
    sub = choices[name]
    return sub, {option for action in sub._actions for option in action.option_strings}


def refuses(**kwargs):
    """解析锚并返回具名拒绝（用例都要求它失败时给出那个具名拒绝）。"""
    with pytest.raises(R.RunnerRefused) as info:
        resolve(**kwargs)
    refused = info.value
    assert refused.label == R.RUN_REFUSED_CLOCK_ANCHOR, refused.reason
    assert refused.gate == R.GATE_AUTHORIZATION
    return refused


# ------------------------------------------------------------ 固定输入与版本
def test_the_runner_version_is_the_clock_anchor_release():
    """行为变更必须升版：运行包 receipt 绑定的 --runner-version 随之变成 1.0.3。"""
    assert R.RUNNER_VERSION == '1.0.3'


def test_the_tolerances_are_hard_coded_and_have_no_switch():
    """§8：容差写死；既没有改容差的开关，也没有任何允许板卡时钟的开关。"""
    assert R.CLOCK_ANCHOR_MAX_AGE_S == 120
    assert R.CLOCK_ANCHOR_FUTURE_TOLERANCE_S == 60
    run, options = subparser_options('run')
    for flag in ('--host-utc', '--anchor-boot-id', '--anchor-uptime-s'):
        assert flag in options, 'run 子命令缺少锚参数 %s；现有=%r' % (flag, sorted(options))
    for flag in ('--anchor-age', '--anchor-age-s', '--anchor-tolerance',
                 '--allow-board-clock', '--board-clock', '--ignore-anchor',
                 '--skip-anchor', '--anchor-future-s', '--rtc'):
        assert flag not in options, '不得存在改容差/放宽锚的开关：%s' % flag
    assert run.get_default('host_utc') is None
    assert run.get_default('anchor_boot_id') is None
    assert run.get_default('anchor_uptime_s') is None
    simulate, simulate_options = subparser_options('simulate')
    for flag in ('--host-utc', '--anchor-boot-id', '--anchor-uptime-s'):
        assert flag not in simulate_options, (
            'simulate 是离线假硬件路径，不应接板卡时钟锚参数：%s' % flag)


# --------------------------------------------------------- 板端自读 /proc
def test_local_board_clock_reads_the_boot_id_and_the_uptime(tmp_path):
    """板端入口自行读取：boot_id 来自 /proc/sys/kernel/random/boot_id，uptime 取第一个字段。"""
    boot = tmp_path / 'boot_id'
    uptime = tmp_path / 'uptime'
    boot.write_text(BOARD_BOOT_ID + NEWLINE, encoding='ascii')
    uptime.write_text('1234.56 999.00' + NEWLINE, encoding='ascii')
    assert R.local_board_clock(boot_id_path=str(boot), uptime_path=str(uptime)) == dict(
        boot_id=BOARD_BOOT_ID, uptime_s=1234.56)

    for text in ('', '   ', 'not-a-number 1.0', '-5.0 1.0', 'nan 1.0', 'inf 1.0'):
        uptime.write_text(text, encoding='ascii')
        with pytest.raises((OSError, ValueError)):
            R.local_board_clock(boot_id_path=str(boot), uptime_path=str(uptime))
    boot.write_text('', encoding='ascii')
    uptime.write_text('1.0 1.0', encoding='ascii')
    with pytest.raises(ValueError):
        R.local_board_clock(boot_id_path=str(boot), uptime_path=str(uptime))
    with pytest.raises(OSError):
        R.local_board_clock(boot_id_path=str(tmp_path / 'missing'),
                            uptime_path=str(tmp_path / 'missing'))


# ---------------------------------------------------------------- 正例
def test_the_anchor_is_the_host_utc_advanced_by_the_boards_own_uptime():
    """§8 的公式：now = host_utc + (board_uptime - anchor_uptime)。"""
    anchor = resolve(uptime_now=ANCHOR_UPTIME_S + 60.5)
    assert anchor.now() == ANCHOR_TIME + datetime.timedelta(seconds=60.5)
    assert anchor.now().isoformat() == '2026-10-15T12:01:00.500000+00:00'
    record = anchor.to_dict()
    assert record['used'] is True
    assert record['board_clock_used'] is False, (
        '锚记录必须明说没有使用板卡时钟：%r' % record)
    assert record['host_utc'] == ANCHOR_UTC
    assert record['board_boot_id'] == BOARD_BOOT_ID
    assert record['anchor_uptime_s'] == ANCHOR_UPTIME_S
    assert record['board_uptime_s'] == ANCHOR_UPTIME_S + 60.5
    assert record['age_s'] == 60.5
    assert record['max_age_s'] == R.CLOCK_ANCHOR_MAX_AGE_S
    assert record['future_tolerance_s'] == R.CLOCK_ANCHOR_FUTURE_TOLERANCE_S
    assert 'boot_id' in record['formula'] and 'host_utc' in record['formula']


def test_the_anchor_accepts_zulu_and_an_offset():
    for host_utc in ('2026-10-15T12:00:00Z', '2026-10-15T20:00:00+08:00',
                     '2026-10-15T12:00:00.000+00:00'):
        assert resolve(host_utc=host_utc).now() == ANCHOR_TIME


def test_the_tolerance_boundaries_are_inclusive():
    """锚龄恰好 120 s 与恰好 0 s 都算在容差内；回退一律拒绝（见负例）。"""
    assert resolve(uptime_now=ANCHOR_UPTIME_S).now() == ANCHOR_TIME
    assert resolve(uptime_now=ANCHOR_UPTIME_S + 120).now() == ANCHOR_TIME + \
        datetime.timedelta(seconds=120)


# ---------------------------------------------------------------- 负例
@pytest.mark.parametrize('kwargs,needle', [
    (dict(host_utc=None), '--host-utc'),
    (dict(anchor_boot_id=None), '--anchor-boot-id'),
    (dict(anchor_uptime_s=None), '--anchor-uptime-s'),
])
def test_a_missing_anchor_parameter_refuses_by_name_without_reading_the_board_clock(
        kwargs, needle):
    """§8 fail-closed：缺任一锚参数 -> 具名拒绝，且绝不读板卡时钟去补。"""
    calls = []

    def probe():
        calls.append(1)
        return dict(boot_id=BOARD_BOOT_ID, uptime_s=ANCHOR_UPTIME_S)
    refused = refuses(reader=probe, **kwargs)
    assert needle in refused.reason
    assert 'clock anchor' in refused.reason
    assert calls == [], '缺锚时不得读板卡时钟：%r' % calls


def test_an_anchor_with_every_parameter_missing_refuses():
    refused = refuses(host_utc=None, anchor_boot_id=None, anchor_uptime_s=None)
    for needle in ('--host-utc', '--anchor-boot-id', '--anchor-uptime-s'):
        assert needle in refused.reason


def test_a_boot_id_that_is_not_the_boards_own_refuses():
    """锚属于另一次启动：boot_id 必须与板卡自读一致。"""
    refused = refuses(boot_id_now=OTHER_BOOT_ID)
    assert 'boot id' in refused.reason
    assert refused.detail.get('board_boot_id') == OTHER_BOOT_ID


def test_an_uptime_that_went_backwards_refuses():
    refused = refuses(uptime_now=ANCHOR_UPTIME_S - 10)
    assert 'backwards' in refused.reason


def test_an_anchor_older_than_the_fixed_tolerance_refuses():
    refused = refuses(uptime_now=ANCHOR_UPTIME_S + R.CLOCK_ANCHOR_MAX_AGE_S + 0.5)
    assert 'tolerance' in refused.reason
    assert refused.detail.get('max_age_s') == R.CLOCK_ANCHOR_MAX_AGE_S


def test_an_anchor_stamped_in_the_future_refuses():
    refused = refuses(uptime_now=ANCHOR_UPTIME_S - R.CLOCK_ANCHOR_FUTURE_TOLERANCE_S - 1)
    assert 'future' in refused.reason


@pytest.mark.parametrize('host_utc,needle', [
    ('2026-10-15T12:00:00', 'UTC offset'),
    ('2026-10-15', 'UTC offset'),
    ('yesterday', 'ISO8601'),
    ('', 'non-empty'),
    (42, 'non-empty'),
])
def test_the_host_utc_anchor_must_be_iso8601_with_a_timezone(host_utc, needle):
    refused = refuses(host_utc=host_utc)
    assert needle in refused.reason, refused.reason


def test_an_unreadable_board_clock_refuses_and_never_falls_back():
    def broken():
        raise OSError('the board has no /proc/uptime here')
    refused = refuses(reader=broken)
    assert 'could not be read' in refused.reason
    assert 'not used as a fallback' in refused.reason


@pytest.mark.parametrize('value', ['soon', '', -1.0, float('nan'), float('inf'), True])
def test_a_non_numeric_or_negative_anchor_uptime_refuses(value):
    refused = refuses(anchor_uptime_s=value)
    assert 'anchor-uptime-s' in refused.reason


@pytest.mark.parametrize('board', [
    None, {}, dict(boot_id=''), dict(uptime_s=1.0),
    dict(boot_id=BOARD_BOOT_ID, uptime_s='soon'),
    dict(boot_id=BOARD_BOOT_ID, uptime_s=float('nan')),
    dict(boot_id=BOARD_BOOT_ID, uptime_s=-1.0),
    dict(boot_id=BOARD_BOOT_ID, uptime_s=True),
])
def test_a_board_clock_read_that_is_not_usable_refuses(board):
    refused = refuses(reader=lambda: board)
    assert refused.reason


# --------------------------------------------------- G0：锚先于任何板卡读取
def test_a_board_run_without_an_anchor_refuses_by_name_at_g0(tmp_path):
    """生产形状（hardware_touched、无注入时钟、无锚）必须在 G0 具名拒绝、零消费。"""
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    bundle.update(clock=None, simulation=False, hardware_touched=True,
                  board_clock_probe=board_clock())
    report = harness.run(bundle)
    assert report['terminal'] == R.RUN_REFUSED_CLOCK_ANCHOR
    assert report['refusal']['gate'] == R.GATE_AUTHORIZATION
    assert report['exit_code'] == R.EXIT_NOT_STARTED
    assert report['study_invoked'] is False
    assert report['retry_allowed'] is False
    assert [gate['gate'] for gate in report['gates']] == [R.GATE_AUTHORIZATION]
    assert report['gates'][0]['board_read'] is False
    assert report['clock_anchor']['used'] is False
    assert report['clock_anchor']['board_clock_used'] is False
    assert bundle['storage_probe'].calls == []
    assert not harness.ledger.exists()
    assert not (harness.evidence / SESSION).exists()
    assert harness.refusal_documents('d' * 32).is_file()


@pytest.mark.parametrize('kwargs,needle', [
    (dict(anchor_boot_id=OTHER_BOOT_ID), 'boot id'),
    (dict(anchor_uptime_s=str(ANCHOR_UPTIME_S + 10)), 'backwards'),
    (dict(anchor_uptime_s=str(ANCHOR_UPTIME_S - 121)), 'tolerance'),
    (dict(anchor_uptime_s=str(ANCHOR_UPTIME_S + 120)), 'future'),
    (dict(host_utc='2026-10-15T12:00:00'), 'UTC offset'),
    (dict(host_utc='not-a-time'), 'ISO8601'),
])
def test_an_unusable_anchor_refuses_the_run_before_any_board_read(tmp_path, kwargs,
                                                                  needle):
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    bundle.update(clock=None, simulation=False, hardware_touched=True,
                  board_clock_probe=board_clock())
    arguments = dict(host_utc=ANCHOR_UTC, anchor_boot_id=BOARD_BOOT_ID,
                     anchor_uptime_s=str(ANCHOR_UPTIME_S))
    arguments.update(kwargs)
    report = harness.run(bundle, **arguments)
    assert report['terminal'] == R.RUN_REFUSED_CLOCK_ANCHOR
    assert needle in report['refusal']['reason'], report['refusal']['reason']
    assert report['refusal']['gate'] == R.GATE_AUTHORIZATION
    assert bundle['storage_probe'].calls == []
    assert bundle['identity_probe'].calls == 0
    assert not harness.ledger.exists()


def test_a_bundle_that_declares_real_hardware_needs_a_board_clock_reader(tmp_path):
    """声明真实硬件却没有读板卡时钟的依赖：拒绝，而不是回退到进程时钟。"""
    harness = Harness(tmp_path)
    bundle = harness.bundle()
    bundle.update(clock=None, simulation=False, hardware_touched=True,
                  board_clock_probe=None)
    report = harness.run(bundle, host_utc=ANCHOR_UTC, anchor_boot_id=BOARD_BOOT_ID,
                         anchor_uptime_s=str(ANCHOR_UPTIME_S))
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert report['refusal']['gate'] == R.GATE_AUTHORIZATION
    assert 'board clock' in report['refusal']['reason']


# ------------------------------------------------ 窗口与新鲜度按锚判定（回归）
def anchored_harness(root, **kwargs):
    """授权窗口/接线确认都围绕锚时刻构造；板卡时钟另设为 2021。"""
    return Harness(root, now=ANCHOR_TIME, **kwargs)


def production_shaped(harness, rtc=RTC_2021):
    """The production bundle shape: real hardware.  rtc=None leaves NO injected
    clock, which is exactly what default_dependencies() builds on the board."""
    bundle = harness.bundle()
    bundle.update(clock=(None if rtc is None else sim.SimClock(rtc)),
                  simulation=False, hardware_touched=True,
                  board_clock_probe=board_clock())
    return bundle


def test_REGRESSION_the_2021_board_clock_no_longer_decides_the_window(tmp_path):
    """实测根因回归：板卡 RTC=2021 时，若用板卡时钟就会「not valid yet」。"""
    harness = anchored_harness(tmp_path)
    report = harness.run(production_shaped(harness), host_utc=ANCHOR_UTC,
                         anchor_boot_id=BOARD_BOOT_ID,
                         anchor_uptime_s=str(ANCHOR_UPTIME_S))
    assert report['terminal'] == R.RUN_COMPLETED, report['refusal']
    assert report['clock_anchor']['used'] is True
    assert report['clock_anchor']['board_clock_used'] is False
    assert report['clock_anchor']['resolved_now'] == ANCHOR_UTC
    assert report['token_consumed_at'] == ANCHOR_UTC
    assert report['generated_at'] == ANCHOR_UTC
    assert report['wiring']['age_s'] == 1800.0
    assert report['authorization']['issued_at'].startswith('2026-10-15T11:00:00')


def test_REGRESSION_the_process_clock_is_never_consulted(monkeypatch, tmp_path):
    """把进程时钟换成会失败的对象：锚定运行照常完成，无锚运行仍具名拒绝。"""
    def forbidden():
        raise AssertionError('the board/process clock was consulted')
    monkeypatch.setattr(R, 'utc_now', forbidden)

    anchored = anchored_harness(tmp_path / 'anchored')
    report = anchored.run(production_shaped(anchored), host_utc=ANCHOR_UTC,
                          anchor_boot_id=BOARD_BOOT_ID,
                          anchor_uptime_s=str(ANCHOR_UPTIME_S))
    assert report['terminal'] == R.RUN_COMPLETED

    unanchored = anchored_harness(tmp_path / 'unanchored')
    refused = unanchored.run(production_shaped(unanchored, rtc=None))
    assert refused['terminal'] == R.RUN_REFUSED_CLOCK_ANCHOR
    assert refused['generated_at'] is None, (
        'no anchor means no authorized instant -- and never an invented one: %r'
        % refused['generated_at'])
    assert refused['clock_anchor']['used'] is False


def test_the_board_ledger_stamps_the_authorized_instant(tmp_path):
    """账本/证据时间戳必须是锚时刻，不能再出现 2021。"""
    harness = anchored_harness(tmp_path)
    ledger_dir = tmp_path / 'board-ledger'
    report = harness.run(production_shaped(harness), host_utc=ANCHOR_UTC,
                         anchor_boot_id=BOARD_BOOT_ID,
                         anchor_uptime_s=str(ANCHOR_UPTIME_S),
                         board_ledger_dir=ledger_dir)
    assert report['terminal'] == R.RUN_COMPLETED
    lines = [json.loads(line) for line in
             (ledger_dir / 'authorization.jsonl').read_text(encoding='utf-8').splitlines()
             if line.strip()]
    assert len(lines) == 1, lines
    assert lines[0]['utc'] == ANCHOR_UTC, lines[0]
    assert lines[0]['clock_anchor']['used'] is True
    assert lines[0]['clock_anchor']['board_clock_used'] is False
    assert lines[0]['runner_version'] == '1.0.3'
    assert not lines[0]['utc'].startswith('2021')


def test_an_expired_window_is_expired_at_the_anchor_not_not_valid_yet(tmp_path):
    """锚合法时的窗口判定：过期必须判 expired（板卡 2021 时钟会误判 not valid yet）。"""
    harness = anchored_harness(tmp_path, expires_in_s=-60)
    report = harness.run(production_shaped(harness), host_utc=ANCHOR_UTC,
                         anchor_boot_id=BOARD_BOOT_ID,
                         anchor_uptime_s=str(ANCHOR_UPTIME_S))
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'expired' in report['refusal']['reason']
    assert 'not valid yet' not in report['refusal']['reason']
    assert not harness.ledger.exists()


def test_a_window_that_is_not_open_yet_is_refused_as_such(tmp_path):
    harness = anchored_harness(tmp_path, issued_ago_s=-3600)
    report = harness.run(production_shaped(harness), host_utc=ANCHOR_UTC,
                         anchor_boot_id=BOARD_BOOT_ID,
                         anchor_uptime_s=str(ANCHOR_UPTIME_S))
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'not valid yet' in report['refusal']['reason']
    assert not harness.ledger.exists()


def test_a_stale_wiring_confirmation_is_judged_at_the_anchor(tmp_path):
    """接线新鲜度同样按锚判定：按板卡 2021 时钟会被误判为「未来时间戳」。"""
    harness = anchored_harness(tmp_path, wiring_age_s=R.WIRING_FRESHNESS_S + 60)
    report = harness.run(production_shaped(harness), host_utc=ANCHOR_UTC,
                         anchor_boot_id=BOARD_BOOT_ID,
                         anchor_uptime_s=str(ANCHOR_UPTIME_S))
    assert report['terminal'] == R.RUN_NOT_STARTED_PREFLIGHT
    assert 'older than the fixed' in report['refusal']['reason']
    assert not harness.ledger.exists()



# ------------------------------------------------- 派发标记的时钟
def test_the_dispatch_marker_clock_is_the_anchor_or_absent_and_never_the_rtc(
        monkeypatch, tmp_path):
    """启动门写派发标记时（G0 之前）也不许写板卡 RTC 时间。

    锚可解析 -> 标记 utc 是锚时刻；锚不可解析 -> utc 为 null 且 clock_source 写明理由
    （绝不写 2021 假时间戳）。
    """
    import argparse
    monkeypatch.setattr(R, 'local_board_clock',
                        lambda: dict(boot_id=BOARD_BOOT_ID, uptime_s=ANCHOR_UPTIME_S))
    good = R.dispatch_marker_clock(argparse.Namespace(
        host_utc=ANCHOR_UTC, anchor_boot_id=BOARD_BOOT_ID,
        anchor_uptime_s=str(ANCHOR_UPTIME_S)))
    assert good['utc'] == ANCHOR_UTC, good
    assert good['clock_source'] == R.CLOCK_ANCHOR_SOURCE
    assert good['board_boot_id'] == BOARD_BOOT_ID

    absent = R.dispatch_marker_clock(argparse.Namespace(
        host_utc=None, anchor_boot_id=None, anchor_uptime_s=None))
    assert absent['utc'] is None, absent
    assert absent['clock_source'].startswith('UNRESOLVED: '), absent
    assert 'clock anchor' in absent['clock_source']
    assert not str(absent['utc']).startswith('2021')

# ------------------------------------------------------------- CLI 观察点
def test_a_run_without_an_anchor_is_a_named_refusal_not_an_argparse_error(
        tmp_path, monkeypatch):
    """缺锚是具名拒绝（写 run-gate 文档），不是 argparse 用法错误。

    The process-wide study slot is replaced by a fresh one so that this CLI run
    claims its own slot instead of the one another CLI test in this suite uses.
    """
    monkeypatch.setattr(R, 'PROCESS_STUDY_SLOT', R.ClaimOnce())
    harness = Harness(tmp_path, now=datetime.datetime.now(datetime.timezone.utc))
    code = R.main(base._cli_arguments(harness, 'run'))
    assert code == R.EXIT_NOT_STARTED
    document = json.loads(harness.refusal_documents('f' * 32).read_text('utf-8'))
    assert document['terminal'] == R.RUN_REFUSED_CLOCK_ANCHOR
    assert document['retry_allowed'] is False
    assert document['study_invoked'] is False
    assert not (harness.evidence / SESSION).exists()
