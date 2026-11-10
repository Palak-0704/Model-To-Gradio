"""Phase 4.2 package integrity: per-model locks, staged writes with rollback, generation fingerprints and the package manifest.

Everything here is backend-only and works on plain files, so it behaves the same on Windows 11 (no WSL, no Docker, no extra services).

* ``model_lock``        - one re-entrant lock per output folder, shared by Analyze and Generate (and by the launch gates).
* ``write_package``     - write the files into a hidden staging folder, check them, then replace the live files one by one. A failure
                          part-way restores every file that was already replaced, so the previous working package survives.
* fingerprints          - deterministic SHA-256 over everything that decides what OpenCode would be asked to write.
* manifest              - ``.m2g_manifest.json``: model/revision identity, the generation fingerprint and a SHA-256 per artifact.
                          It is a hidden sidecar: the package itself stays the six artifacts it always was.
* ``verify_package``    - compares the folder with its manifest before a local or Colab launch.

A folder without a manifest (generated before Phase 4.2) is a legacy package: it stays launchable and is never reused as a cache.
Nothing in this module ever executes generated code or calls the network.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from .security import SecurityError, ensure_within, redact_secrets

MANIFEST_NAME = ".m2g_manifest.json"
MANIFEST_SCHEMA = "m2g-package-manifest/1"
FINGERPRINT_VERSION = "m2g-generation-fingerprint/1"
STAGING_DIRNAME = ".m2g-staging"  # inside the output directory; hidden names are skipped by the app listing
LOCK_DIRNAME = ".m2g-locks"  # process locks; never part of a generated model package or its manifest
LOCK_TIMEOUT_S = 1800.0  # package-write lock only; unrelated to the OpenCode process lifetime
STALE_STAGING_S = 3600.0
# The files whose hashes a launch enforces. The model's own launcher (run_<folder>.bat) is recorded in the manifest but not enforced:
# the backend rewrites it from its template right before every local launch.
ENFORCED_ARTIFACTS = ("app.py", "loader.py", "requirements.txt", "analysis.json", "colab_notebook.ipynb")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$|^\.m2g_manifest\.json$")
# Modules whose source decides what is asked of OpenCode and how the package is assembled; an edit to any of them changes the fingerprint.
_GENERATOR_MODULES = ("gradio_gen", "colab_gen", "local_script", "validate", "gradio_compat")


class PackageStoreError(OSError):
    """A package could not be staged or committed. An OSError so the pipeline reports it as a write-stage failure."""


class PackageBusyError(PackageStoreError):
    """Another analysis or generation holds this model's lock."""


class PackageCommitError(PackageStoreError):
    def __init__(self, message: str, keep_backup: bool = False) -> None:
        super().__init__(message)
        self.keep_backup = keep_backup


# --------------------------------------------------------------------------- hashing


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def disk_bytes(name: str, text: str) -> bytes:
    """The exact bytes a file ends up with on disk. .bat files need CRLF (cmd.exe can miss :labels in LF-only batch files)."""
    return (text.replace("\n", "\r\n") if name.lower().endswith(".bat") else text).encode("utf-8")


def _canonical_hash(doc: Mapping[str, Any]) -> str:
    return sha256_bytes(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8"))


# --------------------------------------------------------------------------- per-model locks

_registry_lock = threading.Lock()
_model_locks: Dict[str, "threading.RLock"] = {}
_held_locks = threading.local()


def lock_key(base: Any, slug: str) -> str:
    """One key per output folder. normcase makes it case-insensitive on Windows, where C:\\Out\\Model and c:\\out\\model are one folder."""
    return os.path.normcase(str(Path(base).expanduser().resolve() / slug))


def _lock_path(base: Any, slug: str) -> Path:
    if not _NAME_RE.match(slug):
        raise PackageStoreError(f"Unsafe model slug {slug!r}.")
    # Windows' realpath is not atomic: it looks a path up twice and only strips its \\?\ prefix when both lookups fail the same way. When
    # another process creates or opens a component between them (two processes starting on the same new model: the lock folder and the
    # lock file are created and held right then), the prefix stays, a legitimate path no longer looks like a child of the output folder
    # and containment fails. So the lock FOLDER is created first, and only existing folders are resolved from there on. Containment is
    # checked on that folder; the file is one name that _NAME_RE limits to a single path component, and a link in its place is refused.
    lock_dir_in = Path(base).expanduser() / LOCK_DIRNAME
    lock_dir_in.mkdir(parents=True, exist_ok=True)
    root = Path(base).expanduser().resolve()
    lock_dir = ensure_within(root, lock_dir_in)
    lock_file = lock_dir / (slug + ".lock")
    if lock_file.is_symlink():
        raise SecurityError("Refusing to write outside the output directory.")
    return lock_file


def _thread_holds(key: str) -> bool:
    return key in getattr(_held_locks, "handles", {})


def _acquire_process_lock(path: Path, wait: float) -> Any:
    """Acquire one byte of a per-slug lock file without leaving ownership state on disk.

    Windows releases a byte-range lock automatically if its owning process dies.  The file itself
    is deliberately retained: deleting a lock file would create a race in which another process
    locks a new file while an older process still owns the old file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + wait
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # keeps the offline test suite usable on non-Windows hosts; production target is Windows.
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise PackageBusyError("Another process is using this model package. Try again when it has finished.")
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _release_process_lock(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextlib.contextmanager
def model_lock(base: Any, slug: str, timeout: Optional[float] = None) -> Iterator[None]:
    """Hold the thread and OS lock for one model folder.

    Re-entrant for a thread, while the outermost acquisition owns a byte-range lock visible to
    other Windows processes.  A crashed process loses its OS lock automatically; lock files contain
    no ownership state and are intentionally retained outside generated packages.
    """
    key = lock_key(base, slug)
    with _registry_lock:
        lock = _model_locks.setdefault(key, threading.RLock())
    wait = LOCK_TIMEOUT_S if timeout is None else max(0.0, float(timeout))
    if not lock.acquire(timeout=wait):
        raise PackageBusyError(f"Another analysis or generation for '{slug}' is still running. Try again when it has finished.")
    handles = getattr(_held_locks, "handles", None)
    if handles is None:
        handles = _held_locks.handles = {}
    outer = key not in handles
    try:
        if outer:
            handles[key] = _acquire_process_lock(_lock_path(base, slug), wait)
        yield
    finally:
        if outer:
            handle = handles.pop(key, None)
            if handle is not None:
                _release_process_lock(handle)
        lock.release()


def require_model_lock(base: Any, slug: str) -> None:
    """Reject a package commit that did not enter ``model_lock`` in this thread."""
    if not _thread_holds(lock_key(base, slug)):
        raise PackageStoreError("write_package requires model_lock for this model package.")


# --------------------------------------------------------------------------- staged write with rollback


def _replace_with_retry(src: Path, dst: Path, attempts: int = 5, delay: float = 0.1) -> None:
    """os.replace is atomic for one file. On Windows it can fail for a moment while an antivirus scanner or an indexer holds the
    target open, so PermissionError is retried a few times before it counts as a failure."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def _cleanup_stale(staging_root: Path, slug: str) -> None:
    """Remove staging folders a crashed run of this model left behind. ``.bak`` folders are never removed here: they can hold the
    only copy of a file a failed rollback could not restore."""
    if not staging_root.is_dir():
        return
    cutoff = time.time() - STALE_STAGING_S
    for entry in staging_root.iterdir():
        if entry.name.startswith(slug + "-") and not entry.name.endswith(".bak"):
            with contextlib.suppress(OSError):
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)


def _rollback(out: Path, backup: Path, journal: List[Tuple[str, bool]]) -> List[str]:
    """Undo a part-finished commit, newest first. Returns the names that could not be restored."""
    failed: List[str] = []
    for name, had in reversed(journal):
        target = out / name
        try:
            if had:
                saved = backup / name
                try:
                    same = target.is_file() and target.read_bytes() == saved.read_bytes()
                except OSError:
                    same = False
                if not same:
                    _replace_with_retry(saved, target)
            elif target.exists():
                target.unlink()
        except OSError:
            failed.append(name)
    return failed


def _commit(out: Path, stage: Path, payload: Mapping[str, bytes], backup: Path) -> None:
    changed: List[str] = []
    for name, data in payload.items():
        target = out / name
        if target.is_symlink() or target.is_dir():
            raise PackageCommitError(f"{name} in the package folder is a link or a folder; it was not replaced.")
        if target.is_file():
            try:
                if target.read_bytes() == data:
                    continue  # unchanged files are never touched (a running app may hold them open)
            except OSError:
                pass
        changed.append(name)
    if not changed:
        return
    journal: List[Tuple[str, bool]] = []
    try:
        backup.mkdir(parents=True, exist_ok=True)
        for name in changed:
            target = out / name
            had = target.is_file()
            if had:
                shutil.copy2(target, backup / name)
            journal.append((name, had))  # recorded before the replace, so a replace that fails half-way is rolled back too
            _replace_with_retry(stage / name, target)
        for name in changed:  # read-back: Windows antivirus or permissions can silently interfere
            if (out / name).read_bytes() != payload[name]:
                raise OSError(f"Read-back mismatch for {name}")
    except Exception as exc:
        failed = _rollback(out, backup, journal)
        reason = f"{type(exc).__name__}: {exc}"
        if failed:
            raise PackageCommitError(
                f"The package could not be replaced ({reason}) and these files could not be restored: {', '.join(sorted(failed))}. "
                f"The previous copies are kept in {STAGING_DIRNAME}/{backup.name}.", keep_backup=True) from exc
        raise PackageCommitError(f"The package could not be replaced ({reason}); the previous package was restored unchanged.") from exc


def write_package(base: Any, slug: str, files: Mapping[str, str]) -> Path:
    """Write ``files`` into ``<base>/<slug>`` without ever leaving a half-written package.

    The files are written and verified in ``<base>/.m2g-staging/<slug>-<id>/`` first. Only then do they replace the live files, one
    atomic ``os.replace`` each; a failure restores every file already replaced. The live folder is never swapped as a whole, because
    on Windows a folder with a running app (its ``.venv`` is inside) cannot be renamed. Files whose content is unchanged are left alone.
    """
    base = Path(base).expanduser().resolve()
    require_model_lock(base, slug)
    out = ensure_within(base, base / slug)
    for name in files:
        if not _NAME_RE.match(name):
            raise PackageStoreError(f"Unsafe artifact name {name!r}.")
    out.mkdir(parents=True, exist_ok=True)
    staging_root = ensure_within(base, base / STAGING_DIRNAME)
    run_id = uuid.uuid4().hex[:12]
    stage = staging_root / f"{slug}-{run_id}"
    backup = staging_root / f"{slug}-{run_id}.bak"
    keep_backup = False
    try:
        _cleanup_stale(staging_root, slug)
        stage.mkdir(parents=True)
        payload: Dict[str, bytes] = {}
        for name, text in files.items():
            data = disk_bytes(name, text)
            target = ensure_within(base, stage / name)
            target.write_bytes(data)
            if target.read_bytes() != data:
                raise PackageStoreError(f"Read-back mismatch for {name} while staging.")
            payload[name] = data
        _commit(out, stage, payload, backup)
    except PackageCommitError as exc:
        keep_backup = exc.keep_backup
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if not keep_backup:
            shutil.rmtree(backup, ignore_errors=True)
        with contextlib.suppress(OSError):
            staging_root.rmdir()  # only succeeds when nothing else is staging
    return out


def publish_verified_binary(base: Any, slug: str, source: Any, name: str, *, expected_size: int, expected_sha256: str) -> Path:
    """Atomically publish an already-authorized binary under the caller's model lock.

    This is intentionally separate from ``write_package``: model binaries are never decoded
    as text and are not accepted from repository inspection.
    """
    base = Path(base).expanduser().resolve(); require_model_lock(base, slug)
    src = Path(source)
    if not _NAME_RE.match(name) or not name.lower().endswith(".onnx"):
        raise PackageStoreError("Unsafe ONNX artifact name.")
    if src.is_symlink() or not src.is_file() or src.stat().st_size != expected_size:
        raise PackageStoreError("Uploaded ONNX artifact is missing, linked, or has an unexpected size.")
    if sha256_file(src) != expected_sha256:
        raise PackageStoreError("Uploaded ONNX artifact checksum changed before publication.")
    out = ensure_within(base, base / slug); out.mkdir(parents=True, exist_ok=True)
    stage_root = ensure_within(base, base / STAGING_DIRNAME); stage_root.mkdir(parents=True, exist_ok=True)
    stage = stage_root / (slug + "-artifact-" + uuid.uuid4().hex[:12])
    try:
        shutil.copyfile(src, stage)
        if stage.is_symlink() or stage.stat().st_size != expected_size or sha256_file(stage) != expected_sha256:
            raise PackageStoreError("ONNX artifact staging verification failed.")
        os.replace(stage, ensure_within(base, out / name))
    finally:
        with contextlib.suppress(OSError): stage.unlink()
        with contextlib.suppress(OSError): stage_root.rmdir()
    return out / name


# --------------------------------------------------------------------------- fingerprints

_source_digest: Optional[str] = None


def generator_fingerprint(settings: Any = None) -> Optional[str]:
    """SHA-256 over what the generator is made of: the source of the generation/validation modules, the OpenCode model and the
    settings that change what may be generated. None when the sources cannot be read (caching is then simply off)."""
    global _source_digest
    try:
        if _source_digest is None:
            digest = hashlib.sha256()
            for name in _GENERATOR_MODULES:
                path = getattr(importlib.import_module(f"{__package__}.{name}"), "__file__", None)
                if not path:
                    return None
                digest.update(name.encode("ascii") + b"\0" + hashlib.sha256(Path(path).read_bytes()).digest())
            _source_digest = digest.hexdigest()
        from .opencode_cli import DEFAULT_MODEL  # the model every generation uses; the factory is not called (tests replace it)
    except Exception:
        return None
    return _canonical_hash({
        "version": FINGERPRINT_VERSION, "sources": _source_digest, "opencode_model": DEFAULT_MODEL,
        "allow_remote_code": bool(getattr(settings, "allow_remote_code", False)),
        "allow_pickle_weights": bool(getattr(settings, "allow_pickle_weights", False)),
    })


def generation_fingerprint(request: str, generator: str, model_id: str, revision: Optional[str]) -> str:
    """The same request text, generator and pinned model always give the same value."""
    return _canonical_hash({"version": FINGERPRINT_VERSION, "generator": generator, "model_id": model_id, "revision": revision,
                            "request_sha256": sha256_bytes(request.encode("utf-8"))})


def fingerprints_for(analysis: Any, settings: Any = None) -> Tuple[Optional[str], Optional[str]]:
    """(generation fingerprint, generator fingerprint) for an analysis. Never raises: (None, ...) means caching is not possible
    for this run and generation simply proceeds."""
    generator = generator_fingerprint(settings)
    if generator is None:
        return None, None
    try:
        from .gradio_gen import build_native_scaffold, build_request
        ref = analysis.model_ref
        # an independent GitHub model has no OpenCode request: its deterministic scaffold text (which carries all recorded evidence) stands in
        text = build_native_scaffold(analysis) if getattr(analysis, "origin", "") == "github-native" else build_request(analysis)
        return generation_fingerprint(text, generator, ref.repo_id, analysis.revision), generator
    except Exception:
        return None, generator


# --------------------------------------------------------------------------- manifest


def build_manifest(files: Mapping[str, str], *, slug: str, source_repo_id: str, source_kind: str, model_id: str,
                   revision: Optional[str], task: Optional[str], fingerprint: Optional[str],
                   generator: Optional[str], readiness: str = "ready", readiness_reason: str = "") -> str:
    """Manifest text for the files about to be written (hashes are of the exact on-disk bytes)."""
    artifacts = {}
    for name in sorted(files):
        if name == MANIFEST_NAME:
            continue
        data = disk_bytes(name, files[name])
        artifacts[name] = {"sha256": sha256_bytes(data), "size": len(data)}
    doc = {
        "schema": MANIFEST_SCHEMA,
        "slug": slug,
        "model": {"source_repo_id": source_repo_id, "source_kind": source_kind, "model_id": model_id, "revision": revision, "task": task},
        "fingerprint": fingerprint,
        "generator_fingerprint": generator,
        "readiness": {"state": readiness, "reason": redact_secrets(readiness_reason)[:500]},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "artifacts": artifacts,
    }
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass(frozen=True)
class ManifestCheck:
    """state: ``absent`` (legacy package, no manifest), ``ok`` (every enforced artifact matches) or ``invalid``."""
    state: str
    problems: Tuple[str, ...] = ()
    manifest: Optional[Dict[str, Any]] = None

    @property
    def usable(self) -> bool:
        return self.state != "invalid"

    def message(self) -> str:
        return redact_secrets("The generated package does not match its manifest: " + "; ".join(self.problems)
                              + ". It was probably edited or only partly regenerated; generate the app again.")


def verify_package(app_dir: Any, *, expect_model_id: Optional[str] = None, expect_revision: Optional[str] = None,
                   required: Sequence[str] = ENFORCED_ARTIFACTS) -> ManifestCheck:
    """Compare a package folder with its manifest. Reads files only; never raises for a bad package (it returns ``invalid``)."""
    folder = Path(app_dir)
    path = folder / MANIFEST_NAME
    if path.is_symlink():
        return ManifestCheck("invalid", ("the manifest must not be a link",))
    if not path.is_file():
        return ManifestCheck("absent")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ManifestCheck("invalid", ("the manifest is unreadable",))
    if not isinstance(doc, dict) or doc.get("schema") != MANIFEST_SCHEMA or not isinstance(doc.get("artifacts"), dict):
        return ManifestCheck("invalid", ("the manifest has an unknown format",))
    problems: List[str] = []
    for name in required:
        entry = doc["artifacts"].get(name)
        target = folder / name
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            problems.append(f"{name} is not listed in the manifest")
        elif target.is_symlink() or not target.is_file():
            problems.append(f"{name} is missing")
        else:
            try:
                if sha256_file(target) != entry["sha256"]:
                    problems.append(f"{name} changed after it was generated")
            except OSError:
                problems.append(f"{name} could not be read")
    model = doc.get("model") if isinstance(doc.get("model"), dict) else {}
    if expect_model_id and isinstance(model.get("model_id"), str) and model["model_id"] != expect_model_id:
        problems.append(f"the manifest records model {model['model_id']} but the code loads {expect_model_id}")
    if expect_revision and isinstance(model.get("revision"), str) and model["revision"] != expect_revision:
        problems.append(f"the manifest records revision {model['revision'][:12]} but the code pins {expect_revision[:12]}")
    return ManifestCheck("invalid" if problems else "ok", tuple(problems), doc)


def cached_package_sources(app_dir: Any, fingerprint: Optional[str], model_id: str,
                           revision: Optional[str]) -> Tuple[Optional[Tuple[str, str]], str]:
    """((loader.py text, app.py text), note) of the existing package when it can stand in for a new OpenCode generation, else
    (None, note). It can when its manifest verifies (nothing changed since it was generated) and its fingerprint equals ``fingerprint``.
    ``note`` is non-empty only when a package existed but failed its integrity check (it is then regenerated)."""
    if not fingerprint:
        return None, ""
    folder = Path(app_dir)
    if not folder.is_dir():
        return None, ""
    check = verify_package(folder, expect_model_id=model_id, expect_revision=revision)
    if check.state == "invalid":
        return None, "The existing package failed its integrity check (" + "; ".join(check.problems)[:300] + ") and is being regenerated."
    if check.state != "ok" or (check.manifest or {}).get("fingerprint") != fingerprint:
        return None, ""
    try:
        return ((folder / "loader.py").read_text(encoding="utf-8"), (folder / "app.py").read_text(encoding="utf-8")), ""
    except (OSError, UnicodeDecodeError):
        return None, ""


def package_is_current(app_dir: Any, generator: Optional[str], required: Sequence[str]) -> bool:
    """True when the folder holds every file of ``required``, its manifest verifies and it was made by this same generator. Used by the
    generation job to skip REA, evidence and OpenCode entirely for a package that nothing has changed."""
    if not generator:
        return False
    folder = Path(app_dir)
    if not all((folder / name).is_file() and not (folder / name).is_symlink() for name in required):
        return False
    check = verify_package(folder)
    return check.state == "ok" and (check.manifest or {}).get("generator_fingerprint") == generator
