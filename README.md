# TomatoTime

Windows 本地番茄钟与学习日志，当前改进版为 v0.2.0。更新内容见 [CHANGELOG.md](CHANGELOG.md)，验证与交付进度见 [PROGRESS.md](PROGRESS.md)。

采用 Python 3.13、uv、PySide6 和 SQLite。完整需求见 [原方案](PLAN.md)，Python 架构与实施阶段见 [Python 方案](PYTHON_PLAN.md)。

已验证本机 Python 3.13.15、uv 0.12.4、PySide6／Qt 6.12.0 和 SQLite 3.50.4；项目虚拟环境、基础和打包依赖已就绪。AI 依赖尚未安装。

## 启动与使用

打包版双击 `dist/v0.2.0/TomatoTime/TomatoTime.exe`。分发时保留完整 `TomatoTime` 文件夹，或解压 `dist/v0.2.0/TomatoTime-Windows-x64-v0.2.0.zip` 后运行。打包版不需要安装 Python。

升级前从旧程序的托盘菜单「退出」，然后运行新版；只关闭旧窗口会继续留在托盘，单实例机制会唤醒旧程序。新版首次打开旧数据时，会在数据目录保存 `before-v0.2-*.sqlite3`，再升级记录结构，保留原任务、时间片和笔记。旧版 ZIP 已保存于 `releases/v0.1.0`。

源码启动：

```powershell
uv run python run_app.py
```

1. 「今日」添加待办、备注与预计用时，选择任务后点击「开始专注」进入计时页。
2. 专注页「选择关联任务」可勾选多个任务。设置专注、短休息、长休息规则与番茄数量，再点击「开始专注」启动。
3. 每个番茄结束，右侧可写学习内容、感想和下一步；输入自动保存，填写不阻塞计时。
4. 最后一个番茄直接完成本次计划，可写周期总结；待办是否完成由你确认。
5. 「结束今天」保存每日总结；之后仍可补记。历史支持日期查看、搜索和 Markdown 导出。

多选任务后时间不会重复累计。未明确任务分配时显示「未细分」；运行中切换「当前记录任务」会立即留下分界，计时不中断，适用于自由计时和普通番茄。

如果忘记切换任务，在「历史」或「今日时间轴」右键原时段，先增加关联任务，再按确认的分界时间划分。原计时保留一条，子任务投入按照有效时间片计算，暂停不分配，总时长和完整番茄数量不变。可以右键修正时间、查看原信息或撤销调整。

补记用于新增没有开过计时的时段，起止时间默认自动计算投入；勾选「期间包含休息／离开」才填写扣除量。报错时表单与输入保留，冲突提示包含任务、日期和精确时间，可选择紧接冲突记录之后开始。只保存记录时不计入时长。混合补记未明确休息位置时，划分任务需要确认按比例估算。

本次专注总结有「这段时间做了什么」「总结与感想」两个框，涵盖整个周期，独立保存，不写入最后一段。可引用各段学习内容。任务标记完成后按钮显示「✓ 已完成」，今日同步更新，可以撤销；重复完成不会重复计数。

迷你窗口提供暂停／继续、记录、结束本次。记录打开小弹窗；专注中写当前段，休息中写刚结束的专注，周期结束写整体总结。可以手动打开完整界面。

今日待办默认按行高显示最多十项，条目少时收缩；下方拖动条可以调整高度。

「新建提醒」设置完整日期时间、标题与备注，支持重复和稍后提醒。到点弹窗，即使主窗口收至托盘也有效。事项提醒不会自动启动计时，预约自动开始单独设置。

关闭主窗口默认收至托盘，退出使用托盘右键菜单；设置中可修改关闭行为。主窗口和迷你窗口分别支持置顶。音效按事件配置，导入 MP3／WAV／OGG 后复制到数据目录。

支持自由计时、手动补记与时间修正、待办迁移、跨午夜统计、未完成计划恢复、JSON 完整备份和恢复。无法确认的混合活动仅保存记录；已确认专注的补记不能与已有专注重叠。

默认数据目录通常为 `%LOCALAPPDATA%/TomatoTime/TomatoTime`，以设置页显示为准。重新打包不会主动删除用户数据。恢复备份前会自动保存现有数据，恢复完成后重新启动应用。

应用退出、电脑关机或睡眠时不保证准时提醒；重新打开／恢复后汇总错过事项。中断时间不默认计入专注。计时每五秒保存检查点，意外崩溃最多可能损失这个间隔内的时间；文本有短延迟自动保存。

历史的「周期总结」可补写以前的总结；今日日期选择器可编辑过去的计划和回顾。每日归档在「结束今天」时生成，同名日志存在时另存版本。AI／DSH 仍为可选后续功能，当前不调用模型 API。

## 准备环境

在本项目目录运行。首次创建环境使用本机独立 Python：

```powershell
uv sync --python 'C:\Users\dz233\AppData\Local\Programs\Python\Python313\python.exe'
```

uv 创建 `.venv` 并根据 `uv.lock` 安装依赖，不使用 Conda。项目缓存通过 `uv.toml` 放在 `.uv-cache`。

后续复现环境：

```powershell
uv sync --locked
```

## 检查环境

```powershell
uv run python scripts/check_environment.py
```

检查使用离屏 Qt 窗口，不会打开可见桌面窗口或创建学习记录。检查项包括解释器、SQLite、Qt Widgets、离屏绘制和 Qt Multimedia 导入，生成 `artifacts/environment-check.png` 供查看。

离屏验证不能替代 Windows 上的真实托盘、置顶、音频播放和到点弹窗测试，这些在对应功能开发后验证。

## 业务验证与构建

```powershell
uv run python -m unittest discover -s tests -v
uv run python scripts/verify_app.py
uv run python scripts/verify_app.py --native
uv run python scripts/verify_improvements.py --native
```

界面验证使用临时数据库，包含暂停、记录绑定、主窗口隐藏时提醒、稍后提醒、迷你窗口和置顶。`--native` 会短暂显示测试窗口，以零音量验证 WAV 解码；完成后关闭。截图位于 `artifacts/app`。

打包阶段：

```powershell
uv sync --group packaging
uv run --group packaging python scripts/build.py
```

构建脚本只为打包子进程设置简化 PATH，排除 Poppler／Conda 等其他程序的 DLL，不修改系统 PATH。缓存位于项目 `.pyinstaller-cache`；输出目录式 exe、使用说明与 ZIP。Qt 使用 Windows 自带 ICU。

可选 AI 阶段：

```powershell
uv sync --extra ai
```

添加运行依赖使用 `uv add`，依赖版本由 `pyproject.toml` 与 `uv.lock` 一起管理。
