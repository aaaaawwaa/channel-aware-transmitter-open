# G1前transport补齐 Implementation Plan（2026-10-08，已授权离线执行）

> 用户明确“你尝试补齐，然后后续我让agent跑G1”。本轮只补两项生产transport缺口，保留当前dirty tree/既有分支，不重置、不自动提交；G1设备构建、安装和实采不在本轮范围。

**Goal:** 唤醒机制初始化失败必须阻止启动；发送背压时终止可被观察并进入既有确认收尾，不在阻塞send中反复EINTR。

**Architecture:** 保留唯一daemon/core、协议与已有self-pipe，提取最小共享transport策略单元，生产接线调用同一受测代码。OS适配器仅负责真实pipe/fcntl/sigaction/poll/非阻塞send，主机通过注入syscall结果复现失败和停止；真实Linux信号/套接字验证交给G1。

**Tech Stack:** 既有C/MinGW假硬件测试与ARM/Linux生产接线；独立HOST_OBJDIR/证据目录；不安装依赖或改变PL/vendor/gain/AGC/算法/协议版本。

## 范围与退出条件

- [x] 保存修复前源码快照/摘要和现有测试基线，核对没有并发产品修改。
- [x] RED：pipe失败、任一fcntl读取/设置失败、任一sigaction安装失败；拒绝继续启动，清部分FD，不在失败路径启动listener/获取控制硬件。
- [x] RED：预存停止、检查后唤醒、发送EAGAIN/partial/EINTR、发送背压时stop、等待错误、整体发送期限；不允许慢进度无限刷新期限。默认成功路径不新增RF硬件操作。
- [x] GREEN：所有初始化结果检查；非阻塞发送＋可等待的self-pipe停止；错误/终止返回既有传输失败或关闭路径，不生成成功frame/trace。避免增加默认capture时序观测clock（transport必要期限时钟单独说明）。
- [x] 同生产策略的主机测试、完整C目标与vendor完整性、相关Python兼容回归；关键变异应先确认已生效再运行。
- [x] 规范→质量独立审阅并处理问题；发布本轮源快照、日志/退出码与交接收据。
- [x] 整理G1精确ARM构建＋Linux运行用例，标记ARM/G1/G2/实采NOT_RUN，G1 agent不得跳过真实信号和背压验证。

实现worker负责board_app C策略/生产接线/Makefile及对应测试。根代理负责方案记录、总回归、证据与交接；两者不互相修改负责文件。选择最小文件边界，不为这两项作无关重构。
**完成（2026-10-08）：** 完整C、相关Python78、vendor10、三项有效变异及规范→质量独立复审通过。51 native输入/17交接工具源码冻结，报告与G1清单固化。ARM/G1/G2/RF仍NOT_RUN。
