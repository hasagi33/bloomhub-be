from __future__ import annotations


class AIError(Exception):
    """Base error for the AI subsystem."""


class TransientAIError(AIError):
    """Transient failures that may succeed on retry (timeout, rate limit, 5xx)."""


class PermanentAIError(AIError):
    """Permanent failures that should be surfaced rather than retried."""


class LLMUnavailableError(PermanentAIError):
    """LLM client cannot be constructed (missing key, missing dep)."""


class LLMInvocationError(TransientAIError):
    """LLM call raised an exception during invoke."""


class LLMParseError(PermanentAIError):
    """LLM produced output that could not be parsed into the expected shape."""


class OrchestratorRoutingError(PermanentAIError):
    """Orchestrator selected an unknown or invalid module."""


class ToolArgumentError(PermanentAIError):
    """Tool arguments failed schema validation."""


class SensitiveActionDenied(PermanentAIError):
    """Sensitive tool invoked without the required ceremony (recent auth, etc.)."""


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, TransientAIError)
