"""
Circuit Breaker for LLM Model Routing

Prevents cascading failures when an LLM provider hits rate limits.
Automatically routes requests to healthy fallback models and tests
recovery after a configurable timeout.
"""

import asyncio
import time
import logging
from enum import Enum, auto
from typing import Dict, List, Optional, Callable, Any

import dspy

from core.config import (
    DEFAULT_MODEL, FALLBACK_MODELS, MAX_TOKENS, TEMPERATURE,
    CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT, CB_HALF_OPEN_SUCCESSES, CB_ENABLED
)
from core.exceptions import LLMError

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ENUMS & EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = auto()     # Normal — requests flow through
    OPEN = auto()       # Tripped — block all requests immediately
    HALF_OPEN = auto()  # Testing — allow one probe request


class AllModelsUnavailableError(LLMError):
    """
    Raised when ALL model circuit breakers are OPEN and no recovery is possible yet.
    The calling code should return {} (empty result) when catching this.
    """
    def __init__(self, model_states: Dict[str, str]):
        self.model_states = model_states
        state_str = ", ".join(f"{m}={s}" for m, s in model_states.items())
        super().__init__(f"All LLM models unavailable. States: {state_str}")


# ─────────────────────────────────────────────────────────────────────────────
# RATE LIMIT DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _is_rate_limit_error(exc: Exception) -> bool:
    """
    Detect HTTP 429 / rate limit errors from any LLM provider.

    Returns True ONLY for rate limit errors.
    Returns False for auth errors, context overflow, parse errors, etc.
    """
    # Check 1: LiteLLM's specific rate limit error class
    try:
        import litellm
        if isinstance(exc, litellm.RateLimitError):
            return True
    except ImportError:
        pass

    # Check 2: Any HTTP exception with 429 status
    if getattr(exc, "status_code", None) == 429:
        return True

    # Check 3: String fallback (catches edge cases)
    msg = str(exc).lower()
    if any(phrase in msg for phrase in ["rate limit", "429", "quota exceeded", "too many requests"]):
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# PER-MODEL CIRCUIT BREAKER
# ─────────────────────────────────────────────────────────────────────────────

class ModelCircuitBreaker:
    """
    A circuit breaker for a single LLM model.

    Only responds to rate limit errors (HTTP 429).
    Other errors pass through without affecting breaker state.

    Thread-safe: uses asyncio.Lock for all state changes.
    """

    def __init__(
        self,
        model_name: str,
        failure_threshold: int,
        recovery_timeout: float,
        half_open_successes: int,
    ):
        self.model_name = model_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_successes = half_open_successes

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._half_open_success_count: int = 0
        self._opened_at: Optional[float] = None

        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def is_available(self) -> bool:
        """Quick check: can this model accept a request right now?

        NOTE: This is a best-effort snapshot. The caller must still acquire
        _lock before acting on the result. Safe for ranking/sorting only.
        """
        # Snapshot state and opened_at atomically enough for a quick check.
        # Python's GIL makes individual attribute reads atomic, but we read
        # both under no lock — acceptable for a heuristic used only by
        # _get_ordered_candidates (which re-checks under lock before acting).
        state = self._state
        opened_at = self._opened_at
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return True
        if state == CircuitState.OPEN:
            if opened_at is not None:
                elapsed = time.monotonic() - opened_at
                return elapsed >= self.recovery_timeout
        return False

    def snapshot(self):
        """Return a consistent (state, opened_at) tuple for lock-free ranking."""
        return self._state, self._opened_at

    async def try_transition_to_half_open(self) -> bool:
        """
        Attempt OPEN → HALF_OPEN transition if recovery_timeout has elapsed.
        Must be called under self._lock.
        """
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - (self._opened_at or 0)
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_success_count = 0
                logger.info(
                    f"[CircuitBreaker] {self.model_name}: "
                    f"OPEN → HALF_OPEN after {elapsed:.1f}s"
                )
                return True
            return False
        return self._state == CircuitState.HALF_OPEN

    async def record_success(self) -> None:
        """Call this after a successful LLM call."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_success_count += 1
                logger.debug(
                    f"[CircuitBreaker] {self.model_name}: "
                    f"probe success {self._half_open_success_count}/{self.half_open_successes}"
                )
                if self._half_open_success_count >= self.half_open_successes:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._opened_at = None
                    logger.info(
                        f"[CircuitBreaker] {self.model_name}: "
                        f"HALF_OPEN → CLOSED (fully recovered)"
                    )
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def record_rate_limit_failure(self) -> None:
        """
        Call this when a rate limit (429) error occurs.
        DO NOT call for other error types.
        """
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                self._failure_count += 1
                logger.warning(
                    f"[CircuitBreaker] {self.model_name}: "
                    f"rate limit {self._failure_count}/{self.failure_threshold}"
                )
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()
                    logger.error(
                        f"[CircuitBreaker] {self.model_name}: "
                        f"CLOSED → OPEN (tripped! will retry after {self.recovery_timeout}s)"
                    )

            elif self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_success_count = 0
                logger.warning(
                    f"[CircuitBreaker] {self.model_name}: "
                    f"HALF_OPEN → OPEN (probe failed, resetting timer)"
                )

            elif self._state == CircuitState.OPEN:
                logger.debug(
                    f"[CircuitBreaker] {self.model_name}: already OPEN, ignoring failure"
                )

    def get_status(self) -> Dict[str, Any]:
        """Return current CB status dict for logging/monitoring."""
        elapsed_since_open = None
        time_until_recovery = None
        if self._opened_at is not None:
            elapsed_since_open = time.monotonic() - self._opened_at
            time_until_recovery = max(0, self.recovery_timeout - elapsed_since_open)

        return {
            "model": self.model_name,
            "state": self._state.name,
            "failure_count": self._failure_count,
            "elapsed_since_open_seconds": elapsed_since_open,
            "time_until_recovery_seconds": time_until_recovery,
        }


# ─────────────────────────────────────────────────────────────────────────────
# MODEL ROUTER
# ─────────────────────────────────────────────────────────────────────────────

class ModelRouter:
    """
    Process-level singleton that routes LLM calls to the best available model
    using per-model circuit breakers.

    Uses dspy.context(lm=...) instead of dspy.configure(lm=...) to avoid
    race conditions when multiple coroutines run concurrently.
    """

    _instance: Optional["ModelRouter"] = None

    def __init__(
        self,
        primary_model: str,
        fallback_models: List[str],
        failure_threshold: int,
        recovery_timeout: float,
        half_open_successes: int,
        max_tokens: int,
        temperature: float,
        enabled: bool = True,
    ):
        self.all_models: List[str] = [primary_model] + fallback_models
        self.enabled = enabled
        self.max_tokens = max_tokens
        self.temperature = temperature

        self._breakers: Dict[str, ModelCircuitBreaker] = {
            model: ModelCircuitBreaker(
                model_name=model,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                half_open_successes=half_open_successes,
            )
            for model in self.all_models
        }

        # Pre-created DSPy LM instances (one per model).
        # num_retries=0: LiteLLM will NOT internally retry on 429.
        # Without this, each 429 wastes ~30 seconds before our CB sees the error.
        self._lm_cache: Dict[str, dspy.LM] = {
            model: dspy.LM(
                model,
                max_tokens=max_tokens,
                temperature=temperature,
                num_retries=0,
            )
            for model in self.all_models
        }

    @classmethod
    def get_instance(cls) -> "ModelRouter":
        """
        Get the process-level singleton ModelRouter.
        Creates it on first call (lazy initialization).
        """
        if cls._instance is None:
            from core.config import (
                DEFAULT_MODEL, FALLBACK_MODELS, MAX_TOKENS, TEMPERATURE,
                CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT,
                CB_HALF_OPEN_SUCCESSES, CB_ENABLED
            )
            cls._instance = cls(
                primary_model=DEFAULT_MODEL,
                fallback_models=FALLBACK_MODELS,
                failure_threshold=CB_FAILURE_THRESHOLD,
                recovery_timeout=CB_RECOVERY_TIMEOUT,
                half_open_successes=CB_HALF_OPEN_SUCCESSES,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                enabled=CB_ENABLED,
            )
            logger.info(
                f"[ModelRouter] Initialized with models: {cls._instance.all_models}"
            )
        return cls._instance

    def _get_ordered_candidates(self) -> List[str]:
        """
        Return models in priority order:
        1. CLOSED models (fully healthy)
        2. HALF_OPEN models (recovering, allow probe)
        3. OPEN models whose recovery timeout has elapsed (eligible for HALF_OPEN probe)
        4. Skip: OPEN models whose recovery timeout hasn't passed yet

        Snapshots each breaker's state to avoid inconsistent reads across
        breakers (each breaker's state+opened_at is read together).
        """
        closed = []
        half_open = []
        eligible_open = []

        for model in self.all_models:
            cb = self._breakers[model]
            state, opened_at = cb.snapshot()

            if state == CircuitState.CLOSED:
                closed.append(model)
            elif state == CircuitState.HALF_OPEN:
                half_open.append(model)
            elif state == CircuitState.OPEN:
                if opened_at is not None:
                    elapsed = time.monotonic() - opened_at
                    if elapsed >= cb.recovery_timeout:
                        eligible_open.append(model)

        return closed + half_open + eligible_open

    async def run_with_routing(
        self,
        async_callable: Callable,
        operation_name: str = "DSPy call",
        **callable_kwargs: Any,
    ) -> Any:
        """
        Execute async_callable with circuit-breaker-based model routing.

        Uses dspy.context(lm=...) — no global state mutation, safe for concurrent use.

        Args:
            async_callable: The async function to call
            operation_name: Used in log messages for debugging
            **callable_kwargs: Passed directly to async_callable

        Returns:
            Whatever async_callable returns

        Raises:
            AllModelsUnavailableError: If all models are rate-limited with no recovery
            Any other exception: Re-raised immediately from async_callable
        """
        if not self.enabled:
            return await async_callable(**callable_kwargs)

        candidates = self._get_ordered_candidates()

        if not candidates:
            all_states = {m: cb.state.name for m, cb in self._breakers.items()}
            logger.error(
                f"[ModelRouter] {operation_name}: No available models! "
                f"States: {all_states}"
            )
            raise AllModelsUnavailableError(model_states=all_states)

        last_rate_limit_error: Optional[Exception] = None

        for model in candidates:
            cb = self._breakers[model]

            # If OPEN and timeout elapsed, transition to HALF_OPEN
            async with cb._lock:
                if cb.state == CircuitState.OPEN:
                    transitioned = await cb.try_transition_to_half_open()
                    if not transitioned:
                        continue

            lm = self._lm_cache[model]
            is_primary = (model == self.all_models[0])

            if not is_primary:
                logger.warning(
                    f"[ModelRouter] {operation_name}: "
                    f"routing to fallback model={model} "
                    f"(primary={self.all_models[0]} state={self._breakers[self.all_models[0]].state.name})"
                )
            else:
                logger.debug(
                    f"[ModelRouter] {operation_name}: using primary model={model}"
                )

            try:
                # KEY FIX: dspy.context() uses Python's contextvars.ContextVar.
                # Each async coroutine gets its OWN model setting — no race conditions.
                # When run_in_executor copies context to thread, the override propagates.
                with dspy.context(lm=lm):
                    result = await async_callable(**callable_kwargs)

                await cb.record_success()
                return result

            except Exception as e:
                if _is_rate_limit_error(e):
                    last_rate_limit_error = e
                    await cb.record_rate_limit_failure()
                    logger.warning(
                        f"[ModelRouter] {operation_name}: "
                        f"rate limit on {model}, "
                        f"CB now={cb.state.name}, trying next model..."
                    )
                    continue
                else:
                    # Not a rate limit — fail fast, don't waste time on fallbacks
                    logger.error(
                        f"[ModelRouter] {operation_name}: "
                        f"non-rate-limit error on {model}: {type(e).__name__}: {e}"
                    )
                    raise

        all_states = {m: cb.state.name for m, cb in self._breakers.items()}
        logger.error(
            f"[ModelRouter] {operation_name}: "
            f"ALL models exhausted with rate limits. States: {all_states}"
        )
        raise AllModelsUnavailableError(model_states=all_states)

    def is_any_breaker_half_open(self) -> bool:
        """Return True if any circuit breaker is in HALF_OPEN state.

        Used by concurrency control to reduce backpressure during recovery.
        """
        for cb in self._breakers.values():
            state, _ = cb.snapshot()
            if state == CircuitState.HALF_OPEN:
                return True
        return False

    def get_all_statuses(self) -> List[Dict[str, Any]]:
        """Return status of all circuit breakers. Useful for health check endpoints."""
        return [cb.get_status() for cb in self._breakers.values()]

    def reset_all_for_testing(self) -> None:
        """Reset all circuit breakers to CLOSED state. Only use in tests."""
        for cb in self._breakers.values():
            cb._state = CircuitState.CLOSED
            cb._failure_count = 0
            cb._opened_at = None
            cb._half_open_success_count = 0
        logger.warning("[ModelRouter] All circuit breakers reset to CLOSED (testing mode)")


__all__ = [
    "CircuitState",
    "ModelCircuitBreaker",
    "ModelRouter",
    "AllModelsUnavailableError",
    "_is_rate_limit_error",
]
