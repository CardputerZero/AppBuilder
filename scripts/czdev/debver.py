"""Debian version comparison implementing the dpkg algorithm.

See deb-version(7): [epoch:]upstream-version[-debian-revision].
This mirrors dpkg's verrevcmp() so czdev agrees with what apt/dpkg (and the
packages-repo CI, which uses `dpkg --compare-versions`) will decide.
"""

_DIGITS = "0123456789"


def _order(c: str) -> int:
    """Sort weight of a single character ('~' sorts before empty/anything)."""
    if c == "~":
        return -1
    if c in _DIGITS:
        return 0
    if "a" <= c <= "z" or "A" <= c <= "Z":
        return ord(c)
    return ord(c) + 256


def _verrevcmp(a: str, b: str) -> int:
    ia = ib = 0
    while ia < len(a) or ib < len(b):
        first_diff = 0

        # Compare the non-digit prefix ('~' < end-of-string < letters < others).
        while (ia < len(a) and a[ia] not in _DIGITS) or (ib < len(b) and b[ib] not in _DIGITS):
            ac = _order(a[ia]) if ia < len(a) else 0
            bc = _order(b[ib]) if ib < len(b) else 0
            if ac != bc:
                return ac - bc
            ia += 1
            ib += 1

        # Compare the numeric run: skip leading zeros, then the first differing
        # digit decides unless one number is longer.
        while ia < len(a) and a[ia] == "0":
            ia += 1
        while ib < len(b) and b[ib] == "0":
            ib += 1
        while ia < len(a) and a[ia] in _DIGITS and ib < len(b) and b[ib] in _DIGITS:
            if not first_diff:
                first_diff = ord(a[ia]) - ord(b[ib])
            ia += 1
            ib += 1
        if ia < len(a) and a[ia] in _DIGITS:
            return 1
        if ib < len(b) and b[ib] in _DIGITS:
            return -1
        if first_diff:
            return first_diff
    return 0


def _split(version: str):
    version = version.strip()
    epoch = 0
    head, sep, rest = version.partition(":")
    if sep and head.isdigit():
        epoch = int(head)
        version = rest
    upstream, sep, revision = version.rpartition("-")
    if not sep:
        upstream, revision = version, ""
    return epoch, upstream, revision


def compare_versions(a: str, b: str) -> int:
    """Return -1 / 0 / 1 like dpkg --compare-versions (a lt/eq/gt b)."""
    ea, ua, ra = _split(a)
    eb, ub, rb = _split(b)
    if ea != eb:
        return 1 if ea > eb else -1
    rc = _verrevcmp(ua, ub)
    if rc == 0:
        rc = _verrevcmp(ra, rb)
    return (rc > 0) - (rc < 0)
