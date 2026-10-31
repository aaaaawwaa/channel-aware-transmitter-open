# 电压精细校准 v2(硬件截断模型)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除电压显示的硬件可解释误差(ampl_scale 整数截断), 使 GUI 显示值与硬件实际输出吻合(±0.05V), 并文档化示波器测量刻度影响。

**Architecture:** 显示模型从连续百分比换算改为**硬件截断模型**: `V(pct) = trunc(127×pct/100) / 127 × cal`(ampl_scale 对 127 LSB 满摆幅的整数截断, 与 RTL `sample×ampl/100` 一致)。示波器测量刻度影响文档化。

**Tech Stack:** PySide6 上位机(host/)、示波器 DSOX1102A USB-TMC、tools/cal_measure.py。

## 实测数据与分析(2026-08-14, 已完成)

每档双刻度 5 次平均(正弦 10kHz):

| 档位 | 2V/div | 小刻度 | 截断硬件值 | 结论 |
|---|---|---|---|---|
| 100% | 9.460 | — | 9.50 | 2V/div 可靠 |
| 80% | 7.600 | 7.368(1V/div,超量程) | 7.56 | 2V/div |
| 70% | 6.680 | 6.470(1V/div,压缩) | 6.58 | 2V/div +0.10 |
| 60% | 5.740 | 5.606 | 5.69 | 2V/div +0.05 |
| 50% | 4.820 | 4.692 | **4.71** | 1V/div 吻合 |
| 40% | 3.900 | 3.740 | **3.74** | 1V/div 吻合 |
| 30% | 3.060 | 2.822 | **2.84** | 0.5V/div 吻合 |
| 20% | 2.120 | 1.898 | **1.87** | 0.5V/div 吻合 |
| 10% | 1.180 | 0.890 | **0.90** | 0.2V/div 吻合 |

**结论:**
1. **示波器刻度相关测量偏差是误差主源**(0.1~0.3V): 小刻度大占屏(≥60%档)压缩偏低; 大刻度小信号量化抬升偏高; 占屏 40~70% 最准(±0.05V)
2. **硬件截断模型 = 真值**: `trunc(127×pct/100)/127×cal`(ampl_scale 整数截断, 1 LSB = 0.075V), 与合适刻度实测吻合 ±0.05V(50%/40%/30%/20%/10% 档)
3. 校准常数 9.50V 可靠(100% 2V/div 多次平均 9.46±0.05)

## 文件结构

| 文件 | 改动 |
|------|------|
| `host/siggen/ui/widgets.py` | `_component_volt`/`_update_vpp` 改用硬件截断模型 |
| `host/tests/test_widgets.py` | 电压断言更新为截断值 |
| `README.md`/`CLAUDE.md` | 示波器刻度测量影响说明 |

---

## Task 1: 显示模型改为硬件截断

**Files:**
- Modify: `host/siggen/ui/widgets.py`
- Modify: `host/tests/test_widgets.py`

- [ ] **Step 1: widgets.py 截断模型**

`_component_volt(pct)` 从连续模型改为硬件截断模型:

```python
    def _component_volt(self, pct):
        """分量百分比 -> 输出 Vpp(硬件截断模型):
        满摆幅 127 LSB, ampl_scale 整数截断 trunc(127×pct/100) 与 RTL 一致。
        电压 = 截断 LSB / 127 × 校准常数 × AMPL%。"""
        lsb = 127 * pct // 100
        return lsb / 127.0 * self.cal_edit.value() * self.ampl.value() / 100.0
```

`_update_vpp` 的 vpp_label 同步截断:

```python
    def _update_vpp(self):
        pct = self.ampl.value()
        lsb = 127 * pct // 100
        vpp = lsb / 127.0 * self.cal_edit.value()
        self.vpp_label.setText(f"≈ {vpp:.2f} Vpp")
        self._update_volt_labels()
```

(cal_hint 的 "满摆幅 X V · 1% = Y V" 用截断模型: 满摆幅 = trunc(127×ampl//100)/127×cal; 1% = 满摆幅/100。)

- [ ] **Step 2: 更新测试断言**

`host/tests/test_widgets.py::test_volt_labels_calibration_linked`:
- 校准 9.60 + AMPL 50%: base 100% → 截断 127/127×9.6×0.5 = **4.80V**(与原来相同, 127//100=1)
- harm 30%: 127×30//100 = 38 → 38/127×9.6×0.5 = **1.436V**(原 1.44)——断言 "1.44V" 需改为 "1.44V"(四舍五入相同)或精确 1.44
- AMPL 80%: 127×80//100 = 101 → 101/127×9.6 = **7.63V**(原 7.68)——断言更新
- cal_hint: 满摆幅 = 127×50//100/127×9.6 = 4.80V(不变); 1% = 0.048V(不变)

`test_preview_vpp_rms_measurement`: 预览标题测量基于波形数据(连续模型), 不随设定值截断——**该测试不受影响**(测量的是波形本身)。

运行: `cd host && python -m pytest tests/ -q` — 全过。

- [ ] **Step 3: 提交**

```bash
git add host/siggen/ui/widgets.py host/tests/test_widgets.py
git commit -m "feat: 电压显示改用硬件截断模型(ampl_scale 整数截断, 与硬件一致)"
```

---

## Task 2: 示波器刻度测量影响文档化

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: README.md**

在电压校准说明处补充:

```
- 示波器测量刻度影响(对比时注意): 垂直刻度应使信号占屏 40~70%——
  占屏过大(1V/div 测 6V+ 信号)会压缩偏低 0.1~0.3V, 占屏过小(2V/div 测
  <3V 信号)量化抬升偏高 0.1~0.3V; 测量本身波动 ±0.05V
- 显示值采用硬件截断模型(与 ampl_scale 整数截断一致, 误差 <1 LSB ≈ 0.075V)
```

- [ ] **Step 2: CLAUDE.md**

在"电压校准经验"节补充:

```
- 示波器测量刻度影响(实测): 垂直刻度占屏 40~70% 最准(±0.05V); 大占屏
  (≥60%档 1V/div)压缩偏低 0.1~0.3V, 小占屏(2V/div 小信号)量化抬升偏高;
  对比 GUI 电压时先看占屏, 测量多次取平均
- GUI 显示用硬件截断模型: V = trunc(127×pct/100)/127×cal(与 RTL 一致)
```

- [ ] **Step 3: 提交**

```bash
git add README.md CLAUDE.md
git commit -m "docs: 示波器测量刻度影响与显示截断模型说明"
```

---

## Task 3: 验证(板上)

- [ ] **Step 1: GUI 显示 vs 示波器实测对比**

设置正弦 10kHz, 档位 10/30/50/70/100%, 每档:
- GUI 标签显示值(截断模型)vs 示波器合适刻度实测(占屏 40~70%)
- 期望: 差异 ≤0.1V(除 70% 档示波器 2V/div 测 6.68 略高 0.1——文档已说明刻度影响)

- [ ] **Step 2: 回归**

```bash
python scripts/scope_verify.py COM11   # 14/14
cd host && python -m pytest tests/ -q   # 全过
```

---

## 自审清单

1. 截断模型与 RTL 一致: `127*pct//100` 对应 `sample×ampl/100` 整数截断(正弦峰值 127)
2. 校准常数 9.50 不变(已实测可靠)
3. Vpp/RMS 标题测量保持连续模型(基于波形数据)——设定值标签截断, 两者差 ≤0.08V, 文档说明
4. 示波器刻度影响文档化(用户对比时的最大误差源)
