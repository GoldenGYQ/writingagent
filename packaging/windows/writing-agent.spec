# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


project_root = Path(SPECPATH).parents[1]
icon_path = project_root / "packaging" / "windows" / "assets" / "jlu-writing-agent.ico"

datas = collect_data_files("nanobot", include_py_files=False)
datas += collect_data_files("nanobot.channels.websocket", include_py_files=True)
datas += collect_data_files("nanobot.web", include_py_files=True)
datas += copy_metadata("nanobot-ai")
datas += [
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
]

hidden_imports = set()
for package in (
    "nanobot.agent.tools",
    "nanobot.channels.websocket",
    "nanobot.knowledge",
    "nanobot.providers",
    "nanobot.web",
):
    hidden_imports.update(
        collect_submodules(package, filter=lambda name: ".tests" not in name)
    )

a = Analysis(
    [str(project_root / "nanobot" / "desktop" / "app.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(hidden_imports),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JLU Writing Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JLU Writing Agent",
)
