# Claude G1 执行复核与 G2 准入判断（2026-10-09）

本轮请求：检查 Claude Code 针对 G1 的执行情况，并据此决定是否继续 G2。
本轮只读会话/产品代码并运行本地证据校验；没有连接板卡、修改产品源码、安装或新增RF帧。

**当前决定：保留 G1 PARTIAL，先补 G1 剩余资格，不启动 G2。**
G2 的一次最多8帧等待对照仍有必要，顺序是 G1 完整闭合后，再安装准确候选并执行。
这不是发现生产代码已经失效；原因是几项已批准准入条件尚无完整执行证据。

## 已独立核实的进度

最新已读执行总结是 Claude 主会话 2026-10-08 19:40（北京时间），G1 明确标为 PARTIAL。
对话及收据均没有声明进入 G2；会话文件最近更新时间不能当成新增执行结果。

- 原冻结51 native + 17 handoff输入均零漂移；native构建收据与51项摘要完全一致。
- 原生ARM ELF、build.json和build.log摘要与G1收据一致；真正调用verify_observer_build通过：daemon1.0.3/major1/minor5。
- 正确发布目录下的准确候选adfac1f14c3cdb379cb92569a21a9054经verify_release(require_software=True)验证通过，未安装。
- 保留的ARM构建日志含BUILD_RC=0、vendor10、daemon445/0等结果；selftest两条FAIL为已有故障注入负例，harness OK。
- Linux harness源码及拉回的ARM二进制摘要一致，原始日志确有74条PASS、0条FAIL、RUN_RC=0。
- harness归档中的51个native输入与冻结源码逐字节一致，另外包含测试TU/build script，共53文件。
- 同IQ环境结果在四份输入上按相同字段、类型与1e-6浮点容差判等通过；输入和结果文件SHA均核对一致。
- 独立从100次原始数据重算P95=290.805352ms（门槛≤300ms）、P50=219.022648ms、max=441.834456ms；100×5组件计时为正且与总时长一致。原生CNN开启，峰值RSS=30816KiB（30.09375MiB）。原要求没有RSS硬阈值；max不是P95门槛，不能据此把已有资源资格判失败。

这些是本轮重新读取、重算的历史产物结果；本轮没有重新执行ARM/Linux测试。
本轮证据：evidence/rf-teaching/s2/claude-g1-review-2026-10-09/provenance-check.json。

## 准入缺口与必要补证

1. **[P1] 退出计时测试实际使用未初始化fd，原5ms证据无效。**
   `harness/test_rf_transport_linux.c:753`声明`fd`后没有赋值，`:771`即传入`conn_read`，而生产`rf_daemon_main.c:633`直接解引用它。
   child不检查读取返回值便`:774`退出0；parent只有150ms延迟，没有READY/进入阻塞同步。因此child可能因错误描述符提前结束，父进程随后收割成功，不能证明SIGTERM唤醒或5秒预算。
   应赋值正确socket端，关闭不使用的端，设置可观测READY/阻塞同步，确认发送信号前child仍存活，检查真实stop及读取返回结果，再按两种信号重跑。原日志保留，原退出结论标无效，不修改历史收据。
2. **真实socket背压与FAULT/owned-core收尾没有组合执行。**
   445项daemon测试中的关闭顺序为注入transport结果；74项Linux测试运行真实transport，但没有链接完整daemon/core收尾。
   应在Linux上把真daemon/core、fake PL、生产conn_read/conn_write_all和真实异步信号组合起来。
   观察TX source/RX arm先清除，owned core再释放，并保留原因及释放时刻；禁止运行真实设备控制。
3. **停止后完整请求不派发仅有注入测试，真实异步信号时点矩阵未齐。**
   在生产读取返回到派发之间通过测试barrier实际送SIGTERM/SIGINT；请求不得产生控制写或响应。
   保留无停止的正常派发对照，并补齐清单要求的两种信号时点；不能靠serve之前预置布尔值替代。
   同时补真实慢读持续进度、无停止信号引起的send/poll EINTR及正常恢复，证明partial/EINTR都不刷新首次send之前开始的1000ms整体期限。现有即时读完与完全不读不能覆盖这些场景。
4. **主机/ARM同IQ判等只覆盖环境分析记录。**
   现有benchmark虽然执行完整路径，但CNN、解调、星座结果未被跨平台比较。
   对准确候选和同4份离线IQ补完整接收结果比较：基础分析、CNN、同步状态/参考/错误比特/BER、星座及环境结果。
   固定种子、配置、比较字段和浮点容差；失步/无参考要比较拒绝状态，不能只挑成功帧。
   依据：`scripts/rf_environment_arm_check.py:59`仅保存environment；`scripts/rf_environment_run_arm_check.py:73`仅比较这些records；主机iterations=0没有执行五组件compute。ARM五组件100次执行记录仍有效，补同IQ结果输出即可，无须为此重跑整个基准。
5. **[P2] 收尾与返回码证明需要准确表述。**
   5ms fork退出不能代替完整daemon的FAULT/owned-core收尾预算；应在组合fixture记录信号、关闭和退出时点。
   sleep日志22/0是真实输出，但make经过tail管道后的返回码取到的是tail状态，应直接保存make实际退出码。

作者报告的“八行仅剩一行”和“全部步骤完成”过于宽泛。实际至少还包含退出用例bug、真实信号/发送场景、完整分析判等和返回码缺口；不能只接受FAULT这一项就进入G2。

上述不通过就保留G1 PARTIAL，不以“人工接受组合缺口”代替原准入条件。
若只新增测试和证据、51项生产输入仍不变，可复用已核验ARM ELF和候选；若产品源码变化，重新冻结、编译、审阅和资格化。

## 后续固定顺序

G1剩余资格补证及规范/质量复审 → G1 PASS → G2实时预检和准确候选安全安装 → 一次最多8尝试、2会话对照 → 两会话解除ACK、完整下载/同IQ重放/最终独立安全读回。

G2固定沿用TX1→RX1、40dB、LO80MHz/Fs60MHz/BW30MHz、2R2T/tap29和批准的0/1000us等待对照。
任一拒绝或失败保留原IQ，不自动补帧或重试；不同时改AGC、gain、发射幅度或阈值。
G2的目标是判断等待对有限对照的支持程度，不是宣称AGC根因已经找到；正式S2 freeze/108+12/真人和S3继续位于后续阶段。

1s发送期限、5s服务退出预算和5ms采集返回后测量资格门禁分开；5ms不是实时TX硬切断。
历史板端G1结束时current=692904251e024a1254aecee679c6322a、双服务active/NRestarts0；未来安装须重新读实时状态。
本轮新增物理帧0，S2开发累计64不变。

## 本轮独立审阅

两位独立审阅者结论均为G1仍PARTIAL、暂不进入G2。C审阅为CHANGES_REQUIRED_FOR_G1，确认退出用例bug和真实组合覆盖缺口；候选审阅确认资源门槛通过、环境parity通过但完整分析parity缺失。
由于子代理工具在创建进程前失败，根代理实际读取逐行源码并转交审阅，另转交100个原始时延供独立JS重算。审阅者未直接运行Linux/ARM、没有重跑变异；本轮根代理运行的是本地证据校验，不声称重现历史板端测试。

原Claude规范审阅APPROVED针对较早harness版本，且明确G1PARTIAL/不授权G2；质量收据是“CHANGES_REQUIRED -> findings addressed”，已读会话末条文本仍在做最终复核，不能等同最终APPROVED。历史self-pipe变异结果按作者证据保留，不称本轮独立复现；补证时保存变异差异与原始日志并完成准确最终版本的规范/质量复审。

本轮证据目录：`evidence/rf-teaching/s2/claude-g1-review-2026-10-09/`，包括可重复本地执行的`provenance-check.py`、`metrics-check.py`及各自结果、只读审阅说明与最终`review-receipt.json`。

## 给后续agent的最小执行顺序

1. 保留当前准确候选和冻结输入，另建补证目录；先修退出harness的fd与同步/断言，不修改51项生产输入。
2. Linux假硬件组合fixture接入真daemon/core和生产socket适配器，补FAULT背压、真实post-read停止guard、完整两信号矩阵及慢读/EINTR固定期限；保存假TX/RX关闭→owned释放→退出轨迹。
3. 补sleep的make真实退出码；准确候选/四份同SHA离线IQ补五组件结果跨平台判等，含拒绝状态；复用本次已核验的100次资源证据。
4. 将新harness/结果/日志/候选身份绑定到新收据，对最终版本完成规范和质量复审。所有条目通过才写G1 PASS；失败保留PARTIAL及原始失败证据。
5. G1 PASS后按原G2声明重新做实时预检、安全安装准确候选，并执行最多8尝试的0/1000us有限对照。当前G2仍NOT_RUN。
