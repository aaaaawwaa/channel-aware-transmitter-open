#!/usr/bin/env python3
"""AC820 PYNQ 状态灯 + 面板状态屏 + 关机按键守护进程。

为什么需要它: 镜像启动过程中直接断电会把根文件系统写坏(本板已发生过一次)。
这个进程给板子一个**物理的优雅关机入口**: 按住 S0 满 3 秒即走 systemd 正常
关机流程(sync + 卸载), 并用 8 个 PL LED 持续显示镜像的关键状态 —— 尤其是
"SD 卡正在写" 与 "已经可以断电" 两个指示灯。

它同时持有 TFT(帧缓冲源由它打开), 所以面板上显示的是本进程画的状态屏: 把 LED 的语义
展开成文字(网络/服务/写卡/负载/运行时长)、镜像那 8 个 LED、并在长按时给出关机进度条。
**屏幕是可选功能**: 界面层导入失败或位流里没有 `axi_gpio_fb` 时自动退化成无界面,
绝不因为显示不出来就丢掉"长按关机"这个安全功能(见 `attach_screen`)。

LED 语义 (LED0 ~ LED7, 板载高电平点亮; 屏幕上按 1~8 编号展示):
    LED0  心跳: 1Hz 闪烁 = 守护进程在跑
    LED1  网络: 常亮 = eth0 拿到 IPv4 地址
    LED2  服务: 常亮 = Jupyter(9090) 正在监听
    LED3  写卡: 常亮 = 最近 1 秒内 SD 卡有写入  ← **亮着不要断电**
    LED4  关机: 快闪 = 正在关机
    LED5  就绪: 常亮 = 关机已完成、可以安全断电
    LED6  长按: 按住 S0 时亮, 显示计时已开始
    LED7  负载: 常亮 = 1 分钟平均负载 > 1

按键 (用板子**丝印**的叫法; 厂商引脚表里把它们记作 S1/S2, 差一位):
    S0 (H18) 长按 3s -> 优雅关机(松手过早则取消)
    S1 (H19) 短按    -> 状态自检: 8 个灯依次闪一遍(看着像跑马灯, 属正常)
    S2 (MIO47, PS 侧) / S3 (POR_B 复位) 本设计不使用

运行环境: root + systemd; 见同目录 pynq-status.service。
"""
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import sys
import threading
import time

import merged_release

os.environ.setdefault("XILINX_XRT", "/usr")

RELEASE_ROOT = Path(os.environ.get("LEDBTN_RELEASE_ROOT", "/home/xilinx/overlays/awg_merged"))
ACTIVE_RELEASE_MARKER = Path("/run/pynq-status/active-release")
HOLD_SECONDS = 3.0        # S0 长按多久触发关机
INPUT_PERIOD = 0.02       # local touch and physical keys; status work has its own cadence
POLL = INPUT_PERIOD
BEAT = 0.5                # 心跳半周期(s)
SLOW = 1.0                # 网络/服务/写卡 的刷新周期(s)
DRAW = 0.1                # LED 刷新周期(s)
UI_PERIOD = 0.1           # 屏幕刷新周期(s) —— 一帧实测 7.5ms, 10fps 不拖慢按键采样
SD_BLOCK = "/sys/block/mmcblk0/stat"
LOUD_BPS = 1.0 * 1024 * 1024   # 超过这个写入速率才算"大量写卡"(板上后台写实测 ~0.4MB/s 以下)
LOUD_HOLD = 5.0                # 一旦算大量, 屏幕上那块告警至少挂这么久(迟滞, 防闪)

# RF sidecar 的只读状态端口 —— 与 `rf-daemon.service` 的 `--port` 必须一致。
# 面板只在 127.0.0.1 上轮询它, 阶段 1 全部是只读 op(见 rf_status_client)。
RF_STATUS_PORT = 5000
RF_POLL = 0.5                  # 状态轮询周期(s)
RF_STALE_AFTER = 2.0           # 超过这么久没拿到新快照就标"过期"并把数值清成未知

LED_HEART, LED_NET, LED_JUPYTER, LED_DISK = 0, 1, 2, 3
LED_OFF, LED_READY, LED_HOLD, LED_LOAD = 4, 5, 6, 7

# `StatusScreen.render(point=...)` 用: 区分"调用方没传"与"传了 None(这一帧确实没有点)"
_UNSET = object()


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Leds:
    """8 位 LED 输出, 底层是 overlay 里的 axi_gpio_led。"""

    def __init__(self, gpio):
        self._gpio = gpio
        self._value = 0

    @property
    def value(self):
        """当前 8 位值(屏幕上要镜像它)。"""
        return self._value

    def set(self, index, on):
        bit = 1 << index
        self._value = (self._value | bit) if on else (self._value & ~bit)

    def write(self):
        self._gpio.channel1.write(self._value, 0xFF)

    def all(self, value):
        self._value = value & 0xFF
        self.write()


class Buttons:
    """S0/S1; 板载按下为低, 这里统一成"按下=True"。"""

    def __init__(self, gpio):
        self._gpio = gpio

    def pressed(self):
        raw = self._gpio.channel1.read() & 0x3
        return bool(~raw & 0x1), bool(~raw & 0x2)


def eth0_ipv4():
    """eth0 的第一个 IPv4 地址(带前缀长度)或 None。"""
    addrs = ipv4_addresses("eth0")
    return "%s/%d" % addrs[0] if addrs else None


RTM_GETADDR, NLM_F_REQUEST, NLM_F_DUMP = 22, 1, 0x300
NLMSG_DONE, RTM_NEWADDR, IFA_ADDRESS = 3, 20, 1


def ipv4_addresses(ifname):
    """netlink RTM_GETADDR -> [(ip, prefixlen)], 只取该接口自己的地址。

    别换成 SIOCGIFADDR: 本镜像把地址挂成带标签的别名(ip addr 显示 eth0:1), 老 ioctl
    按 ifa_label 精确匹配接口名, 问 "eth0" 直接 EADDRNOTAVAIL(板上实测), 于是网络灯
    永远不亮。RTM_GETADDR 按 ifindex 返回, 不受标签影响。
    """
    try:
        idx = socket.if_nametoindex(ifname)
    except OSError:
        return []
    s = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE)
    try:
        s.settimeout(1.0)
        s.bind((0, 0))
        # nlmsghdr(len=24) + ifaddrmsg: 少了后面 8 字节内核既不应答也不收尾(会挂死)
        s.send(struct.pack("=IHHII", 24, RTM_GETADDR, NLM_F_REQUEST | NLM_F_DUMP, 1, 0)
               + struct.pack("=BBBBI", socket.AF_UNSPEC, 0, 0, 0, 0))
        found = []
        buf = b""
        while True:
            try:
                buf += s.recv(65536)
            except OSError:
                return found
            done = False
            while len(buf) >= 16:
                ln, typ = struct.unpack("=IH", buf[:6])
                if ln < 16 or ln > len(buf):
                    break
                body, buf = buf[16:ln], buf[ln:]
                if typ == NLMSG_DONE:
                    done = True
                    break
                if typ != RTM_NEWADDR:
                    continue
                fam, plen, _flags, _scope, index = struct.unpack("=BBBBI", body[:8])
                if fam != socket.AF_INET or index != idx:
                    continue
                pos = 8
                while pos + 4 <= len(body):
                    rlen, rtype = struct.unpack("=HH", body[pos:pos + 4])
                    if rlen < 4:
                        break
                    if rtype == IFA_ADDRESS and rlen >= 8:
                        found.append((socket.inet_ntoa(body[pos + 4:pos + 8]), plen))
                    pos += (rlen + 3) & ~3
            if done:
                return found
    finally:
        s.close()


def port_listening(port):
    """读 /proc/net/tcp{,6} 找 LISTEN(0A) 状态的本机端口, 不 fork 进程。"""
    want = "%04X" % port
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as fh:
                next(fh)
                for line in fh:
                    f = line.split()
                    if len(f) > 3 and f[3] == "0A" and f[1].endswith(":" + want):
                        return True
        except OSError:
            pass
    return False


def load_avg():
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


class DiskActivity:
    """对比 /sys/block/mmcblk0/stat 的写扇区计数, 判断最近是否在写卡, 以及写得多快。

    两个层次是**故意分开**的(板上实测: Jupyter/journal 每 2~4 秒就写几 KB~几百 KB):
    - `poll()` -> "最近有没有写" —— 给 LED 用。LED 小, 一闪一闪正好说明卡在工作。
    - `loud()` -> "是不是在大量写" —— 给屏幕上那块大红告警用。它必须**迟滞**, 否则零散的
      后台写会让整屏告警一开一关, 看着就是在闪, 而且"不要断电"这句话也就没人当真了。
    """

    def __init__(self, window=SLOW, clock=time.monotonic):
        self._window = window
        self._clock = clock
        self._last = self._read()
        self._wrote_at = None
        self._last_poll = clock()
        self._active = False
        self.rate = 0.0                 # 上一区间的写入速率(B/s)
        self._loud_until = None

    @staticmethod
    def _read():
        try:
            with open(SD_BLOCK) as fh:
                return int(fh.read().split()[6])     # 字段 7 = 已写扇区数
        except (OSError, IndexError, ValueError):
            return None

    def poll(self):
        # 熄灭要按"距上次写卡多久"判断, 不能按"距上次轮询多久" —— 轮询间隔恒为
        # window, 用后者永远够不到 2×window, 灯一旦亮起就再不熄灭(板上实测)。
        now = self._clock()
        cur = self._read()
        sectors = 0
        if cur is not None and self._last is not None and cur > self._last:
            sectors = cur - self._last
            self._wrote_at = now
        self._last = cur
        dt = max(now - self._last_poll, 1e-3)
        self._last_poll = now
        self.rate = sectors * 512.0 / dt
        # "大量写"的**判定只在这里做一次**: 放进去的话每次查询都会拿同一个速率把保持期往后
        # 推, 于是告警永远不灭(实测踩到)。
        if self.rate >= LOUD_BPS:
            self._loud_until = now + LOUD_HOLD
        self._active = (self._wrote_at is not None
                        and now - self._wrote_at < 2.0 * self._window)
        return self._active

    def loud(self):
        """当前是否算"正在大量写卡"。只是查询上次 `poll()` 的判决(带保持期), 不改状态。"""
        return self._loud_until is not None and self._clock() < self._loud_until


SLCR_OFFSET = 0xF8000000
SLCR_UNLOCK = 0x0000DF0D
SLCR_LOCK = 0x0000767B

# FPGAx_CLK_CTRL. Values carried over unchanged from the original helper:
# they were verified good from U-Boot (see tools/_axi_jtag_test.tcl, reads of
# 0x41200000 succeed against them) and are NOT what broke under Linux.
FPGA0_CLK_CTRL = 0xF8000170
FPGA1_CLK_CTRL = 0xF8000180
FPGA0_CLK_CTRL_VALUE = 0x00400500
FPGA1_CLK_CTRL_VALUE = 0x00100500

# FPGAx_THR_CNT: the clock-throttle counter. Non-zero gates the output, and
# per UG585 5.6.2 a GPV access while a PS-PL AXI interface clock is stopped
# can hang the entire PS. Linux leaves these at 1 for every FPGA clock no
# client has claimed, so they must be cleared before any MMIO.
FPGA_THR_CNT = (0xF8000178, 0xF8000188, 0xF8000198, 0xF80001A8)
# FPGAx_THR_STA bit16: "clock running".
FPGA_THR_STA = (0xF800017C, 0xF800018C, 0xF800019C, 0xF80001AC)
FPGA_THR_RUNNING = 0x00010000


def configure_ps_clocks():
    """Enable the PS clocks required by the merged AXI fabric before MMIO.

    Clearing FPGAx_THR_CNT is the load-bearing step. Linux gates every FPGA
    output clock that no kernel client claimed, and FPGA0_CLK_CTRL still
    reads back perfectly sane while that has happened -- so the gates can
    only be found by reading THR_CNT/THR_STA, or by an A/B boot.

    UG585 5.6.2: a GPV access with a PS-PL AXI interface clock stopped may
    hang the entire PS. This used to hang the board on the first GPIO read.
    """
    import mmap
    off = SLCR_OFFSET
    fd = os.open("/dev/mem", os.O_RDWR | getattr(os, "O_SYNC", 0))
    try:
        slcr = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                         mmap.PROT_READ | mmap.PROT_WRITE, offset=off)
        try:
            def rd(addr):
                return struct.unpack_from("<I", slcr, addr - off)[0]

            def wr(addr, val):
                struct.pack_into("<I", slcr, addr - off, val)

            wr(off | 0x0008, SLCR_UNLOCK)
            wr(FPGA0_CLK_CTRL, FPGA0_CLK_CTRL_VALUE)
            wr(FPGA1_CLK_CTRL, FPGA1_CLK_CTRL_VALUE)
            aper = rd(off | 0x012C)
            wr(off | 0x012C, aper | 0x01EC004D)
            wr(off | 0x0900, 0x0000000F)
            wr(off | 0x0240, 0x00000000)
            for thr_cnt in FPGA_THR_CNT:
                wr(thr_cnt, 0x00000000)
            wr(off | 0x0004, SLCR_LOCK)
            _verify_fclk_running(rd)
        finally:
            slcr.close()
    finally:
        os.close(fd)


def _verify_fclk_running(rd):
    """每一路 FPGA 时钟都必须真的在跑, 否则**抛异常**而不是接着去读 PL。

    静默返回正是线上症状: 调用方以为时钟好了, 第一次 GPV 访问就把整个 PS
    挂死(串口/网口全哑, 只能靠 JTAG 救), 那比在这里直接报错难查得多。
    """
    for addr in FPGA_THR_STA:
        value = rd(addr)
        if not value & FPGA_THR_RUNNING:
            raise RuntimeError("FCLK throttled: %08X=%08X" % (addr, value))


def resolve_verified_release():
    """Pin the immutable release `current` names, then verify it.

    Resolving before hashing is the whole point: install.sh re-points
    `current` atomically, so a directory resolved once cannot be swapped
    between the verification and the Overlay() load.  The resolved path --
    never the symlink -- is what the bit is loaded from and what the sidecar
    is told to read.
    """
    pinned = (RELEASE_ROOT / "current").resolve(strict=True)
    identity = merged_release.verify_release(pinned, require_software=True)
    if identity.get("release_id") != pinned.name:
        raise RuntimeError(
            "release %r does not match the directory it resolved from (%s)"
            % (identity.get("release_id"), pinned.name))
    return pinned, identity


def load_overlay():
    """Load the verified release's bit, returning (overlay, pinned release).

    The second configure_ps_clocks() is not redundant: loading the overlay
    re-gates the FPGA output clocks (the PL now drives them), and a first
    MMIO against a stopped PS-PL clock hangs the whole PS (UG585 5.6.2), so
    the clocks are re-checked after the load rather than only before.
    """
    from pynq import Overlay
    release, _identity = resolve_verified_release()
    configure_ps_clocks()
    overlay = Overlay(str(release / merged_release.BIT_NAME))
    configure_ps_clocks()
    return overlay, release


def sd_notify_ready():
    """Send READY=1 on systemd's notify socket; False when there is none.

    Under `Type=notify` this is what turns the unit "active" and releases the
    sidecar's dependency.  Run by hand (no NOTIFY_SOCKET) it is a no-op, so
    the daemon still starts offline.
    """
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        log("no NOTIFY_SOCKET: not reporting READY to systemd")
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        sock.sendall(b"READY=1")
    return True


def publish_ready(release):
    """Record the loaded release, then report READY.

    The marker is written (temp + rename) BEFORE the notification on purpose:
    the sidecar that ExecStartPost starts reads it, so it must already name
    the release by the time READY releases the dependency.  A failure to
    load never reaches here -- main() only calls this after load_overlay()
    returned -- so READY is never sent for a release that did not load.
    """
    marker = ACTIVE_RELEASE_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.parent / (".active-release.%d" % os.getpid())
    tmp.write_text(str(release) + "\n", encoding="utf-8")
    os.replace(tmp, marker)
    return sd_notify_ready()


# ---------------- 面板上的状态屏 ----------------
#
# 界面层是**可选依赖**: 它要 PIL/numpy 和一块能用的帧缓冲。缺任何一样, 守护进程都必须
# 照常提供"长按关机"这个安全功能 —— 所以导入放在 try 里, 失败就退化成无界面, 而不是
# 让整个服务起不来。
try:
    from pynq_lcd_ui import (DIM, GREEN, GREY, RED, WHITE, YELLOW,
                             Button, Crosshair, Label, ProgressBar, Ui, Widget)
    from pynq_touch import TouchCursor
except Exception as _UI_EXC:                     # noqa: BLE001
    _UI_IMPORT_ERROR = _UI_EXC
    WHITE = GREY = DIM = RED = GREEN = YELLOW = 0
    Button = Label = ProgressBar = Ui = Crosshair = None
    TouchCursor = None

    class Widget(object):                        # 让下面的控件类定义仍然成立
        def __init__(self, x, y, w, h):
            self.x, self.y, self.w, self.h = x, y, w, h
else:
    _UI_IMPORT_ERROR = None


class Status:
    """守护进程每帧填一次的只读状态快照, 屏幕照着它画。"""

    def __init__(self):
        self.ip = None              # eth0 的 IPv4(str) 或 None
        self.jupyter = False
        self.disk_active = False    # 最近写过卡(给 LED 那一路)
        self.disk_loud = False      # 正在大量写卡(屏幕上才弹大红告警)
        self.disk_rate = 0.0        # 写入速率(B/s), 显示用
        self.load = 0.0
        self.uptime = 0.0
        self.held = None            # 长按已持续秒数(None = 没按)
        self.leds = 0x00            # 8 位 LED 当前值, 屏幕上原样镜像
        self.shutting_down = False


class StatusBoard(Widget):
    """信息区: 每行"标签 + 值 + 右侧指示灯"。

    值每帧都可能变, 所以不留 `Label`(它在构造时按文字算尺寸, 文字一变长度就没用了),
    直接按行画。
    """

    ROW_H = 36

    def __init__(self, x, y, w, labels, row_h=None, value_dx=150):
        self.row_h = self.ROW_H if row_h is None else int(row_h)
        # 值那一列的缩进。整屏版式给 150(标签列宽绰), 嵌进面板时要收紧 —— 否则
        # "Jupyter :9090 监听中"(165px)会越过卡片右边、压到 LED 那一排上。
        self.value_dx = int(value_dx)
        super(StatusBoard, self).__init__(x, y, w, self.row_h * len(labels))
        self.labels = labels
        self.values = [""] * len(labels)
        self.on = [False] * len(labels)
        self.label_fg = GREY
        # 逐行的值颜色: 只有真出问题的那一行该变红(整列一起红反而看不出是哪一行)
        self.value_fg = [WHITE] * len(labels)
        self.tappable = False           # 纯显示, 别参与触摸命中(它压在屏幕左边一大片)

    def draw(self, canvas):
        for i, label in enumerate(self.labels):
            row = self.y + i * self.row_h
            canvas.text(self.x, row, label, self.label_fg, 18)
            canvas.text(self.x + self.value_dx, row, self.values[i],
                        self.value_fg[i], 18)
            color = GREEN if self.on[i] else DIM
            canvas.circle(self.x + self.w - 14, row + 11, 8, color)


class LedRow(Widget):
    """把 8 个 PL LED 的当前状态镜像到屏幕上(带编号, 编号 = LED 序号 + 1)。"""

    def __init__(self, x, y, pitch=26):
        super(LedRow, self).__init__(x, y, pitch * 8, 46)
        self.pitch = pitch
        self.value = 0
        self.tappable = False           # 纯显示, 别参与触摸命中

    def draw(self, canvas):
        for i in range(8):
            cx = self.x + i * self.pitch + self.pitch // 2
            on = bool(self.value & (1 << i))
            canvas.circle(cx, self.y + 12, 10, GREEN if on else DIM)
            canvas.text_center(cx - 13, self.y + 26, cx + 13, self.y + 44,
                               "%d" % (i + 1), GREY, 14)


class StatusScreen:
    """把守护进程的状态画到 TFT 上。

    这是"停机时面板上唯一能看的东西": 8 个 LED 的语义在屏上展开成文字(网络/服务/写卡/
    负载/运行时长 + 长按关机进度), 再镜像一组 LED 指示点。写卡那一行最要紧 —— 它会整块
    变红, 拔电源之前先看它。
    """

    LED_LEGEND = "1心跳  2网络  3服务  4写卡  5关机  6就绪  7长按  8负载"
    ROWS = ("网络", "服务", "写卡", "负载", "运行")

    def __init__(self, lcd, title="AC820 PYNQ 状态", touch=None, on_switch=None,
                 cursor=None, background=None, framebuffer=None, box=None):
        self.lcd = lcd
        self.title = title
        self.state = Status()
        self.on_switch = on_switch          # 点"控制页"时回调(切到 AWG 页)
        # 触摸是**可选**的: 读不到器件(或位流/接线有问题)时只少一个准星, 其余照常。
        # ⚠ `cursor` 优先于 `touch`: 与面板**共用同一个 cursor** 才不会互相读空。
        #   GT911 的状态寄存器是"读走才更新", 一帧读两次第二次就是空 —— 两个读主各持一个
        #   cursor 时, 先读的那个把数据读走, 后读的永远收不到触摸点, 表现就是"某一页的
        #   按钮点不动"(板上实测踩到)。
        if cursor is not None:
            self.cursor = cursor
        else:
            self.cursor = TouchCursor(touch) if touch is not None else None
        # shadow=True: 画在影子缓冲里再整块拷过去 —— 否则"清屏到画完"那几毫秒会被面板扫到
        self.ui = Ui(lcd, background=background if background is not None else 0x0861,
                     shadow=True, framebuffer=framebuffer,
                     source=self.cursor.source() if self.cursor else None)
        # `box`: 本屏可用的矩形 (x0, y0, x1, y1)。给 None = 整屏独立显示(旧路径原样)。
        # 面板把本屏嵌进状态页时用 `relayout` 传工作区 —— 面板的顶栏(y<48)/左导航(x<91)/
        # 底栏(y>416)都压在整屏坐标上, 不缩进这块区域就会"栏与内容重叠"(板上看到的状态页)。
        self._enabled = False
        self._last_sig = None
        self._build(box)

    def relayout(self, box):
        """把本屏重新摆进 `box`(面板工作区)。面板接管本屏时调一次。

        嵌进面板时**必须**调: 整屏版式要 394px 高, 而工作区只有 368px, 直接叠上去会被
        底栏压住一截(板上看到的状态页就是"栏与内容重叠")。
        """
        if box is None:
            return
        self._build(box)

    def _build(self, box):
        """按 `box`(或整屏)建/重建全部控件。"""
        self.box = box
        self.ui.widgets = []
        L = self._layout(self.title, box)
        self.ui.add(Label(*L["title"], self.title, size=L["title_size"], fg=WHITE))
        if self.on_switch is not None and box is None:
            # 有 AWG 控制页时才放这个按钮 —— 没建起来就别给一个点不动的入口。
            # 嵌进面板时导航栏已经能切页, 再放一个按钮只会跟顶栏抢位置。
            self.ui.add(Button(*L["switch"], "控制页", on_tap=self._to_awg, size=18))
        self.board = self.ui.add(StatusBoard(L["board"][0], L["board"][1],
                                             L["board"][2], list(self.ROWS),
                                             row_h=L["row_h"],
                                             value_dx=L["value_dx"]))
        self.leds = self.ui.add(LedRow(L["leds"][0], L["leds"][1], pitch=26))
        self.ui.add(Label(*L["legend"], self.LED_LEGEND, size=14, fg=GREY))
        self.banner = self.ui.add(Label(*L["banner"], "", size=22, fg=RED))
        self.hint = self.ui.add(Label(*L["hint"], "长按 S0 三秒优雅关机",
                                      size=18, fg=GREY))
        self.hold = self.ui.add(ProgressBar(L["hold"][0], L["hold"][1],
                                            L["hold"][2], L["hold"][3]))
        self.touch_text = self.ui.add(Label(*L["touch"], "", size=16, fg=GREY))
        self.cross = self.ui.add(Crosshair(self.ui.lcd.array.shape[1],
                                           self.ui.lcd.array.shape[0]))
        self._last_sig = None               # 版式变了, 下一帧必须重画

    @staticmethod
    def _layout(title, box):
        """把每个控件的位置算出来。`box=None` 是整屏独立显示(旧版式, 逐值不变)。

        `box=(x0, y0, x1, y1)` 是面板工作区 —— 版式整体缩进到这块矩形里, 字号与行高
        收紧一档以塞进 368px 的高度(整屏版式要 394px, 直接叠会被底栏压住)。
        """
        if box is None:
            return {
                "title": (24, 14), "title_size": 28,
                "switch": (600, 12, 180, 40),
                "board": (24, 66, 520), "row_h": StatusBoard.ROW_H,
                "value_dx": 150,
                "leds": (584, 70),
                "legend": (24, 268), "banner": (24, 300),
                "hint": (24, 344),
                "hold": (24, 382, 480, 26),
                "touch": (584, 376),
            }
        x0, y0, x1, y1 = box
        pad = 14
        lx = x0 + pad                   # 105(工作区 91..799)
        top = y0 + 48                   # 96 —— 与面板各页卡片顶(标题行下方)对齐
        return {
            "title": (lx, y0 + 12), "title_size": 18,
            "switch": (0, 0, 0, 0),
            # 板子窄一档(440 而非整屏的 520), 好给右边的 LED 排留位置。
            # `value_dx=150` 保持不动: "Jupyter :9090 监听中"(165px)要装得下,
            # 而这一行最长 —— 缩进太小它就越过卡片右边、压到 LED 上(板上看到过)。
            "board": (lx, top, 440), "row_h": 30, "value_dx": 150,
            "leds": (lx + 465, top + 4),
            # 图例/告警/提示/进度条都放在板子**下面**, 整行铺开(不再挤在右栏)。
            # 行距留够: 告警那行是 22px 的大字, 贴着下一行会显得粘在一起。
            "legend": (lx, top + 158), "banner": (lx, top + 184),
            "hint": (lx, top + 220),
            "hold": (lx, top + 248, 440, 24),
            "touch": (lx + 465, top + 250),
        }

    def _to_awg(self, _b):
        if self.on_switch is not None:
            self.on_switch()

    def invalidate(self):
        """切回本页时调用: 清掉"上一帧签名", 强制重画一次。

        不这么做的话, 切走再切回来时若内容恰好没变, `render` 会以为"已经画过"而
        直接返回 —— 面板上还留着另一页的画面。
        """
        self._last_sig = None

    def render(self, state, point=_UNSET, flush=True):
        """画一帧。内容一个字节都没变就直接返回 —— 不重画, 面板就纹丝不动。

        第一次画完才切到帧缓冲源, 免得先闪一下未初始化的内存。

        `point` 是**外部已经读好**的触摸点(见 `ui_view.UiView.render` 的状态页分支):
        面板把状态页嵌进六页框架时, 一帧只能读一次器件, 于是由面板读、把点传进来。
        不给(默认)就自己读 —— 单独用状态屏(旧路径、`preview_png`)时仍是原样。

        `flush=False` 只画进影子缓冲、不推帧缓冲 —— 面板的状态页要把本屏与顶栏/导航/
        底栏叠成**一张**图, 由面板统一推一次(见 `ui_view.UiView.render`)。
        """
        self.state = state
        b = self.board
        b.values[0] = state.ip or "无 IPv4 地址"
        b.values[1] = "Jupyter :9090 监听中" if state.jupyter else "Jupyter 未监听"
        b.values[2] = ("写卡中, 请勿断电" if state.disk_loud
                       else ("写卡中 %s/s" % _rate_text(state.disk_rate)
                             if state.disk_active else "空闲"))
        b.values[3] = "%.2f%s" % (state.load, "  偏高" if state.load > 1.0 else "")
        b.values[4] = _hms(state.uptime)
        b.on = [bool(state.ip), state.jupyter, state.disk_active,
                state.load > 1.0, True]
        b.value_fg = [WHITE, WHITE, RED if state.disk_active else WHITE,
                      YELLOW if state.load > 1.0 else WHITE, WHITE]

        self.leds.value = state.leds
        if state.shutting_down:
            self.banner.text = "正在关机, 请等待"
            self.banner.fg = YELLOW
        elif state.disk_loud:
            self.banner.text = "SD 卡正在大量写入, 不要断电!"
            self.banner.fg = RED
        else:
            self.banner.text = ""
        self.hint.text = ("松手取消" if state.held is not None
                          else "长按 S0 三秒优雅关机")
        self.hold.value = (0.0 if state.held is None
                           else min(state.held / HOLD_SECONDS, 1.0))
        if self.cursor is not None:
            # **本帧唯一一次**触摸读取; 准星与控件用同一个点(见 TouchCursor 的注释)
            if point is _UNSET:
                point = self.cursor.read()
            # 抬手那一帧 point 是 (x, y, False) —— 准星要收起来, 但**不能**丢掉这一帧:
            # UI 正是靠这个 down=False 合成抬起沿并触发 on_tap(丢了就永远点不成按钮)
            self.cross.point = point[:2] if point and point[2] else None
            if point and point[2]:
                self.touch_text.text = "触摸 %d, %d" % (point[0], point[1])
            elif self.cursor.errors:
                self.touch_text.text = "触摸读失败 %d" % self.cursor.errors
            else:
                self.touch_text.text = "触摸 未按"

        sig = (b.values[0], b.values[1], b.values[2], b.values[3], b.values[4],
               tuple(b.on), tuple(b.value_fg), self.leds.value, self.banner.text,
               self.banner.fg, self.hint.text, round(self.hold.value, 3),
               self.cross.point, self.touch_text.text)
        if sig == self._last_sig:
            return False
        self._last_sig = sig
        self.ui.step(flush=flush)
        if not self._enabled:
            self.ui.lcd.enable()    # 画好第一帧再切源(注意是影子那一层在代理)
            self._enabled = True
        return True


def _rate_text(bps):
    """写入速率写成人看的样子: 小的用 KB/s, 大的用 MB/s。"""
    if bps >= 1024 * 1024:
        return "%.1f MB" % (bps / (1024.0 * 1024))
    return "%.0f KB" % (bps / 1024.0)


def _hms(seconds):
    seconds = int(seconds)
    return "%02d:%02d:%02d" % (seconds // 3600, (seconds // 60) % 60, seconds % 60)


def _uptime():
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def release_touch_reset(overlay):
    """放开 GT911 的复位(`TP_RST` = PL 脚 R15, `axi_gpio_fb` 通道2 的 bit1)。

    位流里这个字的复位值是 0 —— 也就是**器件一直被按在复位里**, 谁也别想读到它。之前
    "总线上没人应答"就有一半是这个原因(另一半是地址编码写错, 见 `pynq_touch` 模块注释)。
    面板一起来就放开它, 触摸才能用; 放在这里是因为 overlay 只有这一处拿得到。
    """
    try:
        overlay.axi_gpio_fb.channel2.write(0b10, 0b10)
    except Exception as exc:                         # noqa: BLE001
        log("touch reset not released (%s: %s)" % (type(exc).__name__, exc))


class PanelSession(object):
    """面板的运行期: 事务线程 + 每帧一次的回读泵。

    **线程约定**(照参考设计 `AGENT_GUIDE.md`): `UiApp` 只由主循环线程碰。事务线程只负责
    跑命令、读回设备, 结果塞进队列; 主循环每帧 `pump()` 取一次交给 `UiApp.finish()`。
    反过来(后台线程直接写 `UiApp`/`draft`)的话, 用户手上没应用的改动会被遥测冲掉。

    串口桥线程与这里共用**同一个 `AwgCtl`**, 但桥只在收到 GET/SET 时才动, 不碰 `UiApp` ——
    所以不会与事务线程抢状态, 只会在总线上排队。
    """

    # 只回读、不下发的作业。用**对象**而不是字符串: 作业是按 `is` 判别的, 字符串靠驻留
    # 才能通过, 换个拼接方式就悄悄失效(空作业会被当成计划去取 `.commands`)。
    SYNC = object()
    LIST_FILES = object()   # 列数据页的波形目录(纯本地 IO, 与设备无关)
    TIMEOUT = 6.0           # 一条事务的超时(s)。板上一条命令几十 ms, 6s 已经很宽
    SYNC_PERIOD = 5.0       # 自动重读设备状态的周期(s) —— 串口侧改了参数面板要跟上

    def __init__(self, app, ctl, clock=time.monotonic, timeout=None,
                 sync_period=None, ring=None, ddr_dir=None):
        self.app = app
        self.ctl = ctl
        self.clock = clock
        self.timeout = self.TIMEOUT if timeout is None else timeout
        self.sync_period = self.SYNC_PERIOD if sync_period is None else sync_period
        # 环形喂数线程(可缺席 —— 位流里没有 DDR PP 或 `awg_ddr_pp` 装不上时降级)。
        # **只由本类读它的 snapshot**, 写全在它自己的线程里。
        self.ring = ring
        self.ddr_dir = ddr_dir
        self.errors = 0
        self._jobs = queue.Queue()
        self._done = queue.Queue()
        self._deadline = None
        # 开机由 `attach_panel` 显式排一次回读, 所以这里从"一个周期之后"起算 ——
        # 否则第一帧就会再排一次重复的。
        self._next_sync = clock() + self.sync_period
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # ---- 主循环线程 ----

    def submit(self, plan):
        self._deadline = self.clock() + self.timeout
        self._jobs.put(plan)

    def sync(self):
        self._jobs.put(self.SYNC)

    def list_files(self):
        """排一次目录列举(数据页的"刷新")。"""
        self._jobs.put(self.LIST_FILES)

    def pump(self):
        """取走已完成的事务结果, 更新 `UiApp`。返回 True 表示界面要重画。

        **绝不抛异常** —— 它跑在画帧那条路径上, 抛出会被 `render_status` 当成"屏幕坏了"
        而把整个屏幕丢掉, 连"长按关机"的显示都跟着没(见模块开头的不变量)。
        """
        changed = False
        while True:
            try:
                job, snap = self._done.get_nowait()
            except queue.Empty:
                break
            changed = True
            try:
                if job is self.SYNC:
                    self._apply_sync(snap)
                elif job is self.LIST_FILES:
                    self._apply_files(snap)
                elif snap is None:
                    self.app.fail()
                else:
                    self.app.finish(snap)
            except Exception as exc:                 # noqa: BLE001
                self.errors += 1
                log("panel result dropped (%s: %s)" % (type(exc).__name__, exc))
        # 环形线程的快照每帧拷一次(它自己按 200ms 节流发布, 所以这里通常是同一份内容,
        # 相等就不算"变了")。**不阻塞**: `snapshot()` 只取锁读一个 dict 的拷贝。
        if self.ring is not None:
            try:
                snap = self.ring.snapshot()
            except Exception as exc:                 # noqa: BLE001
                self.errors += 1
                log("ring snapshot dropped (%s: %s)" % (type(exc).__name__, exc))
            else:
                if snap != self.app.pp:
                    self.app.pp = snap
                    changed = True
        if self.app.pending and self._deadline is not None \
                and self.clock() >= self._deadline:
            # 超时: 失效并重查, 不重发(设备可能已经被改了一部分, 重发不幂等)
            self._deadline = None
            self.app.fail()
            log("panel transaction timed out after %.1fs" % self.timeout)
            changed = True
        now = self.clock()
        if now >= self._next_sync and not self.app.pending:
            self._next_sync = now + self.sync_period
            self.sync()
        return changed

    def _apply_files(self, snap):
        """目录列举的结果落地。`None` 表示目录不存在/读不了 —— 报空表 + 一句提示。"""
        if snap is None:
            self.app.ddr_files = []
            self.app.ddr_status = "目录不可读"
            return
        self.app.ddr_files = list(snap)
        # 删了文件之后页码可能越界, 夹回去(界面读的是 `ddr_page`)
        import ui_model
        self.app.ddr_page = max(0, min(self.app.ddr_page,
                                       ui_model.ddr_pages(len(snap)) - 1))
        if self.app.ddr_selected is not None and self.app.ddr_selected >= len(snap):
            self.app.ddr_selected = None
        self.app.ddr_status = "%d 个文件" % len(snap)

    def _apply_sync(self, snap):
        if snap is None:
            self.app.connected = False
            self.errors += 1
            log("panel readback failed %d time(s), showing disconnected" % self.errors)
            return
        # ⚠ 用户手上有没应用的改动时**不能**把回读同步进草稿(那等于把他的输入抹掉)。
        #   只更新 observed, 差异由底栏的"有待应用参数"显示。
        self.app.set_observed(snap["config"], snap["actual_mode"],
                              resync_draft=not self.app.dirty)

    # ---- 事务线程 ----

    def _run(self):
        while True:
            job = self._jobs.get()
            try:
                snap = self._execute(job)
            except Exception as exc:                 # noqa: BLE001
                self.errors += 1
                log("panel job failed (%s: %s)" % (type(exc).__name__, exc))
                snap = None
            if job is not self.SYNC:
                self._deadline = None
            self._done.put((job, snap))

    def _execute(self, job):
        """跑一个作业并回读设备。返回 `ui_model.finish` 要的那份快照。"""
        if job is self.LIST_FILES:
            return self._list_waveforms()
        if job is not self.SYNC:
            for cmd in job.commands:
                reply = self.ctl.handle(cmd)
                if reply is None or reply.startswith("ERR"):
                    # 固件拒绝了其中一条 —— 事务没做成。**照实报错, 不重试**: 重发在
                    # 部分失败之后不幂等(COMMIT 是拨动 epoch 位, 重发会多拨一次)。
                    log("panel command rejected: %s -> %s" % (cmd.strip(), reply))
                    return None
        return self._snapshot()

    def _list_waveforms(self):
        """列数据页的波形目录: `[(name, size), ...]`, 按名字排序。

        **只在后台线程跑** —— 一次 `listdir` + 每项一次 `stat` 在 SD 卡上可能上百毫秒,
        放主循环里会卡住触摸。目录不存在返回 None(界面显示"目录不可读"), 不抛 ——
        抛了会被 `_run` 记成"作业失败", 界面同样拿不到区分度。
        """
        import ui_model
        d = self.ddr_dir if self.ddr_dir is not None else ui_model.DDR_DIR
        try:
            names = [n for n in os.listdir(d) if n.lower().endswith(ui_model.DDR_SUFFIXES)]
        except OSError:
            return None
        out = []
        for n in sorted(names):
            try:
                out.append((n, os.stat(os.path.join(d, n)).st_size))
            except OSError:
                continue
        return out

    def _snapshot(self):
        """从**真寄存器**解出配置, 外加 `GET DIGMOD` 的实际模式。

        不用 `handle("GET CHx")` 那套影子状态来核对: 它是"写进去再读出来", 必然一致,
        抓不到"写没生效"(本工程有两次真实前科)。见 `awg_ctl.read_registers`。
        """
        import awg_ctl
        cfg = self.ctl.read_registers()
        line = self.ctl.handle("GET DIGMOD")
        parsed = awg_ctl.parse_digmod_line(line) if line else None
        return {"config": cfg,
                "actual_mode": parsed["mode"] if parsed else "OFF",
                "connected": True, "fresh": True}


def attach_screen(overlay):
    """尽力把面板建起来; 建不起来就返回 None, 让守护进程照常跑。

    真实会遇到的失败有好几种: 位流是旧的(没有 axi_gpio_fb)、PIL/numpy 缺失、帧缓冲分配不到。
    哪一种都不该让"长按关机"跟着失效, 所以这里把异常全吞掉并记一行日志。
    """
    if _UI_IMPORT_ERROR is not None:
        log("status screen unavailable (%s: %s), running headless"
            % (type(_UI_IMPORT_ERROR).__name__, _UI_IMPORT_ERROR))
        return None
    try:
        from pynq_lcd import Lcd
        from pynq_lcd_ui import BG
        lcd = Lcd.from_overlay(overlay)
        release_touch_reset(overlay)
        touch = open_touch(overlay)
        # 整块面板共用**同一个 lcd 与同一个 TouchCursor**: 触摸一帧只能读一次(GT911 的
        # 状态是"读走才更新"), 所以不能每个页面各读一次。
        cursor = TouchCursor(touch) if touch is not None else None
        # 先按**整屏**建。面板接管时 `UiView` 会调 `relayout(WORK_BOX)` 把它摆进工作区;
        # 面板起不来的退路(`_StatusOnlyView`)没有导航栏, 就保持整屏版式。
        screen = StatusScreen(lcd, cursor=cursor, background=BG)
        log("status screen up, framebuffer at 0x%08X" % lcd.address)
        return attach_panel(overlay, lcd, cursor, screen)
    except Exception as exc:                         # noqa: BLE001
        log("status screen unavailable (%s: %s), running headless"
            % (type(exc).__name__, exc))
        return None


def open_touch(overlay):
    """尽力把 GT911 打开; 打不开就返回 None, 屏幕照常工作。

    触摸是"锦上添花": 位流、接线、面板任一处不对, 都不该让状态屏和长按关机跟着废掉。
    `Touch.open` 自己会走完整复位序列(先把 TP_INT 拉低再放复位, 于是地址确定为 0x5D),
    并在复位放开后**轮询**等器件自检完成 —— 这段窗口不轮询就会连续 NACK。
    """
    try:
        from pynq_touch import Touch
        touch = Touch.open(rst=lambda up: overlay.axi_gpio_fb.channel2.write(
            0b10 if up else 0x00, 0b10))
        log("touch up: GT911 @ 0x%02X" % touch.addr)
        return touch
    except Exception as exc:                         # noqa: BLE001
        log("touch unavailable (%s: %s), panel only" % (type(exc).__name__, exc))
        return None


class _PanelScreen:
    """把六页面板包装成主循环要的 `render(state)` 接口。

    **守护进程必须是 overlay 的唯一持有者**: 早先 `awg_bridge.py` 是独立进程、自己调
    `Overlay()`, 而守护进程也持有 PL —— 两个进程同时装 overlay 会把板子卡死(见技能
    `ac820-pynq` 的"Never call Overlay() while a daemon holds the PL")。所以桥搬进守护
    进程, 与面板共用**同一个** `AwgCtl` 实例: 面板改的参数, 串口 `GET` 立刻读得到,
    反之亦然 —— 这是验收判据。
    """

    def __init__(self, view, session, bridge, status_screen, rf_worker=None):
        self.view = view
        self.session = session
        self.bridge = bridge
        self.status = status_screen
        self.rf_worker = rf_worker
        self.lcd = view.lcd
        self.ui = view.ui
        self.cursor = view.cursor

    def invalidate(self):
        self.view.invalidate()
        self.status.invalidate()

    @property
    def page(self):
        return self.view.app.page

    def render(self, state):
        if self.rf_worker is not None:
            # **只取最新那份不可变快照** —— socket 往返全在 worker 线程里, 这里(显示线程)
            # 一次 IO 都不做, 所以 daemon 掉线也不会卡住面板(设计 §4)。
            app = self.view.app
            if hasattr(self.rf_worker, "take_frame"):
                snapshot, results, running = self.rf_worker.take_frame()
                app.set_rf_status(snapshot.to_dict())
                for action, result in results:
                    app.set_rf_control_result(action, result)
                if running is not None:
                    app.set_rf_control_pending(running)
            else:
                app.set_rf_status(self.rf_worker.latest().to_dict())
            if hasattr(self.rf_worker, "model_status"):
                app.set_rx_model_status(self.rf_worker.model_status())
            if hasattr(self.rf_worker, "demod_status"):
                app.set_rx_demod_status(self.rf_worker.demod_status())
            if hasattr(self.rf_worker,'teaching_status'):
                app.set_teaching_status(self.rf_worker.teaching_status())
            if hasattr(self.rf_worker,'environment_status'):
                app.set_environment_status(self.rf_worker.environment_status())
            if hasattr(self.rf_worker,'headroom_status'):
                # 余量研究的状态同样只从 worker 的不可变快照落地; 面板把"本进程是否
                # 已跑过一次"画出来, 研究本身由 headroom_run_block 的门禁把守。
                app.set_headroom_status(self.rf_worker.headroom_status())
                if hasattr(self.rf_worker,'headroom_study_status'):
                    app.set_headroom_running(
                        self.rf_worker.headroom_study_status().get('running') is True)
                if hasattr(self.rf_worker,'take_headroom_result'):
                    # 研究是**长事务**, 在 worker 自己的线程上跑; 这里只落地它已经
                    # 冻结的收据, 一次一份, 落地后就是"不能重跑"的依据。
                    finished = self.rf_worker.take_headroom_result()
                    if finished is not None:
                        app.set_headroom_receipt(finished)
            if hasattr(self.rf_worker,'take_experiment_save_result'):
                saved=self.rf_worker.take_experiment_save_result()
                if saved is not None:
                    app.set_rx_save_status(saved)
            if hasattr(self.rf_worker, "take_rx_result"):
                # 采集的 socket 往返与 FFT 都在 worker 的采集线程里做过; 主循环这里**只
                # 落地一份已经冻结的结果**并把状态照实画出来, 不做 IO、也不算 FFT。
                payload, capture_status = self.rf_worker.take_rx_result()
                app.set_rx_capture_status(capture_status)
                if payload is not None:
                    app.set_rx_capture_result(payload)
        if self.session is not None:
            self.session.pump()      # 取事务结果 + 到期自动重读设备状态
            if getattr(state, "shutting_down", False):
                # 关机流程必须**看得见**: 用户可能正停在别的页上, 而那几秒是"别拔电"的提示。
                import ui_model
                self.view.app.page = ui_model.PAGE_STATUS
        self.view.render(state)

    def input_tick(self, state):
        """Only local input and signature-based drawing; no status/network collection."""
        self.view.render(state)

    def stop(self):
        if self.bridge is not None:
            self.bridge.stop()
        if self.rf_worker is not None:
            # 关机路径上**绝不因为 worker 停不下来而失败** —— 它只是个显示线程。
            try:
                self.rf_worker.stop()
            except Exception as exc:                 # noqa: BLE001
                log("RF status worker did not stop cleanly (%s: %s)"
                    % (type(exc).__name__, exc))


def _start_ring_thread(overlay, ctl, on_event=None):
    """尽力把环形喂数线程建起来; 建不起来返回 None(面板的数据页降级成"DDR 不可用")。

    会失败的地方不少: 位流里没有 DDR PP 那套 AXI DMA/GPIO、CMA 分配不到 2 MiB、
    `awg_ddr_pp` 缺依赖。哪一种都不该拖垮面板 —— 数据页只是**多出来的**一页。

    ⚠ **绝不调第二次 `Overlay()`**: `build_board_server` 有 `overlay=`/`ctl=` 注入路径
      正是为此(守护进程从开机起就持着 PL, 第二次 `Overlay()` 会把板子卡死, 本工程
      2026-09-12 与 09-14 各一次)。
    """
    try:
        import awg_ddr_ring
        import ring_thread
    except Exception as exc:                         # noqa: BLE001
        log("ring thread unavailable (%s: %s)" % (type(exc).__name__, exc))
        return None
    try:
        _ov, pp, _banks, server, _ctl = awg_ddr_ring.build_board_server(
            overlay=overlay, ctl=ctl, on_event=on_event)
        th = ring_thread.RingThread(server, pp, ctl=ctl)
        th.start()
        log("ring thread up, UDP %s:%d" % (server.host, server.port))
        return th
    except Exception as exc:                         # noqa: BLE001
        log("ring thread unavailable (%s: %s)" % (type(exc).__name__, exc))
        return None


def _start_rf_status_worker(port=RF_STATUS_PORT, poll_interval=RF_POLL,
                            stale_after=RF_STALE_AFTER):
    """尽力把 RF 只读状态轮询线程建起来; 建不起来返回 None(界面显示"离线")。

    线程里只做 127.0.0.1 上的 NDJSON 往返 —— 显示线程**绝不碰 socket**(设计 §4)。
    建不起来(缺模块/端口非法/线程起不来)只是面板上那几行显示"离线", 不该拖垮守护进程:
    面板与长按关机都不依赖它。
    """
    try:
        import rf_status_worker
    except Exception as exc:                         # noqa: BLE001
        log("RF status worker unavailable (%s: %s)" % (type(exc).__name__, exc))
        return None
    try:
        worker = rf_status_worker.RfStatusWorker(
            client=rf_status_worker.RfStatusClient(port=port),
            poll_interval=poll_interval, stale_after=stale_after,
            release_directory=RELEASE_ROOT / "current")
        worker.start()
        log("RF status worker up (127.0.0.1:%d, read-only)" % port)
        return worker
    except Exception as exc:                         # noqa: BLE001
        log("RF status worker unavailable (%s: %s)" % (type(exc).__name__, exc))
        return None


def attach_panel(overlay, lcd, cursor, status_screen):
    """把控制面(状态真源 + 协议桥 + 七页面板)建起来; 任一步失败就返回 None。

    返回的对象就是主循环认的"屏幕": `render(state)` / `stop()`。控制面建不起来时退化成
    只有状态屏的那套(与 P6 之前一致), 长按关机与 LED 不受影响。
    """
    try:
        import awg_bridge
        import awg_ctl
        import ui_model
        import ui_view
    except Exception as exc:                         # noqa: BLE001
        log("panel unavailable (%s: %s)" % (type(exc).__name__, exc))
        return _PanelScreen(_StatusOnlyView(status_screen, cursor), None, None,
                            status_screen)
    try:
        ctl = awg_ctl.AwgCtl(awg_ctl.PynqBackend(overlay))
    except Exception as exc:                         # noqa: BLE001
        log("AWG backend unavailable (%s: %s)" % (type(exc).__name__, exc))
        return _PanelScreen(_StatusOnlyView(status_screen, cursor), None, None,
                            status_screen)

    bridge = BridgeThread(ctl, awg_bridge)
    bridge.start()
    app = ui_model.UiApp()
    app.page = ui_model.PAGE_STATUS
    ring = _start_ring_thread(overlay, ctl)
    session = PanelSession(app, ctl, ring=ring)
    # RF sidecar 的只读状态 + 控制面: overlay 已经装好(本函数只会在
    # `load_overlay()` 之后被调到), 所以 sidecar 的 `BindsTo/After=pynq-status.service`
    # 依赖此时已满足, 可以开始轮询。先起来, 好让 UiView 的控制出口拿到同一个 worker。
    rf_worker = _start_rf_status_worker()
    view = ui_view.UiView(
        lcd, app, ctl=ctl, status_screen=status_screen, touch_cursor=cursor,
        log=log,
        on_apply=lambda action: _submit(session, app, view, action),
        on_toggle=lambda action: _submit(session, app, view, action),
        on_ddr=lambda cmd, arg=None: _ddr_command(session, app, view, cmd, arg),
        on_rx=lambda event: _rx_action(app, rf_worker, event),
        on_rf_control=lambda action: _rf_control_action(app, rf_worker, action))
    session.sync()                       # 开机先读一次设备状态, 否则底栏一直显示"未连接"
    session.list_files()                 # 数据页的目录也先列一次
    log("panel up, UART bridge on %s" % bridge.path)
    return _PanelScreen(view, session, bridge, status_screen, rf_worker)


def _rf_control_action(app, rf_worker, action):
    """面板控制动作的唯一出口。

    UI 已在 `ui_model` 里过了门禁(可用/确认); 这里只把动作转发给 worker —— 本层不做
    socket IO, 与 `on_rx` 同一条边界。worker 的 `{ok, code, message}` 原样落进
    `app.rf_control`, 失败时画的是 daemon 的码(判据 6)。token 路径用 operator
    provision 的提示路径, 面板本身不携带任何令牌。
    """
    if action in ("load", "start") and getattr(app, "selected_device", None) != "AD9361":
        app.set_rf_control_result(
            action, {"ok": False, "code": "TARGET_MISMATCH",
                     "message": "RF control actions are for AD9361 only"})
        return
    if rf_worker is None:
        app.set_rf_control_result(
            action, {"ok": False, "code": "NO_WORKER",
                     "message": "RF status worker is not running"})
        return
    token_path = app.rf_control.get("token_path_hint")
    args, why = app.rf_parameter_plan(action)
    if why is not None:
        app.set_rf_control_result(action, {"ok": False, "code": "INVALID_ARGUMENT", "message": why})
        return
    if hasattr(rf_worker, "submit_control"):
        result = rf_worker.submit_control(action, token_path, args)
        if result.get("queued") is True:
            app.set_rf_control_pending(action)
        else:
            app.set_rf_control_result(action, result)
    else:
        # Compatibility with synchronous test adapters; production uses submission.
        result = (rf_worker.control(action, token_path, args) if args else
                  rf_worker.control(action, token_path))
        app.set_rf_control_result(action, result)


def _rx_action(app, rf_worker, event):
    """面板 RX 动作的**唯一**出口(与 _rf_control_action 同一条边界)。

    这一层不做 socket IO、不做 FFT、也不重复门禁 —— 门禁在 ui_model 里(可用/在途/
    过期/改配置), 采集执行在 worker 的采集线程里。这里只做三件小事: 过一遍模型门禁、
    把动作转发一次、把失败原因照实写回模型。线缆确认由操作员的显式动作给出,
    面板不携带、也不假装。
    """
    import ui_model

    def refuse(code):
        text = str(code)
        app.set_rx_capture_status({
            "state": ui_model.RX_CAPTURE_REFUSED, "code": text,
            "reason": "%s (%s)" % (text, ui_model.RESULT_TEXT.get(text, text))})

    if event == "clear":
        # 只清本地快照: 不碰 daemon、不碰寄存器、不碰 RF 会话。
        app.clear_rx_snapshot()
        return
    if event in ('save_experiment','save_history'):
        request=app.rx_experiment_request(historical=event=='save_history')
        if request is None or rf_worker is None or not hasattr(rf_worker,'submit_experiment_save'):
            app.set_rx_save_status(dict(state='error',reason='没有可保存的冻结快照'))
            return
        result=rf_worker.submit_experiment_save(**request)
        app.set_rx_save_status(result if result.get('queued') else
            dict(state='error',reason=result.get('message') or result.get('code')))
        return
    if event == "fill_recommendation":
        # 模型"填草稿"只编辑草稿, 绝不写硬件。
        app.rx_fill_draft()
        return
    if event == "cancel_capture":
        if rf_worker is None or not hasattr(rf_worker, "cancel_capture"):
            refuse("NO_WORKER")
            return
        app.set_rx_capture_status(rf_worker.cancel_capture())
        return
    if event == "headroom_study":
        # The FIRST tap only registers the intent.  A study consumes the daemon's
        # single initialization and cannot be re-run in this process, so one tap
        # must never be enough to start it.
        _ok, message = app.headroom_prompt()
        app.teaching_message = message
        return
    if event == "headroom_cancel":
        if app.headroom_running:
            if rf_worker is not None and hasattr(rf_worker,'cancel_headroom_study'):
                rf_worker.cancel_headroom_study()
                app.teaching_message='已请求取消余量研究，等待关闭确认'
            else:
                app.teaching_message='取消请求未送达 RF worker'
            return
        _ok, message = app.headroom_cancel()
        app.teaching_message = message
        return
    if event == "headroom_confirm":
        args, why = app.headroom_study_args()
        if why is not None:
            # 余量研究的拒绝只写它自己的消息, 不去覆盖无关的采集状态。
            app.teaching_message = str(why)
            return
        if rf_worker is None or not hasattr(rf_worker, "submit_headroom_study"):
            app.teaching_message = "NO_WORKER"
            return
        reply = rf_worker.submit_headroom_study(app.new_headroom_session_id(),
                                               token_path=app.rf_control.get('token_path_hint'),
                                               **args)
        if reply.get("queued") is not True:
            app.teaching_message = reply.get("message") or reply.get("code") or "拒绝"
        return
    if event == "loopback":
        why = app.rx_loopback_block()
        if why is not None:
            refuse(why)
            return
        # 显式确认的第一下: 只登记待确认动作, 一个请求都不发。
        app.rx_capture_prompt("loopback")
        return
    if event == "loopback_cancel":
        app.rx_capture_cancel()
        return
    if event == "loopback_confirm" and \
            app.rx_capture.get("pending_action") != "loopback":
        # 第二下只有在第一下真的登记过时才有效: 回环是显式确认的独立动作, 不是"再点一次".
        refuse("NOT_WIRED")
        return
    if event in ("capture", "loopback_confirm"):
        physical = event == "loopback_confirm"
        why = app.rx_loopback_block() if physical else app.rx_capture_block_reason()
        if why is not None:
            refuse(why)
            return
        args, why = app.rx_capture_args(physical=physical)
        if why is not None:
            refuse(why)
            return
        if rf_worker is None or not hasattr(rf_worker, "submit_capture"):
            refuse("NO_WORKER")
            return
        app.rx_capture_submitted(physical=physical)
        try:
            submitted = app.rx_demod_submission()
        except ValueError as exc:
            refuse(str(exc))
            return
        if submitted is None:
            reply = rf_worker.submit_capture(args, physical=physical)
        else:
            environment_args = ({'rf_config':dict(app._rx_submitted_rf_config)}
                                if app.environment_status['available'] else {})
            reply = rf_worker.submit_capture(args, physical=physical,
                demod_config=submitted, demod_revision=submitted.revision, **environment_args)
        if reply.get("queued") is not True:
            app.set_rx_capture_status({
                "state": ui_model.RX_CAPTURE_REFUSED, "code": reply.get("code"),
                "reason": reply.get("message") or "refused"})
        return
    refuse("NOT_WIRED")


def _ddr_command(session, app, view, cmd, arg=None):
    """数据页的一个动作。**全部非阻塞**: 交给环形线程的命令队列, 界面只读快照。

    面板**不直接调 `DdrPp`** —— 那些写(DMA 寄存器 + gpio7)必须只有 RingThread 一个
    写者, 否则两处并发会落到"半条命令"的硬件状态(与 `AwgCtl` 需要一把锁同理)。
    """
    import ui_model
    ring = session.ring
    # 刷新只列本地目录(纯 IO), 与 DDR 无关 —— 环形线程缺席时它**照样该能用**, 否则界面上
    # 那行"本地文件仍可浏览"是句空话(用户连目录都刷不出来)。
    if cmd == "refresh":
        session.list_files()
        view.invalidate()
        return
    if ring is None:
        app.ddr_status = "DDR 不可用"
        view.invalidate()
        return
    if cmd == "enqueue":
        # 行号: 优先用调用方给的, 没有就用面板选中的那行。**两条都要有兜底** —— 早先
        # 入队 chip 的回调是 `self._ddr("enqueue")`(不带 arg), 于是这里 `arg is None`
        # 直接静默跳过, 板上表现就是"点了入队, 队列还是空的"(验收脚本抓到)。
        row = arg if arg is not None else app.ddr_selected
        start, win = ui_model.ddr_window(app.ddr_files, app.ddr_page)
        if row is None or not (0 <= row < len(win)):
            app.ddr_status = "请先在文件表里点一行"
        else:
            name = win[row][0]
            if name not in app.ddr_queue:
                app.ddr_queue.append(name)
                app.ddr_status = "已入队 %s" % name
            else:
                app.ddr_status = "%s 已在队列里" % name
            ring.set_queue([os.path.join(_ddr_dir(session), n) for n in app.ddr_queue])
    elif cmd == "remove":
        if app.ddr_selected is not None:
            start, win = ui_model.ddr_window(app.ddr_files, app.ddr_page)
            if 0 <= app.ddr_selected < len(win):
                name = win[app.ddr_selected][0]
                if name in app.ddr_queue:
                    app.ddr_queue.remove(name)
                    app.ddr_status = "已移除 %s" % name
                    ring.set_queue([os.path.join(_ddr_dir(session), n)
                                    for n in app.ddr_queue])
    elif cmd == "clear":
        app.ddr_queue = []
        app.ddr_status = "队列已清空"
        ring.set_queue([])
    elif cmd == "ring":
        app.ddr_ring = not app.ddr_ring
        ring.set_ring(app.ddr_ring)
        app.ddr_status = "环形已开" if app.ddr_ring else "环形已关"
    elif cmd == "page":
        app.ddr_page = max(0, min(app.ddr_page + (arg or 0),
                                  ui_model.ddr_pages(len(app.ddr_files)) - 1))
    elif cmd == "load":
        # 装填 = 把一个文件写进 bank(**要求 MODE OFF** —— 见 `ui_model.ddr_load_block`)。
        # 优先装选中的那一行, 没选中就用队首。面板不判断可行性, 门禁在 ui_model 里。
        why = ui_model.ddr_load_block(app)
        if why is not None:
            app.ddr_status = why
        else:
            name = None
            start, win = ui_model.ddr_window(app.ddr_files, app.ddr_page)
            if app.ddr_selected is not None and 0 <= app.ddr_selected < len(win):
                name = win[app.ddr_selected][0]
            elif app.ddr_queue:
                name = app.ddr_queue[0]
            if name is None:
                app.ddr_status = "请先入队一个文件"
            else:
                import ring_thread as RT
                ring.submit(RT.CMD_LOAD, (os.path.join(_ddr_dir(session), name), None))
                app.ddr_status = "装填中 %s" % name
    elif cmd == "start":
        why = ui_model.ddr_start_block(app)
        if why is not None:
            app.ddr_status = why
        else:
            # 命令名从 `ring_thread` 取 —— 别在面板里再写一遍字面量(改一处漏一处)。
            import ring_thread as RT
            ring.submit(RT.CMD_START, 2)
            app.ddr_status = "启动中"
    elif cmd == "stop":
        import ring_thread as RT
        ring.submit(RT.CMD_STOP)
        app.ddr_ring = False
        ring.set_ring(False)
        app.ddr_status = "已停止"
    view.invalidate()


def _ddr_dir(session):
    import ui_model
    return session.ddr_dir if session.ddr_dir is not None else ui_model.DDR_DIR


def _submit(session, app, view, action):
    """底栏的一个动作: 校验 + 冻结成计划(`begin`), 通过就交给事务线程。

    `begin` 不做任何 I/O, 失败时返回原因 —— 结果码翻成中文, 而"参数超出范围"这类**带着
    具体字段**的串要原样显示(只报"参数超出范围"等于让用户自己去猜哪一项)。
    """
    import ui_model
    res, plan = app.begin(action)
    if plan is None:
        if res in ui_model.RESULT_TEXT:
            app.last_result, app.last_message = res, ui_model.RESULT_TEXT[res]
        else:
            app.last_result, app.last_message = ui_model.INVALID, res
        log("panel %s refused: %s" % (action, res))
    else:
        session.submit(plan)
    view.invalidate()


class _StatusOnlyView:
    """控制面起不来时的退路: 只有状态页, 没有导航栏。

    存在的意义是**保住"长按关机"那个安全功能** —— 位流旧了、PIL 缺了、帧缓冲分配不到,
    都不该让面板连状态都看不见。
    """

    def __init__(self, status_screen, cursor):
        self.status = status_screen
        self.cursor = cursor
        self.lcd = status_screen.lcd
        self.ui = status_screen.ui

    def invalidate(self):
        self.status.invalidate()

    def render(self, state):
        self.status.render(state)


class BridgeThread(threading.Thread):
    """在守护进程里跑 UART 协议桥 —— 它用的是守护进程已经持有的那个 `AwgCtl`。

    ttyPS0 是 AWG 用的 UART1, 由 `pynq-status.service` 的 systemd
    `Conflicts/Before` 约束独占。这里不再从工作线程里调用 systemctl: 动态停
    getty 会与 systemd 并发竞争同一设备, 造成 bridge 看似启动但客户端收不到 ACK。
    """

    def __init__(self, ctl, bridge_mod, path=None, clock=time.monotonic):
        super(BridgeThread, self).__init__(daemon=True)
        self.ctl = ctl
        self.mod = bridge_mod
        self.path = path or bridge_mod.DEFAULT_PORT
        self.proto = bridge_mod.Protocol(ctl)
        self._stop = threading.Event()
        self.error = None

    def run(self):
        try:
            self._serve()
        except Exception as exc:                     # noqa: BLE001
            self.error = exc
            log("UART bridge stopped (%s: %s)" % (type(exc).__name__, exc))

    def _serve(self):
        import os
        import select
        fd = self.mod.open_serial(self.path)
        self._fd = fd
        os.write(fd, self.proto.banner())
        log("UART bridge ready on %s (115200 8N1)" % self.path)
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue
                chunk = os.read(fd, 256)
                if chunk:
                    os.write(fd, self.proto.feed(chunk))
        finally:
            os.close(fd)

    def stop(self):
        self._stop.set()


def run_self_test(leds):
    """S1: 依次点亮每个灯, 确认 8 路都通。"""
    for i in range(8):
        leds.all(1 << i)
        time.sleep(0.12)
    leds.all(0)
    time.sleep(0.2)


def shutdown(leds, screen=None, status=None):
    """优雅关机。顺序是有意的: 先 sync, 再走一段可见的"正在关机"跑马灯,
    最后全亮 —— CPU halt 之后 PL 保持最后一次写入, 所以"全亮"会一直挂着,
    那就是"可以拔电"的信号。屏幕上会同步显示"正在关机"。

    屏幕在这条路径上只是"锦上添花": 任何异常都立刻放弃屏幕, 绝不拖慢或挡住关机。
    """
    log("shutdown requested by S0 long press")
    subprocess.run(["/bin/sync"], check=False)
    if screen is not None and status is not None:
        status.shutting_down = True
        status.held = HOLD_SECONDS
        try:
            screen.render(status)
        except Exception as exc:                     # noqa: BLE001
            log("status screen failed during shutdown (%s), continuing"
                % type(exc).__name__)
            screen = None
    end = time.monotonic() + 3.0
    spin = 0x01
    step = 0
    while time.monotonic() < end:
        leds.all(spin)
        spin = ((spin << 1) | (spin >> 7)) & 0xFF
        step += 1
        if screen is not None and step % 2 == 0:
            status.leds = leds.value
            try:
                screen.render(status)
            except Exception:                        # noqa: BLE001
                screen = None
        time.sleep(0.12)
    leds.all(0xFF)
    subprocess.run(["/bin/systemctl", "poweroff"], check=False)
    time.sleep(30)      # poweroff 正常会终止本进程; 兜底避免 systemd 立刻重启


def render_status(screen, status):
    """画一帧状态屏; 屏幕出任何问题就返回 None, 让调用方降级成无界面。

    单独拧成一个函数, 是因为"屏幕坏了不影响灯和关机键"这条保证必须能被离线测到 ——
    它是本模块最重要的不变量(见模块开头)。
    """
    try:
        screen.render(status)
        return screen
    except Exception as exc:                         # noqa: BLE001
        log("status screen failed (%s: %s), continuing headless"
            % (type(exc).__name__, exc))
        return None


class _SlowStatusCollector(threading.Thread):
    """Publish system probe snapshots without making touch wait for subprocesses."""

    def __init__(self, sample=None):
        super().__init__(name="panel-system-status", daemon=True)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest = {}
        self._disk = DiskActivity() if sample is None else None
        self._sample = sample or self._sample_system

    def _sample_system(self):
        ip = eth0_ipv4()
        jup = port_listening(9090)
        active = self._disk.poll()
        return {"ip": ip, "jupyter": jup, "disk_active": active,
                "disk_loud": self._disk.loud(), "disk_rate": self._disk.rate,
                "load": load_avg(), "uptime": _uptime()}

    def run(self):
        while not self._stop_event.is_set():
            try:
                snapshot = dict(self._sample())
                with self._lock:
                    self._latest = snapshot
            except Exception as exc:
                log("system status probe failed (%s)" % exc)
            self._stop_event.wait(SLOW)

    def latest(self):
        with self._lock:
            return dict(self._latest)

    def stop(self):
        self._stop_event.set()


def main():
    while True:
        try:
            overlay, release = load_overlay()
            break
        except Exception as exc:                     # noqa: BLE001
            log("overlay load failed (%s), retrying in 3s" % exc)
            time.sleep(3)

    # Only reached once the bit actually loaded: the marker names the release
    # the PL is running and READY releases rf-daemon's dependency on us.
    publish_ready(release)

    leds = Leds(overlay.axi_gpio_led)
    btns = Buttons(overlay.axi_gpio_btn)
    slow_status = _SlowStatusCollector()
    slow_status.start()
    screen = attach_screen(overlay)
    status = Status()
    leds.all(0xFF)
    time.sleep(0.4)
    leds.all(0)
    log("pynq-status daemon up, release=%s" % release)

    held_since = None
    heartbeat_on = False
    ip = None
    jup = disk_active = disk_loud = load_high = False
    disk_rate = 0.0
    load = 0.0
    uptime = 0.0
    next_beat = next_slow = next_draw = next_ui = next_input = 0.0
    next_selftest = 0.0

    while True:
        now = time.monotonic()
        b1, b2 = btns.pressed()

        if b1:
            if held_since is None:
                held_since = now
                log("S0 down, hold %.0fs to power off" % HOLD_SECONDS)
        else:
            if held_since is not None and now - held_since < HOLD_SECONDS:
                log("S0 released early, cancelled")
            held_since = None

        if b2 and now >= next_selftest:
            next_selftest = now + 1.0
            run_self_test(leds)
            next_draw = 0.0
            continue

        if held_since is not None and now - held_since >= HOLD_SECONDS:
            status.leds = leds.value
            # 先放掉串口与 RF 轮询线程(两者都占着资源), 再走可见的关机流程。
            # 退路的 `_StatusOnlyView` 没有 `stop()`, 所以按存在性调用。
            stopper = getattr(screen, "stop", None)
            if stopper is not None:
                stopper()
            slow_status.stop()
            shutdown(leds, screen, status)
            return

        if now >= next_slow:
            next_slow = now + SLOW
            snapshot = slow_status.latest()
            ip = snapshot.get("ip")
            jup = snapshot.get("jupyter", False)
            disk_active = snapshot.get("disk_active", False)
            disk_loud = snapshot.get("disk_loud", False)
            disk_rate = snapshot.get("disk_rate", 0.0)
            load = snapshot.get("load", 0.0)
            load_high = load > 1.0
            uptime = snapshot.get("uptime", 0.0)

        if now >= next_beat:
            next_beat = now + BEAT
            heartbeat_on = not heartbeat_on

        if now >= next_draw:
            next_draw = now + DRAW
            leds.set(LED_HEART, heartbeat_on)
            leds.set(LED_NET, ip is not None)
            leds.set(LED_JUPYTER, jup)
            leds.set(LED_DISK, disk_active)
            leds.set(LED_OFF, False)        # 只在关机流程里点亮
            leds.set(LED_READY, False)      # 只在关机完成后点亮
            leds.set(LED_HOLD, held_since is not None)
            leds.set(LED_LOAD, load_high)
            leds.write()

        if screen is not None and now >= next_ui:
            next_ui = now + UI_PERIOD
            status.ip = ip
            status.jupyter = jup
            status.disk_active = disk_active
            status.disk_loud = disk_loud
            status.disk_rate = disk_rate
            status.load = load
            status.uptime = uptime
            status.held = None if held_since is None else now - held_since
            status.leds = leds.value
            screen = render_status(screen, status)
            next_input = now + INPUT_PERIOD

        if screen is not None and now >= next_input:
            next_input = now + INPUT_PERIOD
            tick = getattr(screen, "input_tick", None)
            if tick is not None:
                try:
                    tick(status)
                except Exception as exc:
                    log("panel input failed (%s), continuing headless" % exc)
                    screen = None

        time.sleep(POLL)


def preview_png(path, disk_active=True, held=1.4):
    """把状态屏渲成一张 PNG —— 不接板子就能改版式, 也能当"面板上应当长这样"的预期图。"""
    import numpy as np
    import pynq_lcd_ui
    from pynq_lcd import HEIGHT, WIDTH

    class _PreviewLcd:
        """只要一块数组; flush/enable 都是空操作。"""

        def __init__(self):
            self.array = np.zeros((HEIGHT, WIDTH), dtype=np.uint16)

        def flush(self):
            pass

        def enable(self):
            pass

    lcd = _PreviewLcd()
    scr = StatusScreen(lcd)
    state = Status()
    state.ip = "192.0.2.10/24"
    state.jupyter = True
    state.disk_active = disk_active
    state.disk_loud = disk_active
    state.disk_rate = 3.2 * 1024 * 1024
    state.load = 0.42
    state.uptime = 3725.0
    state.held = held
    state.leds = 0b0010_1011        # 心跳+网络+写卡+就绪
    scr.render(state)
    pynq_lcd_ui.save_png(lcd.array, path)
    print("wrote %s" % path)
    return path


if __name__ == "__main__":
    if "--preview" in sys.argv:
        preview_png(sys.argv[sys.argv.index("--preview") + 1])
    else:
        main()
