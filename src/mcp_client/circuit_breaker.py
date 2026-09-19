import time
from enum import Enum

from src.core.logging import get_logger

logger = get_logger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None

    def allow_request(self) -> bool:
        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._opened_at
            if elapsed >= self.cooldown_seconds:
                logger.info("circuit_half_open")
                self._state = CircuitState.HALF_OPEN
                return True
            return False

        return True

    def record_success(self):
        if self._state != CircuitState.CLOSED:
            logger.info("circuit_closed", previous_state=self._state.value)
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.failure_threshold and self._state != CircuitState.OPEN:
            logger.warning("circuit_opened", failures=self._failures)
            self._state = CircuitState.OPEN
            self._opened_at = time.time()