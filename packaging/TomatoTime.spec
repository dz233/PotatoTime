# PyInstaller 目录式 Windows 分发；在项目根目录通过 scripts/build.py 构建。
from pathlib import Path

project = Path(SPECPATH).parent
a = Analysis(
    [str(project / "run_app.py")],
    pathex=[str(project / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
              "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
# Qt 使用 Windows 自带的 ICU；不携带来自其他程序的同名 ICU DLL。
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in ("icuuc.dll", "icuin.dll", "icu.dll")]
if any("poppler" in entry[1].lower() or "anaconda" in entry[1].lower() for entry in a.binaries):
    raise RuntimeError("构建混入外部运行库，请检查隔离后的构建环境")
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="TomatoTime", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    icon=str(project / "artifacts" / "tomatotime.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TomatoTime")
