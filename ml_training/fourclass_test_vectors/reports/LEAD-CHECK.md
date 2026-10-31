# Lead 一次增量核对（fourclass_test_vectors）

日期：2026-10-21；范围：`ml_training/fourclass_test_vectors/`（四类 SYNTHETIC 测试输入）。
只新增本目录；未连接板卡/Pluto，未发射/采集/安装/部署，未重训/调阈值/改模型，未重跑历史全套测试。

| 检查 | 结果 |
|---|---|
| 一键 CPU 命令 | `ml_training\.venv\Scripts\python.exe ml_training\fourclass_test_vectors\run_all.py` → **PASS** |
| 复用已有实现 | 6 MSPS 直接走 `pynq_overlay/sw/rf_iq_signal.py` 的 PRBS7/星座映射/RECT/`s16_iq_le` 打包；未新建波形框架 |
| 生成结果 | BPSK/QPSK/QAM16/QAM64 各一份：6 MSPS 254 complex（1,016 B），60 MSPS **4096 complex**（16,384 B）；seed/幅度/映射/格式/SHA 全在 `data/manifest.json` |
| 独立检查 | `results/independent_check.json` **PASS**；独立 PRBS7/轴表重建的 6 MSPS 与 60 MSPS 字节逐字节一致；检查器不导入分类器/生成器 |
| 冻结评估 | `Round2Predictor`（冻结 rules/CNN/baseline）评估 60 MSPS：4/4 accepted、无误判、无拒识；**SYNTHETIC only，不是真实准确率/产品资格** |
| 输入域 | 6 MSPS context=`unsupported_sampling_domain`、file=`invalid_iq_length`，`fed_to_model=false`；未与 60 MSPS 混用 |
| 预览 | `data/constellation_preview.svg` + `data/constellation_points.csv`（依赖零绘图库） |
| 测试 | 本目录定向测试 **7 passed**；未重跑历史全套；无审查代理 |
| 写范围 | 仅新增 `ml_training/fourclass_test_vectors/`；既有模型/阈值/冻结数据/产品代码/原始证据只读 |

边界：6 MSPS 仅为未来 Pluto/GNU Radio 接口说明；本会话未运行 GNU Radio/Pluto。60 MSPS 只用于冻结模型离线检查。
