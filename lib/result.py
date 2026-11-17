"""Small, privacy-safe result contract for model and retrieval boundaries."""

from dataclasses import dataclass
from typing import Any, Optional


class OperationFailed(RuntimeError):
    """Contains a stable error code, never a prompt or upstream response."""


@dataclass(frozen=True)
class Result:
    ok: bool
    content: Any = None
    error: Optional[str] = None
    retryable: bool = False
    retrieval_mode: Optional[str] = None

    def require_content(self):
        if not self.ok:
            raise OperationFailed(self.error or "operation_failed")
        return self.content


def failure(error: str, retryable: bool = False) -> Result:
    return Result(False, error=error, retryable=retryable)


def exception_result(error: Exception) -> Result:
    """Classify without retaining exception messages (may contain private data)."""
    import subprocess

    name = type(error).__name__
    if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)) or name == "APITimeoutError":
        return failure("timeout", True)
    if isinstance(error, FileNotFoundError):
        return failure("command_missing")
    if isinstance(error, subprocess.CalledProcessError):
        return failure("process_error", True)
    if name == "AuthenticationError":
        return failure("authentication_error")
    if name == "RateLimitError":
        return failure("rate_limit", True)
    if name == "NotFoundError":
        return failure("model_not_found")
    if name == "APIConnectionError":
        return failure("connection_error", True)
    if isinstance(error, ImportError):
        return failure("dependency_missing")
    return failure("provider_error")


def diagnose(stage: str, result: Result) -> None:
    import sys

    print(f"[SF] stage={stage} error={result.error} retryable={str(result.retryable).lower()}",
          file=sys.stderr)
