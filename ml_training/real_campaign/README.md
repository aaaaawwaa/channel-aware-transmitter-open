# 真实四类数据验收工具（real_campaign）

> 只写 `ml_training/real_campaign/`；不修改产品文件，不连接设备，不发射、不采集、不部署。
> 本工具只对**已有证据**做只读导入、索引、去重和冻结模型评估；下一批采集另立授权。

## 一键运行

```bat
ml_training\.venv\Scripts\python.exe ml_training\real_campaign\run.py
```

一条命令完成：导入已有真实 IQ → 按会话分组 → 检查重复 IQ/跨 split 泄漏 → 用冻结第二轮
方案评估（干净/质量拒识/干扰压力分开）→ 校验证合成夹具 → 生成索引和报告。

输出：

- `index/records.json`：逐帧索引（原始路径、SHA-256、域、真值来源、身份、状态）；
- `index/sessions.json`：按完整真实会话分组；
- `index/duplicates.json`：重复 IQ 组与 TRAIN/VAL/TEST 泄漏组；
- `index/truth_policy.json`：允许/禁止的真值来源；
- `reports/real_campaign_report.md` / `.json`：报告与机器摘要。

## 数据导入规则

- 已配置源见 `config/sources.json`：三批 BPSK 真实会话（campaign3/4/5，80 MHz 板卡回环）、
  2026-10-21 窄带教学实验帧、合成夹具源。
- 原始 `.iq`/元数据只读；索引只保存路径和摘要，不复制/不修改原始文件。
- 逐帧检查：`s16_iq_le`、4096 复样本、60 MSPS、decim=1、IQ 范围、文件 SHA-256 与元数据
  `iq_sha256` 一致；域不符标 `NOT_RUN_DOMAIN`，不静默改造。
- 身份检查：`release_id` 按帧记录并与独立声明比对；`boot_id` 只在有独立声明时记录
  （campaign3 声明有；其余标 `NOT_DECLARED`，不补造）；`session_id`/`split` 来自会话元数据
  或目录，`UNASSIGNED` 不当作评估 split。

## 真值政策（不可用模型/环境反填）

| 真值 | 来源 | 处理 |
|---|---|---|
| 调制类别 | `demod_result.submitted.config.mode`（显式信号配置） | 只接受四类之一；否则标不可用 |
| 场景/质量标签 | 独立实验记录（`trainval.truth`、`scenario_declaration.truth`） | OFF/窄带等只作质量/压力标签 |
| 模型预测 | `model_result.label` | **仅作非真值诊断**，绝不写为 truth_class |
| 环境标签 | `environment.label` | **仅作非真值诊断**，绝不写为 truth_class |
| 合成夹具 | 冻结 generator 的 `sim-smoke` 输出 | 标 `synthetic=true`，单独评估 |

导入器实际使用上表的白名单；测试夹具中“只有模型标签”“只有环境标签”的元数据会被拒绝。

## 评估语义

- **干净分类**：只统计有明确四类真值的 normal 帧；类别齐全才计算四类总体准确率。
  当前真实数据只有 BPSK，故 `four_class_overall_accuracy = NOT_RUN`（缺失
  QPSK/QAM16/QAM64），不计算 0 或虚假的总体值；BPSK 的按类准确率、拒识率、已知类误拒
  仍正常报告。
- **质量拒识**：OFF/TONE/NOISE/CLIP 类标签单独统计拒识率与误接受，不混入分类准确率。
- **干扰压力**：窄带帧单独统计接受/拒识，明确标为压力测试，不计入干净准确率。
- **去重**：指标按 IQ SHA-256 去重（campaign5 的 TRAIN 副本与 campaign4 重复，索引保留但
  不重复计数）；重复组/泄漏组单独报告，原始文件保留。
- 每会话结果单列；缺失真实类别显式 `NOT_RUN`。

## 本次（当前 checkout）真实数据现状

- 真实帧 71（无 invalid）；独立真实会话 8 个；按 digest 去重后评估 49 帧。
- 干净：42 帧，全部 BPSK；BPSK **42/42 正确**；已知类误拒 0/42；
  **QPSK/QAM16/QAM64 真实类 NOT_RUN，四类总体准确率 NOT_RUN**。
- 质量拒识：OFF **4/4 拒识**，误接受 0，原因 `noise_like`。
- 干扰压力：窄带 **3/3 接受**（只作压力结果，不进入干净准确率）。
- 重复：真实重复组 22（campaign5 TRAIN 为 campaign4 TRAIN 的重复下载），
  **真实跨 split 泄漏 0**；合成夹具泄漏组 1（专门用于测试泄漏检测）。
- 固件/模型：调用冻结第二轮 predictor（规则/CNN/基线哈希由 predictor 校验），不重训、
  不扩展网络、不重跑 ARM。

## 合成夹具（不是真实采集）

`fixtures/synthetic/` 由 `fixtures/build_fixtures.py` 生成：

- 4 个合法四类夹具，每个带 `synthetic=true` 和显式合成真值；
- 4 个错误输入夹具：错误长度、错误采样率、仅模型标签、仅环境标签；
- 3 个重复/泄漏夹具：跨 TRAIN/VAL 同 IQ、同会话重复 IQ；
- 复用 `ml_training/datasets/sim-smoke`（冻结四类 generator）与
  `ml_training/data/reports/reference-check-smoke.json`（映射参考检查 **22/22 PASS**）。

夹具校验结果在报告 `fixture_checks` 与 `index/records.json` 中；合成评估与真实评估分开。

## 采集准备与下一批字段

- 两条路线的接口限制、选择的最少改动路线和四类参数草案：`docs/collection_prep.md`；
- 下一批实测数据必须携带的最小字段清单：`docs/next_batch_fields.md`；
- 本任务不采集、不发射、不自行设定正式验收门限。

## 测试

```bat
ml_training\.venv\Scripts\python.exe -m pytest ml_training\real_campaign\tests -q
```

测试覆盖：导入/域/摘要校验、真值来源拒绝、重复与跨 split 泄漏、缺失类别 NOT_RUN、
冻结评估分离、合成夹具合法/非法/重复、一键命令与报告生成。

## 文件

| 文件 | 作用 |
|---|---|
| `run.py` | 一键入口 |
| `config/sources.json` | 已配置的真实证据源与合成夹具源 |
| `sources.py` / `domain.py` | 源加载与输入域校验 |
| `importer.py` | 逐帧导入、真值/身份/摘要/域检查、索引记录 |
| `dedupe.py` | 会话分组、重复 IQ、跨 split 泄漏 |
| `evaluate.py` | 冻结第二轮方案评估（干净/质量/压力/每会话） |
| `report.py` | 索引与报告写出 |
| `fixtures/build_fixtures.py` | 复用冻结 generator + 参考检查生成合成夹具 |
| `fixtures/synthetic/` | 已生成夹具 |
| `index/` / `reports/` | 生成物 |
| `tests/` | 测试 |
