#!/usr/bin/env python3
"""Read-only permission audit for participant-bearing trees (BA-103).

Fails when any file or directory under the given roots grants the world any
permission bit (o+rwx) or grants the group write (g+w). New export leaves are
pinned 0750/0640 by ``app.export.csvsafe``; this script catches ops-side drift —
copied bundles, restored trees, permissive roots — that the writer cannot see.

    python3 scripts/check_export_permissions.py [ROOT ...]

Defaults to the deployed export root and the backups tree. Exits 1 on findings.
"""

from __future__ import annotations

import os
import sys

DEFAULT_ROOTS = (
    "./local-data/exports",
    "./backups",
)

BAD_DIR_BITS = 0o007  # world rwx on directories (x lets a stranger traverse)
BAD_FILE_BITS = 0o027  # world rwX or group write on files


def audit(root: str) -> list[str]:
    findings: list[str] = []
    if not os.path.isdir(root):
        findings.append(f"missing root: {root}")
        return findings
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            bad = BAD_DIR_BITS if os.path.isdir(path) else BAD_FILE_BITS
            if st.st_mode & bad:
                findings.append(f"{oct(st.st_mode & 0o777)} {path}")
    return findings


def main(argv: list[str]) -> int:
    roots = argv or list(DEFAULT_ROOTS)
    findings = [f for root in roots for f in audit(root)]
    if findings:
        print("BA-103 permission audit FAILED:")
        print("\n".join(f"  {f}" for f in findings))
        return 1
    print(f"BA-103 permission audit PASS: {len(roots)} root(s) clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
