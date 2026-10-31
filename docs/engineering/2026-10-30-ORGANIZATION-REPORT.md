# 2026-10-30 工程整理记录

用途：交付或分享。原则：保留开发原件，独立生成脱敏文本源码包；不改验收标准，不操作板卡。

## 完成内容

- 重写根README为RF/PYNQ/AWG共用入口，新增工程结构与分享/恢复说明。
- 原README完整正文归档到`docs/engineering/awg-legacy-guide.md`，适配相对链接；原始文件快照另存`.local/organization-20261030/`。
- 更新AGENTS入口，去掉明文sudo口令示例，区分AWG PC界面与板载RF界面；CLAUDE增加历史状态说明。
- 新增`pynq-linux-development`、`final-prj-rf-development`两套技能，共10个文件，覆盖Overlay、AXI、部署恢复、本项目构建、接收与外部SDR及证据边界。两套通过skill-creator格式校验。
- 两套技能已安装到个人`$CODEX_HOME/skills/`，与工程维护副本逐文件SHA256一致。
- 新增`tools/project_maintenance.py`及10项维护检查，覆盖越界/跟踪文件保护、可恢复归档、拒绝覆盖、敏感路径排除、源文件与模型数值保持、旧编码源码、文档异常字符、设备标识、变量模板和ZIP内容。
- 增补Git忽略：`.local/`、`dist/`、pytest缓存/临时树、私有`.env`，保留`.env.example`入口。

## 临时项归档实绩

清单：`.local/archive/20261030-170959-317f4306/manifest.json`。

共18个根目录项、17,713个文件、22,026,690字节（约21.01 MiB）。包括旧pytest工作树、pytest缓存、空`.Xil`和两项根目录生成物。不涉及源码、`evidence/`、训练数据或已发布件。移动归档不减少磁盘占用。

恢复命令：

```powershell
python tools/project_maintenance.py restore --manifest .local/archive/20261030-170959-317f4306/manifest.json
```

如果已有新建同名临时目录，先为新目录安排去处，恢复工具不会覆盖它。

## 脱敏与保留

分享输出位于`dist/final-prj-source-<时间>/`及同名ZIP，具体文件、摘要与替换类别数量以其中`SHARE-MANIFEST.json`为准。真实现场配置和原始收据保留本地；分享副本不能替代原件验证旧摘要。

默认排除凭据、Git历史、图片（含可见设备序列号）、探测输出、原始IQ/数据集、发布归档、bit/HWH、参考资料及构建树。参考大ZIP约754.59 MiB并非缓存，未删除；生成IP存在旧仿真引用，未移动。

首次导出发现示波器另一种VISA/IDN串号写法和旧编码源码遗漏，已补规则与检查后重导出。首次副本移入`.local/rejected-exports/`，不作为分享包。旧编码文本转换为UTF-8，转换来源记录于分享清单。

`docs/memory/README.md`原件还包含一个NUL字符；仅在分享文档中清除并记入清单，开发原件不动。源码文件遇到NUL仍拒绝当普通文本导出。

只在分享文本中处理个人路径、私网地址、已识别的实际设备标识和常见口令写法。未执行全Git历史重写或凭据轮换；没有把原始数据改成“脱敏后仍是原始证据”。

## 项目状态边界

本次没有连接板卡、发射、采集、加载Overlay或安装，因此不能据此增加开发完成百分比。保留当前交付范围BPSK/QPSK/有限QAM16与QAM64移除决定，并明确外部BPSK历史23/30同步整体未通过。后续推进依据原始收据与实际现场状态。
