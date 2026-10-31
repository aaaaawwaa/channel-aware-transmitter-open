# 数字调制端到端集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已验证的独立 `iq_digital_mod` I/Q 发射器通过 GPIO11[19:17]、PS `SET/GET DIGMOD`、上位机方案 A 和双通道示波器时域验收接入 AC820 双 DAC，且 OFF 时完全保持现有 AWG 链路。

**Architecture:** `reg_bridge` 拆出 `gpio11[19:17]` 为全局 `digmod_sel`；`awg_top` 解码后启用 `iq_digital_mod`，在 `dac_driver` 前把 DAC0/DAC1 旁路为 I/Q，OFF 与保留编码安全回退标准链路。PS 固件新增共享 `DIGMOD` 状态与命令；上位机协议/控制器/预览/方案 A GUI 以回读确认为准。验证顺序：host 单元测试 → RTL testbench/xsim → bitstream → 固件重载 → 协议回归 → 双通道示波器时域。

**Tech Stack:** Verilog-2001/SystemVerilog（Vivado 2018.3 xsim）、C（Xilinx SDK 裸机）、Python 3.8 + PySide6 + NumPy + pytest、PyVISA 示波器。

---

## 文件结构与职责

| 文件 | 职责 |
|---|---|
| `src/rtl/reg_bridge.v` | 新增 `digmod_sel = gpio11[19:17]` 输出 |
| `src/rtl/awg_top.v` | 实例化 `iq_digital_mod`、解码 digmod、DAC I/Q mux |
| `src/tb/reg_bridge_tb.v` | digmod_sel 拆包与低 17 位保持用例 |
| `src/tb/awg_top_tb.v` | I/Q mux、OFF 回退、符号保持用例 |
| `sdk/src/main.c` | `digmod_mode`、`pack_word_f` 编码、`SET/GET DIGMOD` |
| `host/siggen/protocol.py` | `DIGMODES/digmod_cmd/get_digmod_cmd/parse_digmod_status` |
| `host/siggen/controller.py` | `DigitalModState`、`set_digmod/request_digmod_status`、DIGMOD 行处理 |
| `host/siggen/preview.py` | `make_digital_mod_data/make_constellation_data`、WavePreview 数字模式 |
| `host/siggen/ui/widgets.py` | `DigitalModPanel`、StatusPanel 视图切换 |
| `host/siggen/ui/main_window.py` | 顶栏页签、左栈、视图接线 |
| `host/tests/test_protocol.py` | DIGMOD 命令/解析测试 |
| `host/tests/test_controller.py` | DigitalModState 状态机测试 |
| `host/tests/test_preview.py` | 数字调制数据/星座测试 |
| `host/tests/test_widgets.py` | DigitalModPanel 与页签测试 |
| `scripts/test_bridge.py` | DIGMOD 协议回归用例 |
| `scripts/scope_verify.py` | 双通道 I/Q 时域段 |
| `docs/superpowers/specs/2026-08-26-digital-modulation-integration-design.md` | 已批准规格；本计划不重复修改 |

继续复用且不改契约：`src/rtl/iq_digital_mod.v`、`src/tb/iq_digital_mod_tb.v`、`host/siggen/digital_mod.py`、`host/tests/test_digital_mod.py`。`scripts/create_bd.tcl` 不修改。

---

### Task 1: 上位机协议层 DIGMOD

**Files:**
- Modify: `host/siggen/protocol.py`
- Test: `host/tests/test_protocol.py`

- [ ] **Step 1: 写入失败测试**

在 `host/tests/test_protocol.py` 末尾追加：

```python
def test_digmod_cmd_builds_set():
    assert digmod_cmd("QAM16") == "SET DIGMOD MODE QAM16"


@pytest.mark.parametrize("bad", ["QAM8", "8qam", "", None, 5])
def test_digmod_cmd_rejects_invalid(bad):
    with pytest.raises(ValueError):
        digmod_cmd(bad)


def test_get_digmod_cmd():
    assert get_digmod_cmd() == "GET DIGMOD"


@pytest.mark.parametrize("mode", ["OFF", "BPSK", "QPSK", "QAM4", "QAM16", "QAM64"])
def test_parse_digmod_status_accepts_all(mode):
    assert parse_digmod_status("DIGMOD MODE %s" % mode) == mode


@pytest.mark.parametrize(
    "bad",
    ["DIGMOD MODE", "DIGMOD MODE QAM8", "CH1 WAVE SINE", "DIGMOD FOO OFF"],
)
def test_parse_digmod_status_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_digmod_status(bad)


def test_parse_response_accepts_digmod_status_line():
    ok, text = parse_response("DIGMOD MODE QPSK")
    assert ok is True
    assert text == "DIGMOD MODE QPSK"
```

在 `host/tests/test_protocol.py` 顶部 import 列表加入：`digmod_cmd, get_digmod_cmd, parse_digmod_status`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest host/tests/test_protocol.py -q`
Expected: FAIL with `ImportError: cannot import name 'digmod_cmd'`。

- [ ] **Step 3: 实现协议函数**

在 `host/siggen/protocol.py` 的 `get_cmd` 之后加入：

```python
DIGMODES = ("OFF", "BPSK", "QPSK", "QAM4", "QAM16", "QAM64")


def digmod_cmd(mode) -> str:
    """构造 SET DIGMOD MODE 命令;非法模式抛 ValueError。"""
    name = str(mode).upper()
    if name not in DIGMODES:
        raise ValueError(f"bad digital modulation mode: {mode}")
    return f"SET DIGMOD MODE {name}"


def get_digmod_cmd() -> str:
    return "GET DIGMOD"


def parse_digmod_status(line: str) -> str:
    """解析 'DIGMOD MODE <value>' -> 模式字符串。"""
    parts = line.split()
    if len(parts) != 3 or parts[0] != "DIGMOD" or parts[1] != "MODE":
        raise ValueError(f"bad digmod status line: {line!r}")
    mode = parts[2]
    if mode not in DIGMODES:
        raise ValueError(f"bad digmod mode: {mode}")
    return mode
```

在 `parse_response` 中，紧接 `CH1`/`CH2` 判断之后加入：

```python
    if line.startswith("DIGMOD "):
        return True, line
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest host/tests/test_protocol.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/protocol.py host/tests/test_protocol.py
git commit -m "feat: 上位机协议层 DIGMOD 命令与解析"
```

---

### Task 2: 上位机控制器 DigitalModState

**Files:**
- Modify: `host/siggen/controller.py`
- Test: `host/tests/test_controller.py`

- [ ] **Step 1: 写入失败测试**

在 `host/tests/test_controller.py` 中追加：

```python
def test_digmod_default_off(ctrl):
    assert ctrl.digmod.mode == "OFF"


def test_set_digmod_sends_command(ctrl):
    ctrl.set_digmod("QAM16")
    assert ctrl.worker.sent[-1] == "SET DIGMOD MODE QAM16\r\n"
    assert ctrl._pending == (None, "digmod", "QAM16")


def test_set_digmod_invalid_no_send(ctrl):
    ctrl.set_digmod("QAM8")
    assert ctrl.worker.sent == []
    assert ctrl._pending is None


def test_digmod_ack_applies_state(ctrl):
    ctrl.set_digmod("QAM16")
    ctrl._on_line("OK")
    assert ctrl.digmod.mode == "QAM16"


def test_digmod_status_line_updates_state(ctrl):
    ctrl._on_line("DIGMOD MODE QPSK")
    assert ctrl.digmod.mode == "QPSK"


def test_digmod_ack_error_keeps_state(ctrl):
    ctrl.set_digmod("QAM16")
    ctrl._on_line("ERR BAD VALUE")
    assert ctrl.digmod.mode == "OFF"


def test_request_digmod_status_sends_get(ctrl):
    ctrl.request_digmod_status()
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest host/tests/test_controller.py -q`
Expected: FAIL（`Controller` 无 `digmod`、无 `set_digmod`）。

- [ ] **Step 3: 实现控制器支持**

`host/siggen/controller.py` 改动：

1) import 行加入：`from .protocol import (set_cmd, get_cmd, parse_response, parse_status, base_cmd, phase_cmd, phaselink_cmd, harm_cmd, mod_cmd, digmod_cmd, get_digmod_cmd, parse_digmod_status)`

2) `ChannelState` 之后新增 dataclass：

```python
@dataclass
class DigitalModState:
    mode: str = "OFF"
```

3) `Controller` 增加信号与状态：

```python
    digmod_changed = Signal(str)        # 已确认模式

    def __init__(self, parent=None):
        ...
        self.digmod = DigitalModState()
```

4) 新增方法（放在 `request_status` 之后）：

```python
    def set_digmod(self, mode: str) -> None:
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = digmod_cmd(mode)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._pending = (None, "digmod", mode)
        self.worker.send(cmd + "\r\n")
        self._timer.start(TIMEOUT_MS)
        self.log.emit(f">> {cmd}", "info")

    def request_digmod_status(self) -> None:
        if self.worker is None:
            return
        self.worker.send(get_digmod_cmd() + "\r\n")
```

5) `_on_line` 在 `CH1`/`CH2` 分支之后、pending 分支之前加入：

```python
        if line.startswith("DIGMOD "):
            try:
                self.digmod.mode = parse_digmod_status(line)
            except ValueError:
                self.log.emit(f"<< {line}", "error")
                return
            self.digmod_changed.emit(self.digmod.mode)
            self.log.emit(f"<< {line}", "ok")
            return
```

6) pending 分支开头（`st = self.state[ch]` 之前）加入：

```python
            if ok and field == "digmod":
                self.digmod.mode = str(value).upper()
                self.digmod_changed.emit(self.digmod.mode)
                self.log.emit(f"<< {line}", "ok")
                return
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest host/tests/test_controller.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/controller.py host/tests/test_controller.py
git commit -m "feat: 控制器全局 DigitalModState 状态机"
```

---

### Task 3: 上位机预览 I/Q 时域与星座

**Files:**
- Modify: `host/siggen/preview.py`
- Test: `host/tests/test_preview.py`

- [ ] **Step 1: 写入失败测试**

在 `host/tests/test_preview.py` 追加：

```python
import numpy as np
import pytest

from siggen.preview import make_constellation_data, make_digital_mod_data


def test_digital_mod_data_shape_and_bounds():
    t, i, q = make_digital_mod_data("QAM16", symbols=4, sym_ticks=5)
    assert len(t) == len(i) == len(q) == 20
    assert np.max(np.abs(i)) <= 1.0
    assert np.max(np.abs(q)) <= 1.0


def test_digital_mod_data_holds_symbols():
    t, i, q = make_digital_mod_data("QAM16", symbols=4, sym_ticks=5)
    assert np.array_equal(i[:5], np.full(5, i[0]))
    assert np.array_equal(q[:5], np.full(5, q[0]))


def test_digital_mod_data_sym_rate():
    t, i, q = make_digital_mod_data("BPSK", symbols=2, sym_ticks=10000)
    assert t[1] - t[0] == 0.0001          # 10 kSym/s
    assert len(i) == 20000


@pytest.mark.parametrize("mode, count", [("BPSK", 2), ("QPSK", 4), ("QAM4", 4),
                                         ("QAM16", 16), ("QAM64", 64)])
def test_constellation_data_count(mode, count):
    i, q = make_constellation_data(mode)
    assert len(i) == len(q) == count


def test_constellation_data_bounds():
    i, q = make_constellation_data("QAM64")
    assert np.max(np.abs(i)) <= 1.0 and np.max(np.abs(q)) <= 1.0


@pytest.mark.parametrize("fn", [make_digital_mod_data, make_constellation_data])
def test_digital_mod_rejects_bad_mode(fn):
    with pytest.raises(ValueError):
        fn("QAM8")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest host/tests/test_preview.py -q`
Expected: FAIL（函数不存在）。

- [ ] **Step 3: 实现纯函数**

在 `host/siggen/preview.py` 的 `make_mod_source_data` 之后、`WavePreview` 之前加入：

```python
def make_digital_mod_data(mode: str, symbols: int = 64, sym_ticks: int = 5000):
    """数字调制理论时域: I/Q 符号保持, 时间以秒计(10 kSym/s)。
    幅度归一化到 [-1,1](满量程 ±127)。"""
    from .digital_mod import MODES, generate_symbols
    if mode not in MODES:
        raise ValueError(f"bad digital modulation mode: {mode}")
    if symbols < 1 or sym_ticks <= 0:
        raise ValueError("symbols must be positive and sym_ticks positive")
    i_sym, q_sym = generate_symbols(mode, symbols)
    i = np.repeat(i_sym, sym_ticks)
    q = np.repeat(q_sym, sym_ticks)
    t = np.arange(len(i)) / 10000.0
    return t, i / 127.0, q / 127.0


def make_constellation_data(mode: str):
    """数字调制理论星座: 全星座点, 归一化到 [-1,1]。"""
    from .digital_mod import MODES, constellation_points
    if mode not in MODES:
        raise ValueError(f"bad digital modulation mode: {mode}")
    pts = constellation_points(mode)
    return pts[:, 0] / 127.0, pts[:, 1] / 127.0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest host/tests/test_preview.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/preview.py host/tests/test_preview.py
git commit -m "feat: 数字调制理论时域与星座数据"
```

---

### Task 4: GUI 方案 A（DigitalModPanel + 页签 + 视图切换）

**Files:**
- Modify: `host/siggen/ui/widgets.py`
- Modify: `host/siggen/ui/main_window.py`
- Test: `host/tests/test_widgets.py`

- [ ] **Step 1: 写入失败测试**

在 `host/tests/test_widgets.py` 追加：

```python
from siggen.ui.main_window import MainWindow
from siggen.ui.widgets import DigitalModPanel, StatusPanel


@pytest.fixture
def dig_panel():
    return DigitalModPanel()


def test_digital_panel_defaults(dig_panel):
    assert dig_panel.mode_combo.currentText() == "BPSK"
    assert dig_panel.note.text() == "当前：标准 AWG 输出"


def test_digital_panel_apply_emits_mode(dig_panel):
    got = []
    dig_panel.apply_requested.connect(got.append)
    dig_panel.mode_combo.setCurrentText("QAM16")
    dig_panel.apply_btn.click()
    assert got == ["QAM16"]


def test_digital_panel_off_emits(dig_panel):
    got = []
    dig_panel.off_requested.connect(lambda: got.append(1))
    dig_panel.off_btn.click()
    assert got == [1]


def test_digital_panel_set_confirmed(dig_panel):
    dig_panel.set_confirmed("QAM64")
    assert dig_panel.mode_combo.currentText() == "QAM64"
    assert dig_panel.note.text() == "当前：QAM64（已回读确认）"
    dig_panel.set_confirmed("OFF")
    assert dig_panel.note.text() == "当前：标准 AWG 输出"


def test_status_panel_dig_view_hidden_by_default():
    sp = StatusPanel()
    assert sp.dig_view.isVisible() is False
    sp.set_dig_view_visible(True)
    assert sp.dig_view.isVisible() is True


def test_main_window_has_tabs():
    w = MainWindow()
    assert w.tabs.count() == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest host/tests/test_widgets.py -q`
Expected: FAIL（`DigitalModPanel` 不存在、`MainWindow` 无 `tabs`）。

- [ ] **Step 3: 实现 DigitalModPanel 与 StatusPanel 视图控件**

`host/siggen/ui/widgets.py` 末尾（`StatusPanel` 之后）加入：

```python
class DigitalModPanel(QWidget):
    """共享数字调制面板: 模式 + 固定数据源/符号率 + 应用/关闭。"""
    apply_requested = Signal(str)     # 模式名
    off_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("数字调制"))
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("调制模式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["BPSK", "QPSK", "QAM4", "QAM16", "QAM64"])
        row1.addWidget(self.mode_combo)
        lay.addLayout(row1)
        lay.addWidget(QLabel("数据源：内部固定 PRBS7"))
        lay.addWidget(QLabel("符号率：10 kSym/s（固定）"))
        lay.addWidget(QLabel("DAC0 = I · DAC1 = Q（固定满量程）"))
        btns = QHBoxLayout()
        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(
            lambda: self.apply_requested.emit(self.mode_combo.currentText()))
        self.off_btn = QPushButton("关闭")
        self.off_btn.clicked.connect(self.off_requested.emit)
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.off_btn)
        lay.addLayout(btns)
        self.note = QLabel("当前：标准 AWG 输出")
        lay.addWidget(self.note)

    def set_mode(self, mode: str) -> None:
        if mode in ("BPSK", "QPSK", "QAM4", "QAM16", "QAM64"):
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentText(mode)
            self.mode_combo.blockSignals(False)

    def set_confirmed(self, mode: str) -> None:
        self.set_mode(mode)
        self.note.setText(
            "当前：标准 AWG 输出" if mode == "OFF" else f"当前：{mode}（已回读确认）")
```

`StatusPanel.__init__` 中，在 `addWidget(QLabel("波形预览"))` 之前加入：

```python
        self.dig_view = QComboBox()
        self.dig_view.addItems(["时域", "星座", "对比"])
        self.dig_view.setVisible(False)
        lay.addWidget(self.dig_view)
```

`StatusPanel` 追加方法：

```python
    def set_dig_view_visible(self, on: bool) -> None:
        self.dig_view.setVisible(on)
```

- [ ] **Step 4: 实现页签与接线**

`host/siggen/ui/main_window.py`：

1) import 改为：

```python
from PySide6.QtWidgets import (
    QMainWindow, QScrollArea, QSplitter, QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)
from .widgets import ChannelPanel, ConnectionBar, DigitalModPanel, StatusPanel
```

2) `__init__` 中，在创建 `self.status_panel` 后、`splitter` 之前加入左栈与数字页：

```python
        self.dig = DigitalModPanel()
        self.dig_scroll = QScrollArea()
        self.dig_scroll.setWidget(self.dig)
        self.dig_scroll.setWidgetResizable(True)
        self.dig_scroll.setMinimumWidth(420)
```

3) 把现有 `splitter.addWidget(mid_scroll)` 替换为左栈：

```python
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(mid_scroll)        # 0: 标准波形
        self.left_stack.addWidget(self.dig_scroll)   # 1: 数字调制
        splitter = QSplitter()
        splitter.addWidget(self.left_stack)
        splitter.addWidget(self.status_panel)
        splitter.setSizes([420, 600])
        self.tabs = QTabBar()
        self.tabs.addTab("标准波形")
        self.tabs.addTab("数字调制")
        self.tabs.currentChanged.connect(self._on_tab)
```

4) `root_lay` 加入页签：

```python
        root = QWidget()
        root_lay = QVBoxLayout(root)
        root_lay.addWidget(self.conn)
        root_lay.addWidget(self.tabs)
        root_lay.addWidget(splitter, 1)
        self.setCentralWidget(root)
```

5) 接线区加入：

```python
        self.dig.apply_requested.connect(self._on_digmod_apply)
        self.dig.off_requested.connect(lambda: self._on_digmod_apply("OFF"))
        self.status_panel.dig_view.currentIndexChanged.connect(self._on_dig_view)
        self.ctrl.digmod_changed.connect(self._on_digmod_state)
```

6) `_on_connect` 中 `request_status()` 后追加 `self.ctrl.request_digmod_status()`。

7) 新增方法：

```python
    def _on_tab(self, index: int):
        self.left_stack.setCurrentIndex(index)
        if index == 0:
            self.status_panel.set_dig_view_visible(False)
            self.status_panel.preview.clear_digital()
            self._on_state(0)
        else:
            self._on_digmod_state(self.ctrl.digmod.mode)

    def _on_digmod_apply(self, mode: str):
        self.ctrl.set_digmod(mode)

    def _on_dig_view(self, _index: int):
        if self.tabs.currentIndex() == 1:
            self._on_digmod_state(self.ctrl.digmod.mode)

    def _on_digmod_state(self, mode: str):
        self.dig.set_confirmed(mode)
        active = mode != "OFF"
        self.status_panel.set_dig_view_visible(active)
        if active:
            view = ["time", "constellation", "compare"][
                self.status_panel.dig_view.currentIndex()]
            self.status_panel.preview.set_digital(mode, view)
        else:
            self.status_panel.preview.clear_digital()
```

8) `host/siggen/preview.py` 的 `WavePreview`：

- `__init__` 增加三条数字曲线：

```python
        self._dig_i = self.plot([], [], pen=pg.mkPen("c", width=2), autoDownsample=True)
        self._dig_q = self.plot([], [], pen=pg.mkPen("m", width=2), autoDownsample=True)
        self._dig_scatter = self.plot([], [], pen=None, symbol="o",
                                      symbolSize=7, symbolBrush=(180, 120, 255, 200))
```

- `set_params` 开头（`self._params = ...` 之前）加入 `self.clear_digital()`。

- 追加方法：

```python
    def set_digital(self, mode: str, view: str = "time") -> None:
        """数字调制预览: time=I/Q 时域, constellation=理论星座, compare=时域+仅理论提示。"""
        self._params = None
        for curve in (self._curve, self._mod_curve, self._ch2_curve):
            curve.setData([], [])
        if view == "constellation":
            self._dig_scatter.setData(*make_constellation_data(mode))
            self._dig_i.setData([], [])
            self._dig_q.setData([], [])
            self.getPlotItem().setXRange(-1.1, 1.1, padding=0)
            self.getPlotItem().setYRange(-1.1, 1.1)
            self.setTitle(f"星座 {mode}（理论仿真）")
        else:
            t, i, q = make_digital_mod_data(mode, 64, 5000)
            self._dig_i.setData(t, i)
            self._dig_q.setData(t, q)
            self._dig_scatter.setData([], [])
            self.getPlotItem().setXRange(t[0], t[-1], padding=0)
            self.getPlotItem().setYRange(-1.2, 1.2)
            title = f"时域 {mode} · 10 kSym/s"
            if view == "compare":
                title += " · 对比：仅理论仿真，未接入采集数据"
            self.setTitle(title)

    def clear_digital(self) -> None:
        for curve in (self._dig_i, self._dig_q, self._dig_scatter):
            curve.setData([], [])
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest host/tests/test_widgets.py -q`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add host/siggen/ui/widgets.py host/siggen/ui/main_window.py host/siggen/preview.py host/tests/test_widgets.py
git commit -m "feat: GUI 数字调制页签与理论预览方案 A"
```

---

### Task 5: RTL reg_bridge 拆出 digmod_sel

**Files:**
- Modify: `src/rtl/reg_bridge.v`
- Test: `src/tb/reg_bridge_tb.v`

- [ ] **Step 1: 先写 testbench 用例（TDD RED）**

`src/tb/reg_bridge_tb.v` 改动：

1) 观测 wire 区加入 `wire [2:0] digmod_sel;`
2) 实例化端口加入 `.digmod_sel(digmod_sel),`
3) 在 `initial` 末尾（`if (errs == 0)` 汇总前）加入：

```verilog
        // ---- 数字调制全局模式: gpio11[19:17] = digmod_sel ----
        // 与相位联动/两路 DUTY 同字, 低 17 位必须保持
        gpio11_in = {12'd0, 3'd4, 8'd75, 8'd25, 1'b1};   // QAM16, duty 25/75, link on
        #40;
        chk("digmod_sel unpack", digmod_sel == 3'd4);
        chk("digmod preserves duty", ch1_pulse_duty == 8'd25 && ch2_pulse_duty == 8'd75);
        chk("digmod preserves link", ch_link_en == 1'b1);
        gpio11_in = {12'd0, 3'd7, 8'd50, 8'd50, 1'b0};   // 保留编码 7 直通
        #40;
        chk("digmod_sel reserved passes", digmod_sel == 3'd7);
        gpio11_in = 32'd0;
        #40;
        chk("digmod_sel off", digmod_sel == 3'd0);
```

- [ ] **Step 2: 编译 testbench 确认失败**

Run:

```bash
<LOCAL_PATH> -sv src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
<LOCAL_PATH> reg_bridge_tb -s reg_bridge_tb_sim
<LOCAL_PATH> reg_bridge_tb_sim -runall
```

Expected: xvlog 因 `reg_bridge` 无 `digmod_sel` 端口而失败（端口不匹配）。

- [ ] **Step 3: 实现 reg_bridge 输出**

`src/rtl/reg_bridge.v`：

1) 端口声明区（`ch_phase_off` 之后）加入：

```verilog
    output wire [2:0] digmod_sel,
```

2) 在 `assign ch_link_en = r11[0];` 附近加入：

```verilog
    assign digmod_sel = r11[19:17];
```

- [ ] **Step 4: 重跑 xsim 确认通过**

Run:

```bash
<LOCAL_PATH> -sv src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
<LOCAL_PATH> reg_bridge_tb -s reg_bridge_tb_sim
<LOCAL_PATH> reg_bridge_tb_sim -runall
```

Expected: `PASS reg_bridge_tb`。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
git commit -m "feat: reg_bridge 拆出 digmod_sel(gpio11[19:17])"
```

---

### Task 6: RTL awg_top I/Q mux

**Files:**
- Modify: `src/rtl/awg_top.v`
- Test: `src/tb/awg_top_tb.v`

- [ ] **Step 1: 先写 testbench 用例（TDD RED）**

`src/tb/awg_top_tb.v`：在 CH2 AM 段之后、相位联动段（`gpio10_in = 32'h4000_0000`）之前插入数字调制段：

```verilog
        // ---- 数字调制 I/Q 旁路(DAC0=I, DAC1=Q) ----
        // BPSK: DAC0(I) ∈ {0x01, 0xFF}, DAC1(Q) 恒 0x80
        gpio0_in = pack(20'd1000, 8'd100, 3'd0);
        gpio1_in = pack(20'd1000, 8'd50, 3'd0);
        gpio10_in = 32'd0;
        gpio11_in = {12'd0, 3'd1, 8'd50, 8'd50, 1'b0};   // sel=1 -> BPSK
        #1000;
        reset_stats;
        #400000;   // 8 ms -> ~80 symbols @ 100 us
        chk("digmod BPSK enable", uut.digmod_enable === 1'b1);
        chk("BPSK I offset-binary extreme", ch0_min == 8'h01 && ch0_max == 8'hFF);
        chk("BPSK I holds symbols", ch0_trans < 500);
        chk("BPSK Q constant 0x80", ch1_min == 8'h80 && ch1_max == 8'h80);
        chk("BPSK Q no transitions", ch1_trans == 0);

        // QAM16: I/Q ∈ {0x02, 0x26, 0xAA, 0xFE}
        gpio11_in = {12'd0, 3'd4, 8'd50, 8'd50, 1'b0};   // sel=4 -> QAM16
        #1000;
        reset_stats;
        #400000;
        chk("digmod QAM16 enable", uut.digmod_enable === 1'b1);
        chk("QAM16 I offset-binary range", ch0_min >= 8'h02 && ch0_max <= 8'hFE);
        chk("QAM16 I holds symbols", ch0_trans < 500);
        chk("QAM16 Q holds symbols", ch1_trans < 500);

        // OFF -> 标准 AWG 恢复
        gpio11_in = 32'd0;
        #1000;
        reset_stats;
        #200000;   // 4 ms -> ~4 sine periods @ 1 kHz
        chk("digmod off enable low", uut.digmod_enable === 1'b0);
        chk("standard sine restored", ch0_trans > 1000);
        chk("standard sine bipolar", ch0_min < 8'h80 && ch0_max > 8'h80);
```

- [ ] **Step 2: 编译 testbench 确认失败**

Run:

```bash
<LOCAL_PATH> -sv src/rtl/*.v src/tb/awg_top_tb.v
<LOCAL_PATH> awg_top_tb -s awg_top_tb_sim
<LOCAL_PATH> awg_top_tb_sim -runall
```

Expected: xvlog 失败——`awg_top` 尚无 `iq_digital_mod`/`digmod_enable`，且 `reg_bridge` 端口不匹配。

- [ ] **Step 3: 实现 awg_top mux**

`src/rtl/awg_top.v`：

1) `reg_bridge` 端口连接加入 `.digmod_sel(digmod_sel),`；在 `wire ch_link_en; ...` 附近声明：

```verilog
    wire [2:0] digmod_sel;
```

2) `ampl_scale` 两实例之后、`dac_driver` 之前加入：

```verilog
    // ---- 数字调制: 全局 digmod_sel 控制共享 I/Q 发射器旁路 ----
    wire digmod_enable = (digmod_sel >= 3'd1) && (digmod_sel <= 3'd5);
    wire [2:0] digmod_mode = digmod_sel - 3'd1;
    wire signed [7:0] dig_i, dig_q;
    iq_digital_mod u_dig (
        .clk(sys_clk), .rst_n(rst_n), .enable(digmod_enable), .mode(digmod_mode),
        .i_sample(dig_i), .q_sample(dig_q), .symbol_valid()
    );
    wire [7:0] dac_in0 = digmod_enable ? dig_i : a1;
    wire [7:0] dac_in1 = digmod_enable ? dig_q : a2;
```

3) `dac_driver` 端口改为 `.sample0(dac_in0), .sample1(dac_in1),`。

- [ ] **Step 4: 重跑 xsim 确认通过**

Run:

```bash
<LOCAL_PATH> -sv src/rtl/*.v src/tb/awg_top_tb.v
<LOCAL_PATH> awg_top_tb -s awg_top_tb_sim
<LOCAL_PATH> awg_top_tb_sim -runall
```

Expected: `PASS awg_top_tb`。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/awg_top.v src/tb/awg_top_tb.v
git commit -m "feat: awg_top 数字调制 I/Q 旁路与 OFF 回退"
```

---

### Task 7: PS 固件 SET/GET DIGMOD

**Files:**
- Modify: `sdk/src/main.c`

- [ ] **Step 1: 修改固件**

`sdk/src/main.c`：

1) 文件头注释（SET 列表区）加入两行：

```c
 *   SET DIGMOD MODE OFF|BPSK|QPSK|QAM4|QAM16|QAM64 -> OK
 *   GET DIGMOD                                  -> DIGMOD MODE <value>
```

2) `MODE_NAME` 声明后加入：

```c
static const char *DIGMOD_NAME[6] = {"OFF", "BPSK", "QPSK", "QAM4", "QAM16", "QAM64"};
static u32 digmod_mode = 0;   /* 0=OFF 1=BPSK 2=QPSK 3=QAM4 4=QAM16 5=QAM64 */
```

3) `pack_word_f` 改为：

```c
static u32 pack_word_f(const ch_state_t *s1, const ch_state_t *s2)
{
    return s2->phaselink | (s1->pulse_duty << 1) | (s2->pulse_duty << 9)
         | (digmod_mode << 17);
}
```

4) `do_line` 的 GET 分支开头（`int ci;` 之前）加入：

```c
        if (n == 2 && strcmp(tok[1], "DIGMOD") == 0) {
            char out[64];
            sprintf(out, "DIGMOD MODE %s\r\n", DIGMOD_NAME[digmod_mode]);
            uart_send(out);
            return;
        }
```

5) `do_line` 的 SET 分支开头（`int ci;` 之前）加入：

```c
        if (strcmp(tok[1], "DIGMOD") == 0) {
            if (n != 4 || strcmp(tok[2], "MODE") != 0) {
                uart_send("ERR BAD VALUE\r\n"); return;
            }
            int m;
            for (m = 0; m < 6; m++)
                if (strcmp(tok[3], DIGMOD_NAME[m]) == 0) break;
            if (m >= 6) { uart_send("ERR BAD VALUE\r\n"); return; }
            digmod_mode = (u32)m;
            write_all();
            uart_send("OK\r\n");
            return;
        }
```

- [ ] **Step 2: 重新构建 SDK ELF**

```bash
export PATH="/d/Vivado/SDK/2018.3/gnu/aarch32/nt/gcc-arm-none-eabi/bin:$PATH"
make -C sdk/ws/awg_bridge/Debug clean all
```

Expected: ARM 编译/链接成功生成 `sdk/ws/awg_bridge/Debug/awg_bridge.elf`（PRE_BUILD 步骤报错可忽略，ELF 必须更新且含 DIGMOD 字符串）。若 `sdk/ws/awg_bridge/src/main.c` 是旧副本，先从 `sdk/src/main.c` 同步再构建。

- [ ] **Step 3: 提交（固件源码）**

```bash
git add sdk/src/main.c
git commit -m "feat: PS 固件 SET/GET DIGMOD 全局数字调制命令"
```

板级验证（重载 ELF + 协议回归）在 Task 9。

---

### Task 8: 构建并校验 bitstream

**Files:**
- Run: `scripts/build_bitstream.py`、`scripts/check_bitstream.py`

- [ ] **Step 1: 完整重建**

```bash
python scripts/build_bitstream.py
```

Expected: 检测 `src/rtl/*.v` 变化 → 重跑 create_bd + OOC + 主综合 + 实现 + bit。产物 `awg/awg.runs/impl_1/host_bd_wrapper.bit`。

- [ ] **Step 2: 校验**

```bash
python scripts/check_bitstream.py
```

Expected: 退出码 0，输出 `OK: bitstream 与当前 RTL 一致`。

- [ ] **Step 3: 提交构建哈希记录（若 build_manifest.json 变化）**

```bash
git add build_manifest.json
git commit -m "build: 数字调制集成 bitstream 构建清单"
```

---

### Task 9: 板级协议回归（test_bridge.py DIGMOD）

**Files:**
- Modify: `scripts/test_bridge.py`
- Run: `scripts/test_bridge.py`

前置：新 bitstream 已下载、新 ELF 已加载。ELF 加载用绝对路径：

```bash
cli-anything-vivado --project "<LOCAL_PATH>" sdk run \
  --elf "<LOCAL_PATH>" \
  --hw-platform "<LOCAL_PATH>"
```

- [ ] **Step 1: 在复位循环加入 DIGMOD 复位**

`scripts/test_bridge.py` 中双通道复位循环之后追加：

```python
raw("SET DIGMOD MODE OFF")
```

- [ ] **Step 2: 加入 DIGMOD 用例**

在 `cmd("SET CH1 WAVE SINE", "OK")`（PULSE/NOISE 段结尾）之后、`# 非法输入` 之前插入：

```python
# ---- 数字调制 DIGMOD(共享 I/Q 全局状态) ----
cmd("SET DIGMOD MODE OFF", "OK")
cmd("GET DIGMOD", "DIGMOD MODE OFF")
cmd("SET DIGMOD MODE BPSK", "OK")
cmd("GET DIGMOD", "DIGMOD MODE BPSK")
for _m in ("QPSK", "QAM4", "QAM16", "QAM64"):
    cmd("SET DIGMOD MODE %s" % _m, "OK")
    cmd("GET DIGMOD", "DIGMOD MODE %s" % _m)
cmd("SET DIGMOD MODE OFF", "OK")
# 非法模式 / 缺少模式 / 额外 token / 非法顶层
cmd("SET DIGMOD MODE QAM8", "ERR BAD VALUE")
cmd("SET DIGMOD MODE", "ERR BAD VALUE")
cmd("SET DIGMOD MODE QAM16 EXTRA", "ERR BAD VALUE")
cmd("GET DIGMODX", "ERR BAD CHANNEL")
# 标准通道状态不受 DIGMOD 影响
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
```

- [ ] **Step 3: 运行协议回归**

```bash
python scripts/test_bridge.py COM11
```

Expected: `RESULT: ALL PASS`（含新增 DIGMOD 用例）。

- [ ] **Step 4: 提交**

```bash
git add scripts/test_bridge.py
git commit -m "test: 板级 DIGMOD 协议回归用例"
```

---

### Task 10: 双通道示波器时域（scope_verify.py）

**Files:**
- Modify: `scripts/scope_verify.py`
- Run: `scripts/scope_verify.py`

- [ ] **Step 1: 加入数字调制双通道段**

`scripts/scope_verify.py` 在相位联动段结束之后、`inst.close()` 之前插入：

```python
    # ---- 数字调制双通道时域(DAC0=I, DAC1=Q) ----
    # 垂直刻度量化使示波器码值≠原始 DAC 码; 这里断言电平对称性与符号保持,
    # 不断言精确码值(见规格风险表)。
    def _max_run(y):
        best = 1
        cur = 1
        for a, b in zip(y, y[1:]):
            cur = cur + 1 if a == b else 1
            best = max(best, cur)
        return best

    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 50"])
    setup_board(["SET DIGMOD MODE OFF"])
    for name, sel, i_levels, q_levels in (
        ("BPSK", 1, 2, 1), ("QAM16", 4, 4, 4),
    ):
        setup_board(["SET DIGMOD MODE %s" % name])
        scope_acq(2e-3, 2.0)          # 20 ms 窗 -> ~200 符号
        xinc, y0 = scope_wave("CHAN1")
        _, y1 = scope_wave("CHAN2")
        i_uniq = sorted(set(y0))
        q_uniq = sorted(set(y1))
        i_ok = 1 < len(i_uniq) <= i_levels
        q_ok = 1 <= len(q_uniq) <= q_levels
        c0 = (min(y0) + max(y0)) / 2.0
        span0 = float(max(y0) - min(y0))
        sym_ok = abs((min(y0) - c0) - (max(y0) - c0)) < span0 * 0.25
        hold_ok = _max_run(y0) * xinc >= 60e-6     # ≥60 µs 符号保持(100 µs/符号)
        if name == "BPSK":
            q_const = len(q_uniq) == 1
            detail = "I=%d Q=%d hold=%.0fus" % (len(i_uniq), len(q_uniq), _max_run(y0) * xinc * 1e6)
            report("BPSK I/Q 双通道时域", i_ok and q_const and sym_ok and hold_ok, detail)
        else:
            detail = "I=%d Q=%d hold=%.0fus" % (len(i_uniq), len(q_uniq), _max_run(y0) * xinc * 1e6)
            report("QAM16 I/Q 双通道时域", i_ok and q_ok and sym_ok and hold_ok, detail)
    setup_board(["SET DIGMOD MODE OFF"])
    scope_acq(2e-3, 2.0)
    _, y0 = scope_wave("CHAN1")
    off_ok = (min(y0) < 0x80) and (max(y0) > 0x80)
    report("DIGMOD OFF 恢复标准正弦", off_ok, "min=%d max=%d" % (min(y0), max(y0)))
```

- [ ] **Step 2: 运行示波器闭环**

```bash
python scripts/scope_verify.py COM11
```

Expected: `RESULT: n/n PASS`，新增 BPSK/QAM16/OFF 三项通过。若对称容差 0.25 因垂直刻度偏紧，按规格风险行调整 `sym_ok` 阈值并复测；不得改为始终通过。

- [ ] **Step 3: 提交**

```bash
git add scripts/scope_verify.py
git commit -m "test: 数字调制双通道示波器时域段"
```

---

### Task 11: 全量回归与验收

**Files:**
- Run: 回归命令；Review: `docs/superpowers/specs/2026-08-26-digital-modulation-integration-design.md`

- [ ] **Step 1: 上位机全量测试**

```bash
python -m pytest host/tests/ -q
```

Expected: 全部通过（含新增 test_protocol/test_controller/test_preview/test_widgets 用例），无既有用例被弱化或跳过。

- [ ] **Step 2: RTL 全量 xsim**

分别运行 reg_bridge_tb、awg_top_tb、iq_digital_mod_tb（Task 5/6 命令），并追加独立模块黄金/5000 拍验证（遵循 `verifying-iq-digital-modulation` 技能流程）：

```bash
<LOCAL_PATH> -sv src/rtl/iq_digital_mod.v src/tb/iq_digital_mod_tb.v
<LOCAL_PATH> iq_digital_mod_tb -s iq_digital_mod_sim
<LOCAL_PATH> iq_digital_mod_sim -runall > iq_digital_mod.sim.log
PYTHONPATH=host python -c "import re; from pathlib import Path; from siggen.digital_mod import generate_symbols; text=Path('iq_digital_mod.sim.log').read_text(encoding='utf-8', errors='replace'); rows=[tuple(map(int, x)) for x in re.findall(r'GOLD mode=(\d+) index=(\d+) i=(-?\d+) q=(-?\d+)', text)]; names=('BPSK','QPSK','QAM4','QAM16','QAM64'); actual={m:[(i,q) for _,i,q in sorted((n,i,q) for k,n,i,q in rows if k==m)] for m in range(5)}; expected={m:[(int(i),int(q)) for i,q in zip(*generate_symbols(name,127))] for m,name in enumerate(names)}; assert all(len(actual[m])==127 for m in range(5)); assert actual==expected"
```

Expected: 三台均 `PASS`，GOLD 5×127 逐点一致。生产 5000 拍验证沿用技能：临时副本替换 `SYM_TICKS=5000` 后重跑。

- [ ] **Step 3: 验收清单核对**

按规格 §7 逐项确认为真：

```text
[ ] DIGMOD OFF 时现有 AWG/调制/相位联动回归通过
[ ] BPSK/QPSK/QAM4/QAM16/QAM64 可经 SET/GET 完整切换与回读
[ ] gpio11 低 17 位语义不变
[ ] 保留编码 6/7 安全回退为标准 AWG
[ ] DAC0=I、DAC1=Q，固定满量程且无削顶
[ ] Python/RTL GOLD 5 × 127 完全一致
[ ] 生产 5000 tick xsim 通过
[ ] GUI 仅在回读确认后显示模式，且不伪造实测
[ ] 双通道示波器时域验证通过
```

- [ ] **Step 4: 集成边界收口**

不修改 `scripts/create_bd.tcl`、`src/rtl/iq_digital_mod.v`、`host/siggen/digital_mod.py` 及其测试。可调符号率/幅度、外部数据、RRC、采集导入与实测星座重建留待后续阶段。

- [ ] **Step 5: 提交文档**

```bash
git add README.md CLAUDE.md
git commit -m "docs: 数字调制集成使用与回归说明"
```

---

## 自审记录

- **Spec 覆盖**：GPIO11[19:17]（T5）、I/Q mux 与 OFF/保留回退（T6）、SET/GET DIGMOD（T7）、GUI 方案 A 与仅理论预览（T3/T4）、双通道示波器时域（T10）、协议回归（T9）、bitstream（T8）均覆盖。
- **占位符**：无 TBD/TODO；scope 段给出可直接复制的代码与命令。
- **类型一致**：`DIGMODES`/`digmod_cmd`/`parse_digmod_status` 在 T1 定义并被 T2 使用；`set_digital(mode, view)` 在 T3 定义并被 T4 接线使用；RTL 的 `digmod_sel`/`digmod_enable`/`digmod_mode` 在 T5/T6 一致；固件 `digmod_mode` 与 `pack_word_f` 在 T7 一致。
- **安全边界**：不修改 BD、独立模块契约或既有回归；OFF/保留编码直通标准链路。

Git 提交按任务提供；`master` 上既有未提交文件（含本规格与 AGENTS.md 等）不属于本计划提交范围。
