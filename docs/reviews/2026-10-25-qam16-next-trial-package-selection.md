# QAM 下一次试部署：统一使用 layout2

2026-10-25，当前 Codex 对话对 DSH 第97轮及实际包作局部增量核对。结论：**READY_FOR_ONE_LIMITED_NO_RF_TRIAL，使用 bundle-layout2；执行仍以用户本次新安装授权和新鲜板端预检为准。** 本轮没有连接板卡、安装或 RF。

## 单一派发包

目录：`evidence/rf-teaching/qam16-rect-product-2026-10-24/bundle-layout2/`。实际步骤见其 `NEXT-STEP.md`，相关修复及15项测试见 `docs/reports/2026-10-25-codex-board-check-minimal-install-fix.md`。

- candidate：`cd76818afc3bee7c610678574e892b26`；父/回退：`bedf98c77283002c632dd7f345abaff9`。
- candidate.tgz：`440ba377a84be4ac2ee6e6cd85980106271cff4a26863235cb84718bc8273c17`。
- trial-stage.tgz：`746e156f092f44d5f2d053e0471fa2eb427541898965869d6bee66a6f5e2299d`。
- stage-sha256.json：`441c2f485104352a189a0af1cf2efb82acfd1c38dc710a8bfb8c7eec609c5593`。
- 新事务：`rf-qam16-cd76818a-layout2`；新 unit：`rf-qam16-trial-cd76818a-layout2.service`。

本轮重新验证以上摘要、stage清单与压缩包逐文件一致、62个software及全部release文件与冻结候选一致、模板绑定新的stage路径、安装前布局检查确实在stage中。复用本对话上一轮15项安装/回退测试、19项产品主机验收和7项preflight，无产品字节变化，不重复算法/ARM战役。已证实的旧包缺目录问题在此包中关闭；热重载硬件卡死的直接机制尚未证实解决。

## DSH bundle-v2 的处理

DSH并行完成的 `bundle-v2` 是另一份外层归档（2437b44a… / stage9a6fb480…），具有额外布局和日志材料，可保留作对照。本轮不混用它的摘要、wrapper或命令，也不要求继续制造第三份包。

其交付命令使用 `/var/lib/rf-deployment-trials/rf-qam16-cd76818a/stage` 和旧 `rf-qam16-trial-cd76818a.service`，与已执行失败且仍保存pre-state/before文件的现场冲突。不能直接覆盖旧stage或重启旧unit。选用已有新事务名的layout2可消除该问题，旧失败现场无需移动或删除。

## 后续范围

只执行一次新的无RF安装、包内120秒fixture验收、失败时既有事务回退和独立读回。使用Paramiko，先确认父版本、boot、服务、存储、RF空闲与无残留作业；若候选已安装或存在正在运行的新事务，先确认，不重复安装。原失联不得当作自动重派理由。成功/失败/UNKNOWN分别留原始收据，完成下载和核对前不清理恢复材料。

此次包选择和增量核对不是QAM物理验收。真实四帧接收、接线确认及物理次数另行授权。
