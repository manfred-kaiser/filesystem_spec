"""Is the flush() before the unlock needed when appending to known_hosts?

Part 1 is deterministic: at the moment of LK_UNLCK, a second handle reads the
file and tells whether the line is already in it.

Part 2 is a stress test: several processes append lines at the same time and
the file is checked for lost, torn and duplicated lines. The variant without a
lock is the control, it has to fail, otherwise the test cannot see the race.

Prints a report and only fails when the probe itself breaks.
"""
import contextlib
import multiprocessing as mp
import os
import re
import sys
import tempfile
import time

import paramiko

from fsspec.implementations import sftp

msvcrt = sftp.msvcrt
if msvcrt is None:  # smoke test on POSIX: a lock that does nothing
    import types

    msvcrt = types.SimpleNamespace(
        LK_LOCK=1, LK_UNLCK=0, locking=lambda fd, mode, n: None
    )
    sftp.msvcrt = msvcrt
    print("NOT WINDOWS: results are meaningless")


@contextlib.contextmanager
def lock_no_seek(f):
    """Like sftp._locked, but unlocks without f.seek() (which flushes)."""
    f.seek(1 << 30)
    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    try:
        yield
    finally:
        os.lseek(f.fileno(), 1 << 30, os.SEEK_SET)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def no_lock(f):
    yield


def append(path, line, lock, flush):
    """The body of sftp._append_known_host, with lock and flush selectable."""
    with open(path, "a+b") as f, lock(f):
        size = f.seek(0, os.SEEK_END)
        if size:
            f.seek(size - 1)
            if f.read(1) != b"\n":
                line = b"\n" + line
        f.write(line)
        if flush:
            f.flush()


VARIANTS = {
    # name: (lock, flush)
    "real code (_locked, flush)": (sftp._locked, True),
    "_locked, no flush": (sftp._locked, False),
    "no seek in unlock, flush": (lock_no_seek, True),
    "no seek in unlock, no flush": (lock_no_seek, False),
    "no lock (control)": (no_lock, True),
}


# ---------------------------------------------------------------- part 1
def line_in_file_at_unlock(run, marker):
    """Run `run(path, line)`, return whether marker was in the file at LK_UNLCK."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "known_hosts")
    with open(path, "wb") as f:
        f.write(b"seed line\n")
    line = b"the new line\n"
    seen = []
    real = msvcrt.locking

    def spy(fd, mode, n):
        if mode == msvcrt.LK_UNLCK:
            with open(path, "rb") as g:
                seen.append(marker in g.read())
        return real(fd, mode, n)

    msvcrt.locking = spy
    try:
        run(path, line)
    finally:
        msvcrt.locking = real
    return seen


def part1():
    print("== Part 1: is the line in the file when the lock is released? ==")
    key = paramiko.RSAKey.generate(1024)

    def real_function(path, _line):
        sftp._append_known_host(path, "probe.example", key)

    seen = line_in_file_at_unlock(real_function, b"probe.example ")
    print(f"{'sftp._append_known_host()':34s} line present at unlock: {seen}")
    for name, (lock, flush) in VARIANTS.items():
        if lock is no_lock:
            continue
        seen = line_in_file_at_unlock(
            lambda p, line, lock=lock, flush=flush: append(p, line, lock, flush),
            b"the new line",
        )
        print(f"{name:34s} line present at unlock: {seen}")


# ---------------------------------------------------------------- part 2
LINE_LEN = 1000


def make_line(wid, i):
    head = f"w{wid:02d}-i{i:05d} ".encode()
    return head + (b"%c" % (97 + wid)) * (LINE_LEN - len(head) - 1) + b"\n"


def worker(variant, path, wid, count, barrier, out):
    lock, flush = VARIANTS[variant]
    barrier.wait()
    errors = 0
    for i in range(count):
        while True:
            try:
                append(path, make_line(wid, i), lock, flush)
                break
            except OSError:  # LK_LOCK gave up after 10 s: try again
                errors += 1
    out.put(errors)


def check(path, procs, count):
    with open(path, "rb") as f:
        data = f.read()
    expected = {make_line(w, i) for w in range(procs) for i in range(count)}
    lines = data.split(b"\n")
    got = {}
    bad = blank = 0
    for raw in lines:
        if not raw:
            blank += 1
            continue
        if raw + b"\n" in expected:
            got[raw + b"\n"] = got.get(raw + b"\n", 0) + 1
        elif raw != b"seed line":
            bad += 1
    missing = len(expected - set(got))
    dup = sum(c - 1 for c in got.values() if c > 1)
    return missing, bad, dup


def stress(variant, procs, count, rounds, budget):
    start = time.time()
    failed = 0
    totals = [0, 0, 0]
    retries = 0
    done = 0
    for r in range(rounds):
        if time.time() - start > budget:
            break
        done += 1
        d = tempfile.mkdtemp()
        path = os.path.join(d, "known_hosts")
        with open(path, "wb") as f:
            f.write(b"seed line\n" if r % 2 else b"seed line")  # with/without \n
        barrier = mp.Barrier(procs)
        out = mp.Queue()
        ps = [
            mp.Process(target=worker, args=(variant, path, w, count, barrier, out))
            for w in range(procs)
        ]
        for p in ps:
            p.start()
        for p in ps:
            p.join()
        retries += sum(out.get() for _ in ps)
        res = check(path, procs, count)
        totals = [a + b for a, b in zip(totals, res)]
        failed += any(res)
    print(
        f"{variant:34s} rounds failed: {failed}/{done}  "
        f"missing={totals[0]} torn={totals[1]} duplicated={totals[2]} "
        f"lock-retries={retries}"
    )


def part2(procs, count, rounds, budget=90):
    print(f"== Part 2: {procs} processes x {count} appends, up to {rounds} rounds ==", flush=True)
    for variant in VARIANTS:
        t = time.time()
        stress(variant, procs, count, rounds, budget)
        print(f"   ({time.time() - t:.0f}s)", flush=True)


if __name__ == "__main__":
    print(sys.version)
    print("paramiko", paramiko.__version__, "| cpus:", os.cpu_count())
    part1()
    procs, count, rounds = (int(a) for a in sys.argv[1:4]) if len(sys.argv) > 3 else (8, 200, 10)
    part2(procs, count, rounds)
