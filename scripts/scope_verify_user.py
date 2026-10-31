#!/usr/bin/env python3
"""scope_verify_user.py: 验证 USER/DDR 上传数据在模拟 I/Q 输出上真的正确。

此前只验证过 USER(≤512 B, UART) 与 DDR(≤1 MiB, UDP) 路径的 CRC/元数据;
本脚本上传一段已知 PRBS payload,让固件把它当作符号比特经星座映射/RRC 输出,
用示波器抓 I/Q,与由 payload 直接算出的期望符号对齐求 BER。判据 BER=0。

用法:
    python scripts/scope_verify_user.py --selftest      # 无板卡/示波器,验证参考链
    python scripts/scope_verify_user.py COM11           # 板卡 + 示波器闭环
前置: 比特流 + 桥程序已运行,示波器 CH1=DAC0(I)/CH2=DAC1(Q) 已接。
"""
import argparse
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))

from siggen.digital_mod import user_expected_symbols
from siggen.protocol import (
    USER_CHUNK_MAX_BYTES,
    crc16_ccitt,
    digmod_cmd,
    digmod_filter_cmd,
    digmod_rolloff_cmd,
    digmod_source_cmd,
    digmod_symrate_cmd,
    user_chunk_cmd,
    user_clear_cmd,
    user_commit_cmd,
    user_loop_cmd,
)
from siggen.rrc import dac_scale, rrc_filter_symbols
# NOTE: do NOT import siggen.udp_ddr_pp at module scope. It pulls in PySide6.QtCore,
# and loading Qt before pyvisa's ResourceManager() makes VISA fail with
# VI_ERROR_LIBRARY_NFOUND (Qt/VISA DLL conflict). Import it lazily, after the scope
# resources are open.

_SCOPE_SPEC = importlib.util.spec_from_file_location(
    "scope_verify", Path(__file__).with_name("scope_verify.py"))
scope_verify = importlib.util.module_from_spec(_SCOPE_SPEC)
_SCOPE_SPEC.loader.exec_module(scope_verify)

SYMRATE = 100_000
ROLLOFF = "0.35"
# UART 路径循环播放的载荷。不能用 32 B: QAM16 下只够 64 个符号, 而 RRC 在循环回绕
# 处的瞬态跨约 8~16 个符号(rolloff 0.35, 8 symbol span), trim_symbols=8 的护栏覆盖
# 不全, 实测残留 BER≈0.004(128 B 时为 0.000), 会被误判成数据错误。
UART_PAYLOAD_BYTES = 128      # 128 B QPSK=256 符号 / QAM16=128 符号, UART 路径循环播放
DDR_PAYLOAD_BYTES = 4096      # 4 KiB: 100kSym/s 下 QPSK≈164ms / QAM16≈82ms 播放窗
UART_CASES = ("QPSK", "QAM16")
DDR_CASES = ("QPSK", "QAM16")


def make_payload(nbytes):
    """确定性、近似平衡、非周期的 payload。

    不能用 PRBS7 派生: 其比特序列周期仅 127,与固件内部 PRBS7 不可区分,
    也就无法证明"播放的确实是上传数据"。这里用 xorshift32(周期 2^32-1)。
    """
    state = 0x12345678
    out = bytearray()
    while len(out) < nbytes:
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= state >> 17
        state ^= (state << 5) & 0xFFFFFFFF
        out.append(state & 0xFF)
    return bytes(out[:nbytes])


def _report(name, ok, detail):
    print(("PASS" if ok else "FAIL"), name, "-", detail)
    return 0 if ok else 1


def _selftest():
    """无板卡/示波器: 用期望符号合成捕获,验证参考链与判据。"""
    failures = 0
    xinc = 1.0 / (4 * SYMRATE)
    for mode in UART_CASES:
        exp_i, exp_q = user_expected_symbols(mode, make_payload(UART_PAYLOAD_BYTES))
        reps = 6
        i_tx, q_tx = rrc_filter_symbols(np.tile(exp_i, reps), np.tile(exp_q, reps))
        i_bytes = (dac_scale(i_tx).astype(np.int16) + 128).astype(np.uint8)
        q_bytes = (dac_scale(q_tx).astype(np.int16) + 128).astype(np.uint8)
        recovered = scope_verify.match_filter_capture(i_bytes, q_bytes, SYMRATE, xinc)
        ber, offset, inverted = scope_verify.align_known_sequence(
            recovered, exp_i, exp_q, mode)
        rec_i = np.asarray(recovered[0], dtype=float)
        rec_q = np.asarray(recovered[1], dtype=float)
        rec_i[10] = -rec_i[10]
        ber_bad, _, _ = scope_verify.align_known_sequence(
            (rec_i, rec_q), exp_i, exp_q, mode)
        clean_ok, bad_ok = ber == 0.0, ber_bad > 0.0
        failures += _report(
            "SELFTEST %s" % mode, clean_ok and bad_ok,
            "clean_BER=%s offset=%s inverted=%s corrupt_BER=%.4f"
            % ("0" if clean_ok else ber, offset, inverted, ber_bad))
    # 小数采样相位: 合成捕获整体偏移 0.37 个符号后仍必须回到 BER=0。
    # 这正是板上失败的原因——只用 sample_offset=0 加符号相位搜索时此用例必失败。
    mode = "QPSK"
    exp_i, exp_q = user_expected_symbols(mode, make_payload(UART_PAYLOAD_BYTES))
    i_tx, q_tx = rrc_filter_symbols(np.tile(exp_i, 6), np.tile(exp_q, 6))
    base_xinc = 1.0 / (4 * SYMRATE)
    fine_xinc = base_xinc / 8.0
    src_t = np.arange(i_tx.size) * base_xinc
    dst_t = 0.37 / SYMRATE + np.arange(int(src_t[-1] / fine_xinc)) * fine_xinc
    i_fine = np.interp(dst_t, src_t, i_tx)
    q_fine = np.interp(dst_t, src_t, q_tx)
    i_bytes = (dac_scale(i_fine).astype(np.int16) + 128).astype(np.uint8)
    q_bytes = (dac_scale(q_fine).astype(np.int16) + 128).astype(np.uint8)
    ber, offset, _ = _align_capture(i_bytes, q_bytes, SYMRATE, fine_xinc,
                                    exp_i, exp_q, mode)
    failures += _report(
        "SELFTEST fractional sampling phase", ber == 0.0,
        "BER=%s offset=%s (capture shifted 0.37 symbol)" % (ber, offset))
    # DDR 规模参考: 数千符号周期上的相位搜索必须仍然快速且唯一
    t0 = time.monotonic()
    exp_i, exp_q = user_expected_symbols("QPSK", make_payload(DDR_PAYLOAD_BYTES))
    window = slice(500, 700)
    recovered = (exp_i[window].astype(float), exp_q[window].astype(float))
    ber, offset, _ = scope_verify.align_known_sequence(recovered, exp_i, exp_q, "QPSK")
    elapsed = time.monotonic() - t0
    size_ok = ber == 0.0 and offset == window.start and elapsed < 5.0
    failures += _report(
        "SELFTEST DDR %d symbols" % exp_i.size, size_ok,
        "align %.2fs offset=%s BER=%s" % (elapsed, offset, ber))
    print("SELFTEST RESULT:", "PASS" if failures == 0 else "FAIL")
    return 1 if failures else 0


def _upload_user_uart(payload):
    scope_verify.raw(user_clear_cmd())
    for offset in range(0, len(payload), USER_CHUNK_MAX_BYTES):
        scope_verify.raw(user_chunk_cmd(offset, payload[offset:offset + USER_CHUNK_MAX_BYTES]))
    scope_verify.raw(user_commit_cmd(len(payload) * 8, crc16_ccitt(payload)))


def _configure_digmod(mode):
    scope_verify.setup_board([
        digmod_filter_cmd("RRC"),
        digmod_rolloff_cmd(ROLLOFF),
        digmod_symrate_cmd(SYMRATE),
        digmod_cmd(mode),
    ])


def _align_capture(i_capture, q_capture, symrate, xinc, exp_i, exp_q, mode,
                   rolloff=ROLLOFF):
    """2-D 对齐: 同时搜索符号相位与小数采样相位。

    真实示波器捕获的起点落在符号周期内的任意位置, 固定 sample_offset=0 时 BER 完全
    被这个相位支配(同一路 PRBS7 在该相位下可读出 0.004 或 0.19), 与数据对错无关;
    内部 RRC 校验正是因此搜索 32 个 timing offset(见 scope_verify._align_rrc_capture)。
    """
    best = None
    for timing_offset in np.linspace(0.0, 1.0 / symrate, 32, endpoint=False):
        recovered = scope_verify.match_filter_capture(
            i_capture, q_capture, symrate, xinc,
            sample_offset=float(timing_offset), rolloff=rolloff)
        candidate = scope_verify.align_known_sequence(recovered, exp_i, exp_q, mode)
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best


def _capture_ber(mode, exp_i, exp_q, on_run=None, settle_s=0.8):
    scope_verify.scope_acq(2e-3, 2.0, settle_s=settle_s, on_run=on_run)
    xinc, y_i = scope_verify.scope_wave("CHAN1")
    _, y_q = scope_verify.scope_wave("CHAN2")
    return _align_capture(
        scope_verify.scope_capture_to_digital(y_i),
        scope_verify.scope_capture_to_digital(y_q), SYMRATE, xinc,
        exp_i, exp_q, mode)


def _run_uart_case(mode):
    payload = make_payload(UART_PAYLOAD_BYTES)
    exp_i, exp_q = user_expected_symbols(mode, payload)
    try:
        scope_verify.raw("SET DIGMOD MODE OFF")
        scope_verify.raw(digmod_source_cmd("PRBS7"))
        _upload_user_uart(payload)
        scope_verify.raw(user_loop_cmd(True))
        scope_verify.raw(digmod_source_cmd("USER"))
        _configure_digmod(mode)
        ber, offset, inverted = _capture_ber(mode, exp_i, exp_q)
        return _report("USER UART %s" % mode, ber == 0.0,
                       "BER=%s offset=%s inverted=%s" % (ber, offset, inverted))
    except Exception as exc:
        return _report("USER UART %s" % mode, False, "capture failed: %r" % exc)


def _run_ddr_case(client, mode):
    from siggen.udp_ddr_pp import ReplyError

    payload = make_payload(DDR_PAYLOAD_BYTES)
    exp_i, exp_q = user_expected_symbols(mode, payload)
    try:
        scope_verify.raw("SET DDR PP STOP")
        scope_verify.raw("SET DIGMOD MODE OFF")
        scope_verify.raw(digmod_source_cmd("PRBS7"))
        # 固件要求 user_valid 才允许 SOURCE USER: 先提交一个最小 UART 缓冲解锁,
        # 实际数据仍来自 DDR 流(播放时 user_stream_enable 生效)。
        _upload_user_uart(make_payload(4))
        scope_verify.raw(digmod_source_cmd("USER"))
        # 不能发 SET DDR PP CLEAR: 它把 bank 的 stage_owner 置回 UART,
        # 之后的 UDP BEGIN 会被拒为 BUSY(见 README 门禁串联说明)。
        result = client.upload(0, payload)
        if (result.device_error is not ReplyError.NONE
                or result.payload_crc32 != result.device_crc32):
            raise RuntimeError("DDR upload CRC mismatch")
        _configure_digmod(mode)
        ber, offset, inverted = _capture_ber(
            mode, exp_i, exp_q,
            on_run=lambda: scope_verify.raw("SET DDR PP START"), settle_s=0.05)
        scope_verify.raw("SET DDR PP STOP")
        scope_verify.raw("SET DIGMOD MODE OFF")
        return _report("USER DDR %s" % mode, ber == 0.0,
                       "BER=%s offset=%s inverted=%s" % (ber, offset, inverted))
    except Exception as exc:
        scope_verify.raw("SET DDR PP STOP")
        return _report("USER DDR %s" % mode, False, "capture failed: %r" % exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="USER/DDR 数据路模拟 I/Q 闭环验证")
    parser.add_argument("serial_port", nargs="?", default="COM11")
    parser.add_argument("--host", default="192.0.2.10")
    parser.add_argument("--port", type=int, default=40100)
    parser.add_argument("--selftest", action="store_true",
                        help="无板卡/示波器,仅验证参考链与判据")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    scope_verify.PORT = args.serial_port
    print("== scope_verify_user: board=%s scope=%s" % (args.serial_port, scope_verify.SCOPE))
    scope_verify.open_resources()
    scope_verify.reset_board()
    scope_verify.scope_setup()
    from siggen.udp_ddr_pp import UdpDdrPpClient

    client = UdpDdrPpClient(args.host, args.port)

    failures = 0
    for mode in UART_CASES:
        failures += _run_uart_case(mode)
    for mode in DDR_CASES:
        failures += _run_ddr_case(client, mode)

    scope_verify.raw("SET DIGMOD MODE OFF")
    scope_verify.raw(digmod_source_cmd("PRBS7"))
    scope_verify.inst.close()
    print("RESULT: %d/%d PASS" % (
        len(UART_CASES) + len(DDR_CASES) - failures,
        len(UART_CASES) + len(DDR_CASES)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
