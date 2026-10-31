# unit 原子安装补齐阶段报告

日期: 2026-09-30 (北京时间)。范围: 接续 docs/reports/2026-09-30-ac820-rf-phase2-progress.md
第 7 节下一步待办第 2 项 (unit 非原子安装)。

## 1. 结论

接手时发现上一 session 已在 2026-09-30 08:12:44 (即上述阶段报告落笔 08:05 之后) 把
pynq_overlay/sw/install.sh 改成原子安装 install_unit_atomically, 但既没更新判据也没更新记录,
留下 1 failed / 542 passed 的红色测试树, 和一份仍说 "仍未做" 的失效报告。

本轮: 修好失效判据, 补上原子性判据, 并修掉一个此前未被发现的缺口 -- rf_deploy.py::plan_merged
在 install.sh 之后又用非原子方式 (且取自未哈希上传暂存) 重装 sidecar unit, 使原子修复在真实
部署路径上被绕过。全部为离线验证: 6 组变异全部按预期变红; Python 三套合计 1389 passed /
2 skipped。

板上未做任何写操作。只读探针确认: 板上现跑发布 c85ebdc22d6ec7cb7fd021055d02985f 内的
install.sh 不含原子安装, 即修复尚未上板; 两 unit 当前 LoadState=loaded (未 mask)、active。

## 2. 改动

- rf_overlay/sw/rf_deploy.py: 删除 plan_merged 中安装 sidecar 的那一步。
- pynq_overlay/sw/tests/test_merged_install_contract.py: 重写来源判据 + 新增原子性判据。
- host/tests/test_merged_overlay_package.py: 新增 计划不得写 /etc/systemd/system 判据。

## 3. 计数

pynq 544 passed / 1 skipped; rf 66 passed; host 779 passed / 1 skipped; 合计 1389/2
(接续报告为 1387/2)。sh -n install.sh 退出 0。C 测试未重跑 (未触碰 C 源), 不记通过。

## 4. 诚实边界

- 新判据是源码契约 (钉脚本形状与顺序), 不是运行时证明。
- LoadState=masked 只能在板上观察; 也没有故障注入证明中断不再留下 0 字节 unit。
- 因此 安装后 unit 非 0 字节且未被 mask 只做到源码侧判据加单次只读观察, 仍缺部署后的自动化
  运行期断言, 记为未完成。
- 修复上板需要 --repackage 加重部署 (不需要 Vivado 或 ARM 交叉编译, rf_daemon 未变),
  本轮未做。

## 5. 证据

evidence/2026-09-30-atomic-unit-install/: mutations.py 与 mutations.log (6 组变异)、
suites-after.log (三套计数)、board_readonly_probe.py 与 board-readonly-probe.log (只读探针)、
before/ (改动前快照)。

交付记录: 集成工作树 docs/rf/merged-release-record.md 第 39 节。
进度账本: 集成工作树 .superpowers/sdd/2026-09-27-pynq-ad9361-integration/progress.md。

## 6. 上板验证 (2026-09-30 追加; 取代第 4 节里 记为未完成 的说法)

新发布 **541aa27d463e23d684b8a4014a6b9a14** 已 repackage (无 Vivado / ARM 工具链) 并部署。
与旧发布 c85ebdc2 逐项比对, 软件映射**只有 install.sh 一项不同**; bit/HWH/daemon 均未变
(daemon 605ae0a7… 与板上运行值一致)。

上板复核 POSTCHECK_EXIT 0: current 指向新发布; 发布内 install.sh sha256 = e97fba77… (原子版),
含 install_unit_atomically 3 处, 无 install -m 644 直写 /etc/systemd/system; 两 unit
LoadState=loaded (未 mask)、active、NRestarts=0, 且与发布内副本 cmp 逐字节一致; 无 .tmp. 残留;
--require-software rc=0; fail-closed 6/6; armed=false core=false state=OFFLINE pending=false。

原子性机制与后果在板上实测 (scratch 路径): 旧形态遇失败源留下 **0 字节**真实目标 (复现 §37);
新形态同一次失败触发 REFUSE 且真实目标**未被触碰**、临时文件已清理; 0 字节临时 unit 令
systemctl start 报 **Unit ... is masked.**, 探针已清理。

因此 §7 的 安装后 unit 非 0 字节且未被 mask **已完成** (源码契约 + 部署后运行期断言)。
边界: 故障注入在 /tmp, 不是真打断一次 unit 安装; reboot 项与 TX/RX 数据路径未做。

## 7. 面板控制契约 (2026-09-30 追加; 对应进度报告 §7 第 3 项)

修掉"武装即面板失联": 客户端 poll 的冻结门要求 core is False, 而生产 daemon 的 mode 终身是
status_only、武装后 core 变 True ⇒ 一武装下次轮询就抛 RF daemon is not status-only, 错误
快照覆盖好快照并带走 control_ops, 面板连 disarm 都发不出去。现改为: 冻结门去掉 core, core
类型校验加如实透传; 控制面按广告的 control_ops 分派(与客户端发送门同源), mode 只保留
"不认识即只读"的保守语义。

判据 1 被修订 —— 原文"mode=status_only 时不暴露控制动作"在真机上不可达, 等于把缺陷写成规则;
新增 6 条判据; 变异 5/5 全部按预期变红(M1 的失败消息就是真机症状)。新增客户端通用
control_op(op, args); worker 的 stop 已接到 tx.stop。

计数: pynq 550 passed / 1 skipped、rf 66、host 779 / 1 skipped (合计 1395 / 2)。

诚实边界: init/configure 仍未接通, 原因是**面板没有任何参数输入控件**(已确认无 lo_hz /
sample_rate_hz / attenuation / idelay 字面量), 拿默认值去配置真 TX 硬件不安全 —— 需要参数
录入 UI 与必填契约, 属新工作项。本轮**未上板**; 真机上"武装后仍能只读且能 disarm"的端到端
验证尚未做。

## 8. 面板控制第二次上板: 客户端缺握手 (2026-09-30 追加)

把第 7 节的新代码上传到 /tmp 跑真 daemon(不部署), 发现第二个真缺陷: daemon 要求**每条连接的
第一个请求必须是 protocol.hello**, 而 rf_status_client._control() 开连接后直接发控制 op ⇒
面板与 worker 的每一个控制调用(arm / disarm / stop)都被拒(INVALID_ARGUMENT)。对照实测: 不带
hello 被拒, 带 hello 成功 {"armed": true}。第 7 节修好的发送门因此仍然发不出任何东西; 根因是
历史控制验收一直用原始 NDJSON 探针(先发 hello), 从没走过客户端。

修复: _control 先握手再发 op; 测试夹具改成与 daemon 同规则(跳过握手会失败而不是蒙过去);
新增 1 条判据; 变异 M6/M7 全部按预期变红, 失败消息与真机现象逐字相同。

真板武装态端到端通过: 武装后轮询 error=None(旧门会拒: core is not False -> True), 模型到达
actions=('init','stop','disarm'), arm 与 disarm 真的走通客户端, 收工未武装、OFFLINE。

计数 pynq 551/1、rf 66、host 779/1(合计 1396/2)。板卡 release 541aa27d 未动, /tmp 已清理。
仍未做: init/configure 参数录入 UI; 发布内端到端验证(本次跑的是 /tmp 的新代码)。

## 9. 面板控制发布内验收 (2026-09-30 追加)

新发布 **3e36d6ac65d7306fae62fa223f796e52** 已 repackage 并部署。相对 541aa27d 逐项比对,
软件映射**只有 rf_status_client.py / rf_status_worker.py / ui_model.py 三项不同**;
install.sh 仍是 e97fba77…(原子安装器), bit / HWH / daemon 均未变。

部署 PASS、START_EXIT 0。复核: 两 unit LoadState=loaded / active / NRestarts=0 且与发布内副本
逐字节一致; --require-software rc=0; 运行中 daemon 仍 605ae0a7…; **fail-closed 6/6**;
未武装 OFFLINE; boot_id 未变; POSTCHECK_EXIT 0。

**武装态端到端改用发布内的客户端与模型**(探针自证 import 的是 current/ 下的模块): 武装后
online=True error=None core=True; 面板 surface=control state=armed
actions=('init','stop','disarm') stop_reachable=True; arm 与 disarm 真的走通客户端; 收工未武装。
RELEASE_PANEL_ARMED_OK EXIT 0。第 7 / 8 节的自修复因此从"发布外验证"升级为**发布内验证**。

边界: 本轮只 arm / disarm, **没有** rf.init / rf.configure ⇒ 未改动射频参数; init/configure 仍因
面板没有参数输入控件而未接; TX/RX 数据路径与外部测量、物理断电验收仍未做。