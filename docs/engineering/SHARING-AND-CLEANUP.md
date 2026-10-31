# 分享、隐私与可恢复清理

本次按“交付或分享”整理：开发原件保持私有，另生成脱敏文本源码包。原始IQ、收据与发布归档不原地改写，避免摘要失效。

## 已采用的边界

1. 根目录旧`.pytest-work-*`、`.pytest_cache`及少量生成物可迁入`.local/archive/<批次>/`。只处理超过24小时且未被Git跟踪的指定根目录项，不清理`evidence/`。
2. `evidence/`约2.68 GiB，参考ZIP约754.59 MiB，训练原始NPZ以及生成IP继续本地保留。生成IP可能被旧仿真引用，不能随手删。
3. 分享包使用源码白名单；不含Git历史、凭据目录、令牌/私钥文件、虚拟环境、完整证据、数据集、图片、压缩归档、bit/HWH及构建输出。图片未做文字识别脱敏，故默认不包含。
4. 分享副本替换个人用户路径、私网地址、配置中识别出的真实设备序列号、VISA串号及常见明文口令写法。模型数值与发布摘要不会因长得像序列号而被统一替换。
5. 此包不是原始验收证据或可安装发布包。旧报告中的文件摘要对应开发机原件；脱敏副本不能拿来验证这些旧摘要。
6. 2026-10-31 补充：导出白名单纳入根 `LICENSE`、`NOTICE` 与 `LICENSES/`（含第三方许可证全文与出处说明）；`rf_overlay/sw/board_app/vendor/` 与 `rf_overlay/sw/ref/` 按**原始字节**透传（不做编码转换/脱敏），以保住 `SHA256SUMS` 与“逐字节副本”声明；`SHA256SUMS` 本身也纳入导出。
7. 2026-10-31 补充排除项：`sdr_testbench/generated/`、`sdr_testbench/gui_demo/generated/`（GNU Radio Companion 生成物，可重建，且生成 `.py` 带 GPL-3.0 头）、`rf_overlay/rtl_ref/`（第三方参考引脚约束，非构建输入、无许可证）、`pynq_overlay/tb/sim_*/` 与 `dfx_runtime.txt` 等仿真/运行临时文件。
8. 许可证边界：自有代码 MIT；ADI 驱动与参考应用按 `LicenseRef-ADI-AD9361`；依赖 GNU Radio/PyQt5 的 5 个测试台脚本按 GPL-3.0 分发；5 个第三方参考工程 RTL 与 1 个 Vitis 生成桩出处/许可未知（见 `LICENSES/UNKNOWN-PROVENANCE.md`）。**这 5 个 RTL 是公开发布的未决阻塞项**，取得作者许可或替换前不得对外声称 `rf_overlay` 整体为 MIT。

## 执行入口

在工程根目录使用Python 3.8以上运行（建议明确使用自己的解释器）：

```powershell
python tools/project_maintenance.py plan
python tools/project_maintenance.py archive
python tools/project_maintenance.py export
```

`plan`只列计划；`archive`执行同盘移动并返回清单路径；`export`创建独立`dist/final-prj-source-<时间>/`及同名ZIP，不修改源文件。两个写操作不会覆盖既有输出。

需要恢复临时文件时，把实际清单路径传给：

```powershell
python tools/project_maintenance.py restore --manifest .local/archive/<实际批次>/manifest.json
```

恢复遇到同名目录会停止，不覆盖新测试。归档只是收拢文件，**不会释放磁盘空间**；原件确需永久删除时另行判断。

## 分享后如何使用

- 从`SHARE-README.md`和根README开始阅读。`SHARE-MANIFEST.json`列出包含的文件、脱敏类别计数与副本SHA256；生成时检查ZIP CRC及逐文件摘要。
- 把`<USER>`、`<DEVICE_SERIAL>`、`<SET_LOCALLY>`与文档示例地址替换为接收方本地参数。原有脚本的参数接口各异，按相应`--help`与说明配置；本次没有将所有旧脚本改造为统一环境变量接口。
- 省略的图片、IP生成目录、训练数据或原始收据可能是某些历史测试的依赖。因此源码包不保证直接运行全部历史测试；需要时再提供经过确认的依赖，或从构建入口重建。
- 文本统一导出为UTF-8 + LF（`vendor/`与`sw/ref/`除外，按原始字节透传，清单中标记为`verbatim-bytes`）；旧GB18030/Windows-1252文本文档转换会记入清单，不因此丢弃源码。开发原件字节保持不变。仓库`.gitattributes`以`* -text`禁止Git换行转换，保证清单SHA256与`vendor/SHA256SUMS`在任意平台检出后仍成立。
- 这是降低已识别隐私泄露风险的文本导出，不是对所有历史文档、第三方内容及商业秘密的完整审计。公开发布前仍应按实际分享范围快速查看清单；无需重新进行全仓硬件审查。

## 验证与维护

```powershell
python -m unittest discover -s tools -p test_project_maintenance.py -v
```

检查覆盖目录越界、已跟踪文件保护、恢复拒绝覆盖、凭据路径在打开前排除、源文件不变、模型数值保持与ZIP内容。本次技能按`skill-creator`工具验证；不因为整理文档重新跑板端实验。

本地原README、AGENTS等快照位于`.local/organization-20261030/`，原AWG说明同时保存在`docs/engineering/awg-legacy-guide.md`。不要将`.local/`或整个工程目录直接压缩分享。
