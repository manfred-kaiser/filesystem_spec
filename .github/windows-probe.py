"""Windows diagnostics for the SFTP known_hosts code (prints, never fails)."""
import locale
import os
import stat
import sys
import tempfile

import paramiko

from fsspec.implementations.sftp import _append_known_host

print(sys.version)
print("paramiko", paramiko.__version__, "| preferred encoding:", locale.getpreferredencoding(False))
key = paramiko.RSAKey.generate(2048)
body = f"host1 {key.get_name()} {key.get_base64()}\nhost2 {key.get_name()} {key.get_base64()}\n"
d = tempfile.mkdtemp()


def path(name):
    return os.path.join(d, name)


def attempt(label, fn):
    try:
        print(f"{label:58s} {fn()}")
    except BaseException as exc:  # noqa: BLE001
        print(f"{label:58s} {type(exc).__name__}: {str(exc)[:70]}")


def bom():
    p = path("bom")
    open(p, "wb").write(b"\xef\xbb\xbf" + body.encode())
    hk = paramiko.HostKeys(p)
    return f"host1 found={hk.lookup('host1') is not None} host2 found={hk.lookup('host2') is not None}"


def utf16():
    p = path("utf16")
    open(p, "wb").write(body.encode("utf-16"))
    return f"loaded {len(paramiko.HostKeys(p))} entries"


def crlf():
    p = path("crlf")
    open(p, "wb").write(body.replace("\n", "\r\n").encode())
    _append_known_host(p, "host3", key)
    return f"entries after append: {len(paramiko.HostKeys(p))}; raw tail {open(p, 'rb').read()[-3:]!r}"


def crlf_no_final_newline():
    p = path("crlf2")
    open(p, "wb").write(body.replace("\n", "\r\n").rstrip("\r\n").encode())
    _append_known_host(p, "host3", key)
    return f"entries after append: {len(paramiko.HostKeys(p))}"


def non_ascii():
    p = path("idn")
    _append_known_host(p, "bücher.example", key)
    hk = paramiko.HostKeys(p)
    return f"found after append: {hk.lookup('bücher.example') is not None}"


def read_only():
    p = path("ro")
    open(p, "w").write("")
    os.chmod(p, stat.S_IREAD)
    try:
        _append_known_host(p, "x", key)
    finally:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    return "no error"


def held_open_for_reading():
    p = path("held")
    open(p, "w").write(body)
    with open(p, "rb"):
        _append_known_host(p, "host3", key)
    return f"entries: {len(paramiko.HostKeys(p))}"


def held_open_for_writing():
    p = path("held2")
    open(p, "w").write(body)
    with open(p, "ab"):
        _append_known_host(p, "host3", key)
    return f"entries: {len(paramiko.HostKeys(p))}"


attempt("UTF-8 BOM (PowerShell 5 -Encoding utf8)", bom)
attempt("UTF-16 (PowerShell 5 default)", utf16)
attempt("CRLF file, append", crlf)
attempt("CRLF file without final newline, append", crlf_no_final_newline)
attempt("non-ASCII host name, append then lookup", non_ascii)
attempt("read-only file (S_IREAD), append", read_only)
attempt("file open for reading by this process, append", held_open_for_reading)
attempt("file open for appending by this process, append", held_open_for_writing)
