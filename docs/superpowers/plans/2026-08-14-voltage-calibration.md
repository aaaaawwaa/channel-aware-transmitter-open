# 电压校准(基于示波器实测)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用示波器实测数据校准电压显示(校准常数 + 校准数据沉淀), 使 GUI 电压显示与示波器实测一致。

**Architecture:** 单点校准(一个满摆幅 Vpp 常数)已足够——实测线性度 <1%、各波形满摆幅一致; 将默认校准常数 9.60 → 9.50V, 校准数据与经验沉淀到文档, 验证闭环。

**Tech Stack:** PySide6 上位机(host/)、示波器 DSOX1102A USB-TMC、`python tools/cal_measure.py`(校准数据采集工具)。

## 实测数据(2026-08-14, 已采集, tools/cal_data.csv)

| 用例 | 设定幅度 | 实测 Vpp | RMS | 频率误差 |
|---|---|---|---|---|
| SQUARE 10k | 100% | 9.500V | 4.560V | +0.002% |
| SQUARE 10k | 50% | 4.700V | 2.270V | ✓ |
| SQUARE 10k | 20% | 1.910V | 0.901V | ✓ |
| SINE 10k | 100% | 9.500V | 3.230V | -0.03% |
| SINE 10k | 50% | 4.700V | 1.610V | -0.3% |
| SINE 10k | 20% | 1.910V | 0.637V | -0.1% |
| SINE 100k | 100% | 9.200V | 3.190V | +0.2% |
| SINE 100k | 50% | 4.580V | 1.590V | +0.1% |
| TRIANGLE 10k | 100% | 9.400V | 2.660V | ✓ |
| SAW 10k | 100% | 9.300V | 2.650V | ✓ |

**结论(决定校准模型):**
1. **线性度极好**: 100% → 9.50V, 50% → 4.70V(理论 4.75, -1%), 20% → 1.91V(+0.5%)——**单点校准即可, 无需多点/分段**
2. **各波形满摆幅一致**: 方波/正弦 9.50V, 三角 9.40V(-1%), 锯齿 9.30V(-2%)——统一校准常数适用
3. **高频衰减**: 100kHz 100% → 9.20V(-3%)——频率相关, 超出电压校准范围, 文档记录不做补偿
4. **RMS**: 示波器实测 ≈ 理论×0.96(正弦 3.23/3.36)——示波器测量特性, GUI 保持理论 RMS 显示

## 文件结构

| 文件 | 改动 |
|------|------|
| `host/siggen/ui/widgets.py` | 默认校准常数 9.60 → 9.50 |
| `host/tests/test_widgets.py` | 校准默认值相关断言更新(如有) |
| `README.md` / `CLAUDE.md` | 校准数据与经验(线性度/波形差异/高频衰减/RMS) |
| `tools/cal_measure.py` | 保留为校准数据采集工具(已含谐波残留复位坑) |

---

## Task 1: 更新默认校准常数 + 测试

**Files:**
- Modify: `host/siggen/ui/widgets.py`
- Modify: `host/tests/test_widgets.py`

- [ ] **Step 1: 更新默认校准常数**

`host/siggen/ui/widgets.py` 中 `self.cal_edit.setValue(9.60)` → `9.50`(示波器实测方波/正弦 10kHz 100% = 9.50V)。tooltip 文字 "出厂默认 9.60V" → "出厂默认 9.50V(实测)"。

- [ ] **Step 2: 检查测试中的校准依赖**

`host/tests/test_widgets.py`:
- `test_volt_labels_calibration_linked` 显式 `panel.cal_edit.setValue(9.60)` —— 不依赖默认值, 无需改
- 检查是否有其他测试依赖默认 9.60(如 VoltagePanel 冒烟)——若有, 更新为 9.50 或显式设置

- [ ] **Step 3: 运行测试**

```bash
cd host && python -m pytest tests/ -q
```
期望: 全部 PASS(49 项)。

- [ ] **Step 4: 提交**

```bash
git add host/siggen/ui/widgets.py host/tests/test_widgets.py
git commit -m "feat: 校准常数默认值 9.60 -> 9.50V(示波器实测方波/正弦 10kHz 100%)"
```

---

## Task 2: 校准数据与经验沉淀文档

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: README.md 校准段落**

在功能说明的校准相关处(实时 Vpp 显示描述)补充校准数据:

```
- 校准常数默认 9.50V = 100% 方波 10kHz 实测 Vpp(2026-08-14 示波器实测,
  方波/正弦一致; 50%/20% 线性度 ±1%; 三角/锯齿满摆幅 -1~2%;
  100kHz 高频衰减约 3% —— 频率相关, 不做电压补偿)
- 校准工具: python tools/cal_measure.py COM11(采集各波形/幅度档位实测 Vpp/RMS)
```

- [ ] **Step 2: CLAUDE.md 校准经验**

在"调制经验"后或"示波器要点"补充:

```
## 电压校准经验(2026-08-14)
- 单点校准即可: 实测线性度 <1%(50%/20% 档位), 各波形满摆幅一致(方波/正弦 9.50V)
- 校准常数 = 100% 方波 10kHz 实测 Vpp(GUI 默认 9.50V)
- 100kHz 高频衰减 ~3%(带宽滚降, 非电压校准范围)
- 示波器 RMS 实测 ≈ 理论×0.96(测量特性, GUI 保持理论 RMS)
- 采集工具 tools/cal_measure.py(注意: 复位必须清 HARM1-4, 否则 SINE 用例
  输出残留谐波, 频率/幅度全错——曾致数据误读)
```

- [ ] **Step 3: 提交**

```bash
git add README.md CLAUDE.md
git commit -m "docs: 电压校准数据与经验(实测线性度/波形差异/高频衰减)"
```

---

## Task 3: 校准验证闭环(板上)

**Files:**
- 无代码改动(验证)

- [ ] **Step 1: 重新采集确认**

```bash
python tools/cal_measure.py COM11
```
期望: 关键档位与首次一致(方波/正弦 10k 100% = 9.50V ±0.05V)。

- [ ] **Step 2: 上位机验证**

启动 GUI(`python host/main.py`), 确认:
- 校准框默认 9.50V
- 设置正弦 10kHz 100%: 预览标题 Vpp 显示 ≈ 9.50V, 与示波器实测一致
- 50%: ≈ 4.75V(实测 4.70, 偏差 1% 内)
- 百分比旁电压标签与示波器实测对照一致

- [ ] **Step 3: 回归**

```bash
python scripts/scope_verify.py COM11
```
期望: 14/14 PASS(校准窗口 8.5~10.5V 含 9.5V)。

---

## 自审清单

1. **范围**: 单点校准(数据支持); 不做多点/分波形校准(YAGNI); 高频衰减/RMS 差异仅记录
2. **一致性**: 校准常数在 GUI 默认值、tooltip、README/CLAUDE.md 中统一为 9.50V
3. **验证**: cal_measure 重新采集 + GUI 对照 + scope_verify 回归
