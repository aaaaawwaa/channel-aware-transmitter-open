# docs/figures/ — 公开图片白名单

本目录只放**经过审核、不含个人信息的自绘结构图/海报图**。公开快照**不包含**硬件照片、屏幕截图、原始实验照片或任何含个人/设备信息的图片。

| 文件 | 用途 | 尺寸/格式 | SHA-256 |
|---|---|---|---|
| `rf-system-architecture-poster-4x3.png` | 系统框图（外部 PlutoSDR 激励 → 板卡 AD9361 RX1 → PL/PS → 触摸屏），用于 README、最终设计报告与 AMD 海报/PPT | 1448×1086 PNG（4:3），RGB | `3714cbf199d3d115559377b2f21ea106f751449ef7ed10c65713380deef7d0ac` |

## 边界与来源

- 该图由项目开发者绘制/生成，内容为功能分工与拟采用演示方式；**不代表所有组合已通过完整验收**。
- 图内只包含通用模块名（PC、GNU Radio、PlutoSDR、Zynq-7020、AD9361、PL、PS、AXI、触摸屏等）与能力限定语（BPSK/QPSK/QAM16 limited validation），**没有**个人姓名、联系方式、主机名、设备串号、私网地址或现场照片。
- 公开导出工具 `tools/project_maintenance.py` 只允许本文件这一个图片（白名单 `PUBLIC_BINARY_FILES`），其它图片/截图一律不导出；该图按原始字节透传并在 `SHARE-MANIFEST.json` 标记 `verbatim-binary`。
- 该图的原件与最终公开副本逐字节相同（sha256 见上）；如后续修改图，需要同步更新本文件、README 图注与 `SHARE-MANIFEST.json`。
