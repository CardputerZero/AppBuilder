"""Debian version strings: comparing them, and the names derived from them.

A Debian version may contain '~' and ':', which a git ref cannot and a GitHub
release asset name silently turns into '.'. Both channels of the store (the
portal and `czdev publish`) derive names the same way, so they stay here.
"""

import re


def ref_safe(version: str) -> str:
    """The version as it may appear in a branch name."""
    return re.sub(r"[~:]", "_", version)


def asset_safe(name: str) -> str:
    """The file name GitHub will store a release asset under."""
    return re.sub(r"[~:]", ".", name)


def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _verrevcmp(a: str, b: str) -> int:
    ia = ib = 0
    while ia < len(a) or ib < len(b):
        first_diff = 0
        while (ia < len(a) and not a[ia].isdigit()) or (ib < len(b) and not b[ib].isdigit()):
            ac = _order(a[ia]) if ia < len(a) else 0
            bc = _order(b[ib]) if ib < len(b) else 0
            if ac != bc:
                return ac - bc
            ia += 1
            ib += 1
        while ia < len(a) and a[ia] == "0":
            ia += 1
        while ib < len(b) and b[ib] == "0":
            ib += 1
        while ia < len(a) and a[ia].isdigit() and ib < len(b) and b[ib].isdigit():
            if not first_diff:
                first_diff = ord(a[ia]) - ord(b[ib])
            ia += 1
            ib += 1
        if ia < len(a) and a[ia].isdigit():
            return 1
        if ib < len(b) and b[ib].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def _split(v: str):
    v = v.strip()
    epoch = 0
    head, sep, rest = v.partition(":")
    if sep and head.isdigit():
        epoch, v = int(head), rest
    upstream, sep, revision = v.rpartition("-")
    return (epoch, upstream, revision) if sep else (epoch, v, "")


def compare_versions(a: str, b: str) -> int:
    """`dpkg --compare-versions` semantics: -1, 0 or 1. 1.0~rc1 sorts before 1.0."""
    ea, ua, ra = _split(a)
    eb, ub, rb = _split(b)
    if ea != eb:
        return 1 if ea > eb else -1
    rc = _verrevcmp(ua, ub) or _verrevcmp(ra, rb)
    return (rc > 0) - (rc < 0)
