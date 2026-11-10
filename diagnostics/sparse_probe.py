"""Diagnostic only (not part of the app): measures real on-disk allocation of sparse files on Windows.

Run from the project root:   .venv\Scripts\python.exe diagnostics\sparse_probe.py
Writes diagnostics\sparse_probe.json and prints the same. Creates only temporary files and deletes them.

It answers: which filesystem is the scratch dir on, is it really NTFS-sparse-capable, and WHERE (which ranges) the
allocation of a 1 GiB logical file sits, for three ways of extending the file.
"""
import ctypes
import json
import msvcrt
import os
import sys
import tempfile
import time
import uuid
from ctypes import wintypes
from pathlib import Path

if os.name != "nt":
    sys.exit("Windows only.")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
k32.DeviceIoControl.restype = wintypes.BOOL
k32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
k32.GetCompressedFileSizeW.restype = wintypes.DWORD
k32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
k32.GetFileAttributesW.restype = wintypes.DWORD
k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
k32.SetFilePointerEx.restype = wintypes.BOOL
k32.SetEndOfFile.argtypes = [wintypes.HANDLE]
k32.SetEndOfFile.restype = wintypes.BOOL
k32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                      ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
k32.GetVolumeInformationW.restype = wintypes.BOOL

FSCTL_SET_SPARSE, FSCTL_SET_ZERO_DATA, FSCTL_QUERY_ALLOCATED_RANGES = 0x000900C4, 0x000980C8, 0x000940CF
HEADER = bytes(range(1, 101))
MiB = 1 << 20


def handle(fh):
    return msvcrt.get_osfhandle(fh.fileno())


def ioctl(fh, code, inbuf=None, outbuf=None):
    ret = wintypes.DWORD(0)
    ok = k32.DeviceIoControl(handle(fh), code, inbuf, ctypes.sizeof(inbuf) if inbuf is not None else 0, outbuf,
                             ctypes.sizeof(outbuf) if outbuf is not None else 0, ctypes.byref(ret), None)
    return bool(ok), ctypes.get_last_error(), ret.value


def allocated(path):
    high = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    low = k32.GetCompressedFileSizeW(str(path), ctypes.byref(high))
    return (high.value << 32) | low


def allocated_ranges(path):
    """(count, total_bytes, first few ranges) from FSCTL_QUERY_ALLOCATED_RANGES."""
    size = os.stat(path).st_size
    with open(path, "rb") as fh:
        query = (ctypes.c_longlong * 2)(0, size)
        out = (ctypes.c_longlong * 2000)()
        ok, err, n = ioctl(fh, FSCTL_QUERY_ALLOCATED_RANGES, query, out)
        entries = n // 16
        ranges = [(out[2 * i], out[2 * i + 1]) for i in range(entries)]
        return {"ok": ok, "error": err, "count": entries, "more_data": err == 234,
                "total_listed": sum(r[1] for r in ranges), "first": ranges[:6]}


def volume(path):
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    name, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
    serial, maxc, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    ok = k32.GetVolumeInformationW(root, name, 261, ctypes.byref(serial), ctypes.byref(maxc), ctypes.byref(flags), fs, 261)
    f = flags.value
    return {"root": root, "ok": bool(ok), "filesystem": fs.value, "supports_sparse": bool(f & 0x40), "supports_compression": bool(f & 0x10),
            "volume_is_compressed": bool(f & 0x8000), "flags_hex": hex(f)}


def attrs(path):
    a = k32.GetFileAttributesW(str(path))
    return {"hex": hex(a), "sparse": bool(a & 0x200), "compressed": bool(a & 0x800), "not_content_indexed": bool(a & 0x2000)}


def build(path, size, variant):
    with open(path, "xb") as fh:
        ok, err, _ = ioctl(fh, FSCTL_SET_SPARSE)
        if not ok:
            raise OSError(f"FSCTL_SET_SPARSE failed, Windows error {err}")
        fh.write(HEADER)
        fh.flush()
        if variant == "current":  # what rea_client.create_sparse_file does
            fh.truncate(size)
        elif variant == "setendoffile":  # the same operation done explicitly through the Win32 API
            if not k32.SetFilePointerEx(handle(fh), size, None, 0) or not k32.SetEndOfFile(handle(fh)):
                raise OSError(f"SetEndOfFile failed, Windows error {ctypes.get_last_error()}")
        elif variant == "extend_then_zero_data":  # extend, then explicitly declare the rest a hole
            fh.truncate(size)
            zd = (ctypes.c_longlong * 2)(len(HEADER), size)
            ok, err, _ = ioctl(fh, FSCTL_SET_ZERO_DATA, zd)
            if not ok:
                raise OSError(f"FSCTL_SET_ZERO_DATA failed, Windows error {err}")


def probe(folder, size, variant):
    path = Path(folder) / f"probe_{uuid.uuid4().hex}.bin"
    row = {"folder": str(folder), "logical_mib": size // MiB, "variant": variant}
    try:
        build(path, size, variant)
        row["logical_ok"] = os.stat(path).st_size == size
        row["attrs"] = attrs(path)
        row["alloc_mib_at_0s"] = round(allocated(path) / MiB, 2)
        row["ranges_at_0s"] = allocated_ranges(path)
        time.sleep(3)
        row["alloc_mib_at_3s"] = round(allocated(path) / MiB, 2)
        row["ranges_at_3s"] = allocated_ranges(path)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(path)
        except OSError as exc:
            row["cleanup_error"] = str(exc)
    return row


def main():
    root = Path(__file__).resolve().parent.parent
    scratch = root / ".m2g" / "rea"
    scratch.mkdir(parents=True, exist_ok=True)
    folders = [scratch, Path(tempfile.gettempdir())]
    result = {"python": sys.version, "volumes": {str(f): volume(f) for f in folders}, "rows": []}
    for folder in folders:
        for size in (64 * MiB, 1024 * MiB):
            for variant in ("current", "setendoffile", "extend_then_zero_data"):
                row = probe(folder, size, variant)
                result["rows"].append(row)
                print(json.dumps(row))
    out = root / "diagnostics" / "sparse_probe.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("\nVolumes:", json.dumps(result["volumes"], indent=2))
    print("Written:", out)


if __name__ == "__main__":
    main()
