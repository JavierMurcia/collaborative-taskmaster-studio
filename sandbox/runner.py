"""Subprocess runner with timeout, process cleanup, and sanitized logs."""

from __future__ import annotations

import re
import subprocess
import sys
from time import perf_counter

from sandbox.models import ProcessResult
from sandbox.policy import SandboxPolicy

MAX_LOG_CHARS = 12_000
_SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;]+"
)


class SandboxRunner:
    def __init__(self, policy: SandboxPolicy) -> None:
        self.policy = policy

    def run_unit_tests(self) -> ProcessResult:
        command = (sys.executable, "-m", "pytest", "tests/unit", "-q", "--disable-warnings")
        self.policy.validate_pytest(command)
        started = perf_counter()
        process = subprocess.Popen(  # noqa: S603 - command is fixed and policy-validated
            command,
            cwd=self.policy.workspace,
            env=self.policy.sanitized_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=self.policy.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate(timeout=2)
        return ProcessResult(
            label="generated_unit_tests",
            exit_code=process.returncode,
            timed_out=timed_out,
            duration_ms=max(0, int((perf_counter() - started) * 1000)),
            stdout=_sanitize(stdout),
            stderr=_sanitize(stderr),
        )


def _sanitize(value: str) -> str:
    scrubbed = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return scrubbed[-MAX_LOG_CHARS:]
