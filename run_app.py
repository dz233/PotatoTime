"""源码启动入口：uv run python run_app.py。"""

from pathlib import Path
import sys

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    try:
        from tomatotime.app import main
        code = main()
    except Exception:
        import os
        import traceback
        details = traceback.format_exc()
        try:
            index = sys.argv.index("--data-dir")
            directory = Path(sys.argv[index + 1])
        except (ValueError, IndexError):
            directory = Path(os.environ.get("LOCALAPPDATA", ".")) / "TomatoTime" / "TomatoTime"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "startup-error.log"
        log.write_text(details, encoding="utf-8")
        if sys.stderr:
            sys.stderr.write(details)
        # 自动验证只写日志并返回失败码，不反复打断用户。
        if "--smoke-exit" not in sys.argv and sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"应用启动失败，详细信息已保存：\n{log}", "TomatoTime", 16)
        code = 1
    raise SystemExit(code)
