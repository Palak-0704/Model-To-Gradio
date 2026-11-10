"""Hardware gate for the Windows ``.bat`` deployment route (probe the host, compare it with what the analysis says, decide).

``local_runner._start_verified`` calls ``check_local_hardware`` after the generated app has been verified and BEFORE the launcher
is written or started. The requirements come from the ``hardware`` block of the app's ``analysis.json`` (the analyzer's
``HardwareAssessment``, already hash-verified through the package manifest); the host facts come from small, bounded probes:

* NVIDIA GPU name / total / free VRAM  - ``nvidia-smi`` (never raises; a missing binary means no NVIDIA driver)
* system RAM total / available / commit limit - Win32 ``GlobalMemoryStatusEx`` through ctypes (``os.sysconf`` elsewhere)
* free disk space of the folders that will really be written: the app folder (``.venv``), pip's cache and the Hugging Face cache

Policy (see ``evaluate``): only a failure that cannot be avoided blocks the launch. Everything uncertain is a warning: estimates, a
probe that gave no answer, a model size that is not recorded, a GPU that is merely missing. The loader falls back to the CPU, so a
missing GPU alone never blocks. Probes are lazy: nothing is spawned or read that the analysis does not make relevant.

Nothing here installs, downloads, starts or modifies anything, and nothing here raises into the launch path.
"""
from __future__ import annotations

import ctypes
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

BLOCK, WARN = "block", "warn"

# The analyzer computes its sizes with 1e9 bytes per "GB"; the host probes report GiB (2**30 bytes). Needs are converted once, here.
GB_TO_GIB = 1e9 / 2 ** 30

# Thresholds. The disk figures are rough, UNMEASURED estimates (they only ever produce warnings); DISK_BLOCK_FLOOR_GIB is the one
# disk value that blocks, and it is deliberately far below any real install (torch + transformers + gradio cannot fit in it).
DISK_BLOCK_FLOOR_GIB = 1.0
ENV_INSTALL_ESTIMATE_GIB = 4.0  # new .venv: torch + transformers + gradio and friends
PIP_CACHE_ESTIMATE_GIB = 2.0  # wheels pip keeps in its cache while installing a new .venv
WEIGHTS_DISK_MARGIN = 1.1  # weights are downloaded lazily into the Hugging Face cache
RAM_MARGIN = 1.25  # headroom over the 16-bit weight size before a CPU load is called tight
NVIDIA_SMI_TIMEOUT_S = 5.0  # the API server answers one request at a time, so a probe must never stall it for long
# ``analyzer._estimate_params`` labels: only parameter counts that come from the Hub's own safetensors metadata are firm enough to
# block on. Counts guessed from file sizes or from config.json are estimates and can only warn.
RELIABLE_PARAMS_SOURCES = frozenset({"Hub safetensors metadata"})

APP_LABEL, PIP_LABEL, MODEL_LABEL = "app folder", "pip cache", "model cache"
_SANE_MAX = 1e7  # GiB; larger numbers in analysis.json are treated as garbage and ignored


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class GpuInfo:
    name: str
    total_gib: float
    free_gib: Optional[float] = None


@dataclass(frozen=True)
class MemoryInfo:
    total_gib: float
    available_gib: float
    commit_limit_gib: Optional[float] = None  # physical RAM + page file (Windows); None when the platform does not report it


@dataclass(frozen=True)
class HardwareNeeds:
    """What ``analysis.json`` says about the model. Every size is in GiB, ``None`` where the analysis does not record it."""
    params: Optional[int] = None
    params_source: str = "unknown"
    weights_gib: Optional[float] = None  # 16-bit weights
    vram_fp16_gib: Optional[float] = None  # weights x 1.2 + 1 GB overhead (analyzer estimate)
    vram_4bit_gib: Optional[float] = None
    needs_gpu: bool = False
    runs_locally_cpu: Optional[bool] = None  # only meaningful when the model size is known; otherwise None
    suggest_4bit: bool = False
    quantization: Optional[str] = None
    has_data: bool = False

    @property
    def params_reliable(self) -> bool:
        return self.params is not None and self.params_source in RELIABLE_PARAMS_SOURCES

    @property
    def prequantized_bnb(self) -> bool:
        return self.quantization == "bitsandbytes"

    @property
    def quantized(self) -> bool:
        return self.quantization is not None

    @property
    def gpu_relevant(self) -> bool:
        """Does the analysis make GPU facts relevant at all? (Otherwise nvidia-smi is never started.)"""
        return self.needs_gpu or self.prequantized_bnb or self.runs_locally_cpu is False


@dataclass(frozen=True)
class DiskLocation:
    label: str
    path: str
    key: str  # which drive/volume it is on: locations with the same key share one pool of free space
    free_gib: Optional[float]
    need_gib: float  # rough estimate of what this launch writes there (0: only probed for the summary)


@dataclass(frozen=True)
class HostFacts:
    memory: Optional[MemoryInfo] = None
    gpus: Optional[Tuple[GpuInfo, ...]] = None  # None: not conclusive; (): no NVIDIA GPU. Only meaningful when gpus_checked.
    gpus_checked: bool = False
    disks: Tuple[DiskLocation, ...] = ()
    fresh_env: bool = True  # the app's .venv does not exist yet, so this launch has to create and fill it


@dataclass(frozen=True)
class Finding:
    level: str  # BLOCK | WARN
    code: str
    message: str  # one full sentence (or two), safe to show to the person


@dataclass(frozen=True)
class HardwareReport:
    findings: Tuple[Finding, ...] = ()
    summary: str = ""  # one line of the facts that were really probed, for ``LocalSnapshot.hardware``

    @property
    def blocked(self) -> bool:
        return any(f.level == BLOCK for f in self.findings)

    @property
    def blocking(self) -> List[Finding]:
        return [f for f in self.findings if f.level == BLOCK]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == WARN]

    def block_message(self) -> str:
        return ("The local launch was refused before anything was installed or started. "
                + " ".join(f.message for f in self.blocking))

    def log_lines(self) -> List[str]:
        lines = ["Hardware check: " + self.summary] if self.summary else []
        for f in self.findings:
            lines.append(("Hardware check BLOCKED: " if f.level == BLOCK else "Hardware check WARNING: ") + f.message)
        return lines


# --------------------------------------------------------------------------- requirements from analysis.json


def _number(value: Any, limit: float = _SANE_MAX) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= limit else None


def needs_from_analysis(analysis: Any) -> HardwareNeeds:
    """The model's hardware needs from an ``analysis.json`` dict (``hardware`` block plus the top-level ``quantization``). Missing,
    malformed or absurd values are ignored, never guessed: an analysis without a ``hardware`` block yields ``has_data=False``."""
    data = analysis if isinstance(analysis, Mapping) else {}
    hw = data.get("hardware")
    hw = hw if isinstance(hw, Mapping) else {}
    params = hw.get("params")
    params = params if isinstance(params, int) and not isinstance(params, bool) and 0 < params < 10 ** 15 else None

    def gib(key: str) -> Optional[float]:
        value = _number(hw.get(key))
        return None if value is None else value * GB_TO_GIB

    quantization = data.get("quantization")
    quantization = quantization.strip().lower() if isinstance(quantization, str) and quantization.strip() else None
    runs_cpu = hw.get("runs_locally_cpu")
    source = hw.get("params_source")
    return HardwareNeeds(
        params=params,
        params_source=source if isinstance(source, str) and source else "unknown",
        weights_gib=gib("weights_gb_fp16"),
        vram_fp16_gib=gib("est_vram_gb_fp16"),
        vram_4bit_gib=gib("est_vram_gb_4bit"),
        needs_gpu=hw.get("needs_gpu") is True,
        # HardwareAssessment defaults runs_locally_cpu to False when the size is unknown, so it only counts with a known size
        runs_locally_cpu=runs_cpu if isinstance(runs_cpu, bool) and params is not None else None,
        suggest_4bit=hw.get("suggest_4bit") is True,
        quantization=quantization,
        has_data=bool(hw),
    )


# --------------------------------------------------------------------------- host probes (never raise)


def probe_disk_free_gib(path: Any) -> Optional[float]:
    """Free GiB on the volume that holds ``path`` (or its nearest existing parent); None when it cannot be read."""
    try:
        current = Path(path)
        while not current.exists():
            if current.parent == current:
                return None
            current = current.parent
        return shutil.disk_usage(str(current)).free / 2 ** 30
    except (OSError, ValueError):
        return None


def _find_nvidia_smi() -> Optional[str]:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    root = os.environ.get("SystemRoot", r"C:\Windows")
    for candidate in (os.path.join(root, "System32", "nvidia-smi.exe"),
                      r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if os.path.isfile(candidate):
            return candidate
    return None


def _mib_to_gib(text: str) -> Optional[float]:
    try:
        value = float(text.strip())
    except ValueError:
        return None
    return value / 1024 if math.isfinite(value) and value >= 0 else None


def probe_nvidia_gpus(timeout: float = NVIDIA_SMI_TIMEOUT_S) -> Optional[Tuple[GpuInfo, ...]]:
    """NVIDIA GPUs through ``nvidia-smi``. ``()``: no NVIDIA driver/GPU (binary absent or "No devices were found"). ``None``: the
    tool exists but gave no usable answer (timeout, driver communication failure, unexpected output), which is not evidence of
    anything."""
    exe = _find_nvidia_smi()
    if exe is None:
        return ()
    try:
        result = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    stdout, stderr = result.stdout or "", result.stderr or ""
    if result.returncode != 0:
        return () if "no devices were found" in (stdout + stderr).lower() else None
    gpus: List[GpuInfo] = []
    for line in stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        total = _mib_to_gib(parts[-2])
        if total is None:
            continue
        gpus.append(GpuInfo(",".join(parts[:-2]) or "NVIDIA GPU", total, _mib_to_gib(parts[-1])))
    if not gpus and stdout.strip():
        return None  # it printed something we cannot read
    return tuple(gpus)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def probe_memory() -> Optional[MemoryInfo]:
    """Installed and available RAM (and, on Windows, the commit limit = RAM + page file). None when the platform gives no answer."""
    gib = 2 ** 30
    try:
        if os.name == "nt":
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return None
            return MemoryInfo(status.ullTotalPhys / gib, status.ullAvailPhys / gib, status.ullTotalPageFile / gib)
        page = os.sysconf("SC_PAGE_SIZE")
        return MemoryInfo(page * os.sysconf("SC_PHYS_PAGES") / gib, page * os.sysconf("SC_AVPHYS_PAGES") / gib, None)
    except (OSError, ValueError, AttributeError):
        return None


def _home() -> Optional[Path]:
    try:
        return Path.home()
    except (RuntimeError, OSError):
        return None


def huggingface_cache_dir(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """Where huggingface_hub will put downloaded weights (same precedence it uses); None when nothing can be derived."""
    env = os.environ if environ is None else environ
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"]).expanduser()
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]).expanduser() / "hub"
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]).expanduser() / "huggingface" / "hub"
    home = _home()
    return home / ".cache" / "huggingface" / "hub" if home else None


def pip_cache_dir(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """pip's cache folder (the launcher does not move it); None when nothing can be derived."""
    env = os.environ if environ is None else environ
    if env.get("PIP_CACHE_DIR"):
        return Path(env["PIP_CACHE_DIR"]).expanduser()
    if os.name == "nt":
        base = env.get("LOCALAPPDATA")
        return Path(base) / "pip" / "Cache" if base else None
    home = _home()
    return home / ".cache" / "pip" if home else None


def _drive_key(path: Path) -> str:
    try:
        return os.path.splitdrive(os.path.abspath(str(path)))[0].lower() or os.sep
    except (OSError, ValueError):
        return str(path)


# --------------------------------------------------------------------------- the decision


def _gib(value: float) -> str:
    return f"{value:.1f} GiB"


def _estimate_note(needs: HardwareNeeds) -> str:
    return "" if needs.params_reliable else f" (an estimate: parameter count {needs.params_source})"


def _memory_block(needs: HardwareNeeds, host: HostFacts) -> Optional[Finding]:
    """The one RAM-based block: the weights, even at 16-bit, exceed everything this PC could page in (RAM + page file) AND no NVIDIA
    GPU that was conclusively probed could hold them (4-bit counted where the analysis suggests it). Requires a parameter count that
    comes from the Hub's own safetensors metadata and a model that is not already quantized."""
    memory = host.memory
    if (memory is None or memory.commit_limit_gib is None or needs.weights_gib is None or not needs.params_reliable
            or needs.quantized or not host.gpus_checked or host.gpus is None):
        return None
    gpu_floor = needs.weights_gib * (0.25 if needs.suggest_4bit else 1.0)
    if any(g.total_gib >= gpu_floor for g in host.gpus) or needs.weights_gib <= memory.commit_limit_gib:
        return None
    gpu_text = ("no NVIDIA GPU was detected" if not host.gpus
                else f"the largest NVIDIA GPU has only {_gib(max(g.total_gib for g in host.gpus))} of VRAM")
    return Finding(BLOCK, "weights_exceed_memory",
                   f"The model's weights alone are about {_gib(needs.weights_gib)} even at 16-bit ({needs.params:,} parameters, from the "
                   f"Hugging Face safetensors metadata), but this PC has only {_gib(memory.commit_limit_gib)} of RAM plus page file "
                   f"and {gpu_text}. Loading it would fail with an out-of-memory error.")


def _gpu_findings(needs: HardwareNeeds, host: HostFacts) -> List[Finding]:
    if not host.gpus_checked:
        return []
    gpus = host.gpus
    if gpus is None:
        return [Finding(WARN, "gpu_unknown", "Could not tell whether an NVIDIA GPU is available (nvidia-smi gave no usable answer), "
                                             "so GPU checks were skipped.")]
    if not gpus:
        if needs.prequantized_bnb:
            return [Finding(WARN, "prequantized_needs_gpu",
                            "This model is pre-quantized with bitsandbytes, which needs an NVIDIA GPU, and none was detected. The app "
                            "will probably fail to load the model.")]
        why = ("is flagged as needing a GPU" if needs.needs_gpu else "is not recommended for CPU-only machines")
        return [Finding(WARN, "gpu_not_detected",
                        f"The analysis says this model {why}, but no NVIDIA GPU was detected. The app will run on the CPU, which can be "
                        "very slow or run out of memory.")]
    if needs.vram_fp16_gib is None:
        return []
    need = needs.vram_fp16_gib
    best = max(gpus, key=lambda g: g.total_gib)
    if best.total_gib < need:
        text = (f"The largest NVIDIA GPU ({best.name}, {_gib(best.total_gib)} VRAM) is below the roughly {_gib(need)} this model is "
                f"estimated to need at 16-bit{_estimate_note(needs)}.")
        if needs.vram_4bit_gib is not None:
            fits = " which would fit" if best.total_gib >= needs.vram_4bit_gib else " which would also not fit"
            text += f" A 4-bit load is estimated at about {_gib(needs.vram_4bit_gib)},{fits}."
        return [Finding(WARN, "vram_low", text)]
    fitting = [g for g in gpus if g.total_gib >= need]
    if fitting and all(g.free_gib is not None and g.free_gib < need for g in fitting):
        free = max(g.free_gib for g in fitting if g.free_gib is not None)
        return [Finding(WARN, "vram_free_low",
                        f"A large enough NVIDIA GPU is present, but only {_gib(free)} of its VRAM is free right now (about {_gib(need)} "
                        f"is estimated to be needed{_estimate_note(needs)}). Close other programs that use the GPU.")]
    return []


def _ram_findings(needs: HardwareNeeds, host: HostFacts, blocked: bool) -> List[Finding]:
    if needs.weights_gib is None or blocked:
        return []
    memory = host.memory
    if memory is None:
        return [Finding(WARN, "ram_unknown", f"Could not read the installed RAM, so it was not checked against the model's estimated "
                                             f"{_gib(needs.weights_gib)} of weights.")]
    holds = (host.gpus_checked and bool(host.gpus) and needs.vram_fp16_gib is not None
             and any(g.total_gib >= needs.vram_fp16_gib for g in host.gpus or ()))
    need = needs.weights_gib * RAM_MARGIN
    if holds or memory.available_gib >= need:
        return []
    return [Finding(WARN, "ram_low",
                    f"Only {_gib(memory.available_gib)} of RAM is available, but loading this model on the CPU is estimated to need at "
                    f"least {_gib(need)} ({_gib(needs.weights_gib)} of 16-bit weights plus headroom{_estimate_note(needs)}); a 32-bit "
                    "CPU load can need about twice the weights. Close other programs, or the app may run out of memory.")]


def _disk_findings(host: HostFacts) -> Tuple[List[Finding], List[Finding]]:
    """(blocks, warnings) for the folders this launch writes to, with locations on one volume pooled."""
    blocks: List[Finding] = []
    warns: List[Finding] = []
    groups: Dict[str, List[DiskLocation]] = {}
    for disk in host.disks:
        groups.setdefault(disk.key, []).append(disk)
    for locations in groups.values():
        app = next((d for d in locations if d.label == APP_LABEL), None)
        if app is not None and host.fresh_env and app.free_gib is not None and app.free_gib < DISK_BLOCK_FLOOR_GIB:
            blocks.append(Finding(
                BLOCK, "disk_full",
                f"Only {_gib(app.free_gib)} is free on the drive that holds the app folder ({app.path}). Creating the app's virtual "
                "environment and installing torch, transformers and gradio needs several GiB, so the install cannot succeed. Free up "
                "space on that drive and start again."))
            continue
        need = sum(d.need_gib for d in locations)
        if need <= 0:
            continue
        free = next((d.free_gib for d in locations if d.free_gib is not None), None)
        names = ", ".join(f"{d.label} ({d.path})" for d in locations if d.need_gib > 0)
        if free is None:
            warns.append(Finding(WARN, "disk_unknown", f"Could not read the free disk space for {names}, so it was not checked."))
        elif free < need:
            warns.append(Finding(
                WARN, "disk_low",
                f"{_gib(free)} is free where {names} live, but this launch is estimated to need about {_gib(need)} there (install and "
                "download sizes are rough estimates). Free up space if the install or the model download fails."))
    return blocks, warns


def _summary(host: HostFacts) -> str:
    parts: List[str] = []
    if host.gpus_checked:
        if host.gpus is None:
            parts.append("GPU: unknown")
        elif not host.gpus:
            parts.append("GPU: no NVIDIA GPU detected")
        else:
            shown = ", ".join(f"{g.name} ({_gib(g.total_gib)} VRAM)" for g in host.gpus[:2])
            parts.append("GPU: " + shown + (f" +{len(host.gpus) - 2} more" if len(host.gpus) > 2 else ""))
    if host.memory is not None:
        parts.append(f"RAM: {_gib(host.memory.total_gib)} total, {_gib(host.memory.available_gib)} available")
    app = next((d for d in host.disks if d.label == APP_LABEL and d.free_gib is not None), None)
    if app is not None:
        parts.append(f"free disk: {_gib(app.free_gib)} (app drive)")
    return "; ".join(parts)


def evaluate(needs: HardwareNeeds, host: HostFacts) -> HardwareReport:
    """Compare the model's needs with the host. Pure: no probing happens here.

    BLOCK (clear and unavoidable only):
      * ``disk_full``             a new .venv must be created and the app drive has less than DISK_BLOCK_FLOOR_GIB free
      * ``weights_exceed_memory`` firm parameter count, not quantized, 16-bit weights exceed RAM + page file, and no conclusively
                                  probed NVIDIA GPU can hold them
    WARN (everything else): missing/unknown GPU, low VRAM, low RAM, low disk against rough estimates, a probe that gave no answer,
    a model size the analysis does not record.
    """
    findings: List[Finding] = []
    block = _memory_block(needs, host)
    disk_blocks, disk_warns = _disk_findings(host)
    findings += disk_blocks + ([block] if block else [])
    if needs.weights_gib is None:
        findings.append(Finding(WARN, "model_size_unknown",
                                "The analysis does not record this model's size, so RAM and GPU memory could not be checked against it."))
    findings += _gpu_findings(needs, host) + _ram_findings(needs, host, block is not None) + disk_warns
    findings.sort(key=lambda f: f.level != BLOCK)  # stable: blocks first
    return HardwareReport(tuple(findings), _summary(host))


# --------------------------------------------------------------------------- entry point


def _disk_locations(app_dir: Path, needs: HardwareNeeds, fresh_env: bool,
                    environ: Optional[Mapping[str, str]]) -> List[Tuple[str, Path, float]]:
    """(label, folder, estimated GiB written there) for every folder this launch writes to. Folders that need nothing are left out,
    except the app folder, which is always probed (the summary shows its free space)."""
    out: List[Tuple[str, Path, float]] = [(APP_LABEL, app_dir, ENV_INSTALL_ESTIMATE_GIB if fresh_env else 0.0)]
    pip_dir = pip_cache_dir(environ)
    if fresh_env and pip_dir is not None:
        out.append((PIP_LABEL, pip_dir, PIP_CACHE_ESTIMATE_GIB))
    model_dir = huggingface_cache_dir(environ)
    if needs.weights_gib is not None and model_dir is not None:
        out.append((MODEL_LABEL, model_dir, needs.weights_gib * WEIGHTS_DISK_MARGIN))
    return out


def check_local_hardware(app_dir: Any, analysis: Any = None, *, environ: Optional[Mapping[str, str]] = None,
                         gpu_probe: Optional[Callable[[], Optional[Tuple[GpuInfo, ...]]]] = None,
                         memory_probe: Optional[Callable[[], Optional[MemoryInfo]]] = None,
                         disk_probe: Optional[Callable[[Path], Optional[float]]] = None) -> HardwareReport:
    """Probe this PC and compare it with the model's needs from ``analysis`` (the app's ``analysis.json`` as a dict).

    Probes are lazy: RAM is read only when the analysis records the model's size, and ``nvidia-smi`` only when the analysis makes a
    GPU relevant (or the RAM figures make a block possible). The probe arguments exist for tests. Never raises: an unexpected
    failure becomes one warning, so the check can never be the reason a launch fails.
    """
    try:
        app = Path(app_dir)
        gpus_fn, memory_fn, disk_fn = gpu_probe or probe_nvidia_gpus, memory_probe or probe_memory, disk_probe or probe_disk_free_gib
        needs = needs_from_analysis(analysis)
        fresh_env = not (app / ".venv" / "Scripts" / "python.exe").is_file()
        memory = memory_fn() if needs.weights_gib is not None else None
        may_block = (memory is not None and memory.commit_limit_gib is not None and needs.weights_gib is not None
                     and needs.params_reliable and not needs.quantized and needs.weights_gib > memory.commit_limit_gib)
        want_gpu = needs.gpu_relevant or may_block
        disks = tuple(DiskLocation(label, str(path), _drive_key(path), disk_fn(path), need)
                      for label, path, need in _disk_locations(app, needs, fresh_env, environ))
        host = HostFacts(memory=memory, gpus=gpus_fn() if want_gpu else None, gpus_checked=want_gpu, disks=disks,
                         fresh_env=fresh_env)
        return evaluate(needs, host)
    except Exception as exc:  # a diagnostic must never take the launch path down with it
        return HardwareReport((Finding(WARN, "check_failed", f"The hardware check could not be completed ({type(exc).__name__}); "
                                                             "the launch was not blocked."),), "")
