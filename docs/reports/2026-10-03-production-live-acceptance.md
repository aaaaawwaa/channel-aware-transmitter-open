# 生产发布实板链路验收（2026-10-03）

用户在当前会话明确确认TX1→RX1线缆直连后，本轮完成发布 `e7f1fc6962179eaeb57be15dcad07790` 的真实IQ与生产模型链路验收，以及真实硬件30秒静默看门狗验收。真人GT911触摸及屏幕端到端P95仍待用户现场操作。

## 已完成

固定条件在运行前记录于 `evidence/rx-smart/live-production-2026-10-03/acceptance-declaration.json`：LO80MHz、Fs60MSPS、RX/TX带宽30MHz、内部衰减40dB、PHY模式0、IDELAY tap29、正弦32周期/1024点、峰值码60、4096点捕获、ratio0。
模型/runtime/阈值不变；没有重新训练、安装发布、编译daemon或加载第二个Overlay。

验收经已安装UiView的按下/抬起、回环确认→生产回调→worker→client→唯一daemon，取得真实物理IQ。绘图使用MemoryLcd，因此不把这些软件事件写成真人触摸或实际LCD操作。

| 项目 | 实测结果 |
|---|---|
| OFF→三次ON→OFF | 5帧完整IQ，关闭读回均确认 |
| ON模型输出 | TONE / TONE / TONE，均是生产模型结果 |
| 前后OFF输出 | UNKNOWN / below_energy_floor |
| 削顶 | 0 |
| 谱峰位置 | 六个目标峰均位于±1.875MHz |
| 最小局部谱峰突出度 | 42.55dB，门槛20dB |
| 最小ON/OFF谱峰差 | 32.53dB，门槛10dB |
| 板端预测 vs 离线重放 | 5/5标签及拒识原因一致；最大分数绝对差8.88e-16 |
| 本地建议草稿 | ON可填入；OFF拒识禁止填入；填入前后ARM/source不变 |
| AUTO | 始终不可执行 |
| 真实静默看门狗 | 从成功tx.start响应后计时，30.1642秒读到自动解除、TX source=false |
| 无自动重启 | 自动停机后继续观察超过5秒，ARM/source/RX ARM持续为false |
| 服务 | 两项active；PID6200/6220保持；NRestarts=0 |
| 最终状态 | disarmed；RX ARM=false；TX source=false；safe=true |

本轮只做TONE完整链路正控及OFF拒识，不据三帧TONE推出全部调制方式实时准确率。其余类别仍引用此前冻结物理TEST的正式模型验收证据。

## 计时与失败记录边界

`tone-smoke/raw-iq.json` 的 `software_click_to_result_ms` 实际包络包括额外状态核对、草稿操作、渲染和文件写入，且只有5帧；不用于宣称真正的采集端到端P95，更不等于GT911触摸时延。
上一轮100帧纯推理P95 67.75ms仍是上一轮的CPU推理证据；200ms门槛仅适用于那项检查。

看门狗验证脚本保留两个失败记录：第一轮缺少布防后的操作广告刷新，初始化未发出；关闭连接后独立读回确认安全。第二轮实际在30.157秒自动停机，但脚本对已自动解除的daemon仍请求未广告的disarm，误记清理失败；记录中PL已关闭。脚本改为先刷新广告、仅仍布防时请求解除后，复验exit=0、passed=true、safe=true。没有修改生产客户端或daemon来绕过门禁。
第三轮看门狗源码、37秒只读轮询记录及最终状态完整保留。daemon日志另有 `watchdog: the armed client went silent`，支持该次停机由看门狗触发。

## 真人下一步

具体操作见 [真人测试清单](2026-10-03-production-human-test-checklist.md)。重点为：

1. 板屏30次导航点击录像，统计真实手指抬起到新页面可见的P50/P95/最大值。
2. 字段键盘确认/取消、单位换算、布防和发射确认取消。
3. 现场OFF/回环TONE/OFF，生产模型、仅填接收草稿及AUTO关闭。
4. 超过31秒旧结果失效、新结果恢复；可触发时验证取消采集。
5. 底栏停止立即生效，静默自动停机后实际屏幕准确显示解除、至少5秒无重启。

真人结果未取得前保持NOT RUN；天线/LNA及示波器也仍NOT RUN。

## 证据

目录：`evidence/rx-smart/live-production-2026-10-03/`。

- `acceptance-declaration.json`：固定条件及当前用户接线确认来源。
- `tone-smoke/raw-iq.json`、各`.iq`、`analysis.json`：原始采集与物理谱判据。
- `tone-smoke/offline-comparison.json`：逐帧模型身份、IQ SHA和重放对照。
- `tone-smoke/on-1.png`等：软件事件后的生产模型界面截图，不是现场手指操作。
- `watchdog.json`、`watchdog.log`：真实静默计时、状态序列及safe=true。
- `watchdog-first-failure.*`、`watchdog-second-cleanup-failure.*`：前两轮验证脚本失败记录。
- `final-receipt.json`：最终验收结论及仍未执行项。
