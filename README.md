# TomatoTime

Windows 本地番茄钟与学习日志，四个业务阶段的首版功能已实现。验证与交付进度见 [PROGRESS.md](PROGRESS.md)。

采用 Python 3.13、uv、PySide6 和 SQLite。完整需求见 [原方案](PLAN.md)，Python 架构与实施阶段见 [Python 方案](PYTHON_PLAN.md)。

已验证本机 Python 3.13.15、uv 0.12.4、PySide6／Qt 6.12.0 和 SQLite 3.50.4；项目虚拟环境、基础和打包依赖已就绪。AI 依赖尚未安装。

## 启动与使用

打包版双击 `dist/TomatoTime/TomatoTime.exe`。分发时保留完整 `TomatoTime` 文件夹，或解压 `dist/TomatoTime-Windows-x64.zip` 后运行。打包版不需要安装 Python。

源码启动：

```powershell
uv run python run_app.py
```

1. 「今日」添加待办、备注与预计用时，选择任务后点击「开始专注」进入计时页。
2. 设置专注、短休息、长休息规则与番茄数量，再点击计时器下方的「开始专注」正式启动。
3. 每个番茄结束，右侧可写学习内容、感想和下一步；输入自动保存，填写不阻塞计时。
4. 最后一个番茄直接完成本次计划，可写周期总结；待办是否完成由你确认。
5. 「结束今天」保存每日总结；之后仍可补记。历史支持日期查看、搜索和 Markdown 导出。

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
