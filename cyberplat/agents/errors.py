"""Normalized agent execution errors (taxonomy)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class AgentErrorCode(str, Enum):
    VALIDATION_ERROR = "validation_error"
    RUNNER_NOT_FOUND = "runner_not_found"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class AgentExecutionError:
    code: AgentErrorCode
    message: str
    details: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "error_code": self.code.value,
            "message": self.message,
        }
        if self.details is not None:
            d["details"] = self.details
        return d


def validation_error(message: str, details: Optional[dict[str, Any]] = None) -> AgentExecutionError:
    return AgentExecutionError(code=AgentErrorCode.VALIDATION_ERROR, message=message, details=details)


def runner_not_found(agent_code: str) -> AgentExecutionError:
    return AgentExecutionError(
        code=AgentErrorCode.RUNNER_NOT_FOUND,
        message=f"runner_not_found: {agent_code}",
        details={"agent_code": agent_code},
    )


def execution_error(message: str, details: Optional[dict[str, Any]] = None) -> AgentExecutionError:
    return AgentExecutionError(code=AgentErrorCode.EXECUTION_ERROR, message=message, details=details)

