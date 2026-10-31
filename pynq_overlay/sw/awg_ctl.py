#!/usr/bin/env python3
"""AWG 控制语义的**唯一真源**(P1)。

为什么要有它: 把 TFT/触摸并入 AWG 位流之后, PS 改跑 PYNQ Linux, 原来那套裸机固件
(`sdk/src/main.c`) 不再在场。命令语义必须有人在 Python 里重建一份, 而**不能有两份** ——
协议桥(串口客户端: `test_bridge.py`/`scope_verify.py`/上位机 GUI)与面板触摸 UI 都读写
这一个对象。

判据(两条, 缺一不可)
  (a) 板上 `scripts/test_bridge.py`(230 断言)与 `scripts/scope_verify.py`(61 项)原样通过;
  (b) 离线金标 `docs/golden/command_replies.json`(731 条)逐条**逐字节**复现回包。
      `scope_verify` 还是"寄存器打包对不对"的端到端判据(它从模拟输出判参数)。

移植纪律: 打包/派生/校验**逐位照抄固件**, 出处都写在注释里(`sdk/src/main.c:行号`)。
能对上的地方别"顺手改好看" —— `BASE` 的归一化截断、`parse_u32` 的 2000000 硬上限、
`iq_to_q214` 的右移方向, 都是踩过的坑, 改了就会与固件/PL 分叉。

边界(P1): 实现**配置族**(CHx / CHAN / NOISE / TAP / IQIMP / DIGMOD / HARM / PHASE)
与**只读状态族**(GET USERBUF/DDR PP/PLAY/DMA/NET)。DDR/USER/PLAY 的暂存与播放逻辑属于 P5,
这里只按基线报告它们的默认状态, 且都是构造参数, 便于 P4/P5 换成真值。

P5 落地情况: `GET DDR PP` 由环形线程推快照(`set_pp_status`); `GET PLAY` 与 `GET USERBUF`
的**PL 侧字段**直接从只读状态 GPIO 读(`_status_gpio`)—— 这两处硬件在合流位流里是真实
存在的(`awg_merged.hwh` 有 `axi_gpio_8`/`axi_gpio_10`), 板端却**没有写者**: PLAY 引擎与
512 B BRAM USERBUF 那一路都没被移植, 所以读回来的就是它们真正的静止态。见各自的注释。
"""
import os
import re
import threading

# ---------------- 常量(与固件逐字对应) ----------------

# sdk/src/main.c:341-345
WAVE_NAME = ("SINE", "SQUARE", "TRIANGLE", "SAW", "PULSE", "NOISE")
MODE_NAME = ("OFF", "AM", "FM", "PM", "SWEEP")
DIGMOD_NAME = ("OFF", "BPSK", "QPSK", "QAM4", "QAM16", "QAM64")
DIGMOD_FILTER_NAME = ("RECT", "RRC")
DIGMOD_ROLLOFF_NAME = ("0.35", "0.2", "0.5", "0.35")   # 索引 3 也显示 0.35

# sdk/src/main.c:239-242
FREQ_MIN, FREQ_MAX, AMPL_MAX = 10, 1000000, 100
# 抽头增益是 9 位有符号(见 sign9): -256..255。**单位增益是 255 不是 256** —— 9 位表示不了 256。
GAIN_MIN_9, GAIN_MAX_9 = -256, 255
RX_BUF_MAX = 72

# 信道损伤控制字位(见 src/rtl/reg_bridge.v 与 clamp 注释)
CHAN_EN, CHAN_COMMIT_EPOCH = 1 << 0, 1 << 8
CHAN_TONE_SW, CHAN_IMP_SW, CHAN_IQ_EN = 1 << 9, 1 << 10, 1 << 11

# BRAM 字地址(32 位字): CH1 抽头 0..3, CH2 抽头 4..7, IQ 8..10, CH1 tone/imp 12/13, CH2 14/15
TAP_WORD_BASE = (0, 4)
IQ_WORD_BASE = 8                      # main.c:339
TONE_WORD_BASE = (12, 14)             # main.c:321  CHAN_TONE_WORD_BASE(ch)=12+2*ch

GPIO_WORD, GPIO_A, GPIO_B, GPIO_C, GPIO_D = 0, 1, 2, 3, 4
GPIO_E, GPIO_F, GPIO_SYMSTEP, GPIO_CHAN = 5, 5, 6, 12
# gpio7 ch1 = 用户数据源控制字(`scripts/create_bd.tcl:169` -> awg_top/gpio13_in),
# gpio7 ch2 = DDR stream 的长度/使能。位域照抄 `main.c:209-213`。
GPIO_USER_CTRL = 7
USER_CTRL_SOURCE = 1 << 0
USER_CTRL_LOOP = 1 << 1
USER_CTRL_COMMIT = 1 << 2
USER_CTRL_LENGTH_SHIFT = 3
USER_CTRL_BANK = 1 << 12
# BRAM USERBUF 那一路的 bank 尺寸(`main.c:97`)—— 与 PP 的 1 MiB 是**两个不同的量**。
USER_BANK_BYTES = 512

# `main.c:346` —— PLAY 状态机的状态名(与 `src/rtl/playback_ctrl.v` 的 localparam 同序)。
PLAY_STATE_NAME = ("IDLE", "ARMED", "STARTING", "RUNNING", "DRAIN", "DONE", "ERROR")
# 只读状态 GPIO(`scripts/create_bd.tcl:169-177`): gpio8 ch1 = `awg_top.gpio14_out`
# (BRAM USERBUF 那一路的 PL 位), gpio10 ch1 = `awg_top.play_status_out`(播放 FSM 快照)。
GPIO_USER_STATUS, GPIO_PLAY_STATUS = 8, 10
# `main.c:209-225` 的用户状态位(与 `src/rtl/awg_top.v:510` 的拼接顺序一致)。
USER_STATUS_VALID, USER_STATUS_DONE = 1 << 0, 1 << 1
USER_STATUS_UNDERFLOW, USER_STATUS_READY = 1 << 2, 1 << 3
USER_STATUS_BANK, USER_STATUS_STREAM_DONE = 1 << 4, 1 << 6
# `src/rtl/awg_top.v:421` 的播放状态字位域(与 `main.c:607-618` 的 read_play_status 一致)。
PLAY_STATUS_STATE_MASK = 0x7
PLAY_STATUS_FRAME_DONE = 1 << 3
PLAY_STATUS_UNDERFLOW = 1 << 4
PLAY_STATUS_TRIGGER_SEEN = 1 << 5
PLAY_STATUS_STOP_PENDING = 1 << 6
PLAY_STATUS_ERROR_VALID = 1 << 7
PLAY_STATUS_REPEAT_SHIFT = 8
PLAY_STATUS_REPEAT_MASK = 0xFFFF
PLAY_STATUS_ERROR_SHIFT = 24


# ---------------- 解析助手(照抄固件, 含各自的硬上限) ----------------

def parse_u32_limit(text, limit):
    """`main.c:836` —— 只吃十进制数字; 溢出检查按 (limit-digit)/10 做, 与固件同法。"""
    if not text:
        return None
    value = 0
    for ch in text:
        if ch < "0" or ch > "9":
            return None
        digit = ord(ch) - ord("0")
        if value > (limit - digit) // 10:
            return None
        value = value * 10 + digit
    return value


def parse_u32(text):
    """`main.c:851` —— 通用上限 2000000(TONE 的频率另用 parse_u32_limit(…, 25000000))。"""
    return parse_u32_limit(text, 2000000)


def parse_long(text):
    """`main.c:857` —— 允许 +/- 号, **量值上限 1000000**(所以 IQIMP FREQOFF 正好够用)。"""
    if not text:
        return None
    neg = False
    body = text
    if body[0] == "-":
        neg, body = True, body[1:]
    elif body[0] == "+":
        body = body[1:]
    if not body:
        return None
    value = 0
    for ch in body:
        if ch < "0" or ch > "9":
            return None
        value = value * 10 + (ord(ch) - ord("0"))
        if value > 1000000:
            return None
    return -value if neg else value


def tokenize(line):
    """`main.c:1940` —— 按空白切分, **最多 8 个** token; 第 9 个直接判 BAD VALUE。"""
    toks = line.split()
    if len(toks) > 8:
        return None
    return toks


# ---------------- 打包 / 派生(逐位照抄) ----------------

def pack_word(s):
    """`main.c:434` freq[19:0] | ampl<<20 | wave<<28。"""
    return (s.freq_hz | (s.ampl_pct << 20) | (s.wave << 28)) & 0xFFFFFFFF


def pack_word_a(s):
    """`main.c:439` {base[7:0], h2_amp[6:0], h2_ord[3:0], h1_amp[6:0], h1_ord[3:0]}。"""
    return ((s.base_pct << 22) | (s.h_ampl[1] << 15) | (s.h_ord[1] << 11)
            | (s.h_ampl[0] << 4) | s.h_ord[0]) & 0xFFFFFFFF


def pack_word_b(s):
    """`main.c:445` {reserved, h4_amp[6:0], h4_ord[3:0], h3_amp[6:0], h3_ord[3:0]}。"""
    return ((s.h_ampl[3] << 15) | (s.h_ord[3] << 11)
            | (s.h_ampl[2] << 4) | s.h_ord[2]) & 0xFFFFFFFF


def pack_word_c(s):
    """`main.c:451` {am_depth[7:0], mfreq[18:0], mwave[1:0], mode[2:0]}。"""
    return ((s.am_depth << 24) | (s.mfreq_01 << 5) | (s.mwave << 3) | s.mode) & 0xFFFFFFFF


def pack_word_d(s):
    """`main.c:456` {reserved[3:0], pm_dev[8:0], fm_dev[18:0]}。"""
    return ((s.pm_dev << 19) | s.fm_dev) & 0xFFFFFFFF


def angle_to_phase(angle_0_01):
    """`main.c:461` 0.01° -> 32 位全周期相位字: x × 2^32 / 36000(截断)。"""
    return ((angle_0_01 << 32) // 36000) & 0xFFFFFFFF


def pack_word_e(ch2):
    """`main.c:466` gpio10: CH2 的 32 位相位差。"""
    return angle_to_phase(ch2.phase_0_01)


def pack_word_f(ch1, ch2, digmod):
    """`main.c:471` gpio11: phaselink | 两通道 PULSE DUTY | DIGMOD 四件。"""
    return (ch2.phaselink | (ch1.pulse_duty << 1) | (ch2.pulse_duty << 9)
            | (digmod.mode << 17) | (digmod.filter << 20) | (digmod.rolloff << 21)
            | (digmod.seed << 23)) & 0xFFFFFFFF


def symrate_to_phase_step(symrate_hz):
    """`main.c:429` 符号相位步进 = symrate × 2^32 / 50 MHz(截断)。"""
    return ((symrate_hz << 32) // 50000000) & 0xFFFFFFFF


def normalize(s):
    """`main.c:483` sum>100 时各分量 ×100/sum(**整数截断**); sum=0 保持全 0。

    只在 `SET CHx BASE` 与 `SET CHx HARMn` 两处被调用 —— 所以 `BASE 100` 在清掉谐波之后
    读回来是 70 这种"怪数", 正是它算出来的, 不是残留(金标里有这条)。
    """
    total = s.base_pct + sum(s.h_ampl)
    if total > 100:
        s.base_pct = s.base_pct * 100 // total
        for i in range(4):
            s.h_ampl[i] = s.h_ampl[i] * 100 // total


def chan_tap_word(gain, delay):
    """`main.c:2795` 增益有符号 9 位(低 9 位) | 延迟 9 位 左移 9。"""
    return ((gain & 0x1FF) | ((delay & 0x1FF) << 9)) & 0xFFFFFFFF


def pack_tone(freq_hz, ampl_pct):
    """`main.c:2729` fword = round(f × 2^24 / 50e6) 取 24 位, 高位放幅度 7 位。"""
    fword = (((freq_hz << 24) + 25000000) // 50000000) & 0xFFFFFF
    return (fword | ((ampl_pct & 0x7F) << 24)) & 0xFFFFFFFF


def pack_impulse(period, width, ampl):
    """`main.c:2750` period[15:0] | width[23:16] | ampl(8 位有符号)[31:24]。"""
    return ((period & 0xFFFF) | ((width & 0xFF) << 16)
            | ((ampl & 0xFF) << 24)) & 0xFFFFFFFF


def sign9(value):
    """`main.c:802` 低 9 位按有符号解释(抽头增益)。"""
    return value - 512 if value & 0x100 else value


# ---------------- 解码(寄存器字 -> 字段) ----------------
#
# 与上面的 `pack_*` **逐位对称**, 用于"面板从 PL 真寄存器读回"这条路径。
#
# **为什么需要它**: `AwgCtl.handle("GET ...")` 读的是它**自己的影子状态** —— 写进去再读
# 出来必然一致, 那种"回读校验"是同义反复, 只能证明命令被解析器接受, 证明不了寄存器真的
# 写进去了(本工程有过真实前科: BD 修订落后导致 AXI BRAM 写入不生效、`proc_sys_reset`
# 把 AXI 外设按在复位里 —— 两种情况下 GET 都照常回正确值)。
#
# ⚠ 位域必须与 `src/rtl/reg_bridge.v` 及 `pack_*` 一致; 改了一边就要改另一边。
#   `decode_*` 只覆盖**面板会改的字段**, 不是全量反解: 谐波/调制深度那些由别的命令写,
#   面板读回来只是为了显示, 用不到就解。

def decode_word(word):
    """`pack_word` 的逆: freq[19:0] | ampl<<20 | wave<<28。返回 (freq, ampl, wave)。"""
    word &= 0xFFFFFFFF
    return (word & 0xFFFFF, (word >> 20) & 0xFF, (word >> 28) & 0xF)


def decode_word_f(word):
    """`pack_word_f` 的逆(gpio11)。只解面板关心的四个: duty1/duty2/phaselink/digmod 四件。

    返回 dict: duty (两通道 0..100)、phaselink、mode/filter/rolloff/seed。
    """
    word &= 0xFFFFFFFF
    return {
        "phaselink": word & 1,
        "duty": [(word >> 1) & 0xFF, (word >> 9) & 0xFF],
        "mode": (word >> 17) & 0x7,
        "filter": (word >> 20) & 0x1,
        "rolloff": (word >> 21) & 0x3,
        "seed": (word >> 23) & 0x7F,
    }


def decode_symstep(word):
    """`symrate_to_phase_step` 的逆: step = symrate × 2^32 / 50e6, 取整会丢精度。

    返回**最接近的** symrate。注意 2^32/50e6 不是整数, 所以往返可能有 1 Hz 级误差 ——
    面板显示的是"设备实际用的符号率", 这一点误差是真实的, 不该抹掉。
    """
    return (word * 50000000) >> 32


def decode_tap_word(word):
    """`chan_tap_word` 的逆: 增益有符号 9 位(低 9 位) | 延迟 9 位 左移 9。"""
    word &= 0xFFFFFFFF
    return sign9(word & 0x1FF), (word >> 9) & 0x1FF


def decode_chan_ctrl(word):
    """`GPIO_CHAN` 通道字的逆(面板只关心 使能/噪声)。返回 (enabled, noise_pct)。"""
    word &= 0xFFFF
    return bool(word & CHAN_EN), (word >> 1) & 0x7F


def decode_rolloff(code):
    """GPIO11 bit[22:21] 的滚降编码 -> 名字。`pack_word_f` 用的是**编码**不是索引:
    00->0.35, 01->0.2, 10->0.5, 11->0.35(保留)。"""
    return {0: "0.35", 1: "0.2", 2: "0.5", 3: "0.35"}.get(code & 0x3, "0.35")


# ---------------- 文本回读解析(GET 回包 -> 字段) ----------------
#
# 与上面的 `_ch_line`/`_digmod_line`/`_chan_line` **格式化器**方向相反, 供**串口客户端**
# 场景使用(客户端只能通过 GET 看设备, 拿不到本对象)。面板自己走 `read_registers()`。
#
# ⚠ 解析必须**严格**: 缺字段/非法枚举一律返回 None 而不是给个默认值 —— 参考设计明确要求
#   "字段缺失、非法枚举、数值越界均拒绝"(AGENT_GUIDE.md 实现步骤 2)。给默认值会把"解析
#   失败"伪装成"设备就是这个值", 那比报错危险得多。
#
# ⚠ `GET CHx` 里的 `MODE` 是 **AM/FM/PM/SWEEP**(通道调制模式), **不是** DIGMOD 的模式。
#   面板要的"设备实际模式"必须从 `GET DIGMOD` 取 —— 这两个字段同名但完全无关, 混用会让
#   界面显示错误的运行状态。

def _fields(line, head):
    """把回包按空格切开并校验开头。返回 token 列表或 None。"""
    if not line:
        return None
    toks = line.split()
    if not toks or toks[0] != head:
        return None
    return toks


def _kv(toks, start=0):
    """把 ["A", "1", "B", "2"] 收成 {"A": "1", "B": "2"}; 奇数个返回 None。"""
    if (len(toks) - start) % 2:
        return None
    return {toks[i]: toks[i + 1] for i in range(start, len(toks), 2)}


def parse_ch_line(line):
    """`GET CHx` -> {"wave","freq_hz","ampl_pct","duty_pct","mode",...} 或 None。

    只取面板关心的字段; 谐波/调制深度等照样解析但调用方可忽略。
    """
    toks = _fields(line, "CH1") or _fields(line, "CH2")
    if not toks:
        return None
    f = _kv(toks, 1)
    if f is None or "WAVE" not in f or "FREQ" not in f:
        return None
    try:
        out = {
            "wave": f["WAVE"], "freq_hz": int(f["FREQ"]),
            "ampl_pct": int(f["AMPL"]), "duty_pct": int(f.get("DUTY", 0)),
            "base_pct": int(f.get("BASE", 100)), "mode": f.get("MODE", "OFF"),
            "mwave": f.get("MODWAVE", "SINE"), "mfreq_01": int(f.get("MODFREQ", 0)),
            "am_depth": int(f.get("AMDEPTH", 0)), "fm_dev": int(f.get("FMDEV", 0)),
            "pm_dev": int(f.get("PMDEV", 0)), "phase_0_01": int(f.get("PHASE", 0)),
            "phaselink": f.get("PHASELINK") == "ON",
        }
    except (KeyError, ValueError):
        return None
    if out["wave"] not in WAVE_NAME or out["mode"] not in MODE_NAME:
        return None                       # 非法枚举 -> 拒绝, 不猜
    if not (FREQ_MIN <= out["freq_hz"] <= FREQ_MAX) or not (0 <= out["ampl_pct"] <= AMPL_MAX):
        return None
    return out


def parse_digmod_line(line):
    """`GET DIGMOD` -> {"mode","source","filter","rolloff","seed","symrate_hz","running"} 或 None。

    `running` 由回包末尾的 `STATE RUNNING|STOPPED` 给出, 与 `mode != OFF` 冗余 —— 但
    两者不一致时以 STATE 为准(固件的实际运行态)。
    """
    toks = _fields(line, "DIGMOD")
    if not toks:
        return None
    f = _kv(toks, 1)
    if f is None or "MODE" not in f or "SYMRATE" not in f:
        return None
    try:
        mode = f["MODE"]
        out = {
            "mode": mode, "source": f.get("SOURCE", "PRBS7"),
            "filter": f.get("FILTER", "RECT"), "rolloff": f.get("ROLLOFF", "0.35"),
            "seed": int(f.get("SEED", 127)), "symrate_hz": int(f["SYMRATE"]),
            "running": f.get("STATE") == "RUNNING",
        }
    except (KeyError, ValueError):
        return None
    if mode not in DIGMOD_NAME or out["source"] not in ("PRBS7", "USER"):
        return None
    if out["filter"] not in DIGMOD_FILTER_NAME:
        return None
    if not (1 <= out["seed"] <= 127) or not (10 <= out["symrate_hz"] <= 1000000):
        return None
    return out


def parse_chan_line(line):
    """`GET CHx CHAN` -> {"enabled","noise_pct","taps":[{gain,delay}×4]} 或 None。

    ⚠ 这一行**不能**按 key/value 成对解析: TAP 是 `TAP0 255 3 TAP1 ...`, 延迟是个裸数字,
    没有自己的键名。所以按位置走。

    ⚠ TAP 增益是**9 位有符号**(`sign9` 的解释), 范围 -256..255 —— 不要按无符号读。
    TONE/IMPULSE 不在面板上, 解析出来但调用方可忽略。
    """
    toks = _fields(line, "CH1") or _fields(line, "CH2")
    # 实际形状是 `CH1 CHAN ON NOISE ...` —— CHAN 在 toks[1], ON/OFF 在 toks[2]
    if not toks or len(toks) < 4 or toks[1] != "CHAN":
        return None
    if toks[2] not in ("ON", "OFF"):
        return None
    try:
        noise = int(toks[toks.index("NOISE") + 1])
    except (ValueError, IndexError):
        return None
    if not (0 <= noise <= 100):
        return None
    taps = []
    for k in range(4):
        key = "TAP%d" % k
        if key not in toks:
            return None
        try:
            gain = int(toks[toks.index(key) + 1])
            delay = int(toks[toks.index(key) + 2])
        except (ValueError, IndexError):
            return None
        if not (GAIN_MIN_9 <= gain <= GAIN_MAX_9) or not (0 <= delay <= 511):
            return None
        taps.append({"gain_q8": gain, "delay_samples": delay})
    return {"enabled": toks[2] == "ON", "noise_pct": noise, "taps": taps}


# ---------------- I/Q 定点数学(逐位照抄; 表来自 sdk/src/sincos_q15.h) ----------------

def _load_sincos(path=None):
    """从 `sdk/src/sincos_q15.h` 读 Q1.15 sin/cos 表 —— **不要自己重算**, 否则会和 PL/固件分叉。

    这个头由 `tools/gen_sincos.py` 从 `src/rtl/sincos_rom.v` 的同一份源生成, 所以它是
    "固件与 PL 不会漂移"的那份真源。找不到就退化成用 math 现算(仅供离线粗跑, 会记一条警告)。
    """
    candidates = [path] if path else []
    here = os.path.dirname(os.path.abspath(__file__))
    candidates += [
        os.path.join(here, "sincos_q15.h"),
        os.path.join(here, "..", "..", "sdk", "src", "sincos_q15.h"),
        os.path.join(here, "..", "..", "..", "sdk", "src", "sincos_q15.h"),
    ]
    for cand in candidates:
        if not cand or not os.path.exists(cand):
            continue
        with open(cand, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        tables = []
        for name in ("cos", "sin"):
            m = re.search(r"sincos_q15_%s\[[^\]]*\]\s*=\s*\{(.*?)\}" % name, text, re.S)
            if not m:
                break
            vals = [int(v) for v in re.findall(r"-?\d+", m.group(1))]
            tables.append(vals)
        if len(tables) == 2 and len(tables[0]) == len(tables[1]) == 1024:
            return tables[0], tables[1]
    # 退化路径: 只为让离线粗跑不炸。**必须出声** —— 静默降级会让 IQ 系数与本模块之外
    # (固件/PL)悄悄差 1 LSB, 而这种偏差只会在示波器上表现为镜像抑制变差, 极难发现。
    import math
    import sys
    print("awg_ctl: 警告 —— 找不到 sdk/src/sincos_q15.h, 改用 math 现算的近似表; "
          "IQ 系数可能与固件/PL 不完全一致", file=sys.stderr)
    n = 1024
    cos_t = [int(round(32767.0 * math.cos(2 * math.pi * i / n))) for i in range(n)]
    sin_t = [int(round(32767.0 * math.sin(2 * math.pi * i / n))) for i in range(n)]
    return cos_t, sin_t


_SINCOS_COS, _SINCOS_SIN = _load_sincos()


def iq_qmul(x, y, shift):
    """`main.c:686` Q1.15 乘 -> 指定右移, **四舍五入且符号对称**。"""
    p = x * y
    if p >= 0:
        return (p + (1 << (shift - 1))) >> shift
    return -(((-p) + (1 << (shift - 1))) >> shift)


def iq_sincos(table, deg100):
    """`main.c:695` 查表 + 线性插值, 返回 Q1.15。"""
    n = len(table)
    deg100 %= 36000
    if deg100 < 0:
        deg100 += 36000
    pos = (deg100 * (n << 16)) // 36000
    i0 = (pos >> 16) & (n - 1)
    frac = pos & 0xFFFF
    i1 = (i0 + 1) & (n - 1)
    v0, v1 = table[i0], table[i1]
    return v0 + (((v1 - v0) * frac) >> 16)


def iq_clamp_q214(v):
    """`main.c:708`"""
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


def iq_to_q214(v):
    """`main.c:721` Q1.15 -> Q2.14 是**右移 1 位**(不是乘 2)。

    方向写反会让单位阵变成 32767(=2.0)并在大系数处到处钳位 —— 固件里就是离线比对
    32190 组输入抓出来的。
    """
    if v >= 0:
        return iq_clamp_q214((v + 1) >> 1)
    return iq_clamp_q214(-((-v + 1) >> 1))


def iq_coeffs(phase_deg100, phaseerr_deg100, gain_q14):
    """`main.c:734` `iq_recompose` —— R(phase) × diag(alpha,1) × E(phase_err)。

    返回 (coef_ab, coef_cd), 各自是 {b,a} / {d,c} 两个 16 位有符号拼在一个 32 位字里
    (与 host 的 `apply_iq_impair` 展开式逐项对应)。
    """
    ct = iq_sincos(_SINCOS_COS, phase_deg100)
    st = iq_sincos(_SINCOS_SIN, phase_deg100)
    cp = iq_sincos(_SINCOS_COS, phaseerr_deg100)
    sp = iq_sincos(_SINCOS_SIN, phaseerr_deg100)
    al = gain_q14
    a = iq_to_q214(iq_qmul(ct, al, 14) + iq_qmul(st, sp, 15))
    b = iq_to_q214(-iq_qmul(st, cp, 15))
    c = iq_to_q214(iq_qmul(st, al, 14) - iq_qmul(ct, sp, 15))
    d = iq_to_q214(iq_qmul(ct, cp, 15))
    coef_ab = ((b & 0xFFFF) << 16) | (a & 0xFFFF)
    coef_cd = ((d & 0xFFFF) << 16) | (c & 0xFFFF)
    return coef_ab, coef_cd


def iq_fword(freqoff_hz):
    """`main.c:753` fword = round(Δf / 50e6 × 2^32), 32 位全宽。"""
    num = freqoff_hz * 4294967296
    if num >= 0:
        fw = (num + 25000000) // 50000000
    else:
        fw = -(((-num) + 25000000) // 50000000)
    return fw & 0xFFFFFFFF


# ---------------- 后端: 寄存器/BRAM 写(真板 or 记账替身) ----------------

class RecordingBackend:
    """把写操作记下来 —— 离线测试用的替身(打包/映射对不对, 靠它断言)。

    它**也能读回**(读的就是记下来的值), 所以离线测试可以走完整的
    "写 → 从寄存器解回来" 路径, 不必上板。
    """

    def __init__(self):
        self.gpio = {}       # (instance, channel) -> value
        self.bram = {}       # 字索引 -> value

    def write_gpio(self, instance, channel, value):
        self.gpio[(instance, channel)] = value & 0xFFFFFFFF

    def write_bram_word(self, word_index, value):
        self.bram[word_index] = value & 0xFFFFFFFF

    def read_gpio(self, instance, channel):
        return self.gpio.get((instance, channel), 0)

    def read_bram_word(self, word_index):
        return self.bram.get(word_index, 0)


class PynqBackend:
    """真板上: AXI GPIO 走 PYNQ 的驱动, 抽头表/IQ/tone 走 axi_bram_ctrl 的 MMIO。

    名字与 `scripts/create_bd.tcl` 的实例名一致(`axi_gpio_0`…`axi_gpio_12`、
    `axi_bram_ctrl_1`)。**注意 PYNQ 的 `Overlay()` 不套用 .hwh 里的时钟设置**,
    装 overlay 之后要自己把 FCLK0 设成 50 MHz(AWG 的时序是按 50 MHz 签的)。
    """

    def __init__(self, overlay):
        self.overlay = overlay
        self._bram = None
        # ⚠ **必须缓存驱动对象**: `getattr(overlay, "axi_gpio_N")` 每次调用都会重新构造一个
        # 驱动(实测约 30 ms), 而 `write_all()` 一次要写 12 个通道 —— 于是**一条 SET 要 399 ms**,
        # 单次寄存器写其实只有 0.03 ms。这个延迟会与串口客户端的读超时(0.5 s)打架:
        # 板上有负载时偶发超时 -> 客户端失步 -> 整串断言连锁失败(实测 171 条)。
        # 缓存之后 SET 掉到亚毫秒级。
        self._gpios = {}

    def _gpio(self, instance):
        drv = self._gpios.get(instance)
        if drv is None:
            drv = getattr(self.overlay, "axi_gpio_%d" % instance)
            self._gpios[instance] = drv
        return drv

    def write_gpio(self, instance, channel, value):
        gpio = self._gpio(instance)
        # ⚠ 本镜像的 PYNQ 里 AXI GPIO 的 `write()` **mask 是必填参数**
        # (`write(value, mask)`; 少给会 TypeError)。这些通道在设计里全是全输出,
        # 所以 mask 给全 1 —— 与固件 XGpio_DiscreteWrite(按位写) 的语义一致。
        ch = gpio.channel1 if channel == 1 else gpio.channel2
        ch.write(value & 0xFFFFFFFF, 0xFFFFFFFF)

    def write_bram_word(self, word_index, value):
        if self._bram is None:
            from pynq import MMIO
            ip = self.overlay.axi_bram_ctrl_1
            self._bram = MMIO(ip.mmio.base_addr, ip.mmio.length)
        self._bram.write(word_index * 4, value & 0xFFFFFFFF)

    def read_gpio(self, instance, channel):
        """读回一个 AXI GPIO 通道 —— **面板的"真回读"走这条**。

        设计里这些通道全是**输出**, 读回来就是上次写进去的值(PYNQ 驱动读的是 GPIO 的
        数据寄存器)。所以它能回答"寄存器里到底是不是我写的那个数", 而这正是
        `handle("GET")`(读影子状态)回答不了的 —— 影子状态在写入失败时照样是对的。

        ⚠ 通道是**只读**用的: 这里不做 mask, 也不需要。若哪天某个通道改成双向, 要重新想。
        """
        gpio = self._gpio(instance)
        ch = gpio.channel1 if channel == 1 else gpio.channel2
        return ch.read() & 0xFFFFFFFF

    def read_bram_word(self, word_index):
        if self._bram is None:
            from pynq import MMIO
            ip = self.overlay.axi_bram_ctrl_1
            self._bram = MMIO(ip.mmio.base_addr, ip.mmio.length)
        return self._bram.read(word_index * 4) & 0xFFFFFFFF


# ---------------- 状态 ----------------

class ChState(object):
    """`sdk/src/main.c:244` `ch_state_t` + `main()` 的初值(`main.c:3039`)。"""

    def __init__(self):
        self.wave = 0
        self.freq_hz = 1000
        self.ampl_pct = 50
        self.pulse_duty = 50
        self.base_pct = 100
        self.h_ord = [0, 0, 0, 0]
        self.h_ampl = [0, 0, 0, 0]
        self.mode = 0
        self.mwave = 0
        self.mfreq_01 = 10
        self.am_depth = 100
        self.fm_dev = 0
        self.pm_dev = 0
        self.phase_0_01 = 0
        self.phaselink = 0


class DigmodState(object):
    """`main.c:347-352`"""

    def __init__(self):
        self.mode = 0
        self.filter = 0
        self.rolloff = 0
        self.seed = 127
        self.source = 0
        self.symrate_hz = 10000


class ChanState(object):
    """信道损伤: 控制字 + 抽头 + 单音/冲激(`main.c:306-320`)。"""

    def __init__(self):
        self.word = 0
        self.tap = [0, 0, 0, 0]
        self.tone_freq = 0
        self.tone_word = 0
        self.imp_word = 0


class IqState(object):
    """`main.c:331-338`(GAIN 初值 16384)"""

    def __init__(self):
        self.on = 0
        self.phase_deg100 = 0
        self.freqoff_hz = 0
        self.gain_q14 = 16384
        self.phaseerr_deg100 = 0
        self.coef_ab = 0
        self.coef_cd = 0
        self.fword = 0


# ---------------- 控制器 ----------------

class AwgCtl(object):
    """命令语义: `handle(line)` 收一行, 返回**不含行尾**的回包文本(`None` = 不回应)。

    状态族里 DDR/USER/PLAY 的部分是**只读报告**, 默认值取自 P0 基线金标
    (`docs/golden/command_replies.json` 的 `__defaults_state__`); P5 会把真状态接上。

    **一个实例可能被两个线程调用**(板端守护进程: 串口桥线程 + 面板的事务线程), 所以
    `handle` 与 `read_registers` 用同一把锁。不加锁的话两条 SET 会交错地写 12 个 GPIO
    通道, 硬件落到"两条命令各写了一半"的状态 —— 面板的**回读校验**会把它报成 MISMATCH,
    看着像"写没生效", 其实是自己的两个线程踩了。
    """

    def __init__(self, backend=None, sincos_tables=None,
                 user_valid=False, dma_status=None):
        self.backend = backend if backend is not None else RecordingBackend()
        self.lock = threading.RLock()
        self.ch = [ChState(), ChState()]
        self.digmod = DigmodState()
        self.chan = [ChanState(), ChanState()]
        self.iq = IqState()
        self.user_valid = bool(user_valid)
        self.user_bit_length = 0                      # BRAM USERBUF 那一路(`main.c:283`)
        self.user_active_bank = 0
        self.user_loop = 0                            # `main.c:284`; 面板不发, 恒 0
        self.user_commit_toggle = 0                   # `main.c:285`; 每次 USER COMMIT 翻转
        # `main.c:290` `user_commit_seen`: 与上面那个 epoch 拨动位**是两个量** —— 这是
        # "至少成功提交过一次"的一次性锁存(固件在 `SET USER COMMIT` 成功时置 1), 不是拨动。
        self.user_commit_seen = 0
        self.dma_status = dma_status or {
            "base": 0x40400000, "buffer": 0x02200000, "bytes": 65536,
            "status": 0x00000000, "state": "NOT_INIT",
        }
        self.net = {"init": 1, "link": "UP", "ip": "192.0.2.10", "udp": 40100}
        # 环形线程推来的播放快照(`GET DDR PP` 的真源)。None = 还没推过, 回基线金标。
        self.pp_status = None
        if sincos_tables is not None:                 # 离线测试可注入
            global _SINCOS_COS, _SINCOS_SIN
            _SINCOS_COS, _SINCOS_SIN = sincos_tables
        self.write_all()
        self.write_chan_ctrl()                        # 与固件 main() 一致: 通道默认关

    # ---- 写出(与 write_all / write_chan_ctrl / write_chan_tap 一一对应) ----

    def write_all(self):
        """`main.c:807`。gpio0..6/12 与 BRAM 是本函数管的; gpio7 ch1(用户源控制字)、
        gpio9(PLAY)属于 P5, 分别由 `write_user_ctrl` 与播放侧写 —— 这里只调用, 不内联位域。

        ⚠ 历史教训: `DdrPp.start()` 曾经**绕过** `AwgCtl` 直接写 gpio7 ch1, 于是
        `GET DIGMOD` 的 SOURCE 影子恒显 PRBS7, 且 `DdrPp.stop()` 之后 SOURCE 位保持锁存,
        用户接着起普通数字调制会被接到旧数据源上。所有权必须只有一处。
        """
        b = self.backend
        b.write_gpio(GPIO_SYMSTEP, 1, symrate_to_phase_step(self.digmod.symrate_hz))
        b.write_gpio(GPIO_WORD, 1, pack_word(self.ch[0]))
        b.write_gpio(GPIO_WORD, 2, pack_word(self.ch[1]))
        b.write_gpio(GPIO_A, 1, pack_word_a(self.ch[0]))
        b.write_gpio(GPIO_A, 2, pack_word_a(self.ch[1]))
        b.write_gpio(GPIO_B, 1, pack_word_b(self.ch[0]))
        b.write_gpio(GPIO_B, 2, pack_word_b(self.ch[1]))
        b.write_gpio(GPIO_C, 1, pack_word_c(self.ch[0]))
        b.write_gpio(GPIO_C, 2, pack_word_c(self.ch[1]))
        b.write_gpio(GPIO_D, 1, pack_word_d(self.ch[0]))
        b.write_gpio(GPIO_D, 2, pack_word_d(self.ch[1]))
        b.write_gpio(GPIO_E, 1, pack_word_e(self.ch[1]))
        b.write_gpio(GPIO_F, 2, pack_word_f(self.ch[0], self.ch[1], self.digmod))
        self.write_user_ctrl()                        # `main.c:822`

    def user_ctrl_word(self, source=None, bank=None):
        """`main.c:565-574` 的位域, 逐位镜像。

        **length 字段是 BRAM USERBUF 那一路的长度**(固件里是 `user_bit_length`, 512 B 量级),
        不是 DDR PP 的长度 —— PP 的长度走 gpio7 ch2(`dma_pp_stream_ctrl` 的 `length << 3`)。
        `awg_top` 的 `user_length` 恰恰取自 ch1, 所以往 ch1 塞 1 MiB 会让 BRAM 路径按 1 MiB
        节流, 消费链永远排不完(板上实测: DMA 30 s 不动、`user_status` 恒 0x42)。

        `source`/`bank` 传 `None` 表示取影子状态; 显式传值用于 `DdrPp.start()` —— 它在 PL 里
        把数据源切到 USER, 但**不能**碰 length。
        """
        bytes_ = self.user_bit_length // 8
        encoded = 0 if bytes_ == USER_BANK_BYTES else bytes_
        src = self.digmod.source if source is None else source
        bank_bit = self.user_active_bank if bank is None else bank
        return ((USER_CTRL_SOURCE if src else 0)
                | (USER_CTRL_LOOP if self.user_loop else 0)
                | (USER_CTRL_COMMIT if self.user_commit_toggle else 0)
                | (encoded << USER_CTRL_LENGTH_SHIFT)
                | (USER_CTRL_BANK if bank_bit else 0))

    def write_user_ctrl(self, source=None, bank=None):
        """写 gpio7 ch1。**在 `self.lock` 下** —— 与 `handle()`/`read_registers()` 同一把锁,
        所以 PP 的 ch1 写与任何 `SET` 互斥, 不会交错成"半条命令"的硬件状态。

        `DdrPp.start()/stop()` 的 ch1 写从这里走(板端 `build_board_server` 注入本方法)。
        """
        with self.lock:
            self.backend.write_gpio(GPIO_USER_CTRL, 1, self.user_ctrl_word(source, bank))

    def write_user_ctrl_for_pp(self, bank):
        """`DdrPp.start(bank)` 要的那一次 ch1 写: **只置 SOURCE(+BANK), 绝不带 length**。

        source 位是 PL 消费链的总闸(`awg_top.v:322` 的 `ddr_stream_gate = digmod_source_user
        && user_stream_enable_safe`)—— 不开它, DMA 排出的数据没人取, 状态机只能按超时判死
        (ERROR 0x24, 看起来像环形坏了)。见 `awg_ddr_pp.start()` 的同名注释。

        ⚠ 同时把**影子** `digmod.source` 置 1。这不是记账洁癖: 播放中若来一条 `SET`
        (串口桥/面板随时可能), `write_all()` 会按影子重写 ch1 —— 影子若还是 PRBS7, 那一次
        `SET` 就会把 USER 源悄悄关掉, 正在播的流当场断(DMA 排出的数据没人取)。硬件与影子
        必须一起动。
        """
        with self.lock:
            self.digmod.source = 1
            self.user_active_bank = 1 if bank else 0
            self.write_user_ctrl()

    def _write_chan_ext(self, ci):
        """`main.c:772` tone/impulse 两个字(CH1 12/13, CH2 14/15)。"""
        base = TONE_WORD_BASE[ci]
        self.backend.write_bram_word(base, self.chan[ci].tone_word)
        self.backend.write_bram_word(base + 1, self.chan[ci].imp_word)

    def _write_iq_ext(self):
        """`main.c:762` IQ 系数 + fword 三个字(8/9/10)。"""
        self.backend.write_bram_word(IQ_WORD_BASE, self.iq.coef_ab)
        self.backend.write_bram_word(IQ_WORD_BASE + 1, self.iq.coef_cd)
        self.backend.write_bram_word(IQ_WORD_BASE + 2, self.iq.fword)

    def write_chan_ctrl(self):
        """`main.c:782`: 两个控制字 + tone/impulse + IQ 系数。"""
        self.backend.write_gpio(GPIO_CHAN, 1, self.chan[0].word & 0xFFFF)
        self.backend.write_gpio(GPIO_CHAN, 2, self.chan[1].word & 0xFFFF)
        self._write_chan_ext(0)
        self._write_chan_ext(1)
        self._write_iq_ext()

    def write_chan_tap(self, ci):
        """`main.c:792`: 只写该通道那 4 个字(CH1 0..3 / CH2 4..7)。"""
        base = TAP_WORD_BASE[ci]
        for k in range(4):
            self.backend.write_bram_word(base + k, self.chan[ci].tap[k])

    # ---- 从**真寄存器**读回(面板的回读校验走这条, 不走影子状态) ----

    def read_registers(self):
        """从 PL 寄存器解出一份 `ui_model` 形状的配置。**这才是真的回读校验。**

        与 `handle("GET ...")` 的区别: GET 读的是本对象的影子状态, 写进去再读出来必然
        一致 —— 只能证明命令被接受, 证明不了寄存器真的写进去了。这里读的是 GPIO/BRAM
        的实际内容, 所以能抓到"写没生效"(本项目有过真实前科: BD 修订落后导致 AXI BRAM
        写入不生效、`proc_sys_reset` 把 AXI 外设按在复位里 —— 两种情况下 GET 都照常回对)。

        只解**面板会改的字段**; 谐波/调制深度那些不在面板上, 不读。返回的 dict 与
        `ui_model.default_config()` 同构, 缺失字段填 0 —— 调用方应当只看它关心的字段。

        ⚠ 需要后端支持读(`PynqBackend.read_gpio` / `RecordingBackend.read_gpio`)。老后端
        没有这两个方法时会抛 `AttributeError`, 由调用方决定降级 —— 不要在这里吞掉,
        静默降级成"读影子"会让这个校验重新变成同义反复。

        与 `handle` 同一把锁: 一次回读要连着读好几个通道, 中间被另一条 SET 插进来就会读到
        "半条命令"的硬件状态, 于是回读校验误报不符。
        """
        with self.lock:
            return self._read_registers()

    def _read_registers(self):
        b = self.backend
        awg = []
        for ci in range(2):
            freq, ampl, wave = decode_word(b.read_gpio(GPIO_WORD, ci + 1))
            awg.append({"wave": WAVE_NAME[wave] if wave < len(WAVE_NAME) else "SINE",
                        "freq_hz": freq, "ampl_pct": ampl, "duty_pct": 0})
        f = decode_word_f(b.read_gpio(GPIO_F, 2))
        # DUTY 在 gpio11 的位域里(两通道各 8 位), 与 pack_word_f 对称
        awg[0]["duty_pct"], awg[1]["duty_pct"] = f["duty"][0], f["duty"][1]
        chan = []
        for ci in range(2):
            enabled, noise = decode_chan_ctrl(b.read_gpio(GPIO_CHAN, ci + 1))
            taps = []
            for k in range(4):
                gain, delay = decode_tap_word(b.read_bram_word(TAP_WORD_BASE[ci] + k))
                taps.append({"gain_q8": gain, "delay_samples": delay})
            chan.append({"enabled": enabled, "noise_pct": noise, "taps": taps})
        return {
            "awg": awg,
            "dig": {
                # 符号率与 DIGMOD 四件都在硬件里: 步进在 gpio6, 四件在 gpio11
                "mode": DIGMOD_NAME[f["mode"]] if f["mode"] < len(DIGMOD_NAME) else "OFF",
                # SOURCE 在 gpio7 ch1 的 bit0 —— **必须从寄存器解**, 不能拿影子糊弄。
                # 影子读出来必然等于自己写的, 是同义反复; 而这条恰恰是"写没生效"的高发区
                # (本项目出过 BD 修订落后致寄存器写入不生效)。面板的 DDR 页会显示它。
                "source": ("USER" if (b.read_gpio(GPIO_USER_CTRL, 1) & USER_CTRL_SOURCE)
                           else "PRBS7"),
                "symrate_hz": decode_symstep(b.read_gpio(GPIO_SYMSTEP, 1)),
                "filter": DIGMOD_FILTER_NAME[f["filter"]],
                "rolloff": decode_rolloff(f["rolloff"]),
                "seed": f["seed"],
            },
            "chan": chan,
        }

    # ---- 只读状态族的文本 ----

    def _status_gpio(self, instance, channel):
        """读一个**只读状态** GPIO 通道。与 `read_registers` 同一条路(`backend.read_gpio`),
        同样**不吞异常** —— 后端读不了就抛, 静默降级成"回基线"会让这些回包重新变成桩。

        离线 `RecordingBackend` 里没写过的键读回 0, 而 0 解出来**恰好**就是 P0 基线金标,
        所以金标重放测试不受影响。
        """
        return self.backend.read_gpio(instance, channel)

    def _userbuf_line(self):
        """`main.c:2029` 的格式串逐字对应。

        **这个回包是混合量**(两处真源, 见固件 `read_user_status` 的用法):
          * `VALID/LENGTH/CRC/UPLOAD/LOOP/COMMIT_SEEN` 是**软件**账本 —— 固件里是
            `user_valid`/`user_bit_length`/`user_crc16`/`user_uploading`/`user_loop`/
            `user_commit_seen`。本移植里**没有** USER 上传路径(`SET USER …` 一律
            `ERR UNKNOWN CMD`), 所以它们就是构造出来的静止值。刻意**不从 PL 取**:
            `SET DIGMOD SOURCE USER` 的门禁读的也是这份影子, 两边取不同真源就会出现
            "回包说 VALID 1、切源却说 NOT READY" —— 正是本项目反复踩的"两处真源"。
          * `READY/DONE/UNDERFLOW/BANK` 是 **PL** 的 `gpio14_out` 位(`awg_top.v:510`),
            从 `axi_gpio_8` ch1 读回。固件里这四个也确实是读硬件的(`read_user_status`)。

        ⚠ `BANK` 位来自 ch1 控制字, 而板端 ch1 由 `DdrPp` 写(PP 的 bank)—— 于是它反映的是
        **DDR PP 的活动 bank**。固件同样只有这一个 `user_active_bank`, 所以这是忠实复刻,
        不是串了量。
        """
        hw = self._status_gpio(GPIO_USER_STATUS, 1)
        return ("USERBUF VALID %d LENGTH %d CRC 0000 BANK %d UPLOAD IDLE LOOP %s "
                "COMMIT_SEEN %d READY %d DONE %d UNDERFLOW %d"
                % (1 if self.user_valid else 0, self.user_bit_length,
                   1 if (hw & USER_STATUS_BANK) else 0,
                   "ON" if self.user_loop else "OFF",
                   1 if self.user_commit_seen else 0,
                   1 if (hw & USER_STATUS_READY) else 0,
                   1 if (hw & USER_STATUS_DONE) else 0,
                   1 if (hw & USER_STATUS_UNDERFLOW) else 0))

    def set_pp_status(self, snap):
        """环形线程把它的播放快照推过来, 供 `GET DDR PP` 回真值。

        **谁写谁读**: `DdrPp` 的写只发生在 RingThread 里, 串口桥那边**不能**去碰 `DdrPp`
        (跨线程 + 要读 DMA 寄存器)。所以由线程每帧推一份纯数据快照, 桥只格式化它 ——
        与面板读 `snapshot()` 是同一条路, 只是多了一个消费者。

        不推(或还没推过)时 `_ddr_pp_line` 回 P0 的基线金标, 与 `awg_bridge` 独立起
        (`make_ctl`) 时的行为一致。
        """
        with self.lock:
            self.pp_status = dict(snap) if snap else None

    def _ddr_pp_line(self):
        snap = self.pp_status
        if snap is None:
            return ("DDR PP ACTIVE 0 ACTIVE_BANK NONE NEXT_BANK NONE STATE IDLE ERROR 00 "
                    "BANK0 VALID 0 LENGTH 0 CRC 0000 BANK1 VALID 0 LENGTH 0 CRC 0000")
        # 格式串与 `awg_ddr_pp.status_line()` / 固件 `main.c:1977` 逐字一致。字段取自
        # **同一个快照** —— 分两次读会读到跨帧的 ACTIVE_BANK 与长度, 那是假象。
        # ⚠ `LENGTH` 是**字节数**(快照里已是 bank.bit_length // 8), 不是 bit_length。
        return ("DDR PP ACTIVE %d ACTIVE_BANK %s NEXT_BANK %s STATE %s ERROR %02X "
                "BANK0 VALID %d LENGTH %d CRC %04X "
                "BANK1 VALID %d LENGTH %d CRC %04X"
                % (snap.get("active", 0), snap.get("active_bank", "NONE"),
                   snap.get("next_bank", "NONE"), snap.get("state", "IDLE"),
                   snap.get("error_code", 0),
                   snap.get("bank0_valid", 0), snap.get("bank0_len", 0),
                   snap.get("bank0_crc", 0),
                   snap.get("bank1_valid", 0), snap.get("bank1_len", 0),
                   snap.get("bank1_crc", 0)))

    def _play_line(self):
        """`main.c:2052` 的格式串逐字对应 —— 全部八个字段取自 `axi_gpio_10` ch1, 即
        `awg_top.play_status_out`(播放 FSM 的 CDC 快照, 位域见 `awg_top.v:421`)。

        **这不是桩, 也不是 `ddr_pp_state`**: 固件里 `PLAY STATE` 来自 `playback_ctrl`
        状态机(`main.c:607` 的 `play_hw_status & 7`), 与 `main.c:408` 那个软件字符串
        `ddr_pp_state`(DDR PP 的 bank 路径)是**两个不同的量**。板端没有移植 PLAY 引擎,
        所以这八个字段读回的就是播放 FSM 真正的静止态 —— 如实报告, 不假装在播。

        镜像固件的守卫: 状态索引越界(≥7)回 `ERR PLAY STATUS`(`main.c:622`
        `play_state_valid`)。离线 `RecordingBackend` 读 0 → 状态 IDLE → 恰好是基线金标。
        """
        w = self._status_gpio(GPIO_PLAY_STATUS, 1)
        state = w & PLAY_STATUS_STATE_MASK
        if state >= len(PLAY_STATE_NAME):
            return "ERR PLAY STATUS"
        return ("PLAY STATE %s FRAME_DONE %d UNDERFLOW %d TRIGGER_SEEN %d "
                "STOP_PENDING %d ERROR_VALID %d REPEAT_REMAINING %d ERROR %02X"
                % (PLAY_STATE_NAME[state],
                   1 if (w & PLAY_STATUS_FRAME_DONE) else 0,
                   1 if (w & PLAY_STATUS_UNDERFLOW) else 0,
                   1 if (w & PLAY_STATUS_TRIGGER_SEEN) else 0,
                   1 if (w & PLAY_STATUS_STOP_PENDING) else 0,
                   1 if (w & PLAY_STATUS_ERROR_VALID) else 0,
                   (w >> PLAY_STATUS_REPEAT_SHIFT) & PLAY_STATUS_REPEAT_MASK,
                   (w >> PLAY_STATUS_ERROR_SHIFT) & 0xFF))

    def _dma_line(self):
        d = self.dma_status
        return ("DMA BASE %08X BUFFER %08X BYTES %d STATUS %08X STATE %s"
                % (d["base"], d["buffer"], d["bytes"], d["status"], d["state"]))

    def _net_line(self):
        n = self.net
        return ("NET GEM0 INIT %d LINK %s IP %s UDP %d"
                % (n["init"], n["link"], n["ip"], n["udp"]))

    def _ch_line(self, ci):
        """`main.c:2093` 的格式串逐字对应。"""
        s = self.ch[ci]
        return ("CH%d WAVE %s FREQ %d AMPL %d DUTY %d BASE %d "
                "H1 %d %d H2 %d %d H3 %d %d H4 %d %d "
                "MODE %s MODWAVE %s MODFREQ %d AMDEPTH %d FMDEV %d PMDEV %d "
                "PHASE %d PHASELINK %s"
                % (ci + 1, WAVE_NAME[s.wave], s.freq_hz, s.ampl_pct, s.pulse_duty,
                   s.base_pct, s.h_ord[0], s.h_ampl[0], s.h_ord[1], s.h_ampl[1],
                   s.h_ord[2], s.h_ampl[2], s.h_ord[3], s.h_ampl[3],
                   MODE_NAME[s.mode], WAVE_NAME[s.mwave], s.mfreq_01, s.am_depth,
                   s.fm_dev, s.pm_dev, s.phase_0_01, "ON" if s.phaselink else "OFF"))

    def _chan_line(self, cc):
        """`main.c:2060` 的格式串逐字对应(注意 TAP 增益按 9 位有符号解释)。"""
        c = self.chan[cc]
        imp_ampl = ((c.imp_word >> 24) & 0xFF)
        imp_ampl = imp_ampl - 256 if imp_ampl & 0x80 else imp_ampl
        return ("CH%d CHAN %s NOISE %d TONE %d %d IMPULSE %d %d %d "
                "TAP0 %d %d TAP1 %d %d TAP2 %d %d TAP3 %d %d"
                % (cc + 1, "ON" if c.word & CHAN_EN else "OFF",
                   (c.word >> 1) & 0x7F, c.tone_freq, (c.tone_word >> 24) & 0x7F,
                   c.imp_word & 0xFFFF, (c.imp_word >> 16) & 0xFF, imp_ampl,
                   sign9(c.tap[0] & 0x1FF), (c.tap[0] >> 9) & 0x1FF,
                   sign9(c.tap[1] & 0x1FF), (c.tap[1] >> 9) & 0x1FF,
                   sign9(c.tap[2] & 0x1FF), (c.tap[2] >> 9) & 0x1FF,
                   sign9(c.tap[3] & 0x1FF), (c.tap[3] >> 9) & 0x1FF))

    def _iq_line(self):
        """`main.c:2084` 格式串逐字对应(顺序: PHASE, FREQOFF, GAIN, PHASEERR)。"""
        q = self.iq
        return ("IQIMP %s PHASE %d FREQOFF %d GAIN %d PHASEERR %d"
                % ("ON" if q.on else "OFF", q.phase_deg100, q.freqoff_hz,
                   q.gain_q14, q.phaseerr_deg100))

    def _digmod_line(self):
        d = self.digmod
        return ("DIGMOD MODE %s SOURCE %s FILTER %s ROLLOFF %s SEED %d SYMRATE %d STATE %s"
                % (DIGMOD_NAME[d.mode], "USER" if d.source else "PRBS7",
                   DIGMOD_FILTER_NAME[d.filter], DIGMOD_ROLLOFF_NAME[d.rolloff],
                   d.seed, d.symrate_hz, "RUNNING" if d.mode else "STOPPED"))

    # ---- 命令入口 ----

    def handle(self, line):
        """收一行(已经是原始大小写), 返回回包文本(不含 CRLF)或 None(不回应)。

        整条命令在锁内跑完 —— 一条 SET 会更新影子状态**再**写好几个 GPIO 通道, 中途被
        另一个线程插进来就会写出半个状态(见类注释)。
        """
        if line is None:
            return None
        with self.lock:
            return self._handle(line)

    def _handle(self, line):
        line = line.upper()                      # 固件在收字节时就整体转大写(main.c:3076)
        toks = tokenize(line)
        if toks is None:
            return "ERR BAD VALUE"
        if not toks:
            return None                          # 空行不回应
        if toks[0] == "GET":
            return self._do_get(toks)
        if toks[0] == "SET":
            return self._do_set(toks)
        return "ERR UNKNOWN CMD"

    # ---- GET ----

    def _do_get(self, tok):
        n = len(tok)
        if n == 2 and tok[1] == "IQIMP":
            return self._iq_line()
        if n == 2 and tok[1] == "DIGMOD":
            return self._digmod_line()
        if n == 2 and tok[1] == "USERBUF":
            return self._userbuf_line()
        if n == 2 and tok[1] == "PLAY":
            return self._play_line()
        if n == 2 and tok[1] == "DMA":
            return self._dma_line()
        if n == 2 and tok[1] == "NET":
            return self._net_line()
        if n == 3 and tok[1] == "DDR" and tok[2] == "PP":
            return self._ddr_pp_line()
        if n == 3 and tok[1] in ("CH1", "CH2") and tok[2] == "CHAN":
            return self._chan_line(0 if tok[1][2] == "1" else 1)
        if n >= 2 and tok[1] in ("CH1", "CH2"):
            return self._ch_line(0 if tok[1][2] == "1" else 1)
        return "ERR BAD CHANNEL"

    # ---- SET ----

    def _do_set(self, tok):
        n = len(tok)
        if n >= 2 and tok[1] == "DIGMOD":
            return self._set_digmod(tok)
        if n >= 3 and tok[1] == "IQIMP":
            return self._set_iqimp(tok)
        if n >= 3 and tok[1] in ("CH1", "CH2") and tok[2] == "CHAN":
            return self._set_chan(tok)
        if n == 4 and tok[1] in ("CH1", "CH2") and tok[2] == "NOISE":
            return self._set_noise(tok)
        if n >= 3 and tok[1] in ("CH1", "CH2") and tok[2] == "TAP":
            return self._set_tap(tok)
        # 状态族(DDR/USER/PLAY/DMA)属于 P5: 明确报不支持, 而不是假装成功
        if n >= 2 and tok[1] in ("DDR", "USER", "PLAY", "DMA"):
            return "ERR UNKNOWN CMD"
        return self._set_ch(tok)

    def _set_ch(self, tok):
        """`main.c:2801` CHx 基础参数(含 HARM / PHASE / PHASELINK / BASE)。"""
        n = len(tok)
        if n < 4 or tok[1] not in ("CH1", "CH2"):
            return "ERR BAD CHANNEL"
        ci = 0 if tok[1][2] == "1" else 1
        s = self.ch[ci]
        field, arg = tok[2], tok[3]

        if field == "WAVE":
            if arg not in WAVE_NAME:
                return "ERR BAD VALUE"
            s.wave = WAVE_NAME.index(arg)
        elif field == "DUTY":
            v = parse_u32(arg)
            if v is None or v > 100:
                return "ERR DUTY OUT OF RANGE"
            s.pulse_duty = v
        elif field == "FREQ":
            v = parse_u32(arg)
            if v is None:
                return "ERR BAD VALUE"
            if v < FREQ_MIN or v > FREQ_MAX:
                return "ERR FREQ OUT OF RANGE"
            s.freq_hz = v
        elif field == "AMPL":
            v = parse_u32(arg)
            if v is None:
                return "ERR BAD VALUE"
            if v > AMPL_MAX:
                return "ERR AMPL OUT OF RANGE"
            s.ampl_pct = v
        elif field == "MODE":
            if arg not in MODE_NAME:
                return "ERR MODE OUT OF RANGE"
            s.mode = MODE_NAME.index(arg)
        elif field == "MODWAVE":
            if arg not in WAVE_NAME[:4]:
                return "ERR MODWAVE OUT OF RANGE"
            s.mwave = WAVE_NAME.index(arg)
        elif field == "MODFREQ":
            v = parse_u32(arg)
            if v is None or v < 1 or v > 500000:
                return "ERR MODFREQ OUT OF RANGE"
            s.mfreq_01 = v
        elif field == "AMDEPTH":
            v = parse_u32(arg)
            if v is None or v > 100:
                return "ERR AMDEPTH OUT OF RANGE"
            s.am_depth = v
        elif field == "FMDEV":
            v = parse_u32(arg)
            if v is None or v > 500000:
                return "ERR FMDEV OUT OF RANGE"
            s.fm_dev = v
        elif field == "PMDEV":
            v = parse_u32(arg)
            if v is None or v > 511:
                return "ERR PMDEV OUT OF RANGE"
            s.pm_dev = v
        elif field == "PHASE":
            if ci != 1:
                return "ERR BAD CHANNEL"           # 相位差只有 CH2(相对 CH1)
            v = parse_u32(arg)
            if v is None or v > 36000:
                return "ERR PHASE OUT OF RANGE"
            self.ch[1].phase_0_01 = v
        elif field == "PHASELINK":
            if ci != 1:
                return "ERR BAD CHANNEL"
            if arg == "ON":
                self.ch[1].phaselink = 1
            elif arg == "OFF":
                self.ch[1].phaselink = 0
            else:
                return "ERR BAD VALUE"
        elif field == "BASE":
            v = parse_u32(arg)
            if v is None:
                return "ERR BAD VALUE"
            if v > 100:
                return "ERR BASE OUT OF RANGE"
            s.base_pct = v
            normalize(s)
            self.write_all()
            return "OK"
        elif field.startswith("HARM"):
            return self._set_harm(tok, ci)
        else:
            return "ERR BAD FIELD"
        self.write_all()
        return "OK"

    def _set_harm(self, tok, ci):
        """`main.c:2885` 谐波: 支持 `HARM1` 拼接形式与 `HARM 1` 空格旧形式。"""
        n = len(tok)
        arg = tok[2]
        if len(arg) > 4:
            if arg[4] < "1" or arg[4] > "4":
                return "ERR BAD VALUE"
            hi = ord(arg[4]) - ord("0")
            if n < 5:
                return "ERR BAD VALUE"
            base_idx = 3
        else:
            hi = parse_u32(tok[3]) if n >= 4 else None
            if n < 6 or hi is None or hi < 1 or hi > 4:
                return "ERR BAD VALUE"
            base_idx = 4
        if n <= base_idx + 1:
            return "ERR BAD VALUE"
        what, raw = tok[base_idx], tok[base_idx + 1]
        s = self.ch[ci]
        if what == "ORDER":
            v = parse_u32(raw)
            if v is None:
                return "ERR BAD VALUE"
            if v != 0 and (v < 2 or v > 8):
                return "ERR HARM ORDER OUT OF RANGE"
            s.h_ord[hi - 1] = v
        elif what == "AMPL":
            v = parse_u32(raw)
            if v is None:
                return "ERR BAD VALUE"
            if v > 100:
                return "ERR HARM AMPL OUT OF RANGE"
            s.h_ampl[hi - 1] = v
        else:
            return "ERR BAD FIELD"
        normalize(s)
        self.write_all()
        return "OK"

    def _set_chan(self, tok):
        """`main.c:2707` CHAN ON|OFF|COMMIT|TONE|IMPULSE。"""
        n = len(tok)
        cc = 0 if tok[1][2] == "1" else 1
        c = self.chan[cc]
        if n == 4 and tok[3] == "ON":
            c.word |= CHAN_EN
        elif n == 4 and tok[3] == "OFF":
            c.word &= ~CHAN_EN
        elif n == 4 and tok[3] == "COMMIT":
            c.word ^= CHAN_COMMIT_EPOCH              # 拨动 epoch, 让 PL 重新排空抽头表
        elif n == 6 and tok[3] == "TONE":
            freq = parse_u32_limit(tok[4], 25000000)
            if freq is None:
                return "ERR TONE OUT OF RANGE"
            ampl = parse_u32(tok[5])
            if ampl is None or ampl > 100:
                return "ERR TONE OUT OF RANGE"
            c.tone_freq = freq
            c.tone_word = pack_tone(freq, ampl)
            # 开关在控制字 bit9: 幅度 0 等于关(输出恒 0, 两者等价)
            if ampl == 0:
                c.word &= ~CHAN_TONE_SW
            else:
                c.word |= CHAN_TONE_SW
            self.write_chan_ctrl()
            return "OK"
        elif n == 7 and tok[3] == "IMPULSE":
            period = parse_u32(tok[4])
            if period is None or period < 1 or period > 65535:
                return "ERR IMPULSE OUT OF RANGE"
            width = parse_u32(tok[5])
            if width is None or width < 1 or width > 255:
                return "ERR IMPULSE OUT OF RANGE"
            ampl = parse_long(tok[6])
            if ampl is None or ampl < -100 or ampl > 100:
                return "ERR IMPULSE OUT OF RANGE"
            c.imp_word = pack_impulse(period, width, ampl)
            if ampl == 0:
                c.word &= ~CHAN_IMP_SW
            else:
                c.word |= CHAN_IMP_SW
            self.write_chan_ctrl()
            return "OK"
        else:
            return "ERR BAD VALUE"
        self.write_chan_ctrl()
        return "OK"

    def _set_noise(self, tok):
        """`main.c:2764` NOISE 0..100 -> 控制字 [7:1]。**不在 CHAN 之下**。"""
        cc = 0 if tok[1][2] == "1" else 1
        v = parse_u32(tok[3])
        if v is None or v > 100:
            return "ERR NOISE OUT OF RANGE"
        c = self.chan[cc]
        c.word = (c.word & ~0xFE) | ((v & 0x7F) << 1)
        self.write_chan_ctrl()
        return "OK"

    def _set_tap(self, tok):
        """`main.c:2775` TAP k gain delay / TAP CLEAR(抽头写进 BRAM, 要 COMMIT 才生效)。"""
        n = len(tok)
        cc = 0 if tok[1][2] == "1" else 1
        c = self.chan[cc]
        if n == 4 and tok[3] == "CLEAR":
            c.tap = [0, 0, 0, 0]
            self.write_chan_tap(cc)
            return "OK"
        if n == 6:
            k = parse_u32(tok[3])
            if k is None or k > 3:
                return "ERR BAD VALUE"
            gain, delay = parse_long(tok[4]), parse_long(tok[5])
            if (gain is None or delay is None or gain < -256 or gain > 255
                    or delay < 0 or delay > 511):
                return "ERR TAP OUT OF RANGE"
            c.tap[k] = chan_tap_word(gain, delay)
            self.write_chan_tap(cc)
            return "OK"
        return "ERR BAD VALUE"

    def _set_iqimp(self, tok):
        """`main.c:2651` IQIMP —— 注意它**不复用** CHAN 的字段名, 越界文案也统一是 IQIMP。"""
        n = len(tok)
        q = self.iq
        if n == 3 and tok[2] == "ON":
            q.on = 1
            self.chan[0].word |= CHAN_IQ_EN
            self.write_chan_ctrl()
            return "OK"
        if n == 3 and tok[2] == "OFF":
            q.on = 0
            self.chan[0].word &= ~CHAN_IQ_EN
            self.write_chan_ctrl()
            return "OK"
        if n == 3 and tok[2] == "COMMIT":
            # 复用 CHAN COMMIT 的 epoch 位: chan_cfg 在任一通道 commit 时都会排空字 8-10
            self.chan[0].word ^= CHAN_COMMIT_EPOCH
            self.write_chan_ctrl()
            return "OK"
        if n == 4:
            field = tok[2]
            v = parse_long(tok[3])
            if field == "PHASE":
                if v is None or v < -18000 or v > 18000:
                    return "ERR IQIMP OUT OF RANGE"
                q.phase_deg100 = v
            elif field == "PHASEERR":
                if v is None or v < -18000 or v > 18000:
                    return "ERR IQIMP OUT OF RANGE"
                q.phaseerr_deg100 = v
            elif field == "GAIN":
                if v is None or v < -32768 or v > 32767:
                    return "ERR IQIMP OUT OF RANGE"
                q.gain_q14 = v
            elif field == "FREQOFF":
                # 硬件能到 ±25MHz, 协议收窄到 ±1MHz(正好等于 parse_long 的量值上限)
                if v is None or v < -1000000 or v > 1000000:
                    return "ERR IQIMP OUT OF RANGE"
                q.freqoff_hz = v
                q.fword = iq_fword(v)
            else:
                return "ERR IQIMP OUT OF RANGE"
            if field != "FREQOFF":
                q.coef_ab, q.coef_cd = iq_coeffs(q.phase_deg100, q.phaseerr_deg100,
                                                 q.gain_q14)
            self.write_chan_ctrl()
            return "OK"
        return "ERR IQIMP OUT OF RANGE"

    def _set_digmod(self, tok):
        """`main.c:2550` DIGMOD —— FILTER/ROLLOFF/SYMRATE/SEED/RESET 仅 STOPPED 时可改。"""
        n = len(tok)
        d = self.digmod
        if n == 4 and tok[2] == "SOURCE":
            if d.mode != 0:
                return "ERR DIGMOD RUNNING"
            if tok[3] == "PRBS7":
                d.source = 0
            elif tok[3] == "USER":
                if not self.user_valid:
                    return "ERR USERBUF NOT READY"
                d.source = 1
            else:
                return "ERR BAD VALUE"
        elif n == 4 and tok[2] == "SYMRATE":
            if d.mode != 0:
                return "ERR DIGMOD RUNNING"
            v = parse_u32(tok[3])
            if v is None:
                return "ERR BAD VALUE"
            if v < 10 or v > 1000000:
                return "ERR SYMRATE OUT OF RANGE"
            d.symrate_hz = v
        elif n == 4 and tok[2] == "SEED":
            if d.mode != 0:
                return "ERR DIGMOD RUNNING"
            v = parse_u32(tok[3])
            if v is None or v < 1 or v > 127:
                return "ERR SEED OUT OF RANGE"
            d.seed = v
        elif n == 3 and tok[2] == "RESET":
            if d.mode != 0:
                return "ERR DIGMOD RUNNING"
        elif n == 4 and tok[2] == "FILTER":
            if d.mode != 0:
                return "ERR DIGMOD RUNNING"
            if tok[3] not in DIGMOD_FILTER_NAME:
                return "ERR BAD VALUE"
            d.filter = DIGMOD_FILTER_NAME.index(tok[3])
        elif n == 4 and tok[2] == "ROLLOFF":
            if d.mode != 0:
                return "ERR DIGMOD RUNNING"
            # 0.2 -> 1, 0.35 -> 0, 0.5 -> 2(与 GPIO11 的位域含义一致, 不是索引)
            if tok[3] not in ("0.2", "0.35", "0.5"):
                return "ERR BAD VALUE"
            d.rolloff = {"0.35": 0, "0.2": 1, "0.5": 2}[tok[3]]
        elif n == 4 and tok[2] == "MODE":
            if tok[3] not in DIGMOD_NAME:
                return "ERR BAD VALUE"
            d.mode = DIGMOD_NAME.index(tok[3])
        else:
            return "ERR BAD VALUE"
        self.write_all()
        return "OK"
