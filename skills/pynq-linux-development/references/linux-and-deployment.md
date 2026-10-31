# Linux服务、远程执行与恢复

## 只读基线

使用项目已有远程连接封装。Windows非交互SSH若会触发图形凭据框，选用成熟Paramiko入口，不让后台命令无限等待。主机地址、用户名、凭据及服务根目录从私有配置读取；例子中的占位符必须按当前工程填写。

远端只读片段可按需组合：

```sh
uname -a
cat /proc/sys/kernel/random/boot_id
cat /proc/uptime
readlink -f <release-root>/current
cat <active-release-marker>
systemctl show <overlay-owner.service> <sidecar.service> \
  -p ActiveState -p SubState -p MainPID -p NRestarts
df -h / /boot
dmesg
```

不是每次都要导出全部日志；针对当前启动和相关错误截取足够范围。读回失败记未知。板钟无RTC时，使用主机时间和`boot_id`/uptime关联，不改写历史证据时间。

## 服务模型

- Overlay拥有者应在发布校验与加载成功后才报告READY。依赖该PL的sidecar在其后启动，拥有者退出时停止依赖者。
- systemd的工作目录、解释器和环境不同于交互shell。PYNQ以源码树安装的镜像可能需要显式`PYTHONPATH=<PYNQ源码父目录>`；XRT路径也要按镜像核对，不能假设pip重装可修复。
- 不在命令行拼入明文密码。需要特权时沿用现有获准的sudo/交互配置，不把凭据写进日志。
- 对不可变发布使用`python3 -B`或`PYTHONDONTWRITEBYTECODE=1`，防止root向只读候选写入`__pycache__`，使实际发布树偏离清单。
- 服务active并不说明功能健康；检查READY、实际发布标记和硬件状态。反复重启计数也是失败线索。

## 一次试部署闭环

准备候选及实际回退版本→相关测试→一次适用增量审查→新鲜预检→一次试部署→既定验收→独立读回。

候选必须绑定实际部署的代码、二进制、必要bit/HWH与模型；不为阶段外文档重打整个候选。进程退出0、安装健康与研究指标分别判断，不能用一个`success`掩盖非零BER或未执行项目。

验收失败时按预先声明的回退路径执行，并独立验证父版本。断电或MMC故障不能保证事务完成；未知状态先取证，不重派。若实验只取得负结果，是否触发软件回退取决于事先定义的验收范围，不能事后改标准。

中间产物整理采用清单及可恢复归档；源码、当前/回退发布、原始IQ和收据保留。生成脱敏分享副本，不原地改写受摘要保护的证据。
