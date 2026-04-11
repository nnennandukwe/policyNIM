# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PolicyNIM standalone release bundles."""

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

project_root = Path(SPECPATH).parent
entrypoint = project_root / "src/policynim/interfaces/cli.py"

datas = [
    (str(project_root / "policies"), "policynim/policies"),
    (str(project_root / "evals"), "policynim/evals"),
    (str(project_root / "src/policynim/assets"), "policynim/assets"),
    (str(project_root / "src/policynim/templates"), "policynim/templates"),
]
datas += copy_metadata("policynim")

a = Analysis(
    [str(entrypoint)],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="policynim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="policynim",
)
