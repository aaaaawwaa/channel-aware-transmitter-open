"""理论波形预览:纯函数生成数据 + pyqtgraph 控件封装。

调制路径按 50MHz 相位积分(与硬件同构: dds -> am_stage(±127 饱和) ->
ampl_scale), 输出先钳位 ±1(硬件饱和)再乘 ampl_pct; 低频载波时积分网格
封顶 GRID_CAP(显示本就混叠)防 GUI 卡顿; NOISE 使用与 RTL 相同的确定性 LFSR 序列。
"""
import numpy as np
import pyqtgraph as pg

PERIODS = 3      # 默认显示 3 个载波周期
MAX_CARRIER_PERIODS = 50  # 调制开启时窗口内载波周期上限(防混叠)
MOD_FS = 50e6    # 与硬件一致的调制/相位积分采样率
GRID_CAP = 2_000_000  # 调制积分网格上限(低频载波时 ns 巨大, 显示本就混叠, 粗网格足够)


_LFSR_MASK = 0xFF
_NOISE_VALUES = np.empty(255, dtype=float)
_lfsr_state = 0x01
for _i in range(255):
    _NOISE_VALUES[_i] = (
        (_lfsr_state & 0x7F) if _lfsr_state & 0x80 else _lfsr_state - 128
    ) / 127.0
    _lfsr_state = ((_lfsr_state << 1) & _LFSR_MASK) | (
        ((_lfsr_state >> 7) ^ (_lfsr_state >> 5) ^
         (_lfsr_state >> 4) ^ (_lfsr_state >> 3)) & 1
    )


def _noise_waveform(noise_steps):
    steps = np.asarray(noise_steps, dtype=np.int64)
    return _NOISE_VALUES[np.mod(steps, len(_NOISE_VALUES))]


def _noise_step_index(cycles):
    # t grids generated at different window sizes must agree at phase boundaries.
    return np.floor(np.round(np.asarray(cycles) * 256.0, 9)).astype(np.int64)


def display_periods(freq_hz, mod) -> int:
    """预览窗口(载波周期数)。调制开启时窗口至少覆盖 1 个完整调制周期
    (调制比 3..50), 否则封顶 50 载波周期——否则低频调制时窗口内载波
    完全混叠, 调制源/包络形状与示波器对不上(固定 3 载波周期是历史坑)。"""
    if not mod or mod.get("mode", "OFF") == "OFF":
        return PERIODS
    mfreq = mod.get("mfreq", 0) or 0
    if mfreq <= 0 or freq_hz <= 0:
        return PERIODS
    ratio = freq_hz / mfreq              # 载波周期数 / 调制周期数
    if ratio <= PERIODS:
        return PERIODS                    # 3 载波周期已含调制周期
    if ratio <= MAX_CARRIER_PERIODS:
        return int(round(ratio))          # 恰好 1 个完整调制周期
    return MAX_CARRIER_PERIODS


def _waveform(wave, phase, base_pct, harms, pulse_duty=50, noise_steps=None):
    """phase 为周期单位数组/标量;SINE 含谐波叠加(理论同相, 与硬件一致)。"""
    if wave == "NOISE":
        if noise_steps is None:
            raise ValueError("noise_steps required")
        return _noise_waveform(noise_steps)
    if wave == "SINE":
        y = np.zeros(np.asarray(phase).shape)
        y += (base_pct / 100.0) * np.sin(2 * np.pi * phase)
        for order, a in (harms or []):
            if order:
                y += (a / 100.0) * np.sin(2 * np.pi * phase * order)
        return y
    if wave == "SQUARE":
        return np.where(phase < 0.5, 1.0, -1.0)
    if wave == "PULSE":
        duty = min(max(int(pulse_duty), 0), 100)
        return np.where((duty == 100) | (phase < duty / 100.0), 1.0, -1.0)
    if wave == "TRIANGLE":
        return 4 * np.abs(phase - 0.5) - 1
    if wave == "SAW":
        return 2 * phase - 1
    raise ValueError(f"bad wave: {wave}")


def _mod_wave(mwave, phase):
    if mwave == "SINE":
        return np.sin(2 * np.pi * phase)
    if mwave == "SQUARE":
        return np.where(phase < 0.5, 1.0, -1.0)
    if mwave == "TRIANGLE":
        return 1 - 4 * np.abs(phase - 0.5)
    return 2 * phase - 1


def _mod_int(mwave, phase):
    """调制源在 [0, phase] 内的相位积分(周期单位): ∫_0^φ m(2πu) du。
    零均值波形, 整周期积分为 0, 只需余数段积分。用于 FM 绝对时间轴
    初相计算(任意窗口显示同一段波形, 缩放前后一致)。"""
    if mwave == "SINE":
        return (1.0 - np.cos(2 * np.pi * phase)) / (2 * np.pi)
    if mwave == "SQUARE":
        return np.where(phase < 0.5, phase, 1.0 - phase)
    if mwave == "TRIANGLE":
        return np.where(phase < 0.5,
                        2.0 * phase * phase - phase,
                        -2.0 * phase * phase + 3.0 * phase - 1.0)
    return phase * phase - phase       # SAW: m = 2φ-1


def make_plot_data(wave: str, freq_hz: int, ampl_pct: int,
                   base_pct: int = 100, harms=None, n: int = 10000, mod=None,
                   window_s: float = None, t_start: float = 0.0,
                   phase_off: float = 0.0, pulse_duty: int = 50):
    """返回 (t, y)。harms = [(order, ampl_pct), ...] 归一化值。
    mod = dict(mode, mwave, mfreq, am_depth, fm_dev, pm_dev), 与硬件寄存器同义;
    mfreq 单位 Hz(硬件寄存器是 0.1Hz 单位 mfreq_01, GUI 层换算),
    pm_dev 单位 1/256 周期;mode OFF/缺省时与无调制路径逐位一致。
    phase_off: 载波相位偏移, 单位为周期。
    window_s: 强制时间窗口(秒), 缺省用 display_periods 自适应(预览缩放
    超出数据窗口时由 WavePreview 传更大的 window_s 重新生成)。
    t_start: 窗口起点(绝对时间轴, 可为负)——波形相位始终以绝对 t=0 为
    基准, 任何窗口显示的都是同一段波形, 缩放前后一致(初始窗口 = t_start=0)。"""
    if freq_hz <= 0:
        return np.zeros(n), np.zeros(n)
    mod = mod or {}
    if window_s is not None and window_s > 0:
        periods = window_s * freq_hz       # 任意窗口 -> 等效载波周期数(可非整数)
    else:
        periods = display_periods(freq_hz, mod)
    if mod.get("mode", "OFF") == "OFF":
        t = np.linspace(t_start, t_start + periods / freq_hz, n, endpoint=False)
        phase = (t * freq_hz + phase_off) % 1.0
        noise_steps = _noise_step_index(t * freq_hz + phase_off)
        y = _waveform(wave, phase, base_pct, harms, pulse_duty, noise_steps)
        return t, y * ampl_pct / 100.0

    # 调制路径: 50MHz 相位积分(低频载波时网格封顶 GRID_CAP) + 抽取到 n 点
    total_s = periods / freq_hz
    ns = max(int(round(total_s * MOD_FS)), 2)
    g_ns = min(ns, GRID_CAP)
    dt = total_s / g_ns
    t_abs = t_start + np.arange(g_ns) * dt        # 绝对时间轴
    mfreq = mod.get("mfreq", 1.0)
    m_ph = (t_abs * mfreq) % 1.0
    m = _mod_wave(mod.get("mwave", "SINE"), m_ph)
    mode = mod["mode"]
    if mode == "AM":
        gain = 1.0 + m * mod.get("am_depth", 0) / 100.0
        phase = (t_abs * freq_hz + phase_off) % 1.0
        noise_steps = _noise_step_index(t_abs * freq_hz + phase_off)
        y = _waveform(wave, phase, base_pct, harms, pulse_duty, noise_steps) * gain
    elif mode in ("FM", "SWEEP"):
        f_inst = freq_hz + m * mod.get("fm_dev", 0)
        # 绝对时间轴初相: θ(t_start) = freq·t_start + dev·∫m(整周期积分为 0)
        theta0_unwrapped = (phase_off + t_start * freq_hz
                            + mod.get("fm_dev", 0) * _mod_int(
                                mod.get("mwave", "SINE"), m_ph[0]) / mfreq)
        theta0 = theta0_unwrapped % 1.0
        phase_cycles = theta0 + np.cumsum(f_inst) * dt
        phase = phase_cycles % 1.0
        noise_cycles = theta0_unwrapped + np.cumsum(f_inst) * dt
        noise_steps = _noise_step_index(noise_cycles)
        y = _waveform(wave, phase, base_pct, harms, pulse_duty, noise_steps)
    elif mode == "PM":
        off = m * mod.get("pm_dev", 0) / 256.0          # 周期单位
        phase = (t_abs * freq_hz + off + phase_off) % 1.0
        noise_steps = _noise_step_index(t_abs * freq_hz + phase_off)
        y = _waveform(wave, phase, base_pct, harms, pulse_duty, noise_steps)
    else:
        raise ValueError(f"bad mod mode: {mode}")
    # 饱和在抽取前的全网格进行(与硬件样本级饱和一致): 饱和平台起点由
    # 全网格(0.02us 分辨率)决定, 与窗口/输出网格无关——缩放前后边缘一致。
    # 若在抽取后 clip, 平台起点被输出网格量化(不同窗口错位, 放大回波形不同)。
    y = np.clip(y, -1, 1)
    # 抽取: 输出点 i 对应时间 i*total_s/n -> 网格索引 i*g_ns/n(线性插值)
    k = np.linspace(0, g_ns, n, endpoint=False)
    y_out = np.interp(k, np.arange(g_ns), y)
    t = np.linspace(t_start, t_start + total_s, n, endpoint=False)
    return t, y_out * ampl_pct / 100.0


def make_mod_source_data(freq_hz, mod, n: int = 10000, window_s: float = None,
                         t_start: float = 0.0):
    """调制源波形 (t, m), 窗口与 make_plot_data 一致(自适应: 调制开启时
    至少覆盖 1 个完整调制周期;window_s 强制任意窗口)。调制源是纯波形函数
    m(t) = f(mfreq·t mod 1),无需相位积分(精确);mode=OFF / 无 mod 时返回
    全零(供曲线隐藏)。t_start: 绝对时间轴起点(与 make_plot_data 对齐)。"""
    if freq_hz <= 0 or not mod or mod.get("mode", "OFF") == "OFF":
        return np.linspace(0, PERIODS / freq_hz if freq_hz > 0 else 1.0, n, endpoint=False), np.zeros(n)
    if window_s is not None and window_s > 0:
        periods = window_s * freq_hz
    else:
        periods = display_periods(freq_hz, mod)
    t = np.linspace(t_start, t_start + periods / freq_hz, n, endpoint=False)
    m = _mod_wave(mod.get("mwave", "SINE"), (t * mod.get("mfreq", 1.0)) % 1.0)
    return t, m


def make_digital_mod_data(mode: str, symbols: int = 64, sym_ticks: int = 5000,
                          *, symrate_hz: int = 10_000, filter_name: str = "RECT",
                          rolloff: str = "0.35"):
    """数字调制理论时域: I/Q 符号保持或 RRC 成形(支持全部模式)。"""
    from .digital_mod import MODES, generate_symbols
    if mode not in MODES:
        raise ValueError(f"bad digital modulation mode: {mode}")
    if symbols < 1 or sym_ticks <= 0:
        raise ValueError("symbols must be positive and sym_ticks positive")
    filter_name = str(filter_name).upper()
    if filter_name not in ("RECT", "RRC"):
        raise ValueError(f"bad digital modulation filter: {filter_name}")
    if filter_name == "RRC":
        from .rrc import RRC_INPUT_SHIFT, RRC_SPS, rrc_coefficients, rrc_filter_symbols
        i_sym, q_sym = generate_symbols(mode, symbols)
        i16, q16 = rrc_filter_symbols(
            i_sym, q_sym, coeffs=rrc_coefficients(alpha=float(rolloff)))
        scale = 127.0 * (1 << RRC_INPUT_SHIFT)
        i = i16.astype(np.float64) / scale
        q = q16.astype(np.float64) / scale
        t = np.arange(len(i), dtype=np.float64) / (RRC_SPS * float(symrate_hz))
        return t, i, q
    i_sym, q_sym = generate_symbols(mode, symbols)
    i = np.repeat(i_sym, sym_ticks)
    q = np.repeat(q_sym, sym_ticks)
    t = np.arange(len(i)) / (float(symrate_hz) * sym_ticks)
    return t, i / 127.0, q / 127.0


def make_constellation_data(mode: str):
    """数字调制理论星座: 全星座点, 归一化到 [-1,1]。"""
    from .digital_mod import MODES, constellation_points
    if mode not in MODES:
        raise ValueError(f"bad digital modulation mode: {mode}")
    pts = constellation_points(mode)
    return pts[:, 0] / 127.0, pts[:, 1] / 127.0


class WavePreview(pg.PlotWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLabel("bottom", "time", units="s")
        self.setLabel("left", "amplitude")
        self.setYRange(-1.1, 1.1)
        self.showGrid(x=True, y=True)
        # 滚轮只缩放时基(X), 垂直幅度固定——与示波器操作习惯一致
        self.setMouseEnabled(x=True, y=False)
        # autoDownsample: 视图内点数超阈值自动抽稀(性能), 放大时显示全部真实点
        # (数据 10000 点/窗口, 放大到载波周期级仍有 ~100 点/周期, 波形不失真)
        self._curve = self.plot([], [], pen=pg.mkPen("c", width=2), autoDownsample=True)
        # 调制源曲线(黄色细线): 调制开启时叠加显示, 与调制后输出同窗口对齐
        self._mod_curve = self.plot([], [], pen=pg.mkPen("y", width=1), autoDownsample=True)
        self._ch2_curve = self.plot([], [], pen=pg.mkPen("m", width=2), autoDownsample=True)
        # 数字调制预览曲线(I 青 / Q 洋红 / 星座散点), 仅数字调制模式启用
        self._dig_i = self.plot([], [], pen=pg.mkPen("c", width=2), autoDownsample=True)
        self._dig_q = self.plot([], [], pen=pg.mkPen("m", width=2), autoDownsample=True)
        self._dig_scatter = self.plot([], [], pen=None, symbol="o",
                                      symbolSize=7, symbolBrush=(180, 120, 255, 200))
        legend = self.addLegend()
        legend.addItem(self._curve, "输出")
        legend.addItem(self._mod_curve, "调制源")
        legend.addItem(self._ch2_curve, "CH2")
        self._params = None          # 最近一次 set_params 参数(缩放重生成用)
        self._data_start = 0.0       # 当前数据窗口起点(秒, 可为负)
        self._data_win = 0.0         # 当前数据窗口宽度(秒)
        self._home_win = 0.0         # 初始自适应窗口宽度(秒, set_params 记录)
        self._regenerating = False   # 防递归(setData 触发的 range 变化)
        self._cal_vpp = 9.50         # 满摆幅 Vpp 校准常数(默认 9.50V(实测))
        self.getPlotItem().vb.sigXRangeChanged.connect(self._on_xrange)
        self._update_title()

    def set_calibration(self, cal_vpp: float) -> None:
        """更新校准常数(满摆幅 Vpp), 标题测量值随之刷新。"""
        self._cal_vpp = cal_vpp
        self._update_title()

    def _measure(self):
        """当前视图窗口内波形的 Vpp/RMS(换算电压)。满摆幅 ±1 = ±cal/2 V。"""
        if self._curve.getData() is None or self._cal_vpp <= 0:
            return None
        t, y = self._curve.getData()
        if t is None or len(t) == 0:
            return None
        xr = self.getPlotItem().viewRange()[0]
        mask = (t >= xr[0]) & (t <= xr[1])
        if not np.any(mask):
            return None
        ys = y[mask]
        scale = self._cal_vpp / 2.0
        vpp = (float(ys.max()) - float(ys.min())) * scale
        rms = float(np.sqrt(np.mean(ys ** 2))) * scale
        return vpp, rms

    def _update_title(self):
        """标题显示当前时基窗口宽度 + 视图内波形 Vpp/RMS(对照示波器测量)。"""
        xr = self.getPlotItem().viewRange()[0]
        w = xr[1] - xr[0]
        if w >= 1e-3:
            title = f"窗口 {w * 1e3:.2f} ms"
        else:
            title = f"窗口 {w * 1e6:.0f} µs"
        m = self._measure()
        if m is not None:
            title += f"   Vpp {m[0]:.2f} V   RMS {m[1]:.2f} V"
        self.setTitle(title)

    def mouseDoubleClickEvent(self, ev):
        """双击复位到初始自适应窗口(与刚开始一致), 便于缩放后回到基准视图。"""
        if self._params is not None:
            self._reset_home()
        super().mouseDoubleClickEvent(ev)

    def _on_xrange(self):
        """视图变化处理:
        - 视图放大回初始窗口附近([0, home] 宽约 home): 复位到初始数据
          (相位基准 t=0, 与刚开始完全一致)——滚轮缩放中心任意, 否则
          放大回的是绝对时间轴上不同相位段, 看起来与初始不同;
        - 视图超出数据覆盖范围: 重新生成(示波器式延伸, 不缺失);
        - 放大(视图仍在数据内): 无需重生成(数据已 10000 点)。
        set_params 的 setXRange 恰好等于数据窗口, 不会误触发。"""
        if self._regenerating or self._params is None:
            return
        xr = self.getPlotItem().viewRange()[0]
        x0, x1 = xr[0], xr[1]
        if self._home_win > 0 and abs((x1 - x0) - self._home_win) < self._home_win * 0.1 \
                and abs(x0) < self._home_win * 0.1:
            self._reset_home()
            return
        if x0 < self._data_start or x1 > self._data_start + self._data_win:
            self._regenerate(x0, x1)

    def _render_window(self, window_s: float, t_start: float):
        """Render every visible source over one explicit absolute time window."""
        wave, freq_hz, ampl_pct, base_pct, harms, mod, phase_off, pulse_duty, ch2 = self._params
        t, y = make_plot_data(
            wave, freq_hz, ampl_pct, base_pct, harms, mod=mod,
            window_s=window_s, t_start=t_start, phase_off=phase_off,
            pulse_duty=pulse_duty,
        )
        self._curve.setData(t, y)
        if mod and mod.get("mode", "OFF") != "OFF":
            tm, m = make_mod_source_data(
                freq_hz, mod, n=len(t), window_s=window_s, t_start=t_start,
            )
            self._mod_curve.setData(tm, m)
        else:
            self._mod_curve.clear()

        if ch2 is not None:
            _, y2 = make_plot_data(
                ch2["wave"], ch2["freq_hz"], ch2["ampl_pct"],
                ch2.get("base_pct", 100), ch2.get("harms"), n=len(t),
                mod=ch2.get("mod"), window_s=window_s, t_start=t_start,
                phase_off=ch2.get("phase_off", 0.0),
                pulse_duty=ch2.get("pulse_duty", 50),
            )
            self._ch2_curve.setData(t, y2)
        else:
            self._ch2_curve.clear()

        self._data_start = t_start
        self._data_win = (
            t[-1] + (t[1] - t[0]) - t_start if len(t) > 1 else window_s
        )
        return t

    def _reset_home(self):
        """回到初始自适应窗口数据(相位基准 t=0, 与刚开始一致)。"""
        self._regenerating = True
        try:
            t = self._render_window(self._home_win, 0.0)
            self.getPlotItem().setXRange(0, t[-1], padding=0)
        finally:
            self._regenerating = False
        self._update_title()

    def _regenerate(self, x0, x1):
        self._regenerating = True
        try:
            # 数据从视图起点开始覆盖(可为负时间), 宽度 = max(数据, 视图×2);
            # t_start 保持绝对时间轴: 任何窗口显示的都是同一段波形(相位基准恒为 t=0)
            win = max(self._data_win, (x1 - x0) * 2.0)
            self._render_window(win, x0)
            self._update_title()
        finally:
            self._regenerating = False

    def set_params(self, wave: str, freq_hz: int, ampl_pct: int,
                   base_pct: int = 100, harms=None, mod=None,
                   phase_off: float = 0.0, ch2=None,
                   pulse_duty: int = 50) -> None:
        self.clear_digital()   # 切回标准波形视图时清掉数字调制曲线
        self._params = (wave, freq_hz, ampl_pct, base_pct, harms, mod,
                        phase_off, pulse_duty, ch2)
        # setData/setXRange 都会触发 sigXRangeChanged, 必须屏蔽回调防止误扩展
        self._regenerating = True
        try:
            window_s = display_periods(freq_hz, mod) / freq_hz if freq_hz > 0 else 0.0
            t = self._render_window(window_s, 0.0)
            self._home_win = self._data_win
            # X 轴固定到数据窗口(默认视图 [0,1] 会把数据压扁在左端; 缩放后可双击复位)
            if len(t) > 0:
                self.getPlotItem().setXRange(0, t[-1], padding=0)
        finally:
            self._regenerating = False
        self._update_title()

    def set_digital(self, mode: str, view: str = "time",
                    symrate_hz: int = 10_000, filter_name: str = "RECT",
                    rolloff: str = "0.35") -> None:
        """数字调制预览: time=I/Q 时域, constellation=理论星座, compare=时域+仅理论提示。"""
        filter_name = str(filter_name).upper()
        if filter_name not in ("RECT", "RRC"):
            raise ValueError(f"bad digital modulation filter: {filter_name}")
        self._params = None   # 数字模式不走缩放重生成, 禁用 _on_xrange 扩展
        for curve in (self._curve, self._mod_curve, self._ch2_curve):
            curve.setData([], [])
        rate_text = (f"{symrate_hz // 1000} kSym/s"
                     if symrate_hz % 1000 == 0 else f"{symrate_hz} Sym/s")
        rrc_note = (f" · RRC α={rolloff}（群延迟 16 samples；仅理论仿真，未接入采集数据）"
                    if filter_name == "RRC" else "")
        if view == "constellation":
            self._dig_scatter.setData(*make_constellation_data(mode))
            self._dig_i.setData([], [])
            self._dig_q.setData([], [])
            self.getPlotItem().setXRange(-1.1, 1.1, padding=0)
            self.getPlotItem().setYRange(-1.1, 1.1)
            self.setTitle(f"星座 {mode}（理论仿真{rrc_note}）")
        else:
            t, i, q = make_digital_mod_data(
                mode, 64, 5000, symrate_hz=symrate_hz, filter_name=filter_name,
                rolloff=rolloff)
            self._dig_i.setData(t, i)
            self._dig_q.setData(t, q)
            self._dig_scatter.setData([], [])
            self.getPlotItem().setXRange(t[0], t[-1], padding=0)
            self.getPlotItem().setYRange(-1.2, 1.2)
            title = f"时域 {mode} · {rate_text}{rrc_note}"
            if view == "compare" and filter_name != "RRC":
                title += " · 对比：仅理论仿真，未接入采集数据"
            self.setTitle(title)

    def clear_digital(self) -> None:
        for curve in (self._dig_i, self._dig_q, self._dig_scatter):
            curve.setData([], [])
