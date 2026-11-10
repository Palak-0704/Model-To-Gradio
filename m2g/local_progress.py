"""Evidence-based progress for a local deployment.

``ProgressTracker`` is a passive record owned by ``local_workflow.LocalLauncher``. It has no thread and starts no process: the
launcher reports real events (a stage marker printed by ``run_local.bat``, a finished health check, ...) and the API reads the
current picture with ``snapshot()``.

Rules that keep the numbers honest:

* The percentage is ``completed steps / total steps``. A step counts only after a real event finished (or explicitly skipped) it.
  Time passing, or output arriving inside a step (pip prints a lot), never changes it; pip has no sub-percentage.
* A step that did not need to run (a reused ``.venv``, unchanged requirements) is reported as ``skipped`` and counts as completed.
* 100% is reached only by ``complete()``, which the launcher calls after the Gradio health check succeeded; before that the
  maximum is 87%.
* Silence for ``STUCK_AFTER_S`` seconds changes only the *status* (``possibly_stuck``), never the percentage and never to
  ``failed``: a deployment is failed only when the launcher reports a failure.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

STUCK_AFTER_S = 60.0

# (id, label) in execution order. "complete" is only reached after the health check succeeded.
STEPS = (
    ("validating", "Validating generated app"),
    ("preparing", "Preparing environment"),
    ("creating_env", "Creating virtual environment"),
    ("upgrading_pip", "Upgrading pip"),
    ("installing", "Installing dependencies"),
    ("starting", "Starting Gradio"),
    ("checking_url", "Checking app URL"),
    ("complete", "Deployment complete"),
)
STEP_LABELS = dict(STEPS)
CHECK_STEP_ID = "checking_url"
FINAL_STEP_ID = "complete"  # reachable only through complete(), never through begin()/skip()
RUNTIME_STEP = {"id": "running", "label": "Running application"}  # a crash after the deployment had completed

PENDING, ACTIVE, DONE, SKIPPED, FAILED = "pending", "active", "done", "skipped", "failed"
_COUNTED = (DONE, SKIPPED)

# Overall status values: idle | running | waiting | possibly_stuck | completed | failed | stopped
WARNING_NO_PROGRESS = "No recent progress — checking whether the process is still active."
MESSAGE_STILL_RUNNING = "Still running — may be taking longer."


def _iso(epoch: Optional[float]) -> Optional[str]:
    return None if epoch is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class ProgressTracker:
    def __init__(self, clock: Callable[[], float] = time.time, stuck_after: float = STUCK_AFTER_S) -> None:
        self._clock = clock
        self._stuck_after = stuck_after
        self._lock = threading.RLock()
        self._reset()

    # ------------------------------------------------------------------ state

    def _reset(self) -> None:
        self._steps: Dict[str, Dict[str, Any]] = {
            sid: {"state": PENDING, "started_at": None, "finished_at": None, "detail": ""} for sid, _ in STEPS
        }
        self._started_at: Optional[float] = None
        self._last_activity: Optional[float] = None
        self._finished_at: Optional[float] = None
        self._active: Optional[str] = None
        self._completed = False  # the health check succeeded
        self._outcome: Optional[str] = None  # None (in progress) | "complete" | "failed" | "stopped"
        self._failed_step: Optional[Dict[str, str]] = None
        self._error = ""

    @property
    def started(self) -> bool:
        return self._started_at is not None

    # ------------------------------------------------------------------ events reported by the launcher

    def start(self) -> None:
        """A new deployment begins: forget the previous one."""
        with self._lock:
            self._reset()
            self._started_at = self._last_activity = self._clock()

    def start_if_idle(self) -> None:
        """Start only when no deployment is being tracked (or the tracked one is over)."""
        with self._lock:
            if not self.started or self._outcome in ("failed", "stopped") or self._completed:
                self.start()

    def touch(self) -> None:
        """Any sign of life (an output line, a stage change)."""
        with self._lock:
            if self.started and self._outcome is None:
                self._last_activity = self._clock()

    def _finish_active(self, now: float) -> None:
        if self._active is not None:
            step = self._steps[self._active]
            step["state"], step["finished_at"] = DONE, now
            self._active = None

    def begin(self, step_id: str) -> None:
        with self._lock:
            if not self.started or self._outcome is not None or step_id not in self._steps or step_id == FINAL_STEP_ID:
                return
            if self._active == step_id:
                self.touch()
                return
            if self._steps[step_id]["state"] != PENDING:  # already finished or skipped: markers are idempotent
                self.touch()
                return
            now = self._clock()
            self._finish_active(now)
            self._steps[step_id].update(state=ACTIVE, started_at=now)
            self._active = step_id
            self._last_activity = now

    def finish(self, step_id: Optional[str] = None) -> None:
        with self._lock:
            if not self.started or self._outcome is not None:
                return
            if step_id is not None and step_id != self._active:
                return
            now = self._clock()
            self._finish_active(now)
            self._last_activity = now

    def skip(self, step_id: str, detail: str = "", keep_active: bool = False) -> None:
        """The step did not need to run (reused environment, unchanged requirements). It counts as completed.

        ``keep_active`` leaves the step that is currently running open (a reused environment is still being verified)."""
        with self._lock:
            if not self.started or self._outcome is not None or step_id not in self._steps or step_id == FINAL_STEP_ID:
                return
            if self._steps[step_id]["state"] != PENDING:
                return
            now = self._clock()
            if not keep_active and self._active is not None and self._active != step_id:
                self._finish_active(now)  # the evidence that this step is not needed also ends the step before it
            self._steps[step_id].update(state=SKIPPED, finished_at=now, detail=detail)
            self._last_activity = now

    def complete(self) -> None:
        """The Gradio health check succeeded: the only way to reach 100%."""
        with self._lock:
            if not self.started or self._outcome is not None:
                return
            now = self._clock()
            self._finish_active(now)
            for sid, _label in STEPS:
                step = self._steps[sid]
                if step["state"] == PENDING:
                    step.update(state=DONE if sid == "complete" else SKIPPED, finished_at=now,
                                detail="" if sid == "complete" else "Not needed for this deployment")
            self._completed = True
            self._outcome = "complete"
            self._finished_at = self._last_activity = now

    def fail(self, error: str, step_id: Optional[str] = None) -> None:
        """The launcher reported a failure. Records the exact step that was running and the error."""
        with self._lock:
            if not self.started or self._outcome in ("failed", "stopped"):
                return
            now = self._clock()
            if self._completed:  # the app crashed after a successful deployment
                self._failed_step = dict(RUNTIME_STEP)
            else:
                sid = step_id or self._active or next((s for s, _ in STEPS if self._steps[s]["state"] == PENDING), "validating")
                self._steps[sid].update(state=FAILED, finished_at=now, detail=error)
                self._active = None
                self._failed_step = {"id": sid, "label": STEP_LABELS[sid]}
            self._error = error
            self._outcome = "failed"
            self._finished_at = now

    def stop(self) -> None:
        """The app ended on request (or exited normally). Progress stays as it was."""
        with self._lock:
            if not self.started or self._outcome in ("failed", "stopped"):
                return
            self._outcome = "stopped"
            self._finished_at = self._finished_at or self._clock()

    # ------------------------------------------------------------------ read side

    def completed_steps(self) -> int:
        with self._lock:
            return sum(1 for s in self._steps.values() if s["state"] in _COUNTED)

    def percent(self) -> int:
        with self._lock:
            return int(100 * self.completed_steps() / len(STEPS))

    def snapshot(self, process_alive: Optional[bool] = None) -> Dict[str, Any]:
        """JSON-safe picture. ``process_alive`` is the launcher's live check of its process (None: unknown/no process)."""
        with self._lock:
            now = self._clock()
            steps: List[Dict[str, Any]] = []
            for sid, label in STEPS:
                s = self._steps[sid]
                steps.append({"id": sid, "label": label, "state": s["state"], "detail": s["detail"],
                              "started_at": _iso(s["started_at"]), "finished_at": _iso(s["finished_at"])})
            if not self.started:
                return {"status": "idle", "percent": 0, "completed_steps": 0, "total_steps": len(STEPS),
                        "step_id": "", "step_label": "", "steps": steps,
                        "message": "Not started", "warning": "", "elapsed_seconds": 0, "seconds_since_activity": None,
                        "started_at": None, "last_activity_at": None, "finished_at": None,
                        "failed_step": None, "error": ""}
            end = self._finished_at if self._finished_at is not None else now
            elapsed = max(0.0, end - self._started_at)
            since = max(0.0, (self._finished_at if self._finished_at is not None else now) - self._last_activity)
            step_id = self._active or (self._failed_step or {}).get("id") or ("complete" if self._completed else "")
            warning = ""
            if self._outcome == "failed":
                status = "failed"
                message = f"Failed at {self._failed_step['label']}: {self._error}"
            elif self._outcome == "stopped":
                status, message = "stopped", "Stopped"
            elif self._completed:
                status, message = "completed", STEP_LABELS["complete"]
            else:
                if since >= self._stuck_after:
                    warning = WARNING_NO_PROGRESS
                    if process_alive is False:
                        status, message = "waiting", "The process has ended; waiting for its result."
                    elif process_alive:
                        status, message = "possibly_stuck", MESSAGE_STILL_RUNNING
                    else:
                        status, message = "possibly_stuck", WARNING_NO_PROGRESS
                elif self._active is None:
                    status, message = "waiting", "Waiting for the next step to start."
                else:
                    status, message = "running", STEP_LABELS[self._active]
            return {
                "status": status,
                "percent": self.percent(),
                "completed_steps": self.completed_steps(),
                "total_steps": len(STEPS),
                "step_id": step_id,
                "step_label": STEP_LABELS.get(step_id, ""),
                "steps": steps,
                "message": message,
                "warning": warning,
                "elapsed_seconds": round(elapsed, 1),
                "seconds_since_activity": round(since, 1),
                "started_at": _iso(self._started_at),
                "last_activity_at": _iso(self._last_activity),
                "finished_at": _iso(self._finished_at),
                "failed_step": dict(self._failed_step) if self._failed_step else None,
                "error": self._error,
            }
