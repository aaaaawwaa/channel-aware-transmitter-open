# 真人TX–RX录像与回环入口核对（2026-10-03）

用户确认真人测试第1、2项正常，无需补视频。本轮录像对应的回环按钮阻断由运行状态解释：进入接收页时已连续发射（`TX_ACTIVE`、TX选择“是”），当前有限回环只接受 `READY`；静默期限到达后变为未武装 `OFFLINE`，仍不可采集。未发现确认回调缺失，未修改生产代码、模型、门禁或发布。

## 录像证据

来源：`<LOCAL_PATH>

约14–40秒：接收页 `UNAVAILABLE`、`TX_ACTIVE`、TX选择“是”；右栏原因“daemon 状态 TX_ACTIVE 不是 READY”在末尾被行宽截断。约50秒：`OFFLINE`、未武装、TX选择“否”。本录像未见从READY成功进入“确认回环”的过程，真人回环不得记PASS；没有足够的30次导航计时记录，不能报告真人P95。

竖屏文件已提取并旋转可读关键帧至 `evidence/rx-smart/human-loopback-fix-2026-10-03/upright-*.jpg`，原录像未改动。

## 源码链路

- `ui_model.UiApp.rx_capture_capability()`：仅当前在线、新鲜、已武装、广告rx.capture且daemon READY允许采集。
- `rx_loopback_block()`：再要求当前连接 `tx_session_ready=True`。
- `ui_view.UiView._sync_rx()`：门禁决定回环按钮是否启用；按钮按下/抬起触发 `_rx_event('loopback')`。
- `pynq_status._rx_action()`：第一次只登记待确认动作；第二次 `loopback_confirm` 才提交物理回环给后台worker。取消不提交采集。
- `RfStatusClient`：停止清除保留连接的加载凭据，物理回环要求重新加载；普通快照不启动TX。

两种操作有不同起点：连续发射测试是“加载→发射→确认”，有限回环测试是“加载→直接进入接收→回环捕获→确认回环”。后者从READY打开TX、采IQ并关闭，不能从已经连续发射的状态启动。

## 本轮补充实板验证

保持原发布 `e7f1fc6962179eaeb57be15dcad07790`，TX1→RX1直连；LO80MHz/Fs60MHz、40dB内部衰减、PHY0、tap29、32周期/1024点、峰值码60、4096点D1。使用已安装的UiView和生产异步RF回调，实板daemon是硬件唯一控制者；未调用另一Overlay，未写真实LCD帧缓冲。

布防→确认、初始化→确认、配置→确认、加载均通过实际hit/press/release，并由生产 `_PanelScreen` 原子领取后台结果。参数草稿由脚本填写；GT911真人输入仍属于人工验证范围。此前脚本直接准备TX，本轮补齐了准备动作的UI回调边界。

加载后实读 `READY`、已武装、TX source=false、tx_session_ready=true，画面为“已加载 / 待发射”。接收页回环按钮可命中、启用；点击后出现“确认回环／取消回环”。先取消一次，无采集提交、TX关闭。随后普通OFF→3次确认回环ON→普通OFF共5帧：标签 `UNKNOWN/TONE/TONE/TONE/UNKNOWN`，削顶0、±1.875MHz峰、最小突出度41.89dB、ON/OFF差36.76dB，冻结模型离线重放5帧分数差0。最后解除布防、RX ARM/source关闭、safe=true。两服务active、NRestarts0，PID仍6200/6220。

回归：RX视图、RF异步控制链和触摸参数共75项通过。首次运行73项通过、2项因默认pytest临时目录沙箱权限在setup报错，改用工作区独立basetemp后75项通过，无生产代码修订。

另外补验了录像中的进入路径：在相同参数下经UI“发射→确认”实读TX_ACTIVE/source=true；按禁用的回环按钮没有登记确认、没有启动采集。随后在接收页点底栏“停止 AD9361”，无需再次确认，实读READY/source=false，但回环门禁为NOT_LOADED；返回发射页重新加载，确认入口恢复。此后另5帧OFF/三ON/OFF同样通过，削顶0、最小突出度41.05dB/ON-OFF差35.59dB，离线重放一致，最终safe=true。证据 `evidence/rx-smart/human-loopback-fix-2026-10-03/tx-active-recovery/`，包括TX_ACTIVE按钮禁用与恢复后确认页。

证据：`evidence/rx-smart/human-loopback-fix-2026-10-03/full-ui-chain/`。关键渲染：`control-load.png`、`ready-loopback-enabled.png`、`loopback-confirm-visible.png`。它们是实板运行生产UI的MemoryLcd结果，不能当作真人触摸或实体LCD计时通过。沿用脚本的 `software_click_to_result_ms` 包含后续验证、绘图与保存，仍不能作为完整采集端到端延迟/P95。

## 真人复测

第1、2项按用户反馈记录正常，不再要求补视频。只需复测这次受阻路径：停止连续TX→重新加载（若自动解除则先重新布防/初始化/配置）→看到“已加载 / 待发射”→直接进入接收→核对READY/已武装/TX选择否→回环捕获→确认回环→智能页TONE。先取消一次，再确认一次即可定位真人确认入口；完整OFF/三ON/OFF再按更新清单复验。若这三项起始状态都满足仍无法出现确认按钮，记录当时画面即可，不重复测试第1、2项。

上述段落为复测前状态。最新真人反馈如下。

## 真人照片复测结果

用户表示按修订步骤操作，并提供实体板屏照片 `f4e2a0f2d3c3df42b58597743d5e5f04.jpg`。图中接收→智能页显示：实测IQ分类TONE、模型得分1.000、来源loopback.physical.capture、版本tiny-iq-cnn-v1、“生产模型：仅填草稿”、“仅填本地草稿；AUTO永久关闭”；“建议填入接收草稿”按钮呈启用状态。右侧采集、生产建议为已接入，同步、解调、帧数据为未接入。

因此真人确认回环→有限物理采集→生产模型识别→实体屏显示TONE的单次链路通过，前次“没有确认入口”的阻断已解除。右侧未接入描述同步/解调/帧数据等独立功能状态，不是本次回环错误。模型得分1.000是当前帧输出分数，不代表整体准确率100%。

原照片按字节复制留存为 `evidence/rx-smart/human-loopback-fix-2026-10-03/human-success.jpg`，SHA256 `5975fb6c66220e0158f8584d3b2de68303eb93616b75c5a51e7b6e5640ad2d96`。本轮只记录反馈，不另行操作板卡或改变发布。

照片未覆盖完整OFF/三ON/OFF、按钮实际填草稿后的硬件状态、31秒过期、真人停止/静默停机及P95，保留待确认；未由底栏“停止 AD9361”按钮推断仍在发射或已经安全解除。天线/LNA/示波器未运行。第1、2项仍按用户直接确认记录正常，无需补视频。

## 最新真人收尾确认

随后明确要求验证“建议填入接收草稿只修改草稿；超过31秒结果过期且建议禁用；最后停止并解除布防”，用户回复“可以，验证通过”。按这一紧邻上下文记录三项真人验证PASS，证据来源为用户直接确认，无需另补视频。单次回环→生产TONE→建议草稿→过期禁用→停止解除的真人交互闭环已完成。

本轮没有板卡访问、代码修改或部署。停止/解除及仅改草稿是用户现场确认，不冒充本轮独立硬件读回；此前软件硬件验收已有safe=true和watchdog证据。完整真人OFF/三ON/OFF、真人静默watchdog定时及P95量化没有在这句确认的指定步骤中，仍未独立确认；天线/LNA/示波器未运行。新增收据 `evidence/rx-smart/human-loopback-fix-2026-10-03/human-final-confirmation.json`，此前照片收据保留为当时状态。
