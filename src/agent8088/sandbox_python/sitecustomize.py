"""Preserve the DSH temp ACL when Python 3.13 creates private temp dirs."""

from __future__ import annotations

import os


def install() -> None:
    """Keep 0700 mkdirs inheriting the sandbox ACL, only under DSH's temp."""
    root = os.environ.get("AGENT8088_SANDBOX_TEMP")
    if os.name != "nt" or not root:
        return
    root = os.path.normcase(os.path.realpath(root))
    mkdir = os.mkdir

    def sandbox_mkdir(path, mode=0o777, *, dir_fd=None):
        if mode == 0o700:
            try:
                candidate = os.path.normcase(os.path.realpath(os.fsdecode(path)))
                if os.path.commonpath((root, candidate)) == root:
                    mode = 0o777
            except (OSError, TypeError, ValueError):
                pass
        if dir_fd is None:
            return mkdir(path, mode)
        return mkdir(path, mode, dir_fd=dir_fd)

    os.mkdir = sandbox_mkdir


install()
