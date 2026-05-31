"""Make CTranslate2 (used by faster-whisper) find the cuDNN/cuBLAS DLLs that ship
in the nvidia-*-cu12 wheels.

On Windows, CTranslate2 loads cublas64_12.dll via a plain LoadLibrary call, which
searches PATH. os.add_dll_directory alone is NOT sufficient, so we also prepend the
wheel bin directories to PATH. Call ensure_cuda_dlls() once before constructing a
WhisperModel on CUDA. No-op on non-Windows or if the wheels are absent.
"""
from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

_DONE = False


def ensure_cuda_dlls() -> None:
    global _DONE
    if _DONE or sys.platform != "win32":
        _DONE = True
        return
    site = Path(sysconfig.get_paths()["purelib"])
    for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
        d = site / sub
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    _DONE = True
