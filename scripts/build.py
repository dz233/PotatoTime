"""生成图标和目录式 Windows 应用。"""

import os
from pathlib import Path
import subprocess
import sys
import zipfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
root = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(root / "src"))
from PySide6.QtWidgets import QApplication
from tomatotime.widgets import icon
from tomatotime import __version__


def main():
    app = QApplication([])
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    if not icon().pixmap(256, 256).save(str(artifacts / "tomatotime.ico"), "ICO"):
        raise RuntimeError("图标生成失败")
    build_env = os.environ.copy()
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    # 仅限制这个构建子进程。避免 PATH 中的 Poppler／Conda DLL 混入 Qt。
    build_env["PATH"] = os.pathsep.join(str(path) for path in
        (Path(sys.executable).parent, Path(sys.base_prefix), windows / "System32", windows))
    for key in ("PYTHONPATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        build_env.pop(key, None)
    cache = root / ".pyinstaller-cache"
    release = root / "dist" / f"v{__version__}"
    work = root / "build" / f"v{__version__}"
    for generated in (cache, work, release):
        if not generated.resolve().is_relative_to(root):
            raise RuntimeError("构建目录必须位于项目内")
    build_env["PYINSTALLER_CONFIG_DIR"] = str(cache)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(release), "--workpath", str(work), str(root / "packaging" / "TomatoTime.spec")], cwd=root, env=build_env, check=True)
    directory = release / "TomatoTime"
    (directory / "使用说明.txt").write_text(
        f"TomatoTime {__version__}\n双击 TomatoTime.exe 启动。请完整保留该文件夹。\n"
        "升级前通过旧版托盘菜单退出旧程序；新版本首次启动会备份并兼容旧记录。\n"
        "可多选关联任务，切换当前任务时计时不中断；历史右键可划分或修正时间。\n"
        "补记默认由起止时间计算专注，含休息时再填写扣除量；报错后输入会保留。\n"
        "迷你窗口提供暂停、记录和结束本次，记录可在小弹窗填写。\n"
        "首次使用在今日页面添加待办，再到专注页面设置时长与轮数。\n"
        "关闭主窗口默认收至托盘；托盘右键菜单可退出。\n"
        "提醒在应用运行时有效；睡眠或退出后重新打开会提示错过事项。\n"
        "数据保存于 Windows 用户应用数据目录，设置页面可查看路径及导出备份。\n"
        "AI 和 DSH 尚未启用。\n", encoding="utf-8")
    archive = release / f"TomatoTime-Windows-x64-v{__version__}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in directory.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(release))
    print("Executable:", directory / "TomatoTime.exe")
    print("Archive:", archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
