# PyInstaller spec: builds dist/Arrow/Arrow.exe with its own Python inside.
# Build on Windows with:  pyinstaller packaging/arrow.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
# packages only (sounddevice/miniaudio are single modules; PyInstaller's own hooks cover them)
for pkg in ("uiautomation", "edge_tts", "certifi", "_sounddevice_data"):
    try:
        d, b, h = collect_all(pkg)
    except Exception as exc:  # a missing optional package must not break the build
        print(f"[arrow.spec] skip {pkg}: {exc}")
        continue
    datas += d; binaries += b; hiddenimports += h
hiddenimports += collect_submodules("arrow_assistant")
hiddenimports += collect_submodules("comtypes")
hiddenimports += collect_submodules("pynput")
hiddenimports += ["keyring.backends.Windows", "miniaudio", "_miniaudio", "sounddevice"]

a = Analysis(
    ["arrow_launcher.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["faster_whisper", "ctranslate2", "torch", "pyttsx3", "tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Arrow",
    console=False,          # tray app: no black console window
    icon=None,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Arrow")
