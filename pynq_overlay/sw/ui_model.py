#!/usr/bin/env python3

"""面板 UI 的**纯逻辑**层: 草稿/设备回读分离 + 显式应用的事务模型。

这一层不碰显示框架、不碰 PL、不碰串口 —— 它只做三件事:

1. **校验**一份配置是否在协议允许的范围内(`validate`);
2. 把一次"应用"动作**冻结**成一串按序执行的命令(`begin`);
3. 拿设备回读**核对**这次事务是否真的生效(`finish`), 不生效/超时则失效重查(`fail`)。

移植自 `docs/reference/ui_model.c`(那套 C99 参考实现), 语义逐条对齐, 但按本工程的实际情况
做了三处**有意的偏离**, 每一处都在下面注明理由:

- **`DIGMOD_NAME` 含 `QAM4`**(固件有 6 个数字模式), 参考模型只有 5 个槽并声明
  "QAM4 归一为 QPSK"。这里在**读回**时做归一化(见 `normalize_digmod_mode`), 而**写**仍走
  固件的完整枚举 —— 面板不该露出一个协议里没有的槽, 也不该丢掉固件真实支持的模式。
- **`SET CHx CHAN COMMIT` 是异或 epoch 位**(`awg_ctl.CHAN_COMMIT_EPOCH`), 是**拨动**而不是
  置位。所以"提交抽头"这件事不能靠重发同一条命令来保证, 必须知道当前 epoch 才能确定拨动
  方向 —— `begin` 生成的计划里 COMMIT 只发一次(见 `_plan_channel`)。
- **回读校验的来源**: 参考设计假设后端能回读"设备实际状态"。本工程里 `AwgCtl.handle("GET")`
  读的是它**自己的影子状态**, 写进去再读出来必然一致, 那样的校验是同义反复。所以这一层
  把"回读"做成一个**可注入的接口**(`Snapshot` 由调用方提供), 由调用方决定它是影子状态、
  还是从 PL 寄存器解出来的真值。见 `awg_ctl.decode_*` 与模块 `ui_view`/`pynq_status` 的接线。
"""

import time

import rf_iq_signal as IQ
import awg_ctl as A
from rf_rx_analysis import IQ_SNAPSHOT_FRESHNESS_S, snapshot_is_fresh
from rf_rx_types import RxModelResult
from rf_tx_waveform import cycles_for_frequency

try:  # the single source, in every release that ships the teaching profile
    from rf_teaching_profile import ATTENUATION_MDB
except ImportError:  # pragma: no cover - only trees built under pre-profile contracts
    # Compatibility read for the software maps that predate the teaching profile
    # (cnn-v1 / rx-v1 / cnn-bpsk-v1).  Such a tree ships these CURRENT panel sources,
    # so the current domain value is the correct one; a release built from the
    # historical sources carries its own literal instead.  The equality with
    # rf_teaching_profile.ATTENUATION_MDB in the current tree is asserted by
    # test_rf_teaching_profile, so this pin cannot drift silently here.
    ATTENUATION_MDB = 46000

# ---------------- AD9361/RX 观察契约 ----------------

RX_INPUT_FS_HZ = 60000000
RX_DECIM_OPTIONS = (1, 2, 4, 8, 16)
RX_DEFAULT_LEN = 4096
RX_SOURCES = ("simulated", "digital_loopback", "analog_loopback", "antenna")
RX_STATES = ("UNAVAILABLE", "IDLE", "BUSY", "DONE", "ERROR")
TX_SUBPAGES = ("awg", "parameters", "transmit")
RX_SUBPAGES = ("overview", "sampling", "demod", "smart", "constellation", "environment")
RX_STATUS_KEYS = ("sample_valid", "carrier_sync", "timing_sync", "frame_sync", "crc")
DEVICES = ("ACM2108", "AD9361")

# 环境场景选择器的**真实支持范围**: 必须与产品场景模块 rf_experiment_scenarios.KINDS
# 完全一致(顺序也一致, 界面按此顺序排按钮)。 "off" 是 TRAIN/VAL 矩阵的能量地板负控
# (零波形, 不选中 TX 源), 由场景模块真实支持; 场景入口此前把它挡在门外, 于是 off
# 槽位加载出来的声明仍是 normal。 漂移由 pynq_overlay/sw/tests/test_rf_environment_ui.py
# 的相等测试守住。
ENVIRONMENT_SCENES = ("normal", "narrowband", "pulse", "compound", "off")

# ---------------- RX 采集控制面(Task 4) ----------------
#
# 采集控件的可用性**在运行时算出来**(daemon 广告 + 当前状态 + 已实现的 client), 不写死为开:
# 'capabilities.capture' 由 'UiApp._refresh_rx_capabilities' 从最近一份快照推导,
# 控件再读实例门禁 'UiApp.rx_capture_block_reason()'。
RX_CAPTURE_IDLE = "idle"
RX_CAPTURE_QUEUED = "queued"
RX_CAPTURE_RUNNING = "running"
RX_CAPTURE_CANCELLING = "cancelling"
RX_CAPTURE_CANCELLED = "cancelled"
RX_CAPTURE_DONE = "done"
RX_CAPTURE_ERROR = "error"
RX_CAPTURE_REFUSED = "refused"
# 在途(排队/进行/取消中): 控件此时是"取消", 不再提交第二次采集。
RX_CAPTURE_INFLIGHT = (RX_CAPTURE_QUEUED, RX_CAPTURE_RUNNING, RX_CAPTURE_CANCELLING)
# 这七个名字是**唯一一份**声明: `rf_status_worker` 从这里 import(它 publish
# 同一批字符串), 所以重命名任何一个都会在 import 处立刻炸出来, 而不是让面板的
# 在途判定(rx_capture_inflight)悄悄失效。
RX_CAPTURE_STATES = (RX_CAPTURE_IDLE, RX_CAPTURE_QUEUED, RX_CAPTURE_RUNNING,
                     RX_CAPTURE_CANCELLING, RX_CAPTURE_CANCELLED, RX_CAPTURE_DONE,
                     RX_CAPTURE_ERROR)


def rx_capture_state_vocabulary():
    """采集状态字符串的**唯一**出处(worker 从这里 import)。"""
    return RX_CAPTURE_STATES


# 回环的固定内部衰减(mdB): **唯一真源**是 rf_teaching_profile.ATTENUATION_MDB,
# client 的显式确认门与它同值; 面板不据此发射任何东西, 回环必须由操作员显式确认,
# 普通捕获按钮**不会**变成发射按钮。
RX_LOOPBACK_ATTENUATION_MDB = ATTENUATION_MDB
# 建议"填草稿"只允许改这些**本地草稿**字段; 别的键一律不落地(更不会写硬件)。
RX_DRAFT_ACTION_KEYS = ("decim", "rx_len", "expected_mode")

# 质量原因码: 每一项都必须在页面上**看得见**, 不是被悄悄丢掉。
RX_QUALITY_OVERLOAD = "overload"
RX_QUALITY_CAPTURING = "capturing"
RX_QUALITY_CANCELLING = "cancelling"
RX_QUALITY_EXPIRED = "expired"
RX_QUALITY_NO_ENERGY = "no_energy"
RX_QUALITY_CANCELLED = "cancelled"
RX_QUALITY_CLOSE_UNCONFIRMED = "close_unconfirmed"
RX_QUALITY_REFUSED = "refused"
# 当前分析已经不算"现在正在接收": 会话断开/过期/FAULT/daemon 换了发布。
RX_QUALITY_SESSION_LOST = "session_lost"
# 当前分析是在**旧配置**下采的: 一次成功下发并生效的 configure 让新配置成为"现在这套",
# 旧 IQ 不再代表当前接收。与 session_lost 分开: 这是本地作废, 既不是硬件故障,
# 也不是"关闭未确认"(spec §数据与控制链 / §接收分析与界面: 改配置使当前分析失效)。
RX_QUALITY_CONFIG_CHANGED = "config_changed"
RX_QUALITY_OK = "ok"

# daemon 生命周期里**算"活的"**的状态: 与 client 保留采集会话的判据同源
# (rf_status_client 只在 READY/RX_CAPTURING/TX_ACTIVE 下保留那个会话)。
RX_LIVE_DAEMON_STATES = ("READY", "RX_CAPTURING", "TX_ACTIVE")


# ---------------- RF 控制面(Task 5/6) ----------------

# 面板控制面**只认这两个 mode 字符串**; 任何别的值(包括 daemon 实际会发的
# `control_test`)一律按"未知 / 只读"处理, 绝不当作 control —— fail-closed 是默认。
# 判据见 docs/superpowers/plans/2026-09-29-merged-control-phase2.md 的 Task 5/6。
RF_READ_ONLY_MODES = ("status_only",)   # recognised mode; control availability comes from control_ops
RF_CONTROL_MODES = ("control",)         # recognised mode; same rule
RF_SURFACE_MODES = RF_READ_ONLY_MODES + RF_CONTROL_MODES
RF_SURFACE_STATUS_ONLY = RF_READ_ONLY_MODES[0]
RF_SURFACE_CONTROL = RF_CONTROL_MODES[0]

# 操作员在板上自行 provision 的令牌**路径**。这里没有令牌, 也没有默认值: 面板只提示
# 这个路径, 由操作员填入; 令牌永不进 release/manifest, 也永不进任何面板状态。
RF_CONTROL_TOKEN_PATH = "/etc/rf-control.token"

# 面板侧的控制生命周期(计划 Task 5 Step 2):
#   read_only -> armed -> initialized -> configured -> stopped
# 它只由**显式操作**推进 —— 读回只能证明"daemon 还在不在武装", 分不出 init 与 configure。
RF_CONTROL_STATES = ("read_only", "armed", "initialized", "configured", "stopped", "loaded")

# 每个面板状态允许的动作。与 daemon 实际广告的 `control_ops` 取交集后才暴露(见
# `_refresh_rf_control`): 面板没有的动作**不存在**, 不是"禁用"。
RF_CONTROL_ACTIONS = ("arm", "init", "configure", "stop", "disarm", "load", "start")
RF_CONTROL_FOR_STATE = {
    "read_only": ("arm",),
    "armed": ("init", "stop", "disarm"),
    "initialized": ("configure", "stop", "disarm"),
    "configured": ("load", "configure", "stop", "disarm"),
    "stopped": ("load", "configure", "stop", "disarm"),
    "loaded": ("start", "configure", "stop", "disarm"),
}
# 动作 -> daemon 广告的 op 名。
RF_CONTROL_OP = {
    "arm": "control.arm",
    "init": "rf.init",
    "configure": "rf.configure",
    "load": "tx.load",
    "start": "tx.start",
    "stop": "tx.stop",
    "disarm": "control.disarm",
}
# 动作成功后的面板状态。
RF_CONTROL_NEXT_STATE = {
    "arm": "armed",
    "init": "initialized",
    "configure": "configured",
    "load": "loaded",
    "start": "loaded",
    "stop": "stopped",
    "disarm": "read_only",
}
# arm/init/configure 必须显式确认; stop 永远不需要(判据 6: stop 必须始终可达)。
RF_CONTROL_CONFIRM = ("arm", "init", "configure", "start")

# ---- control-parameter contract: the daemon is the authority, this file is not --------
#
# Read out of rf_overlay/sw/board_app/rf_daemon.c rather than invented:
#   rf.init      : lo_hz (must be positive), sample_rate_hz (non-zero) and
#                  attenuation_mdB are ALL REQUIRED -- the daemon says so in as many
#                  words: "rf.init requires an explicit attenuation_mdB; omitting it
#                  ...".  There is no default and no fallback on either side.
#   rf.configure : lo_hz, sample_rate_hz, rx_bandwidth_hz, tx_bandwidth_hz and
#                  attenuation_mdB are all required; idelay_en / idelay_tap are
#                  optional.  attenuation_mdB above RF_SAFE_ATTENUATION_MDB is
#                  refused EARLY, before any state or hardware work.
RF_SAFE_ATTENUATION_MDB = 89750
RF_PROTOCOL_INT_MAX = 2147483647
RF_LO_MIN_HZ = 70000000
RF_LO_MAX_HZ = 2100000000
RF_SAMPLE_RATE_MAX_HZ = 61440000
RF_ACTION_PARAMS = {
    "init": ("lo_hz", "sample_rate_hz", "attenuation_mdB", "phy_mode_1r1t"),
    "configure": ("lo_hz", "sample_rate_hz", "rx_bandwidth_hz", "tx_bandwidth_hz",
                  "attenuation_mdB", "phy_mode_1r1t", "idelay_en", "idelay_tap"),
}
# Explicit mode and IDELAY prevent implicit 2R2T selection or delay clearing.
RF_ACTION_OPTIONAL_PARAMS = {"init": (), "configure": ()}
RF_POSITIVE_PARAMS = ("lo_hz", "sample_rate_hz", "rx_bandwidth_hz", "tx_bandwidth_hz")
TX_WAVEFORM_TYPES = ("sine", "cw", "square", "triangle", "sawtooth", "multitone", "chirp", "noise", "pulse")
TX_WAVEFORM_NAMES = ("正弦", "CW载波", "方波", "三角", "锯齿", "双音", "Chirp", "噪声", "脉冲")
TX_WAVEFORM_INDEX_LABELS = ("1=Sine", "2=CW", "3=Square", "4=Triangle", "5=Sawtooth", "6=MultiTone", "7=Chirp", "8=Noise", "9=Pulse")


def rf_waveform_name(value):
    """Return normalized waveform key from a name or legacy 1-based index, or None."""
    if isinstance(value, str):
        return value if value in TX_WAVEFORM_TYPES else None
    if type(value) is int and 1 <= value <= len(TX_WAVEFORM_TYPES):
        return TX_WAVEFORM_TYPES[value - 1]
    return None


def rf_waveform_fields(wf, advanced=False):
    """Fields the UI should expose for a waveform in normal or legacy/advanced mode."""
    if wf not in TX_WAVEFORM_TYPES:
        return ()
    fields = []
    if advanced:
        if wf != "cw" and wf != "noise":
            fields.append("tx_cycles")
        fields.append("tx_amplitude")
        if wf == "multitone":
            fields += ["tone2_cycles", "tone2_amplitude"]
        if wf == "chirp":
            fields.append("chirp_f1_cycles")
        if wf == "noise":
            fields.append("seed")
        if wf == "pulse":
            fields.append("duty_pct")
    else:
        if wf != "cw" and wf != "noise":
            fields.append("tx_frequency_hz")
        fields.append("tx_amplitude_pct")
        if wf == "multitone":
            fields += ["tone2_frequency_hz", "tone2_amplitude_pct"]
        if wf == "chirp":
            fields.append("chirp_f1_hz")
        if wf == "noise":
            fields.append("seed")
        if wf == "pulse":
            fields.append("duty_pct")
    return tuple(fields)


def rf_parameter_error(name, value):
    """None if `value` may be sent as `name`; otherwise why the daemon would refuse it.

    Strict on purpose: `bool` is not an integer here, because True would travel as 1 and
    silently mean "tap 1" or "attenuation 1 mdB" -- a typo the hardware must not absorb.
    """
    if name == "tx_waveform":
        return None if rf_waveform_name(value) is not None else \
            "tx_waveform must be a name in %s or an integer 1..%d" % (", ".join(TX_WAVEFORM_TYPES), len(TX_WAVEFORM_TYPES))
    if name in ("tx_amplitude", "tone2_amplitude"):
        return None if type(value) is int and 0 <= value <= 2047 else \
            "%s must be an integer 0..2047" % name
    if name in ("tx_cycles", "tone2_cycles", "chirp_f1_cycles"):
        return None if type(value) is int and 1 <= value <= 511 else \
            "%s must be an integer 1..511" % name
    if name in ("tx_frequency_hz", "tone2_frequency_hz", "chirp_f1_hz"):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return "%s must be a positive integer" % name
        return None
    if name in ("tx_amplitude_pct", "tone2_amplitude_pct", "duty_pct"):
        return None if type(value) is int and 0 <= value <= 100 else \
            "%s must be an integer 0..100" % name
    if name == "seed":
        return None if type(value) is int and 0 <= value <= 65535 else "seed must be an integer 0..65535"
    if name == "lo_hz":
        if isinstance(value, bool) or not isinstance(value, int) or not RF_LO_MIN_HZ <= value <= RF_LO_MAX_HZ:
            return "lo_hz must be an integer %d..%d" % (RF_LO_MIN_HZ, RF_LO_MAX_HZ)
        return None
    if name == "sample_rate_hz":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= RF_SAMPLE_RATE_MAX_HZ:
            return "sample_rate_hz must be an integer 1..%d" % RF_SAMPLE_RATE_MAX_HZ
        return None
    if name in ("rx_bandwidth_hz", "tx_bandwidth_hz"):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= RF_PROTOCOL_INT_MAX:
            return "%s must be an integer 1..%d" % (name, RF_PROTOCOL_INT_MAX)
        return None
    if name == "attenuation_mdB":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "attenuation_mdB must be a non-negative integer"
        if value > RF_SAFE_ATTENUATION_MDB:
            return ("attenuation_mdB must not exceed %d (the ceiling the daemon refuses "
                    "before it touches IDELAY or LO)" % RF_SAFE_ATTENUATION_MDB)
        return None
    if name in ("idelay_en", "phy_mode_1r1t"):
        return None if type(value) is int and value in (0, 1) \
            else "%s must be an integer 0 or 1" % name
    if name == "idelay_tap":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 31:
            return "idelay_tap must be an integer 0..31"
        return None
    return "unknown RF parameter %r" % (name,)


def rf_digital_parameter_error(name, value):
    """Validate one AD9361 RF digital draft parameter; return None or a reason."""
    if name == "amplitude":
        return None if type(value) is int and RF_DIGITAL_AMPLITUDE_MIN <= value <= RF_DIGITAL_AMPLITUDE_MAX \
            else "amplitude must be an integer %d..%d" % (RF_DIGITAL_AMPLITUDE_MIN, RF_DIGITAL_AMPLITUDE_MAX)
    if name == "mode":
        return None if value in RF_DIGITAL_MODES else "mode 必须为 BPSK/QPSK/QAM16（QAM64超出本次交付支持范围）"
    if name == "filter":
        return None if value in RF_DIGITAL_FILTERS else "filter must be RECT or RRC"
    if name == "rolloff":
        return None if value in RF_DIGITAL_ROLLOFFS else "rolloff must be 0.2/0.35/0.5"
    if name == "seed":
        return None if type(value) is int and 1 <= value <= 127 else "PRBS7 seed must be an integer 1..127"
    if name == "symrate_hz":
        name = "symbol_rate_hz"
    if name == "symbol_rate_hz":
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return "symbol_rate_hz must be a positive integer"
        return None
    return "unknown RF digital parameter %r" % (name,)


def rf_channel_parameter_error(name, value):
    """Validate one AD9361 RF channel draft parameter; return None or a reason."""
    if name == "enabled":
        return None if isinstance(value, bool) else "enabled must be a bool"
    if name == "noise_pct":
        return None if type(value) is int and RF_CHANNEL_NOISE_MIN <= value <= RF_CHANNEL_NOISE_MAX \
            else "noise_pct must be an integer 0..100"
    if name == "noise_seed":
        return None if type(value) is int and value >= 0 else "noise_seed must be a non-negative integer"
    if name == "gain_q8":
        return None if type(value) is int and RF_CHANNEL_GAIN_MIN <= value <= RF_CHANNEL_GAIN_MAX \
            else "gain_q8 must be an integer -256..255"
    if name == "delay_samples":
        return None if type(value) is int and RF_CHANNEL_DELAY_MIN <= value <= RF_CHANNEL_DELAY_MAX \
            else "delay_samples must be an integer 0..511"
    return "unknown RF channel parameter %r" % (name,)


def rf_digital_symrate_range(fs):
    """Return the finite-frame symbol rate range supported by a configured Fs."""
    return (fs // 32, fs // 2) if isinstance(fs, int) and fs > 0 else (0, 0)
def _copy_value(value):
    """复制后端发布的快照，避免 UI 持有后台线程的可变对象。"""
    if isinstance(value, dict):
        return {k: _copy_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_copy_value(v) for v in value)
    return value


class RxCapabilities(object):
    """设备服务已确认的 RX 能力；False 不代表失败，而是尚未接入。"""

    def __init__(self, capture=False, sync=False, demod=False, frames=False,
                 crc=False, ber=False, evm=False, model=False,
                 input_fs_hz=RX_INPUT_FS_HZ, decim=RX_DECIM_OPTIONS,
                 sample_format="I16/Q16 low12", reason="RF 服务未接入"):
        self.capture = bool(capture)
        self.sync = bool(sync)
        self.demod = bool(demod)
        self.frames = bool(frames)
        self.crc = bool(crc)
        self.ber = bool(ber)
        self.evm = bool(evm)
        self.model = bool(model)
        self.input_fs_hz = int(input_fs_hz)
        self.decim = tuple(int(x) for x in decim)
        self.sample_format = str(sample_format)
        self.reason = str(reason)

    @classmethod
    def unavailable(cls, reason="RF 服务未接入"):
        return cls(reason=reason)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return cls(**value.to_dict())
        value = value or {}
        return cls(
            capture=value.get("capture", value.get("rx_capture", False)),
            sync=value.get("sync", value.get("rx_sync", False)),
            demod=value.get("demod", value.get("rx_demod", False)),
            frames=value.get("frames", value.get("rx_frames", False)),
            crc=value.get("crc", False), ber=value.get("ber", False),
            evm=value.get("evm", False), model=value.get("model", False),
            input_fs_hz=value.get("input_fs_hz", RX_INPUT_FS_HZ),
            decim=value.get("decim", RX_DECIM_OPTIONS),
            sample_format=value.get("sample_format", "I16/Q16 low12"),
            reason=value.get("reason", "RF 服务未接入"))

    def to_dict(self):
        return {
            "capture": self.capture, "sync": self.sync, "demod": self.demod,
            "frames": self.frames, "crc": self.crc, "ber": self.ber,
            "evm": self.evm, "model": self.model,
            "input_fs_hz": self.input_fs_hz, "decim": self.decim,
            "sample_format": self.sample_format, "reason": self.reason,
        }

    def supports(self, name):
        return bool(getattr(self, name, False))


class RxSnapshot(object):
    """一次冻结的 RX 采集元数据，不包含大块 IQ 数据。"""

    def __init__(self, state="UNAVAILABLE", schema_version=1, session_id="",
                 capture_id="", source="", input_fs_hz=RX_INPUT_FS_HZ, decim=1,
                 output_fs_hz=RX_INPUT_FS_HZ, rx_len=RX_DEFAULT_LEN,
                 actual_len=0, channels=2, sample_format="I16/Q16 low12",
                 filter_profile="—", valid=False, overflow=False, stale=False,
                 expired=False, config_rev="", capture_path="", error="RF 服务未接入",
                 synthetic=False, started_at=0.0, finished_at=0.0):
        self.state = state if state in RX_STATES else "ERROR"
        self.schema_version = int(schema_version)
        self.session_id = str(session_id)
        self.capture_id = str(capture_id)
        self.source = str(source)
        self.input_fs_hz = int(input_fs_hz)
        self.decim = int(decim)
        self.output_fs_hz = int(output_fs_hz)
        self.rx_len = int(rx_len)
        self.actual_len = int(actual_len)
        self.channels = int(channels)
        self.sample_format = str(sample_format)
        self.filter_profile = str(filter_profile)
        self.valid = bool(valid)
        self.overflow = bool(overflow)
        self.stale = bool(stale)
        self.expired = bool(expired)
        self.config_rev = str(config_rev)
        self.capture_path = str(capture_path)
        self.error = str(error)
        self.synthetic = bool(synthetic)
        self.started_at = float(started_at)
        self.finished_at = float(finished_at)

    @classmethod
    def unavailable(cls, reason="RF 服务未接入"):
        return cls(error=reason)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return cls(**value.to_dict())
        value = value or {}
        data = {k: value[k] for k in (
            "state", "schema_version", "session_id", "capture_id", "source",
            "input_fs_hz", "decim", "output_fs_hz", "rx_len", "actual_len",
            "channels", "sample_format", "filter_profile", "valid", "overflow",
            "stale", "expired", "config_rev", "capture_path", "error",
            "synthetic", "started_at", "finished_at") if k in value}
        return cls(**data)

    def to_dict(self):
        return {k: getattr(self, k) for k in (
            "state", "schema_version", "session_id", "capture_id", "source",
            "input_fs_hz", "decim", "output_fs_hz", "rx_len", "actual_len",
            "channels", "sample_format", "filter_profile", "valid", "overflow",
            "stale", "expired", "config_rev", "capture_path", "error",
            "synthetic", "started_at", "finished_at")}

    @property
    def usable(self):
        return (self.state == "DONE" and self.valid and not self.overflow
                and not self.stale and not self.expired and not self.synthetic)


class RxFrame(object):
    """接收帧目录项；payload_preview 只是预览，不能冒充完整导出。"""

    def __init__(self, index=0, length=0, crc="—", payload_preview=b"",
                 valid_payload=False, session_id="", synthetic=False, error=""):
        self.index = int(index)
        self.length = int(length)
        self.crc = str(crc)
        self.payload_preview = bytes(payload_preview) if not isinstance(payload_preview, str) else payload_preview.encode("utf-8")
        self.valid_payload = bool(valid_payload)
        self.session_id = str(session_id)
        self.synthetic = bool(synthetic)
        self.error = str(error)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return cls(**value.to_dict())
        value = value or {}
        return cls(**{k: value[k] for k in (
            "index", "length", "crc", "payload_preview", "valid_payload",
            "session_id", "synthetic", "error") if k in value})

    def to_dict(self):
        return {k: getattr(self, k) for k in (
            "index", "length", "crc", "payload_preview", "valid_payload",
            "session_id", "synthetic", "error")}

    @property
    def exportable(self):
        return self.valid_payload and self.crc == "PASS" and not self.synthetic


class RxMetrics(object):
    """把采样、同步、CRC、BER、EVM 分开保存；不支持项保持 None。"""

    def __init__(self, statuses=None, cfo_hz=None, evm_pct=None, ber=None,
                 reason="未接入分析后端"):
        self.statuses = dict((k, None) for k in RX_STATUS_KEYS)
        if statuses:
            self.statuses.update((k, statuses[k]) for k in statuses if k in RX_STATUS_KEYS)
        self.cfo_hz = cfo_hz
        self.evm_pct = evm_pct
        self.ber = ber
        self.reason = str(reason)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return cls(value.statuses, value.cfo_hz, value.evm_pct, value.ber, value.reason)
        value = value or {}
        return cls(value.get("statuses"), value.get("cfo_hz"), value.get("evm_pct"),
                   value.get("ber"), value.get("reason", "未接入分析后端"))

    def to_dict(self):
        return {"statuses": dict(self.statuses), "cfo_hz": self.cfo_hz,
                "evm_pct": self.evm_pct, "ber": self.ber, "reason": self.reason}


class Recommendation(object):
    """模型/规则只能发布建议，不能携带可执行寄存器写入。"""

    def __init__(self, mode="UNKNOWN", confidence=None, reason="未接入真实数据",
                 source="N/A", stale=True, synthetic=False, action=None):
        self._frozen = False
        self.mode = str(mode)
        self.confidence = confidence
        self.reason = str(reason)
        self.source = str(source)
        self.stale = bool(stale)
        self.synthetic = bool(synthetic)
        self.action = _copy_value(action) if action is not None else None

    def __setattr__(self, name, value):
        if getattr(self, "_frozen", False):
            raise AttributeError("a measured model recommendation is immutable")
        object.__setattr__(self, name, value)

    @property
    def action(self):
        return _copy_value(self._action)

    @action.setter
    def action(self, value):
        self._action = _copy_value(value)

    def freeze(self):
        self._frozen = True
        return self

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return cls(value.mode, value.confidence, value.reason, value.source,
                       value.stale, value.synthetic, value.action)
        value = value or {}
        return cls(value.get("mode", "UNKNOWN"), value.get("confidence"),
                   value.get("reason", "未接入真实数据"), value.get("source", "N/A"),
                   value.get("stale", True), value.get("synthetic", False),
                   value.get("action"))

    def to_dict(self):
        return {"mode": self.mode, "confidence": self.confidence,
                "reason": self.reason, "source": self.source, "stale": self.stale,
                "synthetic": self.synthetic, "action": _copy_value(self.action)}

    @property
    def actionable(self):
        return bool(self.action) and self.mode != "UNKNOWN" and not self.stale \
            and not self.synthetic


def rx_output_fs(input_fs_hz, decim):
    if decim not in RX_DECIM_OPTIONS or input_fs_hz <= 0:
        return None
    return int(input_fs_hz) // int(decim)


def rx_draft_value_ok(name, value):
    """一个建议字段能不能写进本地草稿(只做范围校验, 不碰硬件)。

    越界的建议宁可整条丢掉: 草稿不变总比变成"下一次采集必被拒的 INVALID"好
    (decim=3 / rx_len=99999 正是这么进来的)。契约只有一份, 这里复用 ——
    不另立一套范围。
    """
    if name in ("decim", "rx_len"):
        if type(value) is not int:
            return False
        probe = {"input_fs_hz": RX_INPUT_FS_HZ, "decim": RX_DECIM_OPTIONS[0],
                 "rx_len": RX_DEFAULT_LEN}
        probe[name] = value
        return validate_rx_config(probe)[0] == OK
    if name == "symrate_hz":
        # RX symbol rate is independent of the low-rate ACM2108 AWG control range.
        return type(value) is int and 1 <= value <= RX_INPUT_FS_HZ // 2
    if name == "expected_mode":
        return isinstance(value, str) and value in MODES
    return False


def validate_rx_config(config):
    """校验采样契约；rx_len 是抽取后的复样点数。"""
    config = config or {}
    decim = config.get("decim", 1)
    input_fs = config.get("input_fs_hz", RX_INPUT_FS_HZ)
    rx_len = config.get("rx_len", RX_DEFAULT_LEN)
    if input_fs != RX_INPUT_FS_HZ:
        return INVALID, "输入采样率必须为 60 MSPS"
    if decim not in RX_DECIM_OPTIONS:
        return INVALID, "抽取倍率必须为 1/2/4/8/16"
    if not isinstance(rx_len, int) or not (1 <= rx_len <= 4096):
        return INVALID, "抽取后点数必须为 1..4096"
    return OK, ""


def rx_capture_block(app):
    """RX 捕获门禁；未接入能力时不产生任何硬件动作。"""
    if not app.connected:
        return DISCONNECTED
    if app.pending or app.rx_snapshot.state == "BUSY":
        return BUSY
    if not app.capabilities.capture:
        return UNSUPPORTED
    if app.running:
        return RUNNING
    result, _ = validate_rx_config(app.rx_draft)
    return result if result != OK else None


def rx_auto_block(app):
    """自动执行永远关闭，建议只允许进入草稿。"""
    if not app.capabilities.model:
        return UNSUPPORTED
    if not app.rx_snapshot.usable:
        return STALE
    if not app.recommendation.actionable:
        return UNSUPPORTED
    return UNSUPPORTED


# ---------------- RX 采集结果的落地与质量原因(Task 4) ----------------

def _telemetry_dict(value):
    """A plain telemetry copy of a frozen contract object (or a dict); nothing else."""
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else {}


def rx_capture_inflight(app):
    """Is a capture queued / running / being cancelled right now?"""
    capture = getattr(app, "rx_capture", None)
    capture = capture if isinstance(capture, dict) else {}
    return capture.get("state") in RX_CAPTURE_INFLIGHT


def rx_snapshot_from_capture(frame, analysis):
    """Map one validated capture + its analysis onto the page's RxSnapshot contract.

    PURE metadata mapping -- no socket, no FFT, no register -- so the drawing thread can
    do it.  An OVERLOAD (a code-rail clip) or a zero-energy record is NOT dropped: it
    lands with 'overflow'/'valid' set and a reason, which is what keeps it visible as a
    quality reason instead of silently disappearing.

    There is deliberately no Vpp, dBm, SNR, EVM or BER field: this path has no calibrated
    absolute level, and 'output_clipped' is only a CODE-RAIL event.
    """
    frame = _telemetry_dict(frame)
    summary = _telemetry_dict(analysis)
    samples = int(frame.get("samples") or summary.get("samples") or 0)
    decim = int(frame.get("decimation") or summary.get("decimation") or 1)
    input_fs = int(frame.get("sample_rate_hz") or summary.get("input_fs_hz")
                   or RX_INPUT_FS_HZ)
    output_fs = frame.get("output_fs_hz")
    if output_fs is None:
        # 复用唯一的抽取换算(它自己会挡住越界的 decim), 不在这里再算一遍。
        output_fs = rx_output_fs(input_fs, decim) or 0
    clipped = summary.get("output_clipped") is True
    no_energy = summary.get("no_energy") is True
    valid = bool(summary.get("valid") is True and not no_energy)
    moment = frame.get("completed_at_monotonic")
    if moment is None:
        moment = summary.get("completed_at_monotonic")
    fresh = snapshot_is_fresh(moment, time.monotonic())
    if clipped:
        error = "输出削顶 %d 点" % int(summary.get("clipping_count") or 0)
    elif summary.get("reason"):
        error = str(summary["reason"])
    elif not fresh:
        error = "快照已过期"
    else:
        error = ""
    return RxSnapshot(
        state="DONE",
        schema_version=int(summary.get("schema_version") or 1),
        session_id=str(frame.get("capture_id") or ""),
        capture_id=str(frame.get("capture_id") or ""),
        source=str(frame.get("op") or ""),
        input_fs_hz=input_fs, decim=decim, output_fs_hz=int(output_fs),
        rx_len=samples, actual_len=samples, channels=2,
        sample_format="I16/Q16 low12",
        filter_profile=str(summary.get("spectrum_window") or "—"),
        valid=valid, overflow=bool(clipped), stale=not fresh, expired=not fresh,
        config_rev=str(frame.get("config_revision", "")),
        capture_path=str(frame.get("op") or ""), error=error, synthetic=False,
        started_at=float(moment or 0.0), finished_at=float(moment or 0.0))


def _quality(code, text, severe):
    return {"code": code, "text": text, "severe": bool(severe)}


def rx_quality_reasons(app, now=None):
    """Every quality reason the CURRENT receive result must stay VISIBLE with.

    Overload (a code-rail clip) and expiry are not silently dropped: they are returned as
    reasons AND they keep the record out of 'usable', so no page can present a clipped or
    expired capture as a fresh measurement.  A cancelled or failed capture never has an
    analysis at all, so its reason is the only thing there is to show.
    """
    now = time.monotonic() if now is None else now
    reasons = []
    capture = getattr(app, "rx_capture", None)
    capture = capture if isinstance(capture, dict) else {}
    state = capture.get("state")
    if state in (RX_CAPTURE_QUEUED, RX_CAPTURE_RUNNING):
        reasons.append(_quality(RX_QUALITY_CAPTURING,
                                "质量: 采集中 (结果尚未落地)", False))
    elif state == RX_CAPTURE_CANCELLING:
        reasons.append(_quality(RX_QUALITY_CANCELLING,
                                "质量: 取消中 (等待采集请求返回)", True))
    elif state == RX_CAPTURE_CANCELLED or capture.get("cancelled"):
        if capture.get("close_confirmed") is False:
            reasons.append(_quality(RX_QUALITY_CLOSE_UNCONFIRMED,
                                    "质量: 关闭未确认 (不显示已停)", True))
        detail = capture.get("reason")
        reasons.append(_quality(RX_QUALITY_CANCELLED,
                                "质量: 已取消 (%s)" % detail if detail
                                else "质量: 已取消 (结果未落地)", True))
    elif state in (RX_CAPTURE_ERROR, RX_CAPTURE_REFUSED):
        reasons.append(_quality(RX_QUALITY_REFUSED,
                                "质量: %s" % (capture.get("reason") or "采集未完成"), True))
    # 当前分析已经被会话丢失/过期/FAULT 作废时, 原因**必须看得见** ——
    # 否则页面会退回"质量: 无异常", 而它正是被告诉不能再报的那一类结论。
    invalid = getattr(app, "rx_analysis_invalid", None)
    if isinstance(invalid, dict) and invalid.get("text"):
        reasons.append(_quality(invalid.get("code", RX_QUALITY_SESSION_LOST),
                                invalid["text"], True))
    snapshot = getattr(app, "rx_snapshot", None)
    if snapshot is not None and snapshot.state == "DONE":
        if snapshot.overflow:
            reasons.append(_quality(RX_QUALITY_OVERLOAD,
                                    "质量: 输出削顶 (码值到轨, 非前端线性结论)", True))
        if rx_analysis_field(app, "no_energy", False):
            reasons.append(_quality(RX_QUALITY_NO_ENERGY, "质量: 无能量 (无频谱)", True))
        if not _rx_snapshot_is_current(app, now):
            reasons.append(_quality(RX_QUALITY_EXPIRED,
                                    "质量: 快照已过期 (仅历史, 不证明当前接收)", True))
    if not reasons:
        reasons.append(_quality(RX_QUALITY_OK, "质量: 无异常", False))
    return tuple(reasons)


def rx_analysis_field(app, name, default=None):
    """One field of the frozen analysis, whether it arrived as an object or a mapping."""
    analysis = getattr(app, "rx_analysis", None)
    if isinstance(analysis, dict):
        return analysis.get(name, default)
    return getattr(analysis, name, default)


def _rx_snapshot_is_current(app, now=None):
    """Is the frozen analysis still CURRENT reception (default 30 s), or only history?

    Age is measured from the LOCAL monotonic completion moment, which is exactly what
    'rf_rx_analysis.snapshot_is_fresh' fixes; this module keeps no window of its own.
    """
    moment = rx_analysis_field(app, "completed_at_monotonic")
    if moment is None:
        summary = getattr(app, "rx_analysis_summary", None)
        moment = summary.get("completed_at_monotonic") if isinstance(summary, dict) else None
    return snapshot_is_fresh(moment, time.monotonic() if now is None else now,
                             limit=IQ_SNAPSHOT_FRESHNESS_S)

# ---------------- 枚举(应用内部表示, **不是**寄存器位编码) ----------------

# Reference v2 的七个 canonical 页面。守护进程状态屏不是独立一级页面：它在 overview
# 中继续由 `StatusScreen` 提供，避免丢失网络/写卡/长按关机这条安全路径。
PAGE_OVERVIEW, PAGE_TX, PAGE_RX, PAGE_DATA, PAGE_AI, PAGE_TOOLS, PAGE_SYSTEM = (
    "overview", "tx", "rx", "data", "ai", "tools", "system")

# 旧页面 ID 保留给既有调用方。AWG/DDR/RX/FUTURE 是 canonical 页的同值别名；
# digital/channel 是 tools 容器打开的 legacy 子页，不占一级导航。
PAGE_STATUS = PAGE_OVERVIEW
PAGE_AWG = PAGE_TX
PAGE_DIGITAL, PAGE_CHANNEL = "digital", "channel"
PAGE_DDR = PAGE_DATA
PAGE_FUTURE = PAGE_RX
PAGE_SMART = PAGE_AI
LEGACY_PAGES = (PAGE_DIGITAL, PAGE_CHANNEL)
PAGES = (PAGE_OVERVIEW, PAGE_TX, PAGE_RX, PAGE_DATA, PAGE_AI, PAGE_TOOLS,
         PAGE_SYSTEM)

# 数据页的文件目录(板端本地)。用户经 Jupyter/scp 把 `*.bin` 丢进来, 面板自己浏览排队 ——
# 这是"板子自洽"那条决策的落点: 不依赖主机 GUI。
DDR_DIR = "/home/xilinx/waveforms"
DDR_SUFFIXES = (".bin", ".dat")
DDR_PAGE_ROWS = 4          # 文件表一屏几行(没有滚动, 用上/下页翻)
                           # ⚠ 4 不是随便取的: 右栏卡片只有 302px 高, 表头 22 + 行高 34,
                           #   再加翻页 chip 34、动作 chip 40、底部提示 16 —— 5 行会溢出。

# 波形: 直接用固件的名字表, 面板不该自己另立一套
WAVES = A.WAVE_NAME
# 数字调制: **只露出参考设计的 5 个槽**, 不含 QAM4
# QAM64 已移出本次交付支持范围；QAM64 源码与历史证据保留，仅从可交付模式列表移除。
MODES = ("OFF", "BPSK", "QPSK", "QAM16")
FILTERS = ("RECT", "RRC")
ROLLOFFS = A.DIGMOD_ROLLOFF_NAME[:3]      # 0.35 / 0.2 / 0.5
SOURCES = ("PRBS7", "USER")

SYMRATE_MIN, SYMRATE_MAX = 10, 1000000
FREQ_MIN, FREQ_MAX = A.FREQ_MIN, A.FREQ_MAX
SEED_MIN, SEED_MAX = 1, 127
PCT_MIN, PCT_MAX = 0, 100
GAIN_MIN, GAIN_MAX = -256, 255
DELAY_MIN, DELAY_MAX = 0, 511
NOISE_MIN, NOISE_MAX = 0, 100
TAPS = 4

# AD9361 RF software digital/channel drafts (Chunk C).  These are independent of
# the ACM2108 `draft` and are routed only through the RF worker tx.load path.
RF_DIGITAL_MODES = ("BPSK", "QPSK", "QAM16")  # QAM64超出本次交付支持范围
RF_DIGITAL_FILTERS = ("RECT", "RRC")
RF_DIGITAL_ROLLOFFS = ("0.2", "0.35", "0.5")
RF_DIGITAL_AMPLITUDE_MIN, RF_DIGITAL_AMPLITUDE_MAX = 0, 2047
RF_CHANNEL_NOISE_MIN, RF_CHANNEL_NOISE_MAX = 0, 100
RF_CHANNEL_GAIN_MIN, RF_CHANNEL_GAIN_MAX = -256, 255
RF_CHANNEL_DELAY_MIN, RF_CHANNEL_DELAY_MAX = 0, 511
RF_CHANNEL_TAPS = 4
RF_SIGNAL_KINDS = ("waveform", "digital")

# ---------------- 结果码(照 ui_model.h 的 ui_result_t) ----------------

OK, INVALID, RUNNING, DISCONNECTED, STALE, BUSY, UNSUPPORTED, OVERFLOW, MISMATCH, IO_ERROR = (
    "OK", "INVALID", "RUNNING", "DISCONNECTED", "STALE", "BUSY", "UNSUPPORTED",
    "OVERFLOW", "MISMATCH", "IO_ERROR")

# ---- RX 采集(Task 4)自己的结果码 ------------------------------------------------
# 复用上面的通用码(连接/忙碌/不支持/过期/参数), 再加三条**只有采集路径**才有的:
# 生命周期不是 READY、操作员改了配置但还没重新下发、物理回环还没有已加载的 TX。
NOT_READY = "NOT_READY"
CONFIG_CHANGED = "CONFIG_CHANGED"
NOT_LOADED = "NOT_LOADED"

# 中文提示: 参考设计要求"把服务错误映射为中文提示"(AGENT_GUIDE.md 的实现步骤 2)。
# 界面直接显示这些串, 不显示裸的结果码。
RESULT_TEXT = {
    OK: "已生效",
    INVALID: "参数超出范围",
    RUNNING: "数字调制运行中, 请先关闭",
    DISCONNECTED: "设备未连接",
    STALE: "状态已过期, 请重新读取",
    BUSY: "有事务在途, 请稍候",
    UNSUPPORTED: "当前后端不支持该数据源",
    OVERFLOW: "命令过多, 无法一次提交",
    MISMATCH: "设备回读与下发不一致",
    IO_ERROR: "通信失败",
    NOT_READY: "daemon 状态不是 READY, 不能采集",
    CONFIG_CHANGED: "配置已改动但还没重新下发",
    NOT_LOADED: "物理回环需要当前连接上已加载的发射波形",
}

# 动作(照 ui_model.h 的 ui_action_t)
APPLY_AWG, APPLY_DIGITAL, APPLY_CHANNEL, START_DIGITAL, STOP_DIGITAL = (
    "apply_awg", "apply_digital", "apply_channel", "start_digital", "stop_digital")

MAX_COMMANDS = 12


# ---------------- 数据 ----------------

def _default_awg():
    return {"wave": "SINE", "freq_hz": 100000, "ampl_pct": 50, "duty_pct": 50}


def _default_chan():
    taps = [{"gain_q8": 255, "delay_samples": 0}] + [
        {"gain_q8": 0, "delay_samples": 0} for _ in range(TAPS - 1)]
    return {"enabled": False, "noise_pct": 0, "taps": taps}


def default_config():
    """出厂默认(与固件 main() 的初值一致, 也照参考设计的 ui_init)。"""
    return {
        "awg": [_default_awg(), _default_awg()],
        "dig": {"mode": "QPSK", "source": "PRBS7", "symrate_hz": 100000,
                "filter": "RRC", "rolloff": "0.35", "seed": 127},
        "chan": [_default_chan(), _default_chan()],
    }


def clone(cfg):
    return {
        "awg": [dict(c) for c in cfg["awg"]],
        "dig": dict(cfg["dig"]),
        "chan": [{"enabled": c["enabled"], "noise_pct": c["noise_pct"],
                  "taps": [dict(t) for t in c["taps"]]} for c in cfg["chan"]],
    }


def normalize_digmod_mode(name):
    """协议名 → 面板枚举。**QAM4 归一为 QPSK** —— 参考设计只有 5 个槽, 而固件把 QAM4 与
    QPSK 当作两种模式(实际映射表不同但星座相同)。面板不该露出一个参考设计里没有的槽。"""
    if name in ("QAM4", "QPSK"):
        return "QPSK"
    return name if name in MODES else None


def digmod_mode_to_protocol(name):
    """面板枚举 → 协议名。**QPSK 发出去的是 QPSK 而不是 QAM4** —— 固件两者都认, 发 QPSK
    更直接(它自己的名字表里就有)。"""
    return name if name in A.DIGMOD_NAME else None


# ---------------- 校验 ----------------

def validate(cfg):
    """返回 (OK, "") 或 (INVALID, "哪个字段不对")。照 ui_model.c 的 ui_validate。"""
    if not cfg:
        return INVALID, "配置为空"
    d = cfg["dig"]
    if d.get("mode") == "QAM64":
        # 旧配置显式拒绝；绝不自动回落到其他模式。
        return INVALID, "QAM64超出本次交付支持范围，请重新选择 BPSK/QPSK/QAM16"
    if d["mode"] not in MODES:
        return INVALID, "数字调制模式"
    if d["source"] not in SOURCES:
        return INVALID, "数据源"
    if d["filter"] not in FILTERS:
        return INVALID, "滤波类型"
    if d["rolloff"] not in ROLLOFFS:
        return INVALID, "滚降系数"
    if not (SEED_MIN <= d["seed"] <= SEED_MAX):
        return INVALID, "PRBS7 种子(1..127)"
    if not (SYMRATE_MIN <= d["symrate_hz"] <= SYMRATE_MAX):
        return INVALID, "符号率(10..1e6)"
    for ci in range(2):
        w = cfg["awg"][ci]
        if w["wave"] not in WAVES:
            return INVALID, "CH%d 波形" % (ci + 1)
        if not (FREQ_MIN <= w["freq_hz"] <= FREQ_MAX):
            return INVALID, "CH%d 频率(10..1e6)" % (ci + 1)
        if not (PCT_MIN <= w["ampl_pct"] <= PCT_MAX):
            return INVALID, "CH%d 幅度(0..100)" % (ci + 1)
        if not (PCT_MIN <= w["duty_pct"] <= PCT_MAX):
            return INVALID, "CH%d 占空比(0..100)" % (ci + 1)
        c = cfg["chan"][ci]
        if not (NOISE_MIN <= c["noise_pct"] <= NOISE_MAX):
            return INVALID, "CH%d 噪声等级(0..100)" % (ci + 1)
        for k, t in enumerate(c["taps"]):
            if not (GAIN_MIN <= t["gain_q8"] <= GAIN_MAX):
                return INVALID, "CH%d TAP%d 增益(-256..255)" % (ci + 1, k)
            if not (DELAY_MIN <= t["delay_samples"] <= DELAY_MAX):
                return INVALID, "CH%d TAP%d 延迟(0..511)" % (ci + 1, k)
    return OK, ""


# ---------------- 数据页的纯逻辑 ----------------

def ddr_pages(n_files, rows=DDR_PAGE_ROWS):
    """文件表要几页。至少 1 页 —— 0 页会让页码显示成 "1/0"。"""
    return max(1, (n_files + rows - 1) // rows)


def ddr_window(files, page, rows=DDR_PAGE_ROWS):
    """文件表当前窗口的 `(起点下标, [(name, size), ...])`。

    **没有滚动**, 所以窗口是切片而不是滚动偏移 —— 页码越界时夹回最后一页(删了文件之后
    页码可能越界, 夹回去比显示空白好)。
    """
    page = max(0, min(page, ddr_pages(len(files), rows) - 1))
    start = page * rows
    return start, list(files[start:start + rows])


def ddr_start_block(app):
    """数据页"启动"的放行条件, 返回 None 表示可以起, 否则返回中文原因。

    镜像主机侧的判据(`widgets.py` 的 `_update_ddr_stream_controls`): 要有 DDR 线程、
    要连上设备、**数字调制必须开着**、不能已经在播、并且**至少有一个 bank 已经装好数据**。

    ⚠ 队列非空**不算数** —— 这一条与直觉相反, 但它是硬约束: 装填一个 bank 要求
      `digmod_mode == 0`(`DdrPp.feed_file` 的门禁: 数据源还在 AWG 手里时写 bank 是 BUSY),
      而起播要求非 0。两者**不可能同时成立**, 所以"起播"这条命令里装不进任何东西 ——
      队列里排着的文件必须**先**用「装填」放进去(此时 MODE OFF), 再起播。
      把"队列非空"算作可播, 结果就是"点了启动, 状态直接变成 ERROR"。

    ⚠ MODE OFF 也**不许**起播 —— `DdrPp.start()` 的门禁(`awg_ddr_pp.py:312` 的
      `self._digmod_mode() == 0`)会把状态直接置成 `ERROR / PP_START_FAILED`。这一条与
      「装填要求 MODE OFF」是一对: 装填完必须**打开调制**才点得动启动。不查这一条,
      用户看到的就是"装填好了、点启动、状态变 ERROR"。
    """
    pp = app.pp or {}
    if not pp.get("present"):
        return "DDR 不可用"
    if not app.connected:
        return "设备未连接"
    if pp.get("active"):
        return "已在播放"
    if app.actual_mode == "OFF":
        return "请先打开数字调制"
    if not (pp.get("bank0_len") or pp.get("bank1_len")):
        return "请先装填一个文件"
    return None


def ddr_load_block(app):
    """数据页"装填"的放行条件, 返回 None 表示可以装, 否则返回中文原因。

    装填 = 把一个文件写进 bank 并锁存, **要求 MODE OFF**(见 `ddr_start_block` 的注释)。
    这是唯一一条会被 `digmod_mode` 挡住的数据页动作 —— 面板必须把这个原因说出来,
    否则用户看到的就是"点了装填, 什么也没发生"。
    """
    pp = app.pp or {}
    if not pp.get("present"):
        return "DDR 不可用"
    if not app.connected:
        return "设备未连接"
    if pp.get("active"):
        return "播放中不能装填"
    if app.actual_mode != "OFF":
        return "请先关闭数字调制"
    if not (app.ddr_queue or app.ddr_selected is not None):
        return "请先入队一个文件"
    return None

class CommandPlan(object):
    """一次事务的**冻结**快照: 目标配置 + 按序命令 + 期望的运行时模式。

    命令是"发完就不改"的 —— 事务在途时用户还能继续编辑草稿, 但计划必须自洽。
    """

    def __init__(self, action, channel, target, commands, expected_mode):
        self.action = action
        self.device = "ACM2108"          # legacy command plans always address this peripheral
        self.channel = channel
        self.target = target                 # 冻结的配置副本
        self.commands = list(commands)       # 含行尾
        self.expected_mode = expected_mode   # 完成后设备应处的 DIGMOD 模式

    def __len__(self):
        return len(self.commands)


def _plan_awg(cfg, ci):
    w = cfg["awg"][ci]
    n = ci + 1
    return [
        "SET CH%d WAVE %s\n" % (n, w["wave"]),
        "SET CH%d FREQ %d\n" % (n, w["freq_hz"]),
        "SET CH%d AMPL %d\n" % (n, w["ampl_pct"]),
        "SET CH%d DUTY %d\n" % (n, w["duty_pct"]),
        "GET CH%d\n" % n,
    ]


def _plan_digital(cfg, start):
    d = cfg["dig"]
    cmds = [
        "SET DIGMOD SOURCE %s\n" % d["source"],
        "SET DIGMOD FILTER %s\n" % d["filter"],
        "SET DIGMOD ROLLOFF %s\n" % d["rolloff"],
        "SET DIGMOD SEED %d\n" % d["seed"],
        "SET DIGMOD SYMRATE %d\n" % d["symrate_hz"],
    ]
    if start:
        # **启动事务最后才发 MODE** —— 前面的参数都要求 MODE OFF 才改得动
        cmds.append("SET DIGMOD MODE %s\n" % digmod_mode_to_protocol(d["mode"]))
    cmds.append("GET DIGMOD\n")
    return cmds


def _plan_channel(cfg, ci):
    c = cfg["chan"][ci]
    n = ci + 1
    cmds = ["SET CH%d TAP %d %d %d\n" % (n, k, t["gain_q8"], t["delay_samples"])
            for k, t in enumerate(c["taps"])]
    # COMMIT 是**拨动 epoch 位**, 发一次就换一次装载; 4 个 TAP 之后必须发, 且只发一次
    cmds.append("SET CH%d CHAN COMMIT\n" % n)
    cmds.append("SET CH%d NOISE %d\n" % (n, c["noise_pct"]))
    cmds.append("SET CH%d CHAN %s\n" % (n, "ON" if c["enabled"] else "OFF"))
    cmds.append("GET CH%d CHAN\n" % n)
    return cmds


class UiApp(object):
    """面板的**全部可变状态**。UI 线程独占它(照 AGENT_GUIDE 的线程约定)。

    `draft` 是操作者意图(可编辑、随时改), `observed` 是设备回读(只在 finish 时整体替换)。
    **后台线程绝不能写 draft** —— 否则用户还没应用就被遥测覆盖掉了。
    """

    def __init__(self, config=None):
        self.page = PAGE_DIGITAL
        self.selected_device = "ACM2108"
        self.selected_channel = 0
        self.draft = clone(config or default_config())
        self.observed = clone(self.draft)     # 首次同步前先当作"已知默认"
        self.connected = False
        self.fresh = False
        self.actual_mode = "OFF"              # 设备的 DIGMOD 实际模式(来自 GET DIGMOD)
        self.pending = False
        self.in_flight = None
        self.last_result = OK
        self.last_message = ""
        self.log = []                         # 命令记录(系统页显示)
        self.caps = {"ad9361": False, "receiver": False, "model": False,
                     "user_upload": False}
        # ---- RF 观察契约 ----
        # 这些对象只由主循环接收后台发布的完整快照替换，UI 不持有 RX 缓冲，也不写 RF 寄存器。
        self.capabilities = RxCapabilities.unavailable()
        self.rx_draft = {"input_fs_hz": RX_INPUT_FS_HZ, "decim": 1,
                         "rx_len": RX_DEFAULT_LEN, "filter_profile": "N/A",
                         "expected_mode": "BPSK", "symrate_hz": 3000000,
                         "shaping": "RECT", "reference_enabled": False,
                         "reference_seed": 1}
        self.rx_demod_revision = 0
        self.rx_demod_result = None
        self.rx_demod_status = {"available": False, "algorithm_version": None,
                                "algorithm_sha256": None, "release_id": None,
                                "qpsk_available": False,
                                "qpsk_algorithm_version": None,
                                "qpsk_algorithm_sha256": None,
                                "rrc_available": False,
                                "rrc_algorithm_version": None,
                                "rrc_algorithm_sha256": None,
                                "qam16_available": False,
                                "qam16_algorithm_version": None,
                                "qam16_algorithm_sha256": None,
                                "qam64_available": False,
                                "qam64_algorithm_version": None,
                                "qam64_algorithm_sha256": None,
                                "reason": "未接入解调"}
        self._rx_demod_reason = "WAITING_CAPTURE"
        self.rx_snapshot = RxSnapshot.unavailable()
        self.rx_frames = ()
        self.rx_metrics = RxMetrics()
        self.recommendation = Recommendation()
        self.rx_model_result = None
        self._rx_model_recommendation = None
        self.rx_model_status = {"available": False, "production": False,
                                "reason": "无模型", "release_id": None,
                                "model_sha256": None, "model_version": None,
                                "preprocessing_version": None}
        # ---- RX 采集控制面(Task 4) ----
        # 'rx_analysis' 是**不可变的冻结分析对象**(唯一持有频谱/IQ 预览的地方), 绘制线程
        # 只读它; 'rx_analysis_summary' 是同一份结果的纯标量副本地图, 只用来进签名与显示。
        # 历史的当前状态分开: 过期/被清掉的快照进 'rx_history', 当前锁定的那份才是
        # "现在正在接收"的唯一候选(见 rx_snapshot_is_current)。
        self.rx_analysis = None
        self.rx_analysis_summary = None
        self.rx_history = []
        # 当前分析是**哪一次会话**取得的: 落地时印上帧里携带的 connection_generation
        # 与 release 身份。`rx_analysis_invalid` 非 None = 这份分析已经不算"现在正在
        # 接收", 且页面必须把这个原因画出来(记录已经推进 rx_history)。
        self.rx_analysis_generation = None
        self.rx_analysis_release_id = None
        # 这份分析是在哪一个**已生效配置代次**下采的(落地时印上当时的
        # _rx_config_revision)。它与当前 _rx_config_revision 不等 = 操作员此后
        # 成功下发过新配置, 所以这份 IQ 属于旧参数, 不再是"现在正在接收"。
        self.rx_analysis_config_revision = None
        self.rx_analysis_invalid = None
        self._rx_session_confirmed = False
        self._rx_config_revision = 0
        self.rx_capture = {
            "state": RX_CAPTURE_IDLE,
            "physical": False,
            "capture_id": None,
            "code": None,
            "reason": "",
            "close_confirmed": None,
            "cancelled": False,
            "pending_action": None,
            # None = 还没有一次待落地的提交; 只有提交过才谈得上"提交之后配置又变了"。
            "config_revision": None,
            # worker 发布的最后一个状态序号; 本地终态(refused/配置已变)靠它
            # 判断"worker 到底有没有前进", 见 set_rx_capture_status。
            "worker_seq": None,
            "local_terminal": False,
        }
        self.rx_subpage = "overview"
        self.teaching_status = dict(available=False, release_id=None, module_sha256={},
                                    reason='教学模块未接入')
        self.teaching_message = ''
        self.rx_constellation = None
        self.rx_environment = None
        self.environment_status = dict(available=False, release_id=None, module_sha256={},
            baseline_sha256=None, environment_version=None, reason='环境诊断未接入')
        self._rx_environment_reason = '等待真实采集'
        self.environment_scene = 'normal'
        # HEADROOM-1 is a research plan, not a capture mode: it is never chosen
        # by rendering, by a status poll, or by a session that is merely live.
        # `available` therefore needs BOTH the installed release to carry the
        # research module AND the running daemon to announce the contract -- a
        # release can be installed while the daemon is still an older build.
        self.headroom_status = dict(available=False, release_id=None,
            module_sha256={}, daemon_version=None, protocol_minor=None,
            live=False, reason='余量研究未接入')
        self.headroom_receipt = None
        # A study is an explicit, two-tap, operator action on hardware: the first
        # tap only registers the intent, the second one runs it.  Nothing here
        # starts a study by itself.
        self.headroom_pending = False
        self.headroom_running = False
        self._rf_loaded_scenario = None
        self._rx_submitted_scenario = None
        self._rx_record_scenario = None
        self.rx_raw_frame = None
        self.rx_record_history = None
        self._rx_submitted_rf_config = {}
        self._rx_record_rf_config = {}
        self.rx_save_status = dict(state='idle',capture_id=None,reason='')
        self._rx_constellation_reason = '等待真实快照'
        self.tx_subpage = "awg"
        self.rx_status_message = "RF 服务未接入"
        self.rf_status = {
            "online": False, "stale": False,
            "error": "RF daemon 未连接", "received_at_monotonic": None,
            "mode": None, "daemon_state": None, "core": None,
            "pl_status": {"io_ok": False, "sampled_at_monotonic_ns": None,
                          "tx": {"source_selected": None, "length_words": None,
                                 "position_words": None},
                          "rx": {"armed": None, "done": None,
                                 "position_words": None}},
        }
        # ---- RF 控制面(Task 5/6) ----
        # `rf_status` 是**只读遥测**; `rf_control` 才是"面板允许暴露哪些控制动作"。
        # 两者分开: 一份不可信的 daemon 回复不能因为"在线"就换来控制按钮。
        self._rf_control_mode = None
        self._rf_control_online = False
        self._rf_control_armed = False
        self._rf_control_ops = ()
        # Operator-supplied control parameters.  Deliberately EMPTY: the panel
        # must not carry a default that could configure real TX hardware with a
        # number nobody chose (rf_daemon.c has no default either).
        self._rf_parameters = {}
        self._rf_wave_advanced = False
        self._rf_applied_parameters = {}
        self._rf_tx_loaded = False
        # AD9361 software RF digital/channel drafts (Chunk C).
        self._rf_digital_draft = {
            "mode": "QPSK", "filter": "RRC", "rolloff": "0.35",
            "seed": 127, "symbol_rate_hz": 2000000, "amplitude": 2047,
        }
        self._rf_channel_draft = {
            "enabled": False, "noise_pct": 0, "noise_seed": 0,
            "taps": [
                {"gain_q8": 255, "delay_samples": 0},
                {"gain_q8": 0, "delay_samples": 0},
                {"gain_q8": 0, "delay_samples": 0},
                {"gain_q8": 0, "delay_samples": 0},
            ],
        }
        self._rf_signal_draft_kind = "waveform"
        self._rf_loaded_kind = None
        self._rf_include_impairment = False
        self._rf_tx_loaded = False
        self._rf_inflight_action = None
        self._rf_inflight_stop = False
        self._rf_control_state = "read_only"
        self.rf_control = {
            "surface": "unknown",       # status_only / control / unknown
            "state": "read_only",
            "actions": (),              # 面板当前真的暴露的动作(空 = 一个都没有)
            "stop_reachable": False,
            "confirm_actions": RF_CONTROL_CONFIRM,
            "token_path_hint": RF_CONTROL_TOKEN_PATH,
            "inflight_action": None,
            "pending_action": None,     # 等确认的动作(UI 用)
            "last_action": None,
            "last_code": None,          # daemon 的码, 不是泛化消息
            "last_error": None,
            "reason": "RF 控制面未接入",
        }
        # 采样/发射默认互斥；当前未接入 RF 服务，因此不会自行改变 actual_mode。
        self.rf_role = "IDLE"
        #
        # `ddr_files` 是**后台线程的目录列表**拷进来的([(name, size)]), 由 `pump()` 落地;
        # `ddr_queue` 是用户点的入队顺序; `pp` 是 RingThread 的快照(空字典 = 环形线程缺席,
        # 界面据此显示"DDR 不可用", 而不是一份全 0 的假状态 —— 见 `ring_thread` 的
        # `present` 字段)。
        self.ddr_files = []
        self.ddr_queue = []
        self.ddr_ring = False
        self.ddr_page = 0                     # 文件表的窗口页码(没有滚动, 靠翻页)
        self.ddr_selected = None              # 文件表里点中的行(窗口内下标)
        self.ddr_status = ""                  # 一次操作的短提示(入队/移除/刷新结果)
        self.pp = {}

    # ---- 派生状态(给界面用) ----

    def select_device(self, device):
        """Select an editing target without changing outputs or sending commands."""
        if device not in DEVICES:
            return "未知设备"
        if device == self.selected_device:
            return None
        if self.pending or self._rf_inflight_action is not None:
            return "请等待当前设备事务完成"
        self.rf_control_cancel()
        self._invalidate_rx_demod("DEVICE_CHANGED")
        self.selected_device = device
        self.tx_subpage = "awg" if device == "ACM2108" else "parameters"
        return None

    @property
    def running(self):
        return self.actual_mode != "OFF"

    @property
    def dirty(self):
        """草稿与设备**有实质差异**。

        ⚠ 与 `draft != observed` **不是一回事**: `dig.mode` 是"下次要跑哪个模式"的**选择**,
        设备在 MODE OFF 时它恒是 OFF, 所以两边必然不同 —— 把它算进差异的话, 面板一开机就
        显示"有待应用参数", 而用户什么都没改(板上实测: 底栏一直挂着这个提示)。
        参考设计同样把 `actual_mode` 与 `config.dig.mode` 分开放(`ui_model.h`:
        `ui_mode_t mode; /* selected mode; OFF is actual runtime state */`)。
        """
        if self.draft["awg"] != self.observed["awg"] \
                or self.draft["chan"] != self.observed["chan"]:
            return True
        return {k: v for k, v in self.draft["dig"].items() if k != "mode"} != \
            {k: v for k, v in self.observed["dig"].items() if k != "mode"}

    def set_observed(self, cfg, actual_mode=None, connected=True, resync_draft=True):
        """设备回读到达: 整体替换 observed。

        `resync_draft=True` 时**同时**把 observed 同步进 draft。理由: 本工程面板是
        "看到设备现状再改", 若首次同步(或串口侧改了参数)后 draft 仍是出厂默认, 用户一点
        应用就会把设备推回默认 —— 那是更糟的意外。(参考设计的 draft 完全不被遥测覆盖,
        这里是有意的偏离。)

        **回读不符时必须传 `resync_draft=False`**: 那种情况下设备的值是"没接受用户的改动",
        把它同步进 draft 等于**静默丢掉用户刚输入的值**, 用户想重试都没有原值了。

        ⚠ **`dig.mode` 是唯一不同步的字段**: 它是"下次要跑哪个模式"的选择, 不是设备状态
        (设备那侧对应的是 `actual_mode`)。同步它会把用户的**选择**抹成设备当前的 OFF,
        于是底栏点"启动数字调制"发出去的 START 计划里 `SET DIGMOD MODE OFF` —— 板上的
        现象就是"点了启动, 结果 `STALE`, 什么也没发生"(验收脚本抓到)。
        参考设计把两者分得很清楚(`ui_model.h`: `mode /* selected mode; OFF is actual
        runtime state */`, 且 `draft /* never overwritten by telemetry polling */`)。
        """
        self.observed = clone(cfg)
        if actual_mode is not None:
            self.actual_mode = normalize_digmod_mode(actual_mode) or "OFF"
        self.connected = bool(connected)
        self.fresh = True
        if resync_draft and not self.pending:
            selected_mode = self.draft["dig"]["mode"]
            self.draft = clone(cfg)
            self.draft["dig"]["mode"] = selected_mode

    def set_capabilities(self, value):
        """替换设备服务能力快照，并同步兼容旧的 caps 标志。"""
        self.capabilities = RxCapabilities.from_value(value)
        self.caps.update({
            "ad9361": self.capabilities.capture,
            "receiver": self.capabilities.capture,
            "model": self.capabilities.model,
        })
        self.rx_status_message = self.capabilities.reason

    def set_rf_status(self, snapshot):
        """落地一份 daemon 快照, 并据此重算只读遥测与**面板控制面**。

        只读门改看已知 mode 与 capabilities.ops(只读 op 集); core 不再参与该门(修订依据见交付记录 §41)。
        `control` 模式是目标态(计划 Task 5/6): 它可以 core=True。控制面另算
        (`_align_rf_control_state`), 绝不因为"收到了回复"就放行控制。
        """
        was_transmitting = self.rf_transmitting()
        source = snapshot.to_dict() if hasattr(snapshot, "to_dict") else snapshot
        if not isinstance(source, dict):
            source = {"error": "RF status malformed"}
        source = _copy_value(source)
        online = source.get("online") is True
        stale = source.get("stale") is True
        mode = source.get("mode")
        if online and mode not in RF_SURFACE_MODES:
            online = False
            source["error"] = "RF daemon is not a recognised mode"
        elif online and source.get("core") is not None \
                and not isinstance(source.get("core"), bool):
            online = False
            source["error"] = "RF daemon reported a non-boolean core state"
        pl = source.get("pl_status")
        pl = pl if isinstance(pl, dict) else {}
        tx = pl.get("tx") if isinstance(pl.get("tx"), dict) else {}
        rx = pl.get("rx") if isinstance(pl.get("rx"), dict) else {}
        known = online and not stale
        armed = source.get("armed") is True
        raw_ops = source.get("control_ops")
        if isinstance(raw_ops, (list, tuple)):
            control_ops = tuple(op for op in raw_ops if isinstance(op, str))
        else:
            control_ops = ()
        self.rf_status = {
            "tx_session_ready": source.get("tx_session_ready") is True,
            "online": online, "stale": stale,
            "error": source.get("error"),
            "received_at_monotonic": source.get("received_at_monotonic"),
            "mode": mode,
            "daemon_state": source.get("daemon_state"),
            "core": source.get("core"),
            "pending": source.get("pending") is True,
            "armed": armed,
            "control_ops": control_ops,
            # release 身份与"采集会话是否仍被保留": 当前分析是否还算
            # "现在正在接收"就看这两个(见 _rx_session_loss_reason)。
            "release_id": source.get("release_id"),
            "rx_session_ready": source.get("rx_session_ready") is True,
            "pl_status": {
                "io_ok": pl.get("io_ok") is True if known else False,
                "sampled_at_monotonic_ns": pl.get("sampled_at_monotonic_ns") if known else None,
                "tx": {key: tx.get(key) if known else None for key in
                       ("source_selected", "length_words", "position_words")},
                "rx": {key: rx.get(key) if known else None for key in
                       ("armed", "done", "position_words")},
            },
        }
        self._rf_control_mode = mode
        self._rf_control_online = online and not stale
        self._rf_control_armed = armed
        self._rf_control_ops = control_ops
        if not known or not armed or self.rf_status["tx_session_ready"] is not True or (was_transmitting and not self.rf_transmitting()):
            self._invalidate_rf_loaded()
        if not known or not armed:
            self._rf_applied_parameters = {}
        self._align_rf_control_state()
        # 能力**跟着快照算**, 不再无条件写 unavailable: 写死的话采集按钮按构造点不亮。
        self._refresh_rx_capabilities()
        if rx_capture_inflight(self):
            if self.rf_status.get("rx_session_ready") is True:
                self.rx_capture["session_confirmed"] = True
            elif self.rx_capture.get("session_confirmed"):
                # Retain this evidence even if a new healthy session appears before
                # the old capture's result reaches the drawing thread.
                self.rx_capture["session_lost_reason"] = "采集会话已失效"
        # 会话/发布代次变了, 或者 daemon 已经不再保留产生这份分析的采集会话时, 当前分析
        # 立刻作废 —— 否则左边画"未接入真实采集", 右边还在拿一份已经不存在的
        # 接收当测量报出来(spec: 意外断连/FAULT 使当前快照和建议失效)。
        self._invalidate_rx_analysis_if_session_gone(online, stale, armed)

    def _rx_session_loss_reason(self, online, stale, armed):
        """当前分析还算"现在正在接收"吗? None = 还算, 否则是要画出来的原因。

        快照本身只能证明"收到了一份回复": 一份已经落地的分析之所以算"当前",
        靠的是产生它的那个会话还在。所以三件事一律让它失效:

        1. 会话没了(离线/过期), 或者 daemon 不再武装/生命周期不是活的;
        2. 发布身份变了(release 不一样 = 换了一个 daemon);
        3. `rx_session_ready` 从真变为假。它是 client 唯一带代次语义的
           会话证据: 只在"产生这一帧的那个连接代次仍被保留"时为真,
           一旦被丢弃就清零(新会话不是这一帧的来源)。

        第 3 条要求"曾经见过它为真": 分析刚落地时快照还可能是采集之前取的
        那一份(状态轮询 0.5 秒一次), 不能因此把一份刚刚好下来的采集判成失效。
        """
        status = self.rf_status
        if not online:
            return "会话已断开"
        if stale:
            return "状态已过期"
        if not armed:
            return "daemon 已停止武装"
        state = status.get("daemon_state")
        if state not in RX_LIVE_DAEMON_STATES:
            return "daemon 状态 %s" % (state,)
        release = status.get("release_id")
        if release is not None and self.rx_analysis_release_id is not None \
                and release != self.rx_analysis_release_id:
            return "daemon 已更换发布"
        if self._rx_session_confirmed and status.get("rx_session_ready") is not True:
            return "采集会话已失效"
        return None

    def _invalidate_rx_analysis(self, why, code=RX_QUALITY_SESSION_LOST):
        """把当前分析降级成历史, 并留下页面必须画出来的原因。

        历史与当前从头到尾是两份东西: 记录进 rx_history, 当前那份清掉 —— 不让一份
        已经不存在的接收继续被当成当前测量画在屏上。

        **唯一**的作废路径: 会话丢失/过期/FAULT 与改配置走的是同一条(只是原因码不同),
        所以"推进历史 + 清当前 + 留下画得出来的原因"只有一份实现。
        """
        self._invalidate_rx_demod("INVALIDATED: " + why)
        if self.rx_analysis_summary is not None:
            self.rx_history.insert(0, self.rx_analysis_summary)
            del self.rx_history[8:]
        self.rx_analysis = None
        self.rx_analysis_summary = None
        self.rx_model_result = None
        self.recommendation = Recommendation(reason="当前接收已失效: " + why).freeze()
        self.set_rx_snapshot(RxSnapshot.unavailable("当前接收已失效: " + why))
        self.rx_analysis_invalid = {
            "code": code,
            "text": "质量: %s (旧快照仅历史, 不证明当前接收)" % why,
            "severe": True}
        self.rx_analysis_generation = None
        self.rx_analysis_release_id = None
        self.rx_analysis_config_revision = None
        self._rx_session_confirmed = False

    def _invalidate_rx_analysis_if_session_gone(self, online, stale, armed):
        """没有当前分析就什么都不做; 否则先记下会话证据, 再看它是不是还在。"""
        if self.rx_analysis_summary is None:
            return
        if self.rf_status.get("rx_session_ready") is True:
            self._rx_session_confirmed = True
        why = self._rx_session_loss_reason(online, stale, armed)
        if why is not None:
            self._invalidate_rx_analysis(why)

    def _invalidate_rx_analysis_if_config_changed(self):
        """一次**成功下发并生效**的配置让当前分析作废 —— 它是在旧参数下采下来的。

        与 session_lost 复用同一条作废路径, 只换原因码: 这是操作员的本地改配置, 不是
        硬件故障, 也不是"关闭未确认"。只认**已生效**的配置 —— 草稿被编辑(填草稿/改
        字段)不推进 _rx_config_revision, 所以那不会作废当前分析; 那份旧分析仍然代表
        它被采下来的那套参数, 只是**下一次**采集会被 CONFIG_CHANGED 挡住。
        """
        if self.rx_analysis_summary is None:
            return False
        stamped = self.rx_analysis_config_revision
        if stamped is not None and stamped != self._rx_config_revision:
            self._invalidate_rx_analysis("配置已变更", RX_QUALITY_CONFIG_CHANGED)
            return True
        return False

    @staticmethod
    def _rf_surface_of(mode, control_ops=()):
        """面板该显示哪一面, 由 daemon **实际广告的能力**决定, 不由 mode 决定。

        原实现按 mode 分派, 那使控制面在真机上**按构造不可达**: 生产 daemon 终身发
        mode="status_only"(武装后也一样), 可用性它用 control_ops 表达。按广告的 op 分派
        还让面板与客户端的发送门**同源**。mode 只保留一层保守语义: 不认识的 mode = unknown。
        修订依据: 交付记录 §41; 被修订的是**判据**, 不是顺手改绿。
        """
        if mode not in RF_SURFACE_MODES:
            return "unknown"
        return "control" if control_ops else "status_only"

    def rf_transmitting(self):
        status = getattr(self, "rf_status", {})
        pl = status.get("pl_status", {})
        return bool(status.get("online") and not status.get("stale")
                    and status.get("armed") and status.get("daemon_state") == "TX_ACTIVE"
                    and pl.get("io_ok") is True
                    and pl.get("tx", {}).get("source_selected") is True)

    def rf_tone_frequency_hz(self):
        rate = self._rf_applied_parameters.get("sample_rate_hz")
        cycles = self._rf_parameters.get("tx_cycles")
        if cycles is not None and rate is not None:
            return rate * cycles / 1024.0
        freq = self._rf_parameters.get("tx_frequency_hz")
        if freq is not None and rate is not None:
            try:
                return cycles_for_frequency(freq, rate)[1]
            except ValueError:
                return None
        return None

    def _invalidate_rf_loaded(self):
        self._rf_tx_loaded = False
        self._rf_loaded_scenario = None
        if self._rf_control_state == "loaded":
            self._rf_control_state = "configured"

    def _align_rf_control_state(self):
        """让面板状态与快照能证实的事实对齐。

        快照只能证实三件事: 是不是可控制模式、在不在线、daemon 还武装着没有。
        它证不出面板做到 init 还是 configure —— 那两个状态只由成功的显式操作推进。
        所以这里**只降不升**: 未武装/离线/未知一律回 read_only; 武装了而面板还停在
        read_only 时升到 armed。
        """
        surface = self._rf_surface_of(self._rf_control_mode, self._rf_control_ops)
        if surface != "control" or not self._rf_control_online \
                or not self._rf_control_armed:
            if self._rf_inflight_action is None:
                self._rf_control_state = "read_only"
        elif self._rf_control_state == "read_only":
            self._rf_control_state = "armed"
        self._refresh_rf_control(surface)

    def _refresh_rf_control(self, surface=None):
        """把内部状态投影成 `rf_control`。**只暴露 daemon 真的广告了的 op**。"""
        surface = surface if surface is not None \
            else self._rf_surface_of(self._rf_control_mode, self._rf_control_ops)
        control_surface = (surface == "control" and self._rf_control_online)
        if not control_surface and self._rf_inflight_action is None:
            self._rf_control_state = "read_only"
        state = self._rf_control_state
        if control_surface:
            advertised = set(self._rf_control_ops)
            allowed = ("stop", "disarm") if self.rf_transmitting() else RF_CONTROL_FOR_STATE[state]
            # stop 在任何非只读状态必须可达(判据 6); daemon 武装时会广告 tx.stop,
            # 这里即使广告缺了也保住 stop —— "停不下来"比"发一个被本地拒的请求"更糟。
            actions = tuple(action for action in allowed
                            if action == "stop" or RF_CONTROL_OP[action] in advertised)
        else:
            # 只读 / 未知 mode / 离线: 一个动作都不暴露(判据 1/3)。
            actions = ()
        stop_reachable = (surface == "control" and self._rf_control_online
                          and state != "read_only")
        if surface == "unknown":
            reason = "无法识别的 mode %r: 按未知 / 只读处理" % (self._rf_control_mode,)
        elif not self._rf_control_online:
            reason = "RF 状态离线: 不暴露任何控制动作"
        elif state == "read_only" and not self._rf_control_armed:
            reason = ("daemon 未武装: 只暴露 arm" if actions
                      else "daemon 没有广告 control.arm")
        else:
            reason = "已武装(state=%s)" % state
        if self._rf_inflight_action is not None:
            # Retain only an already available STOP while a submitted operation runs.
            # Telemetry remains stale/unknown; this does not manufacture a fresh poll.
            actions = ("stop",) if self._rf_inflight_stop else ()
            stop_reachable = self._rf_inflight_stop
            reason = "等待硬件响应: %s" % self._rf_inflight_action
        control = self.rf_control
        control["inflight_action"] = self._rf_inflight_action
        control["surface"] = surface
        control["state"] = state
        control["tx_loaded"] = self._rf_tx_loaded
        control["transmitting"] = self.rf_transmitting()
        control["tone_hz"] = self.rf_tone_frequency_hz()
        control["actions"] = actions
        control["stop_reachable"] = stop_reachable
        control["reason"] = reason
        pending = control.get("pending_action")
        if pending is not None and pending not in actions:
            control["pending_action"] = None

    # ---------------- RX 采集控制面(Task 4) ----------------

    def rx_capture_capability(self):
        """运行时推导的采集能力: (结果码 or None, 原因)。

        能力由三样东西共同决定 —— daemon 实际广告的操作、当前状态、以及这个面板已经实现
        的 client。任一不满足就返回它**自己的**码, 而不是一个笼统的"不可用":
        not READY / 未武装 / 没广告 rx.capture / 离线 / 已过期 各有各的原因。
        """
        surface = self._rf_surface_of(self._rf_control_mode, self._rf_control_ops)
        if surface != RF_SURFACE_CONTROL:
            return UNSUPPORTED, "RF 控制面未开放: 只读或未识别的 mode"
        if self.rf_status.get("stale") is True:
            return STALE, "RF 状态已过期: 不能采集"
        if not self._rf_control_online:
            return DISCONNECTED, "RF 状态离线: 不能采集"
        if "rx.capture" not in self._rf_control_ops:
            return UNSUPPORTED, "daemon 没有广告 rx.capture: 不能采集"
        if not self._rf_control_armed:
            return UNSUPPORTED, "daemon 未武装: 不能采集"
        state = self.rf_status.get("daemon_state")
        if state != "READY":
            return NOT_READY, "daemon 状态 %s 不是 READY" % (state,)
        return None, "RX 采集已接入 (rx.capture)"

    def _refresh_rx_capabilities(self):
        """把运行时能力投影成 'capabilities' —— 未接入的东西继续为 False 并给出原因。"""
        code, reason = self.rx_capture_capability()
        self.set_capabilities(RxCapabilities(capture=code is None,
                                             model=self.rx_model_status["production"],
                                             sync=(self.rx_demod_status["available"]
                                                   or self.rx_demod_status.get("qpsk_available", False)
                                                   or self.rx_demod_status.get("rrc_available", False)
                                                   or self.rx_demod_status.get("qam16_available", False)),
                                             demod=(self.rx_demod_status["available"]
                                                    or self.rx_demod_status.get("qpsk_available", False)
                                                    or self.rx_demod_status.get("rrc_available", False)
                                                    or self.rx_demod_status.get("qam16_available", False)),
                                             ber=(self.rx_demod_status["available"]
                                                  or self.rx_demod_status.get("qpsk_available", False)
                                                  or self.rx_demod_status.get("rrc_available", False)
                                                  or self.rx_demod_status.get("qam16_available", False)),
                                             reason=reason))

    def _invalidate_rx_demod(self, reason):
        if (getattr(self,'rx_raw_frame',None) is not None
                and getattr(self,'rx_constellation',None) is not None
                and self.rx_demod_result is not None):
            self.rx_record_history = self._rx_record_values(historical=True)
        self.rx_raw_frame = None
        self.rx_constellation = None
        self.rx_environment = None
        self._rx_environment_reason = reason
        self._rx_record_scenario = None
        self._rx_record_rf_config = {}
        self._rx_constellation_reason = reason
        self.rx_demod_result = None
        self._rx_demod_reason = reason

    def set_teaching_status(self, status):
        status = status if isinstance(status, dict) else {}
        hashes = status.get('module_sha256') or {}
        names = ('rf_teaching_profile.py','rf_rx_constellation.py','rf_experiment_record.py')
        valid = (isinstance(status.get('release_id'),str) and bool(status['release_id'])
                 and isinstance(hashes,dict) and all(isinstance(hashes.get(name),str)
                 and len(hashes[name])==64 and all(c in '0123456789abcdef' for c in hashes[name])
                 for name in names))
        updated = dict(available=status.get('available') is True and valid,
            release_id=status.get('release_id'), module_sha256=dict(hashes),
            reason=status.get('reason') or ('' if valid else '教学身份未验证'))
        if updated != self.teaching_status:
            self._invalidate_rx_demod('TEACHING_CHANGED')
        self.teaching_status = updated

    def set_environment_status(self, status):
        # 环境域版本只有一个出处(rf_environment.VERSION): 写死旧字面量会让面板在域版本
        # 迁移后把可用的环境静默显示成不可用。
        from rf_environment import VERSION as ENVIRONMENT_VERSION
        status = status if isinstance(status,dict) else {}
        hashes = status.get('module_sha256') or {}
        names = ('rf_environment.py','rf_experiment_scenarios.py','rf_environment_baseline.json')
        def digest(value):
            return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
        valid = (isinstance(hashes,dict) and all(digest(hashes.get(n)) for n in names)
            and digest(status.get('baseline_sha256'))
            and status.get('environment_version')==ENVIRONMENT_VERSION
            and isinstance(status.get('release_id'),str) and bool(status['release_id']))
        updated = dict(available=status.get('available') is True and valid,
            release_id=status.get('release_id'),module_sha256=dict(hashes),
            baseline_sha256=status.get('baseline_sha256'),environment_version=status.get('environment_version'),
            reason=status.get('reason') or ('' if valid else '环境身份未验证'))
        if updated != self.environment_status:
            self._invalidate_rx_demod('ENVIRONMENT_CHANGED')
        self.environment_status = updated

    def set_headroom_status(self, status):
        """Land the worker's research capability report; never enables a study.

        Two independent facts must both hold.  The installed release attests the
        research module (`module_sha256`), and the RUNNING daemon announced the
        contract on a live `device.info`.  Reporting either one as available is
        how a panel would come to offer a study the daemon cannot run, so the
        two are kept apart here and only their conjunction is `available`.
        """
        status = status if isinstance(status,dict) else {}
        hashes = status.get('module_sha256') or {}
        def digest(value):
            return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
        release_ok = (digest(hashes.get('rf_headroom.py'))
            and isinstance(status.get('release_id'),str) and bool(status['release_id']))
        live = status.get('live') if isinstance(status.get('live'),dict) else None
        try:
            from rf_headroom import capability_available, STUDY, DAEMON_VERSION, PROTOCOL_MINOR
            live_ok = capability_available(live)
        except ImportError:
            # A release without the research module cannot run the study, and
            # that is the same answer as "not available" -- not an error.
            live_ok = False
            STUDY, DAEMON_VERSION, PROTOCOL_MINOR = 'HEADROOM-1', None, None
        updated = dict(
            available=bool(release_ok and live_ok and status.get('available') is True),
            release_id=status.get('release_id'), module_sha256=dict(hashes),
            study=STUDY, daemon_version=(live or {}).get('daemon_version') or DAEMON_VERSION,
            protocol_minor=(live or {}).get('protocol_minor') or PROTOCOL_MINOR,
            live=bool(live_ok), release_attested=bool(release_ok),
            reason=status.get('reason') or ('' if (release_ok and live_ok)
                                            else '余量研究未获实时能力'))
        self.headroom_status = updated

    def headroom_summary(self):
        """What the panel may show about the research plan.  Read-only."""
        from rf_headroom import (ATTEMPT_BUDGET, RESEARCH_ATTENUATION_MDB, SCENARIOS,
                                 TEACHING_ATTENUATION_MDB)
        return dict(available=self.headroom_status['available'],
            release_attested=self.headroom_status['release_attested'],
            live=self.headroom_status['live'],
            study=self.headroom_status.get('study'), attempts=ATTEMPT_BUDGET,
            order=list(SCENARIOS), attenuation_mdB=RESEARCH_ATTENUATION_MDB,
            teaching_attenuation_mdB=TEACHING_ATTENUATION_MDB,
            running=bool(self.headroom_running), pending=bool(self.headroom_pending),
            receipt=self.headroom_receipt,
            reason=self.headroom_status['reason'])

    def headroom_run_block(self):
        """Why a study may not be started now, or None when it may.

        A study is an explicit operator action on hardware.  It is never started
        by rendering a page, by a status poll, or as a side effect of arming.
        """
        if not self.headroom_status['available']:
            return self.headroom_status['reason'] or '余量研究未获实时能力'
        if self.rf_status.get('armed') is not False:
            return '余量研究要求未布防初始状态；确认后使用控制令牌布防'
        if self.headroom_receipt is not None:
            return '本进程已完成一次余量研究, 不能重跑'
        if self.headroom_running:
            return '余量研究正在进行'
        state = self.rf_status.get('daemon_state')
        if state not in ('OFFLINE', 'OFF'):
            return '余量研究要求安全的 OFFLINE 初始状态'
        return None

    def headroom_prompt(self):
        """The first tap: register the intent and send nothing.

        The study consumes the daemon's single initialization and cannot be
        re-run in this process, so one tap must never be enough to start it.
        """
        why = self.headroom_run_block()
        if why is not None:
            return False, why
        self.headroom_pending = True
        return True, '再次点击确认: 5 次尝试 / 46 dB / 单次初始化, 不可重跑'

    def headroom_cancel(self):
        """Drop a registered intent.  Nothing was sent, so nothing is undone."""
        self.headroom_pending = False
        if self.headroom_running:
            return False, '运行中取消必须由 RF worker 确认接收'
        return True, '已取消余量研究'

    def headroom_study_args(self):
        """The exact call the panel makes, or (None, why).

        `cable_confirmed=True` is the operator's own confirmation, which is why
        it is only ever produced from the second tap of a registered intent.
        """
        why = self.headroom_run_block()
        if why is not None:
            return None, why
        if not self.headroom_pending:
            return None, 'NOT_CONFIRMED'
        return dict(cable_confirmed=True), None

    def set_headroom_running(self, running):
        """Land the worker's in-flight flag; the panel only draws it."""
        self.headroom_running = bool(running)

    def set_headroom_receipt(self, receipt):
        """Land the finished study's receipt; this is what refuses a re-run."""
        if isinstance(receipt, dict):
            self.headroom_receipt = receipt
            self.headroom_pending = False
            self.headroom_running = False

    def new_headroom_session_id(self):
        """A fresh study id, generated in the one place that owns the shape."""
        from rf_headroom import new_session_id
        return new_session_id()

    def _land_rx_environment(self, result, frame):
        if result is None or not self.environment_status['available'] or self.rx_demod_result is None:
            return
        from rf_environment import EnvironmentResult
        envelope = self.rx_demod_result
        keys = ('capture_id','release_id','connection_generation','config_revision','completed_at_monotonic')
        if (not isinstance(result,EnvironmentResult)
                or any(getattr(result,k)!=frame.get(k) for k in keys)
                or result.submitted_revision!=envelope.submitted.revision
                or result.algorithm_version!=envelope.algorithm_version
                or result.environment_version!=self.environment_status['environment_version']
                or result.baseline_sha256!=self.environment_status['baseline_sha256']
                or result.release_id!=self.environment_status['release_id']):
            self._rx_environment_reason='RESULT_MISMATCH'
            return
        self.rx_environment = result
        self._rx_environment_reason = result.reason

    def rx_environment_summary(self):
        self.rx_demod_summary()
        value = self.rx_environment
        return dict(available=self.environment_status['available'],current=value is not None,
            capture_id=value.capture_id if value else None,label=value.label if value else None,
            reason=value.reason if value else self._rx_environment_reason,
            features=dict(value.features) if value else {},
            baseline_sha256=value.baseline_sha256 if value else self.environment_status['baseline_sha256'])

    def set_environment_scene(self, kind):
        if not self.environment_status['available']:
            return '环境场景模块未验证'
        if kind not in ENVIRONMENT_SCENES:
            return '未知实验场景'
        if self.rf_transmitting() or self._rf_inflight_action is not None or rx_capture_inflight(self):
            return '请先停止并结束当前操作'
        self.environment_scene = kind
        self._rf_signal_draft_kind = 'environment'
        self._rf_include_impairment = False
        self._invalidate_rx_demod('SCENE_CHANGED')
        self._invalidate_rf_loaded()
        self.rf_control_cancel()
        self._refresh_rf_control()
        return None

    def _land_rx_teaching(self, raw, constellation, frame):
        if not self.teaching_status['available'] or self.rx_demod_result is None:
            return
        from rf_rx_types import RxIqFrame
        from rf_rx_constellation import RxConstellation
        keys=('capture_id','release_id','connection_generation','config_revision','completed_at_monotonic')
        envelope=self.rx_demod_result
        if (not isinstance(raw,RxIqFrame) or raw.to_dict()!=frame
                or not isinstance(constellation,RxConstellation)
                or any(getattr(constellation,key)!=frame.get(key) for key in keys)
                or constellation.submitted_revision!=envelope.submitted.revision
                or constellation.algorithm_version!=envelope.algorithm_version
                or frame.get('release_id')!=self.teaching_status['release_id']):
            self._rx_constellation_reason='RESULT_MISMATCH'
            return
        self.rx_raw_frame,self.rx_constellation=raw,constellation
        self._rx_record_rf_config=dict(self._rx_submitted_rf_config)
        self._rx_record_scenario = dict(self._rx_submitted_scenario) if self._rx_submitted_scenario else None
        self._rx_constellation_reason=constellation.reason

    def rx_constellation_summary(self):
        demod=self.rx_demod_summary()
        if not demod['current']:
            self.rx_raw_frame=self.rx_constellation=None
        value=self.rx_constellation
        return dict(available=self.teaching_status['available'],current=value is not None,
            capture_id=value.capture_id if value is not None else None,
            reason=value.reason if value is not None else self._rx_constellation_reason,
            point_count=len(value.points) if value is not None else 0,
            symbol_count=value.symbol_count if value is not None else 0,
            normalization=value.normalization if value is not None else 'symbol_rms')

    def _rx_record_values(self,historical=False):
        values = dict(frame=self.rx_raw_frame,envelope=self.rx_demod_result,
                    constellation=self.rx_constellation,analysis=self.rx_analysis,
                    model_result=self.rx_model_result,
                    rf_config=dict(self._rx_record_rf_config),historical=historical)
        if self.environment_status['available']:
            values['environment'] = self.rx_environment
            values['scenario_declaration'] = dict(self._rx_record_scenario) if self._rx_record_scenario else None
        return values

    def rx_experiment_request(self,historical=False):
        """Only freeze already acquired data; saving never captures or extends freshness."""
        current=self.rx_constellation_summary()['current']
        if historical:
            return dict(self.rx_record_history) if self.rx_record_history is not None else None
        if not current or self.rx_raw_frame is None:
            return None
        return self._rx_record_values(False)

    def set_rx_save_status(self,status):
        if isinstance(status,dict):
            self.rx_save_status=dict(status)

    def apply_teaching_profile(self):
        if not self.teaching_status['available']:
            return self.teaching_status['reason'] or '教学模块未接入'
        from rf_teaching_profile import fill_drafts
        why = fill_drafts(self)
        if why is None:
            self._invalidate_rx_analysis('教学草稿已更新', RX_QUALITY_CONFIG_CHANGED)
            self._invalidate_rf_loaded()
            self._rf_loaded_kind = None
            self._refresh_rf_control()
        self.teaching_message = why or '草稿已填入；请独立检查、配置和加载'
        return why

    def set_rx_demod_status(self, status):
        """Land worker-verified scalar identity without importing new release modules."""
        status = status if isinstance(status, dict) else {}
        digest, release = status.get("algorithm_sha256"), status.get("release_id")
        bpsk_valid = (isinstance(digest, str) and len(digest) == 64
                      and all(c in "0123456789abcdef" for c in digest)
                      and status.get("algorithm_version") == "bpsk-rect-snapshot-v2"
                      and isinstance(release, str) and bool(release))
        qpsk_digest = status.get("qpsk_algorithm_sha256")
        qpsk_valid = (status.get("qpsk_available") is True
                      and isinstance(qpsk_digest, str) and len(qpsk_digest) == 64
                      and all(c in "0123456789abcdef" for c in qpsk_digest)
                      and status.get("qpsk_algorithm_version") == "qpsk-rect-snapshot-v1"
                      and isinstance(release, str) and bool(release))
        rrc_digest = status.get("rrc_algorithm_sha256")
        rrc_valid = (status.get("rrc_available") is True
                     and isinstance(rrc_digest, str) and len(rrc_digest) == 64
                     and all(c in "0123456789abcdef" for c in rrc_digest)
                     and status.get("rrc_algorithm_version") == "rrc-alpha035-sps20-v3"
                     and isinstance(release, str) and bool(release))
        qam16_digest = status.get("qam16_algorithm_sha256")
        qam16_valid = (status.get("qam16_available") is True
                       and isinstance(qam16_digest, str) and len(qam16_digest) == 64
                       and all(c in "0123456789abcdef" for c in qam16_digest)
                       and status.get("qam16_algorithm_version") == "qam16-rect-snapshot-v1"
                       and isinstance(release, str) and bool(release))
        # QAM64 已移出交付范围：无论旧 worker/旧发布声明什么，能力恒为 unavailable。
        # 原始 version/sha 字段仍原样透传用于追溯，但不构成可用/成功状态。
        qam64_digest = status.get("qam64_algorithm_sha256")
        qam64_valid = False
        updated = {"available": bpsk_valid,
                   "algorithm_version": status.get("algorithm_version"),
                   "algorithm_sha256": digest, "release_id": release,
                   "qpsk_available": qpsk_valid,
                   "qpsk_algorithm_version": status.get("qpsk_algorithm_version"),
                   "qpsk_algorithm_sha256": qpsk_digest,
                   "rrc_available": rrc_valid,
                   "rrc_algorithm_version": status.get("rrc_algorithm_version"),
                   "rrc_algorithm_sha256": rrc_digest,
                   "qam16_available": qam16_valid,
                   "qam16_algorithm_version": status.get("qam16_algorithm_version"),
                   "qam16_algorithm_sha256": qam16_digest,
                   "qam64_available": False,
                   "qam64_algorithm_version": status.get("qam64_algorithm_version"),
                   "qam64_algorithm_sha256": qam64_digest,
                   "qam64_reason": "QAM64超出本次交付支持范围",
                   "reason": status.get("reason") or ("" if bpsk_valid else "解调身份未验证")}
        if updated != self.rx_demod_status:
            self._invalidate_rx_demod("ALGORITHM_CHANGED")
        self.rx_demod_status = updated
        # 清掉界面残留的 QAM64 解调 envelope/成功态；原始镜像/历史记录不在此层删除。
        if "Qam64" in type(self.rx_demod_result).__name__:
            self._invalidate_rx_demod("QAM64超出本次交付支持范围")
        self._refresh_rx_capabilities()

    def set_rx_demod_setting(self, key, value):
        """Uniform local setter; RX intent never sends commands or adopts TX seeds."""
        if key == "expected_mode" and value == "QAM64":
            # 旧配置/外部调用显式拒绝：不自动回落，也不触发任何采集/发射。
            return "QAM64超出本次交付支持范围，请重新选择 BPSK/QPSK/QAM16"
        valid = False
        if key == "input_fs_hz":
            valid = type(value) is int and value == RX_INPUT_FS_HZ
        elif key in ("decim", "rx_len", "expected_mode", "symrate_hz"):
            valid = rx_draft_value_ok(key, value)
        elif key == "shaping":
            valid = value in ("RECT", "RRC")
        elif key == "reference_enabled":
            valid = type(value) is bool
        elif key == "reference_seed":
            valid = type(value) is int and 1 <= value <= 127
        if not valid:
            return "无效 RX 配置: %s" % key
        if self.rx_draft.get(key) == value:
            return None
        self.rx_draft[key] = value
        self.rx_demod_revision += 1
        self._invalidate_rx_demod("CONFIG_CHANGED")
        if key in ("input_fs_hz", "decim", "rx_len") and self.rx_analysis_summary is not None:
            self._invalidate_rx_analysis("RX采样草稿已变更", RX_QUALITY_CONFIG_CHANGED)
        return None

    def rx_demod_submission(self):
        """Freeze the explicit engine selected by mode+shaping."""
        expected = self.rx_draft.get("expected_mode")
        shaping = self.rx_draft.get("shaping")
        if expected == "QAM64":
            # QAM64 超出本次交付支持范围：绝不冻结/提交该解调配置，也不导入 pipeline。
            return None
        if expected == "QAM16":
            if shaping != "RECT" or not self.rx_demod_status.get("qam16_available"):
                return None
            from rf_qam16_pipeline import freeze_qam16_demod_config
            return freeze_qam16_demod_config(
                self.rx_draft, self.rx_demod_revision, iq_order="QI")
        if shaping == "RRC":
            if not self.rx_demod_status.get("rrc_available"):
                return None
            from rf_rrc_pipeline import freeze_rrc_demod_config
            # Real capture boundary is explicitly QI; never guessed/global-swapped.
            return freeze_rrc_demod_config(
                self.rx_draft, self.rx_demod_revision, iq_order="QI")
        if expected == "QPSK" and self.rx_demod_status.get("qpsk_available"):
            from rf_qpsk_pipeline import freeze_qpsk_demod_config
            return freeze_qpsk_demod_config(
                self.rx_draft, self.rx_demod_revision, iq_order="QI")
        if not self.rx_demod_status["available"]:
            return None
        from rf_bpsk_pipeline import freeze_demod_config
        return freeze_demod_config(self.rx_draft, self.rx_demod_revision)

    def _land_rx_demod(self, envelope, frame):
        self._invalidate_rx_demod("NO_DEMOD_RESULT")
        if envelope is None:
            return
        if not (self.rx_demod_status["available"]
                or self.rx_demod_status.get("qpsk_available")
                or self.rx_demod_status.get("rrc_available")
                or self.rx_demod_status.get("qam16_available")
                or self.rx_demod_status.get("qam64_available")):
            self._rx_demod_reason = "UNAVAILABLE"
            return
        from rf_bpsk_pipeline import BpskEnvelope
        try:
            from rf_qpsk_pipeline import QpskEnvelope
        except ImportError:
            QpskEnvelope = ()
        try:
            from rf_rrc_pipeline import RrcEnvelope
        except ImportError:
            RrcEnvelope = ()
        try:
            from rf_qam16_pipeline import Qam16Envelope
        except ImportError:
            Qam16Envelope = ()
        if isinstance(envelope, BpskEnvelope):
            expected_version = self.rx_demod_status["algorithm_version"]
            if not self.rx_demod_status["available"]:
                self._rx_demod_reason = "RESULT_MISMATCH"
                return
        elif QpskEnvelope and isinstance(envelope, QpskEnvelope):
            expected_version = self.rx_demod_status.get("qpsk_algorithm_version")
            if not self.rx_demod_status.get("qpsk_available"):
                self._rx_demod_reason = "RESULT_MISMATCH"
                return
        elif RrcEnvelope and isinstance(envelope, RrcEnvelope):
            expected_version = self.rx_demod_status.get("rrc_algorithm_version")
            if not self.rx_demod_status.get("rrc_available"):
                self._rx_demod_reason = "RESULT_MISMATCH"
                return
        elif Qam16Envelope and isinstance(envelope, Qam16Envelope):
            expected_version = self.rx_demod_status.get("qam16_algorithm_version")
            if not self.rx_demod_status.get("qam16_available"):
                self._rx_demod_reason = "RESULT_MISMATCH"
                return
        elif type(envelope).__name__ == "Qam64Envelope":
            # 旧 envelope 不得显示 QAM64 BER/成功态。
            self._rx_demod_reason = "QAM64超出本次交付支持范围"
            return
        else:
            self._rx_demod_reason = "RESULT_MISMATCH"
            return
        result = envelope.result
        keys = ("capture_id", "release_id", "connection_generation", "config_revision",
                "completed_at_monotonic")
        if (any(getattr(result, key) != frame.get(key) for key in keys)
                or envelope.submitted != self.rx_demod_submission()
                or envelope.submitted.rx_len != frame.get("samples")
                or envelope.submitted.input_fs_hz != frame.get("sample_rate_hz")
                or type(frame.get("ratio")) is not int
                or frame.get("ratio") not in range(5)
                or envelope.submitted.decim != 1 << frame["ratio"]
                or result.completed_at_monotonic != self.rx_snapshot.finished_at
                or result.completed_at_monotonic != rx_analysis_field(self, "completed_at_monotonic")
                or envelope.algorithm_version != expected_version
                or result.algorithm_version != envelope.algorithm_version
                or frame.get("release_id") != self.rx_demod_status["release_id"]):
            self._rx_demod_reason = "RESULT_MISMATCH"
            return
        if not self.rx_snapshot_is_current():
            self._rx_demod_reason = "EXPIRED"
            return
        self.rx_demod_result = envelope
        self._rx_demod_reason = result.reason

    def rx_demod_summary(self):
        """Measured values exist only while the same identity/config snapshot is current."""
        envelope = self.rx_demod_result
        if envelope is not None:
            if not self.rx_snapshot_is_current():
                self._invalidate_rx_demod("EXPIRED")
            elif (envelope.submitted != self.rx_demod_submission()
                    or envelope.result.capture_id != self.rx_snapshot.capture_id
                    or envelope.result.release_id != self.rx_analysis_release_id
                    or envelope.result.connection_generation != self.rx_analysis_generation
                    or self.rx_analysis_config_revision != self._rx_config_revision):
                self._invalidate_rx_demod("RESULT_MISMATCH")
        result = self.rx_demod_result.result if self.rx_demod_result is not None else None
        envelope = self.rx_demod_result
        summary = {"available": self.rx_demod_status["available"]
                   or self.rx_demod_status.get("qpsk_available", False)
                   or self.rx_demod_status.get("rrc_available", False)
                   or self.rx_demod_status.get("qam16_available", False),
                   "current": result is not None,
                   "algorithm_version": (envelope.algorithm_version if envelope is not None
                                         else self.rx_demod_status["algorithm_version"]),
                   "engine": (type(envelope).__name__ if envelope is not None else None),
                   "reason": result.reason if result is not None else self._rx_demod_reason,
                   "polarity_ambiguous": bool(result is not None and result.timing_sync
                                               and result.reference_sync is not True)}
        for name in ("carrier_sync", "timing_sync", "reference_sync", "compared_bits",
                     "bit_errors", "ber", "cfo_hz", "capture_id", "phase_ambiguity",
                     "timing_offset_samples", "alignment_bits",
                     "comparison_start_symbol", "comparison_end_symbol",
                     "valid_symbols", "valid_symbol_start", "valid_symbol_end",
                     "group_delay_samples", "transition_symbols",
                     "evm_available", "evm_rms"):
            summary[name] = getattr(result, name, None) if result is not None else None
            if summary[name] is None and envelope is not None:
                summary[name] = getattr(envelope, name, None)
        summary["symbol_preview"] = tuple(getattr(result, "symbol_preview", ())
                                          or ())[:64]
        summary["bit_preview"] = tuple(getattr(result, "bit_preview", ())
                                       or ())[:128]
        summary["qpsk_available"] = self.rx_demod_status.get("qpsk_available", False)
        summary["qpsk_algorithm_version"] = self.rx_demod_status.get(
            "qpsk_algorithm_version")
        summary["rrc_available"] = self.rx_demod_status.get("rrc_available", False)
        summary["rrc_algorithm_version"] = self.rx_demod_status.get(
            "rrc_algorithm_version")
        summary["qam16_available"] = self.rx_demod_status.get("qam16_available", False)
        summary["qam16_algorithm_version"] = self.rx_demod_status.get(
            "qam16_algorithm_version")
        summary["qam64_available"] = False
        summary["qam64_algorithm_version"] = None
        summary["qam64_reason"] = "QAM64超出本次交付支持范围"
        return summary

    def set_rx_model_status(self, status):
        """Receive a worker-verified model declaration; this performs no file IO."""
        status = status if isinstance(status, dict) else {}
        digest = status.get("model_sha256")
        identity_ok = (isinstance(digest, str) and len(digest) == 64
                       and all(ch in "0123456789abcdef" for ch in digest)
                       and status.get("model_version") == "tiny-iq-cnn-v1"
                       and status.get("preprocessing_version") == "dc-complex-rms-v1"
                       and isinstance(status.get("release_id"), str)
                       and bool(status["release_id"]))
        available = status.get("available") is True and identity_ok
        self.rx_model_status = {"available": available,
                                "production": available and status.get("production") is True,
                                "reason": str(status.get("reason") or "无模型"),
                                "release_id": status.get("release_id"),
                                "model_sha256": digest,
                                "model_version": status.get("model_version"),
                                "preprocessing_version": status.get("preprocessing_version")}
        if self.rx_model_result is not None and (
                not self._rx_model_matches_status(self.rx_model_result)
                or self.rx_analysis_release_id != self.rx_model_status["release_id"]):
            self.rx_model_result = None
            self._rx_model_recommendation = None
            self.recommendation = Recommendation(reason="模型身份已失效").freeze()
        self._refresh_rx_capabilities()

    def _rx_model_matches_status(self, result):
        status = self.rx_model_status
        return (isinstance(result, RxModelResult) and status["available"]
                and result.model_sha256 == status["model_sha256"]
                and result.model_version == status["model_version"]
                and result.preprocessing_version == status["preprocessing_version"]
                and result.experimental == (not status["production"]))

    def rx_model_display(self):
        result = self.rx_model_result
        if result is None:
            return {"label": "UNKNOWN", "score": None, "source": "N/A",
                    "version": "N/A", "state": self.rx_model_status["reason"]}
        if not self.rx_snapshot_is_current():
            state = "快照已过期"
        elif result.rejected or result.label == "UNKNOWN":
            state = ("实验 UNKNOWN: " if result.experimental else "UNKNOWN: ") + result.reason
        elif result.experimental:
            state = "实验模型: 建议关闭"
        else:
            state = "生产模型: 仅填草稿"
        score = (result.scores[result.class_index]
                 if result.class_index is not None and result.class_index < len(result.scores)
                 else max(result.scores) if result.scores else None)
        if result.label == "QAM64":
            # 模型仍输出 QAM64：明确标注超出支持范围；原始预测/概率/维度保留在
            # result 内，不改称 QAM16、不删除、不伪造置信度。
            return {"label": "QAM64（超出本次支持范围）", "score": score,
                    "source": result.source, "version": result.model_version,
                    "state": "QAM64超出本次交付支持范围；原始预测与概率已保留"}
        return {"label": result.label, "score": score, "source": result.source,
                "version": result.model_version, "state": state}

    def _rf_config_changed(self):
        """操作员的 RF 草稿与**已下发并生效**的配置不一致 = 改过配置还没重新下发。

        采集必须在下发过的配置下进行: 否则拿回来的 IQ 属于一套面板并不认识的参数,
        界面上却会把它当成当前配置的结果来读。
        """
        applied = self._rf_applied_parameters
        if not applied:
            return True
        return any(self._rf_parameters.get(name) != applied.get(name)
                   for name in RF_ACTION_PARAMS["configure"])

    def rx_capture_block_reason(self):
        """采集控件的实例门禁; None = 可以发。每个拒绝都有自己的码。

        与模块级 'rx_capture_block(app)' 的分工: 那边是**设备/配置**规则(连接、在途事务、
        运行中、抽取契约), 这边再加只有一份实时 daemon 回复才能证明的运行时规则。
        设备规则仍然只写一遍 —— 最后一步就是转交给那边。
        """
        if self.pending or self.rx_snapshot.state == "BUSY":
            return BUSY
        code, _detail = self.rx_capture_capability()
        if code is not None:
            return code
        if rx_capture_inflight(self) or self._rf_inflight_action is not None:
            return BUSY
        if self.rf_status.get("stale") is True:
            return STALE
        if self._rf_config_changed():
            return CONFIG_CHANGED
        return rx_capture_block(self)

    def rx_loopback_block(self):
        """物理回环的额外门禁: 当前连接上必须有已加载的 TX(与 client 的要求同源)。"""
        code = self.rx_capture_block_reason()
        if code is not None:
            return code
        if self.rf_status.get("tx_session_ready") is not True:
            return NOT_LOADED
        return None

    def rx_capture_args(self, physical=False):
        """本次采集要发的确切参数; ratio **永远显式给出**。

        daemon 在参数缺席时保留它初始化时的 ratio(1 => 抽取 2), 所以这里绝不省略:
        草稿的抽取倍率在这一处换算成寄存器选择码, client 那边不再有第二份换算。
        """
        result, why = validate_rx_config({
            "input_fs_hz": self.rx_draft.get("input_fs_hz"),
            "decim": self.rx_draft.get("decim"),
            "rx_len": self.rx_draft.get("rx_len")})
        if result != OK:
            return None, why
        args = {"samples": int(self.rx_draft["rx_len"]),
                "ratio": RX_DECIM_OPTIONS.index(self.rx_draft["decim"])}
        if physical:
            # 显式确认的独立动作: 线缆确认与单源域衰减(46000 mdB)是 client 的硬要求。
            args["cable_confirmed"] = True
            args["attenuation_mdB"] = RX_LOOPBACK_ATTENUATION_MDB
        return args, None

    def rx_capture_submitted(self, physical=False):
        """冻结这次提交: 记下提交时的配置代次, 结果只在同一代次下才允许落地。"""
        self._rx_submitted_rf_config=dict(self._rf_applied_parameters)
        self._rx_submitted_scenario = dict(self._rf_loaded_scenario) if self._rf_loaded_scenario else None
        # 新一次提交取代了"刚刚失效"那条原因: 它已经进了 rx_history,
        # 而接下来该看的是这次采集自己的结果。
        self._invalidate_rx_demod("PROCESSING")
        self.rx_analysis_invalid = None
        self.rx_model_result = None
        self.recommendation = Recommendation(reason="等待当前真实 IQ").freeze()
        self.rx_capture = {
            "state": RX_CAPTURE_QUEUED, "physical": bool(physical),
            "capture_id": None, "code": None, "reason": "",
            "close_confirmed": None, "cancelled": False, "pending_action": None,
            "config_revision": self._rx_config_revision,
            "session_confirmed": self.rf_status.get("rx_session_ready") is True,
            "session_lost_reason": None,
            "worker_seq": self.rx_capture.get("worker_seq"),
            "local_terminal": True,
        }

    def set_rx_capture_status(self, status):
        """落地 worker 发布的采集状态(socket 与 FFT 都已在 worker 线程里做过)。

        主循环**每一帧**都会把 worker 的当前状态送进来, 包括它还没前进的那一份。
        所以这里用 worker 自己的状态序号做闸门: 面板刚判定的终态(refused /
        配置已变)在 worker 真的发布下一份状态之前不被顶掉 —— 否则"采集状态"会读成 DONE
        而事实上什么都没落地。取消中/关闭未确认的语义不受影响: 它们本来就是
        worker 发布的状态, 各自带着自己的序号。
        """
        status = status if isinstance(status, dict) else {}
        state = status.get("state")
        state = state if isinstance(state, str) else RX_CAPTURE_IDLE
        seq = status.get("seq")
        held_seq = self.rx_capture.get("worker_seq")
        if seq is not None and self.rx_capture.get("local_terminal") and seq == held_seq:
            return
        if state in (RX_CAPTURE_ERROR, RX_CAPTURE_CANCELLED, RX_CAPTURE_CANCELLING):
            self._invalidate_rx_demod("CAPTURE_" + state)
        pending = self.rx_capture.get("pending_action")
        if state in RX_CAPTURE_INFLIGHT:
            pending = None
        self.rx_capture = {
            "state": state, "physical": status.get("physical") is True,
            "capture_id": status.get("capture_id"), "code": status.get("code"),
            "reason": status.get("reason") or "",
            "close_confirmed": status.get("close_confirmed"),
            "cancelled": status.get("cancelled") is True,
            "pending_action": pending,
            "config_revision": self.rx_capture.get("config_revision",
                                                   self._rx_config_revision),
            "session_confirmed": self.rx_capture.get("session_confirmed", False),
            "session_lost_reason": self.rx_capture.get("session_lost_reason"),
            "worker_seq": seq if seq is not None else held_seq,
            "local_terminal": seq is None,
        }

    def set_rx_capture_result(self, payload):
        """落地一份**已完成**的采集结果; 未完成或改过配置的一律不落地。

        返回 (landed, reason)。不落地不是"出错", 而是这条链路的规则: 取消的结果和配置
        代次已经变了的旧结果都只作废, 不当数据用。
        """
        payload = payload if isinstance(payload, dict) else {}
        frame = payload.get("frame")
        analysis = payload.get("analysis")
        submitted_at = self.rx_capture.get("config_revision")
        if submitted_at is not None and submitted_at != self._rx_config_revision:
            self._invalidate_rx_demod("CONFIG_CHANGED")
            self.set_rx_capture_status({"state": RX_CAPTURE_CANCELLED, "cancelled": True,
                                        "reason": RESULT_TEXT.get(CONFIG_CHANGED,
                                                                  CONFIG_CHANGED),
                                        "close_confirmed":
                                            self.rx_capture.get("close_confirmed")})
            return False, CONFIG_CHANGED
        if not isinstance(frame, dict) or analysis is None:
            self._invalidate_rx_demod("RESULT_MISMATCH")
            return False, MISMATCH
        status = self.rf_status
        why = self.rx_capture.get("session_lost_reason") or self._rx_session_loss_reason(
            status.get("online") is True, status.get("stale") is True,
            status.get("armed") is True)
        release, captured_release = status.get("release_id"), frame.get("release_id")
        if release is not None and captured_release is not None and release != captured_release:
            why = "daemon 已更换发布"
        if why:
            self._invalidate_rx_analysis(why)
            self.set_rx_capture_status({"state": RX_CAPTURE_CANCELLED,
                                        "cancelled": True, "reason": why,
                                        "close_confirmed":
                                            self.rx_capture.get("close_confirmed")})
            return False, RX_QUALITY_SESSION_LOST
        summary = _telemetry_dict(analysis)
        snapshot = rx_snapshot_from_capture(frame, analysis)
        if self.rx_analysis_summary is not None:
            # 历史与当前分开: 被替换掉的那份仍在历史里可查, 不是被删掉。
            self.rx_history.insert(0, self.rx_analysis_summary)
            del self.rx_history[8:]
        self.rx_analysis = analysis
        summary["capture_id"] = snapshot.capture_id
        # 快照上的**会话印记**: 这份分析是在哪一个连接代次下取得的。
        summary["connection_generation"] = frame.get("connection_generation")
        self.rx_analysis_summary = summary
        self.rx_analysis_generation = frame.get("connection_generation")
        self.rx_analysis_release_id = frame.get("release_id")
        # 快照上的**配置印记**: 这份分析是在哪一次已生效配置下采的。记的是提交时冻结的
        # 那一代(没有提交记录时就是当前代), 不是"落地这一刻"的当前值 —— 一份结果只在
        # 配置代次没变过时才允许落地, 两者正常情况下相等, 但印记必须说明它是哪一代。
        self.rx_analysis_config_revision = (submitted_at if submitted_at is not None
                                            else self._rx_config_revision)
        summary["config_revision"] = self.rx_analysis_config_revision
        # 这份新分析还没被"会话仍被保留"证实过, 而上一份的失效原因也到此为止。
        self._rx_session_confirmed = False
        self.rx_analysis_invalid = None
        self.set_rx_snapshot(snapshot)
        self.rx_capture = dict(self.rx_capture, state=RX_CAPTURE_DONE,
                               capture_id=snapshot.capture_id, code=None,
                               reason=snapshot.error, cancelled=False,
                               local_terminal=False)
        self.rx_status_message = snapshot.error or snapshot.state
        self.rx_model_result = None
        self.recommendation = Recommendation(reason=self.rx_model_status["reason"]).freeze()
        model = payload.get("model_result")
        if (self._rx_model_matches_status(model) and model.capture_id == snapshot.capture_id
                and model.completed_at_monotonic == snapshot.finished_at
                and model.source == snapshot.source
                and self.rx_model_status["release_id"] == frame.get("release_id")):
            self.rx_model_result = model
            accepted = (self.rx_model_status["production"] and not model.experimental
                        and not model.rejected and model.quality == "OK"
                        and snapshot.usable and self.rx_snapshot_is_current())
            # Classification is not a symbol-rate estimator. Only measured sampling
            # and a supported expected mode become local draft fields.
            action = {"decim": snapshot.decim, "rx_len": snapshot.actual_len}
            if model.label in MODES and model.label != "QAM64":
                action["expected_mode"] = model.label
            self.recommendation = Recommendation(
                mode=model.label, confidence=None, source=model.source,
                stale=not accepted, reason=("仅填本地草稿; 符号率未估计" if accepted
                                            else model.reason or "实验模型: 建议关闭"),
                action=action if accepted else None).freeze()
            self._rx_model_recommendation = self.recommendation
        self._land_rx_demod(payload.get("demod_result"), frame)
        self._land_rx_teaching(payload.get('raw_frame'),payload.get('constellation'),frame)
        self._land_rx_environment(payload.get('environment_result'),frame)
        return True, None

    def rx_snapshot_is_current(self, now=None):
        """当前那份冻结结果还算"现在正在接收"吗(默认 30 秒)? 过期就只是历史。"""
        return _rx_snapshot_is_current(self, now)

    def rx_quality_text(self):
        """把当前的质量原因拼成一行(页面直接画它)。"""
        return "; ".join(item["text"] for item in rx_quality_reasons(self))

    def clear_rx_snapshot(self):
        """只清**本地**当前快照: 不动硬件、不发网络、不改 daemon 会话。

        正常收尾保留已冻结的样点 —— 所以"清除"是把当前那份推进历史视图再清掉当前,
        而不是把证据删掉。历史与当前状态从头到尾是两份东西。
        """
        self._invalidate_rx_demod("CLEARED")
        if self.rx_analysis_summary is not None:
            self.rx_history.insert(0, self.rx_analysis_summary)
            del self.rx_history[8:]
        self.rx_analysis = None
        self.rx_analysis_summary = None
        self.rx_model_result = None
        self.recommendation = Recommendation(reason="本地快照已清除").freeze()
        self.set_rx_snapshot(RxSnapshot.unavailable("本地快照已清除"))
        self.rx_analysis_invalid = None
        self.rx_analysis_generation = None
        self.rx_analysis_release_id = None
        self.rx_analysis_config_revision = None
        self._rx_session_confirmed = False
        if self.rx_capture.get("state") != RX_CAPTURE_IDLE \
                and not rx_capture_inflight(self):
            # local_terminal: 清掉是操作员的本地动作, worker 没前进之前不能把
            # 它上一份 DONE 又送回来。
            self.rx_capture = dict(self.rx_capture, state=RX_CAPTURE_IDLE,
                                   capture_id=None, code=None, reason="",
                                   cancelled=False, close_confirmed=None,
                                   local_terminal=True)

    def rx_capture_prompt(self, action):
        """回环的第一下: 只登记待确认动作, 不执行、不发任何请求。"""
        self.rx_capture = dict(self.rx_capture, pending_action=action)
        return True, "请再次点击确认 %s" % action

    def rx_capture_cancel(self):
        self._invalidate_rx_demod("CANCELLED")
        self.rx_capture = dict(self.rx_capture, pending_action=None)

    def rx_fill_block(self):
        """只读建议的门禁: 建议不可用时"填草稿"按钮就是灰的。"""
        model = self.rx_model_result
        if not self.capabilities.model or not self.rx_model_status["production"]:
            return UNSUPPORTED
        if not self.rx_snapshot_is_current():
            return STALE
        if (not self._rx_model_matches_status(model) or model.experimental or model.rejected
                or model.quality != "OK" or model.label in ("UNKNOWN", "QAM64")
                or model.capture_id != self.rx_snapshot.capture_id
                or model.completed_at_monotonic != self.rx_snapshot.finished_at
                or not self.rx_snapshot.usable
                or self.rx_analysis_config_revision != self._rx_config_revision
                or self.rx_analysis_release_id != self.rx_model_status["release_id"]
                or self.recommendation is not self._rx_model_recommendation):
            return UNSUPPORTED
        return None if self.recommendation.actionable else UNSUPPORTED

    def rx_fill_draft(self):
        """把建议里**允许的本地草稿字段**填进草稿; 不碰硬件、不碰 daemon、不发射。

        只接受 RX_DRAFT_ACTION_KEYS 里的键: 建议是外部数据, 它不该能把任意字段塞进面板
        状态(更不该写寄存器)。AUTO 永久关闭, 这里也不"顺便"应用或启动任何东西。
        """
        if self.rx_fill_block() is not None:
            return "建议不可用"
        action = self.recommendation.action
        if not isinstance(action, dict):
            return "建议的动作不是字段表"
        applied = []
        for name in RX_DRAFT_ACTION_KEYS:
            if name not in action:
                continue
            value = action[name]
            if not rx_draft_value_ok(name, value):
                # 越界/类型不对的建议字段一律不落地。
                continue
            self.set_rx_demod_setting(name, value)
            applied.append(name)
        return None if applied else "建议里没有可填的草稿字段"

    def rf_control_block_reason(self, action):
        """控制动作的模型侧门禁: 返回 None 表示可以发, 否则返回拒绝原因。

        只读 / 未知 mode / 未武装 / daemon 没广告 / 需要确认但还没确认, 都在这里被挡下。
        """
        if action not in RF_CONTROL_ACTIONS:
            return "unknown RF control action %r" % (action,)
        if action not in self.rf_control["actions"]:
            return ("%s is not available in control state %s (%s)"
                    % (action, self.rf_control["state"], self.rf_control["reason"]))
        if self.rf_requires_confirmation(action) \
                and self.rf_control.get("pending_action") != action:
            return "%s requires explicit confirmation" % action
        _args, why = self.rf_parameter_plan(action)
        if why is not None:
            return why
        return None

    # ---- AD9361 RF software digital/channel drafts (Chunk C) ----

    def rf_digital_values(self):
        """Copy of the AD9361 digital modulation draft."""
        return dict(self._rf_digital_draft)

    def rf_digital_symrate_bounds(self):
        """AD9361 symbol-rate keypad bounds using the currently applied Fs (fallback 60 MHz)."""
        fs = self._rf_applied_parameters.get("sample_rate_hz")
        if fs is None:
            fs = RX_INPUT_FS_HZ
        return rf_digital_symrate_range(fs)

    def set_rf_digital_parameter(self, name, value):
        """Validate and set one AD9361 digital draft field (no hardware I/O)."""
        if name == "symrate_hz":
            name = "symbol_rate_hz"
        if self.rf_transmitting() or self._rf_inflight_action is not None:
            return "RF parameters are locked while transmitting or awaiting hardware"
        if name == "symbol_rate_hz":
            fs = self._rf_applied_parameters.get("sample_rate_hz")
            if fs is not None:
                lo, hi = rf_digital_symrate_range(fs)
                if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi or fs % value != 0:
                    return "symbol_rate_hz must be in %d..%d and divide sample_rate_hz (sps 2..32)" % (lo, hi)
        why = rf_digital_parameter_error(name, value)
        if why is not None:
            return why
        self._rf_digital_draft[name] = value
        self._rf_include_impairment = False
        self._invalidate_rf_loaded()
        self._refresh_rf_control()
        self.rf_control_cancel()
        return None
    def rf_channel_values(self):
        """Copy of the AD9361 channel impairment draft."""
        return {
            "enabled": self._rf_channel_draft["enabled"],
            "noise_pct": self._rf_channel_draft["noise_pct"],
            "noise_seed": self._rf_channel_draft["noise_seed"],
            "taps": [dict(t) for t in self._rf_channel_draft["taps"]],
        }

    def set_rf_channel_parameter(self, name, value):
        """Validate and set one AD9361 channel impairment field."""
        if self.rf_transmitting() or self._rf_inflight_action is not None:
            return "RF parameters are locked while transmitting or awaiting hardware"
        why = rf_channel_parameter_error(name, value)
        if why is not None:
            return why
        self._rf_channel_draft[name] = value
        self._invalidate_rf_loaded()
        self._refresh_rf_control()
        self.rf_control_cancel()
        return None

    def set_rf_channel_tap(self, index, field, value):
        """Validate and set one tap field in the AD9361 channel impairment draft."""
        if not (0 <= index < RF_CHANNEL_TAPS):
            return "tap index must be 0..%d" % (RF_CHANNEL_TAPS - 1)
        why = rf_channel_parameter_error(field, value)
        if why is not None:
            return why
        if self.rf_transmitting() or self._rf_inflight_action is not None:
            return "RF parameters are locked while transmitting or awaiting hardware"
        self._rf_channel_draft["taps"][index][field] = value
        self._invalidate_rf_loaded()
        self._refresh_rf_control()
        self.rf_control_cancel()
        return None

    def set_rf_signal_kind(self, kind):
        """Select whether the next RF load is a common waveform or digital modulation."""
        if kind not in RF_SIGNAL_KINDS:
            return "RF signal kind must be waveform or digital"
        if self.rf_transmitting() or self._rf_inflight_action is not None:
            return "RF parameters are locked while transmitting or awaiting hardware"
        self._rf_signal_draft_kind = kind
        self._rf_include_impairment = False
        self._invalidate_rf_loaded()
        self._refresh_rf_control()
        self.rf_control_cancel()
        return None

    def set_rf_channel_apply(self):
        """Prepare a load that re-generates the currently loaded kind with impairment.

        Returns None when ready, or a refusal reason.  This does not send anything.
        """
        if self._rf_loaded_kind is None:
            return "没有已加载信号，请先在对应信号页加载"
        if self.rf_transmitting() or self._rf_inflight_action is not None:
            return "RF parameters are locked while transmitting or awaiting hardware"
        self._rf_signal_draft_kind = self._rf_loaded_kind
        self._rf_include_impairment = True
        self._refresh_rf_control()
        self.rf_control_cancel()
        return None
    def set_rf_parameter(self, name, value):
        """Record one operator-chosen control parameter.

        Returns None on acceptance, or the refusal reason -- and on refusal it changes
        NOTHING, so a rejected value can never be the one that reaches the daemon.
        """
        if self.rf_transmitting() or self._rf_inflight_action is not None:
            return "RF parameters are locked while transmitting or awaiting hardware"
        why = rf_parameter_error(name, value)
        if why is not None:
            return why
        self._rf_parameters[name] = value
        self._invalidate_rf_loaded()
        self._refresh_rf_control()
        self.rf_control_cancel()
        return None

    def set_rf_waveform_mode(self, advanced):
        """Switch RF waveform editing between normal (frequency/percent) and legacy
        (cycles/code) modes.  Changing mode invalidates any loaded waveform."""
        self._rf_wave_advanced = bool(advanced)
        self._invalidate_rf_loaded()
        self._refresh_rf_control()
        self.rf_control_cancel()

    def _pct_to_code(self, pct):
        return round(2047 * pct / 100)

    def _require_frequency_fields(self, wf):
        names = ["tx_amplitude_pct"]
        if wf != "cw":
            names.append("tx_frequency_hz")
        if wf == "multitone":
            names += ["tone2_frequency_hz", "tone2_amplitude_pct"]
        if wf == "chirp":
            names.append("chirp_f1_hz")
        if wf == "noise":
            names.append("seed")
        if wf == "pulse":
            names.append("duty_pct")
        missing = [k for k in names if k not in self._rf_parameters]
        if missing:
            return {}, "Fill waveform parameters: " + ", ".join(missing)
        return None

    def _rf_waveform_plan_frequency(self, wf):
        err = self._require_frequency_fields(wf)
        if err is not None:
            return err
        rate = self._rf_applied_parameters.get("sample_rate_hz")
        if rate is None:
            return {}, "Applied sample rate is required for frequency mode"
        amplitude_pct = self._rf_parameters["tx_amplitude_pct"]
        amplitude = self._pct_to_code(amplitude_pct)
        args = {"waveform": wf, "amplitude": amplitude}
        if wf != "cw":
            freq = self._rf_parameters["tx_frequency_hz"]
            try:
                cycles, actual = cycles_for_frequency(freq, rate)
            except ValueError as exc:
                return {}, str(exc)
            args["cycles"] = cycles
            args["actual_hz"] = actual
        if wf == "multitone":
            freq2 = self._rf_parameters["tone2_frequency_hz"]
            amp2_pct = self._rf_parameters["tone2_amplitude_pct"]
            amp2 = self._pct_to_code(amp2_pct)
            try:
                cycles2, actual2 = cycles_for_frequency(freq2, rate)
            except ValueError as exc:
                return {}, str(exc)
            args["tone2_cycles"] = cycles2
            args["tone2_amplitude"] = amp2
            args["tone2_actual_hz"] = actual2
            if amplitude + amp2 > 2047:
                return {}, "请降低两路幅度: 双音峰值和不能超过 2047"
        if wf == "chirp":
            f1 = self._rf_parameters["chirp_f1_hz"]
            try:
                cycles1, _actual1 = cycles_for_frequency(f1, rate)
            except ValueError as exc:
                return {}, str(exc)
            args["chirp_f1_cycles"] = cycles1
        if wf == "noise":
            args["seed"] = self._rf_parameters["seed"]
        if wf == "pulse":
            args["duty_pct"] = self._rf_parameters["duty_pct"]
        return args, None

    def _rf_waveform_plan_legacy(self, wf):
        names = ["tx_amplitude"]
        if wf in ("sine", "square", "triangle", "sawtooth", "multitone", "chirp", "pulse"):
            names.append("tx_cycles")
        if wf == "multitone":
            names += ["tone2_cycles", "tone2_amplitude"]
        if wf == "chirp":
            names.append("chirp_f1_cycles")
        if wf == "noise":
            names.append("seed")
        if wf == "pulse":
            names.append("duty_pct")
        missing = [k for k in names if k not in self._rf_parameters]
        if missing:
            return {}, "Fill waveform parameters: " + ", ".join(missing)
        args = {"waveform": wf, "amplitude": self._rf_parameters["tx_amplitude"]}
        if "tx_cycles" in self._rf_parameters:
            args["cycles"] = self._rf_parameters["tx_cycles"]
        if "tone2_cycles" in self._rf_parameters:
            args["tone2_cycles"] = self._rf_parameters["tone2_cycles"]
            args["tone2_amplitude"] = self._rf_parameters["tone2_amplitude"]
        if "chirp_f1_cycles" in self._rf_parameters:
            args["chirp_f1_cycles"] = self._rf_parameters["chirp_f1_cycles"]
        if "seed" in self._rf_parameters:
            args["seed"] = self._rf_parameters["seed"]
        if "duty_pct" in self._rf_parameters:
            args["duty_pct"] = self._rf_parameters["duty_pct"]
        return args, None

    def rf_parameter_values(self):
        """Copy of the operator draft; reading it has no hardware side effect."""
        return dict(self._rf_parameters)

    def clear_rf_parameters(self):
        if self.rf_transmitting() or self._rf_inflight_action is not None:
            return
        self._rf_parameters.clear()
        self._invalidate_rf_loaded()
        self._refresh_rf_control()
        self.rf_control_cancel()

    def set_rf_control_pending(self, action):
        if self._rf_inflight_action is None:
            self._rf_inflight_stop = bool(self.rf_control.get("stop_reachable"))
        if action in ("stop", "disarm"):
            self._invalidate_rx_demod("CONTROL_" + action.upper())
        self._rf_inflight_action = action
        self.rf_control_cancel()
        self._refresh_rf_control()

    def _digital_load_args(self):
        """Build and validate the tx.load args for the AD9361 digital draft."""
        if not self._rf_applied_parameters:
            return {}, "Apply the complete RF configuration before loading or starting"
        if self._rf_applied_parameters.get("phy_mode_1r1t") != 0:
            return {}, "Dual TX validation requires explicit 2R2T (PHY=0)"
        args = {
            "waveform": "digital",
            "mode": self._rf_digital_draft["mode"],
            "filter": self._rf_digital_draft["filter"],
            "rolloff": self._rf_digital_draft["rolloff"],
            "seed": self._rf_digital_draft["seed"],
            "symbol_rate_hz": self._rf_digital_draft["symbol_rate_hz"],
            "sample_rate_hz": self._rf_applied_parameters.get("sample_rate_hz"),
            "amplitude": self._rf_digital_draft["amplitude"],
        }
        why = IQ.validate_digital_args(args)
        if why is not None:
            return {}, why
        return args, None
    def rf_parameter_plan(self, action):
        """(args, error) for one action: what it would send, or what is still missing.

        An action with a missing parameter is NOT sendable, and the reason names the
        parameters, because "NOT_WIRED" told the operator nothing actionable.
        """
        if action in ("load", "start"):
            if not self._rf_applied_parameters or any(
                    self._rf_parameters.get(k) != self._rf_applied_parameters.get(k)
                    for k in RF_ACTION_PARAMS["configure"]):
                return {}, "Apply the complete RF configuration before loading or starting"
            if self._rf_applied_parameters.get("phy_mode_1r1t") != 0:
                return {}, "Dual TX validation requires explicit 2R2T (PHY=0)"
            if action == "start":
                return ({}, None) if self._rf_tx_loaded else ({}, "Load waveform on the current connection first")
            if self._rf_signal_draft_kind == 'environment':
                if not self.environment_status['available']:
                    return {}, '环境场景模块未验证'
                from rf_teaching_profile import teaching_capability
                if any(self._rf_applied_parameters.get(k)!=v for k,v in teaching_capability().rf_parameters):
                    return {}, '环境实验需要已应用的80MHz / 60MHz / 30MHz / 40dB教学配置'
                return dict(waveform='environment',scenario=dict(kind=self.environment_scene)), None
            if self._rf_signal_draft_kind == "digital":
                args, why = self._digital_load_args()
                if why is not None:
                    return {}, why
            else:
                wf = rf_waveform_name(self._rf_parameters.get("tx_waveform", 1))
                if wf is None:
                    return {}, "Unknown or missing waveform type"
                # Normal mode is used when the operator supplied any frequency/percent/duty
                # field.  Legacy cycle/code mode remains the default for existing callers.
                use_frequency = (not self._rf_wave_advanced and any(
                    name in self._rf_parameters for name in (
                        "tx_frequency_hz", "tx_amplitude_pct", "tone2_frequency_hz",
                        "tone2_amplitude_pct", "chirp_f1_hz")))
                if use_frequency:
                    args, why = self._rf_waveform_plan_frequency(wf)
                    if why is not None:
                        return {}, why
                else:
                    args, why = self._rf_waveform_plan_legacy(wf)
                    if why is not None:
                        return {}, why
            if self._rf_include_impairment:
                args["impairment"] = self.rf_channel_values()
            return args, None
        required = RF_ACTION_PARAMS.get(action)
        if required is None:
            return {}, None
        args, missing = {}, []
        for name in required:
            if name in self._rf_parameters:
                args[name] = self._rf_parameters[name]
            else:
                missing.append(name)
        for name in RF_ACTION_OPTIONAL_PARAMS.get(action, ()):
            if name in self._rf_parameters:
                args[name] = self._rf_parameters[name]
        if missing:
            return {}, ("%s needs operator parameters the panel does not have yet: %s"
                        % (action, ", ".join(missing)))
        return args, None
    def rf_requires_confirmation(self, action):
        return action in RF_CONTROL_CONFIRM or (
            action=='load' and self._rf_signal_draft_kind=='environment')

    def rf_control_prompt(self, action):
        """界面第一下。返回 (accepted, needs_confirm, message)。

        需要确认的动作只登记, 不执行; 不需要确认的动作(stop/disarm)直接放行。
        """
        if action not in self.rf_control["actions"]:
            return False, False, ("%s is not available: %s"
                                  % (action, self.rf_control["reason"]))
        _args, why = self.rf_parameter_plan(action)
        if why is not None:
            # Refused BEFORE the confirmation dance: asking an operator to confirm an
            # action that cannot be sent would waste the one gesture the panel has.
            return False, False, why
        if not self.rf_requires_confirmation(action):
            return True, False, ""
        self.rf_control["pending_action"] = action
        return True, True, "请再次点击确认 %s" % action

    def rf_control_cancel(self):
        self.rf_control["pending_action"] = None

    def set_rf_control_result(self, action, result):
        """把一次控制动作的结果落地: 成功推进面板状态机, 失败保留状态并记 daemon 的码。"""
        if action in ("stop", "disarm"):
            self._invalidate_rx_demod("CONTROL_" + action.upper())
        result = result if isinstance(result, dict) else {}
        ok = result.get("ok") is True
        if action == self._rf_inflight_action:
            self._rf_inflight_action = None
            self._rf_inflight_stop = False
        self.rf_control["pending_action"] = None
        self.rf_control["last_action"] = action
        if ok:
            if action == "configure":
                applied = result.get("args", {})
                self._rf_applied_parameters = {k: applied[k] for k in RF_ACTION_PARAMS["configure"] if k in applied}
                # 本地配置代次: 采集结果只在它没变过的时候才允许落地(见
                # set_rx_capture_result), 因为一份 IQ 只属于它被采下来的那套配置。
                self._rx_config_revision += 1
                # 同一代次也是"当前分析还算不算现在接收"的判据: 新配置已经生效,
                # 屏上那份旧 IQ 属于旧 LO/采样率, 不能继续当着当前接收画。
                self._invalidate_rx_analysis_if_config_changed()
                self._invalidate_rf_loaded()
            elif action in ("stop", "disarm", "init", "arm"):
                self._invalidate_rx_demod("CONTROL_" + action.upper())
                self._invalidate_rf_loaded()
                if action in ("disarm", "init", "arm"):
                    self._rf_applied_parameters = {}
            elif action == "load":
                self._rf_loaded_scenario = None
                self._rf_tx_loaded = self.rf_status.get("tx_session_ready") is True and self._rf_control_online and self._rf_control_armed
                if self._rf_tx_loaded:
                    self._rf_loaded_kind = self._rf_signal_draft_kind
                    if self._rf_loaded_kind == 'environment' and isinstance(result.get('scenario_declaration'),dict):
                        self._rf_loaded_scenario = dict(result['scenario_declaration'])
                self._rf_include_impairment = False
            self._rf_control_state = RF_CONTROL_NEXT_STATE.get(
                action, self._rf_control_state)
            if action == "load" and not self._rf_tx_loaded:
                self._rf_control_state = "configured"
            self.rf_control["last_code"] = None
            self.rf_control["last_error"] = ""
        else:
            code = result.get("code")
            code = code if isinstance(code, str) and code else "UNKNOWN"
            message = result.get("message")
            message = message if isinstance(message, str) else ""
            self.rf_control["last_code"] = code
            self.rf_control["last_error"] = ("%s: %s" % (code, message)).strip(": ")
        self._refresh_rf_control()

    def set_rx_snapshot(self, snapshot, frames=None, metrics=None,
                        recommendation=None):
        """落地一份已完成/已失败的完整快照，不接受正在写入的 IQ 缓冲。"""
        snap = RxSnapshot.from_value(snapshot)
        if snap.decim not in RX_DECIM_OPTIONS:
            snap.state = "ERROR"
            snap.valid = False
            snap.error = "抽取配置无效"
        expected_fs = rx_output_fs(snap.input_fs_hz, snap.decim)
        if expected_fs is None or snap.output_fs_hz != expected_fs:
            snap.state = "ERROR"
            snap.valid = False
            snap.error = "采样率与抽取配置不一致"
        if snap.actual_len < 0 or snap.actual_len > snap.rx_len:
            snap.state = "ERROR"
            snap.valid = False
            snap.error = "实际点数超出冻结缓冲"
        self.rx_snapshot = snap
        if frames is not None:
            self.rx_frames = tuple(RxFrame.from_value(f) for f in frames)
        if metrics is not None:
            self.rx_metrics = RxMetrics.from_value(metrics)
        if recommendation is not None:
            self.recommendation = Recommendation.from_value(recommendation)
        self.rx_status_message = snap.error if snap.error else snap.state

    def set_rx_metrics(self, metrics):
        """替换分析 worker 发布的不可变指标；不由绘制线程计算。"""
        self.rx_metrics = RxMetrics.from_value(metrics)

    def set_recommendation(self, recommendation):
        """只记录建议，禁止把建议隐式写进 draft 或硬件。"""
        self.recommendation = Recommendation.from_value(recommendation)

    def rx_status(self, key):
        """返回显示用状态；None/未知必须由视图显示为破折号。"""
        return self.rx_metrics.statuses.get(key)

    # ---- 事务 ----

    def begin(self, action, channel=None):
        """校验并把 draft 冻结成命令计划。**不做任何 I/O**(照 AGENT_GUIDE: `ui_begin`
        本身不执行 I/O, 返回值用于显示失败原因)。

        成功时创建在途事务并返回 (OK, plan); 失败返回 (原因, None)。
        """
        if self.selected_device != "ACM2108" and action != STOP_DIGITAL:
            return UNSUPPORTED, None
        if action not in (APPLY_AWG, APPLY_DIGITAL, APPLY_CHANNEL,
                          START_DIGITAL, STOP_DIGITAL):
            return INVALID, None
        if self.pending:
            return BUSY, None
        if not self.connected:
            return DISCONNECTED, None
        # 显式 STOP 在状态过期时**仍允许** —— `SET DIGMOD MODE OFF` 是幂等的, 而且
        # "关不掉"比"关得掉但状态旧"更危险(AGENT_GUIDE 必须保留的行为 6)。
        if not self.fresh and action != STOP_DIGITAL:
            return STALE, None
        if action != STOP_DIGITAL:
            res, why = validate(self.draft)
            if res != OK:
                return "%s: %s" % (RESULT_TEXT[INVALID], why), None
        ci = self.selected_channel if channel is None else channel
        if action in (APPLY_DIGITAL, START_DIGITAL) and self.draft["dig"]["source"] != "PRBS7":
            return UNSUPPORTED, None
        # 运行中不允许改会改变输出路由的东西(模式选择/AWG 编辑) —— 参考设计比固件更保守,
        # 因为"参数改到一半时输出是什么"是歧义的(AGENT_GUIDE 必须保留的行为 1)。
        if self.running and action in (APPLY_AWG, APPLY_DIGITAL, START_DIGITAL):
            return RUNNING, None

        expected = self.actual_mode
        if action == APPLY_AWG:
            cmds = _plan_awg(self.draft, ci)
        elif action in (APPLY_DIGITAL, START_DIGITAL):
            cmds = _plan_digital(self.draft, start=(action == START_DIGITAL))
            if action == START_DIGITAL:
                expected = digmod_mode_to_protocol(self.draft["dig"]["mode"]) or "OFF"
        elif action == APPLY_CHANNEL:
            cmds = _plan_channel(self.draft, ci)
        else:                                     # STOP_DIGITAL
            cmds = ["SET DIGMOD MODE OFF\n", "GET DIGMOD\n"]
            expected = "OFF"
        if len(cmds) > MAX_COMMANDS:
            return OVERFLOW, None

        plan = CommandPlan(action, ci, clone(self.draft), cmds, expected)
        self.in_flight = plan
        self.pending = True
        self.log.extend(c.strip() for c in cmds)
        self.log = self.log[-70:]
        return OK, plan

    def finish(self, snapshot):
        """设备回读到达: 核对这次事务是否生效。

        `snapshot` 是调用方给的**已验证且属于当前事务**的回读(不能塞任意缓存结果进来)。
        回读不符时返回 MISMATCH 且**不假装回滚成功** —— 设备可能已经被改了一部分,
        照实报告, 由用户决定下一步(AGENT_GUIDE 必须保留的行为 5)。
        """
        if not self.pending or self.in_flight is None:
            return INVALID
        plan = self.in_flight
        if not snapshot.get("connected", True) or not snapshot.get("fresh", True):
            self.fail()
            return IO_ERROR
        cfg = snapshot["config"]
        mode = normalize_digmod_mode(snapshot.get("actual_mode", "OFF")) or "OFF"
        ok = self._matches(plan, cfg, mode)
        self.pending = False
        self.in_flight = None
        # ⚠ 回读不符时**不能**把设备的值同步进 draft —— 那会静默丢掉用户刚输入的值
        #   (设备报的是"没接受", 用户还想重试)。只更新 observed, 让界面显示差异。
        self.set_observed(cfg, mode, connected=True, resync_draft=ok)
        self.last_result = OK if ok else MISMATCH
        self.last_message = RESULT_TEXT[self.last_result]
        return self.last_result

    def fail(self):
        """事务失败/超时: **失效并重查**, 不重发、不假装回滚。

        设备可能已经部分改变, 所以唯一诚实的做法是把状态标成过期, 让界面显示"需要重新
        读取", 而不是继续显示一份可能不对的回读(AGENT_GUIDE 必须保留的行为 5/6)。
        """
        self.pending = False
        self.in_flight = None
        self.fresh = False
        self.last_result = IO_ERROR
        self.last_message = RESULT_TEXT[IO_ERROR]

    @staticmethod
    def _matches(plan, cfg, actual_mode):
        """回读是否与这次事务的目标一致。照 ui_model.c 的 matches()。

        **只比这次事务碰过的字段** —— 不要求整台设备与 draft 完全一致, 否则用户只改了
        频率却会因为别的字段还没同步而报不符。
        """
        if actual_mode != normalize_digmod_mode(plan.expected_mode) or \
                (plan.expected_mode == "OFF") != (actual_mode == "OFF"):
            return False
        if plan.action == STOP_DIGITAL:
            return True
        if plan.action in (APPLY_DIGITAL, START_DIGITAL):
            d, r = plan.target["dig"], cfg["dig"]
            # 选定的模式在 MODE OFF 时**观测不到**, 所以不比 mode 字段
            # ⚠ 符号率容 1 Hz: 硬件存的是 `symrate × 2^32 / 50e6` 的**截断**相位步进,
            #   反解回来恒偏小 1 Hz(实测全量程都是 1, 见 awg_ctl.decode_symstep)。
            #   不容差的话每一次改符号率都会误报"回读不符"。
            return (d["source"] == r["source"]
                    and abs(d["symrate_hz"] - r["symrate_hz"]) <= 1
                    and d["filter"] == r["filter"] and d["rolloff"] == r["rolloff"]
                    and d["seed"] == r["seed"])
        ci = plan.channel
        if plan.action == APPLY_AWG:
            return all(plan.target["awg"][ci][k] == cfg["awg"][ci][k]
                       for k in ("wave", "freq_hz", "ampl_pct", "duty_pct"))
        if plan.action == APPLY_CHANNEL:
            a, b = plan.target["chan"][ci], cfg["chan"][ci]
            if a["enabled"] != b["enabled"] or a["noise_pct"] != b["noise_pct"]:
                return False
            return all(a["taps"][k] == b["taps"][k] for k in range(TAPS))
        return False
