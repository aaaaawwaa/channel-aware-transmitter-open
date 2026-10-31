"""`awg_bridge` 线协议层的离线测试(不需要板子/串口)。

桥的价值在于"客户端一行都不用改", 所以这里钉住的就是**客户端依赖的那些线协议细节**:
行尾、大写、行长上限、半行超时、握手串。任何一条改错, 板上那两套套件会以很难懂的方式失败
(比如握手串变了就只是"等 3 秒然后继续")。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import awg_bridge as br  # noqa: E402
import awg_ctl as a  # noqa: E402


def _proto():
    return br.Protocol(a.AwgCtl())


def test_banner_is_what_the_suites_wait_for():
    # scripts/test_bridge.py 的 wait_for_bridge() 逐字节比这一串; 改了就静默退化成"等 3 秒"
    assert br.Protocol.BANNER == "AC820-AWG bridge ready"
    assert _proto().banner() == b"AC820-AWG bridge ready\r\n"


def test_crlf_and_bare_lf_both_terminate_a_line():
    p = _proto()
    assert p.feed(b"GET CH1\r\n").endswith(b"\r\n"), "回包也要带 CRLF"
    assert p.feed(b"GET CH1\n").startswith(b"CH1 WAVE")
    assert p.feed(b"GET CH1\r").startswith(b"CH1 WAVE")


def test_a_line_split_across_reads_is_reassembled():
    p = _proto()
    assert p.feed(b"GET CH") == b""
    assert p.feed(b"1\r\n").startswith(b"CH1 WAVE")


def test_empty_line_gets_no_reply():
    assert _proto().feed(b"\r\n") == b""
    assert _proto().feed(b"\n\n") == b""


def test_overflow_at_rx_buf_max_drops_the_line_and_complains():
    p = _proto()
    p.feed(b"X" * 71)                     # 顶到 71 字节还没行尾
    assert p.feed(b"Y") == b"ERR BAD VALUE\r\n"
    assert p.feed(b"\r\n") == b"", "溢出之后缓冲要丢掉, 不能把半行当命令"


def test_partial_line_times_out_like_the_firmware():
    now = [1000.0]
    p = br.Protocol(a.AwgCtl(), timeout=0.5, clock=lambda: now[0])
    p.feed(b"GET CH")
    assert p.poll_timeout() == b""
    now[0] += 0.6
    assert p.poll_timeout() == b"ERR TIMEOUT\r\n"
    assert p.poll_timeout() == b"", "报过一次就清掉"


def test_lowercase_reaches_the_parser_and_uppercase_reply_comes_back():
    p = _proto()
    assert p.feed(b"set ch1 freq 3000\r\n") == b"OK\r\n"
    assert b"FREQ 3000" in p.feed(b"get ch1\r\n")


def test_unknown_command_and_bad_channel_texts():
    p = _proto()
    assert p.feed(b"FOO\r\n") == b"ERR UNKNOWN CMD\r\n"
    assert p.feed(b"GET CH3\r\n") == b"ERR BAD CHANNEL\r\n"


def test_protocol_drives_the_real_library_end_to_end():
    # 一条完整往返: 设参数 -> 读回, 回包与固件同格式
    p = _proto()
    p.feed(b"SET CH1 WAVE SQUARE\r\n")
    p.feed(b"SET CH1 FREQ 250000\r\n")
    line = p.feed(b"GET CH1\r\n").decode().strip()
    assert "WAVE SQUARE" in line and "FREQ 250000" in line


def test_golden_replays_through_the_wire_layer_too():
    """比 `test_awg_ctl` 那条更严: 金标走**完整线协议**(组行 + 大写 + 回包加 CRLF)。

    这一条才是"板上的 test_bridge/scope_verify/上位机一行都不用改"的依据 ——
    它们看到的字节流必须与真固件逐字节相同。
    """
    import json
    golden = (Path(__file__).resolve().parents[3] / "docs" / "golden"
              / "command_replies.json")
    doc = json.loads(golden.read_text(encoding="utf-8"))
    p = _proto()
    bad = []
    for case in doc["cases"]:
        wire = p.feed(case["cmd"].encode() + b"\r\n")
        want = "" if case["reply"] == "" else case["reply"] + "\r\n"
        if wire.decode("ascii", "replace") != want:
            bad.append((case["scenario"], case["cmd"], want, wire))
    assert not bad, "线协议对不上 %d 条, 前 5 条: %s" % (len(bad), bad[:5])
