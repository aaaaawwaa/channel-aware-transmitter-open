#!/usr/bin/env python3
"""AWG **协议桥**: 把串口/网络上的一行行命令交给 `awg_ctl`, 回包原样写回。

为什么需要它: 合流之后 PS 跑的是 PYNQ Linux, 原来那套裸机固件不在了; 而现成的两套验收
套件(`scripts/test_bridge.py` 230 断言、`scripts/scope_verify.py` 61 项)和上位机 GUI 都是
**串口 + ASCII** 的客户端。桥把这层线协议原样提供出来, 于是它们**一行都不用改** ——
反过来, 它们就成了控制库移植正确性的端到端判据。

线协议的细节照抄固件(`sdk/src/main.c` 的收字节逻辑), 因为客户端会依赖它们:
  * 行尾 `\\r` 或 `\\n` 都算结束; 空行**不回应**。
  * 整行**转大写**后才解析(在 `awg_ctl.handle` 里做, 与固件 main.c:3076 一致)。
  * 行长上限 `RX_BUF_MAX=72`: 缓冲满(71 字节)还来字节 -> 丢缓冲并回 `ERR BAD VALUE`。
  * 半行闲置超过约 6 秒 -> 回 `ERR TIMEOUT` 并丢缓冲(固件按 333333333 个 XTime tick 判)。
  * 启动打一行握手: `AC820-AWG bridge ready`(客户端靠它确认桥在; 等不到只会超时继续)。

用法:
    # 板上(先停掉 pynq-status —— overlay 只能有一个持有者)
    sudo env XILINX_XRT=/usr PYTHONPATH=/home/xilinx python3 -u awg_bridge.py
    # 指定串口 / 换 TCP / 不碰 PL 只跑协议(没有位流也能验线协议)
    ... python3 -u awg_bridge.py --port /dev/ttyPS1
    ... python3 -u awg_bridge.py --tcp 40101
    ... python3 -u awg_bridge.py --dry-run
"""
import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import awg_ctl  # noqa: E402

BANNER = "AC820-AWG bridge ready"
RX_BUF_MAX = 72                 # 与固件同名常量
LINE_TIMEOUT_S = 6.0            # 固件 TIMEOUT_TICKS(333333333 @ ~55.6MHz) ≈ 6s
BAUD = 115200
# 本目录下的默认位流(合流后的 AWG 位流)。本镜像的 PYNQ 版本 `Overlay()` 不接受无参调用。
DEFAULT_BITFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "awg_merged.bit")
# ⚠ 这个镜像的设备树里**只有一个串口节点**(`/dev/ttyPS0`), 而它就是 AWG 用的 UART1
#   (MIO48/49) —— 也就是说**它同时是 Linux 控制台**。跑桥之前必须停掉
#   `serial-getty@ttyPS0`(控制台改走 SSH), 否则两边抢同一个口。
DEFAULT_PORT = "/dev/ttyPS0"
# termios 只在 Linux 上有 —— **不要**顶层 import, 否则协议层在开发机(Windows)上不可测。
# 串口那两条函数里按需 import。


class Protocol(object):
    """字节流 <-> 命令行的粘合层。**纯逻辑, 不碰 I/O**, 所以离线可测。"""

    BANNER = BANNER

    def __init__(self, ctl, timeout=LINE_TIMEOUT_S, clock=time.monotonic):
        self.ctl = ctl
        self.timeout = timeout
        self.clock = clock
        self.buf = bytearray()
        self.pending_since = None

    def feed(self, data):
        """吃一段字节, 返回要回给客户端的字节串(可能为空)。"""
        out = bytearray()
        for byte in bytearray(data):
            if byte in (0x0D, 0x0A):                 # \r 或 \n
                if self.buf:
                    out += self._dispatch()
                continue
            if len(self.buf) < RX_BUF_MAX - 1:
                self.buf.append(byte)
                if self.pending_since is None:
                    self.pending_since = self.clock()
            else:
                # 固件: 缓冲满还来字节 -> 丢掉重来
                self.buf = bytearray()
                self.pending_since = None
                out += b"ERR BAD VALUE\r\n"
        return bytes(out)

    def _dispatch(self):
        line = self.buf.decode("ascii", "replace")
        self.buf = bytearray()
        self.pending_since = None
        reply = self.ctl.handle(line)
        return b"" if reply is None else (reply.encode("ascii", "replace") + b"\r\n")

    def poll_timeout(self):
        """半行放太久 -> 与固件同样回 ERR TIMEOUT。定期调用(或每来一段字节后调用)。"""
        if (self.buf and self.pending_since is not None
                and self.clock() - self.pending_since > self.timeout):
            self.buf = bytearray()
            self.pending_since = None
            return b"ERR TIMEOUT\r\n"
        return b""

    def banner(self):
        return (BANNER + "\r\n").encode()


# ---------------- 传输 ----------------

def open_serial(path, baud=BAUD):
    """把串口配成 115200 8N1 raw 模式。**用标准库的 termios**, 不依赖 pyserial ——
    板上那套 Python 里有没有 pyserial 不能假定。"""
    import termios                                  # 按需 import(只有 Linux 有)
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0                                   # iflag: 不做任何输入转换
    attrs[1] = 0                                   # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0                                   # lflag: 原始
    attrs[4] = attrs[5] = getattr(termios, "B%d" % baud)
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def run_serial(path, proto, baud=BAUD):
    fd = open_serial(path, baud)
    os.write(fd, proto.banner())
    print("awg_bridge: %s ready (%d 8N1)" % (path, baud), flush=True)
    try:
        while True:
            try:
                chunk = os.read(fd, 256)
            except BlockingIOError:
                chunk = b""
            if chunk:
                os.write(fd, proto.feed(chunk))
            else:
                time.sleep(0.002)
            out = proto.poll_timeout()
            if out:
                os.write(fd, out)
    finally:
        os.close(fd)


def _serve_conn(conn, proto):
    conn.sendall(proto.banner())
    while True:
        chunk = conn.recv(256)
        if not chunk:
            return
        conn.sendall(proto.feed(chunk))


def run_tcp(port, make_proto, host="0.0.0.0"):
    """也给网络开一口: 上位机可以改走 TCP, 于是串口能整个留给别的东西
    (板子的串口控制台就还在)。每个连接**一个独立的协议实例**, 避免两条连接互相踩状态。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    print("awg_bridge: tcp %s:%d ready" % (host, port), flush=True)
    while True:
        conn, addr = srv.accept()
        print("awg_bridge: %s connected" % (addr,), flush=True)
        try:
            _serve_conn(conn, make_proto())
        except OSError as exc:
            print("awg_bridge: %s dropped (%s)" % (addr, exc), flush=True)
        finally:
            conn.close()


# ---------------- 组装 ----------------

def make_ctl(bitfile=None, dry_run=False):
    if dry_run:
        print("awg_bridge: --dry-run, 不碰 PL(只验线协议)", flush=True)
        return awg_ctl.AwgCtl()
    from pynq import Clocks, Overlay
    # 本镜像的 PYNQ 版本里 `Overlay()` **必须**给 bitfile(无参调用会 TypeError:
    # __init__() missing 1 required positional argument), 所以默认就用本目录的位流。
    overlay = Overlay(bitfile or DEFAULT_BITFILE)
    # Overlay() **不会**套用 .hwh 里的时钟设置: 实测装载前 FCLK0 是镜像启动默认的 75 MHz,
    # 而 AWG 的时序是按 50 MHz 签的(见技能 ac820-pynq)。必须显式设。
    Clocks.fclk0_mhz = 50.0
    print("awg_bridge: overlay 已装, FCLK0 = %s MHz" % Clocks.fclk0_mhz, flush=True)
    return awg_ctl.AwgCtl(awg_ctl.PynqBackend(overlay))


def main(argv=None):
    ap = argparse.ArgumentParser(description="AWG 协议桥(awg_ctl 的线协议前端)")
    ap.add_argument("--port", default=DEFAULT_PORT,
                    help="串口设备(默认 %s; 注意它与 Linux 控制台同口, 要先停 serial-getty)" % DEFAULT_PORT)
    ap.add_argument("--tcp", type=int, help="改走 TCP 端口(不占用串口)")
    ap.add_argument("--bitfile", help="overlay 位流路径(默认用 PYNQ 的默认 overlay)")
    ap.add_argument("--dry-run", action="store_true", help="不装 overlay, 只跑协议")
    args = ap.parse_args(argv)

    ctl = make_ctl(args.bitfile, args.dry_run)
    if args.tcp:
        run_tcp(args.tcp, lambda: Protocol(ctl))
    else:
        run_serial(args.port, Protocol(ctl))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nawg_bridge: 退出", flush=True)
