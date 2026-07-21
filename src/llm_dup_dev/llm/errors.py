"""Exceptions forming part of the LLM abstraction boundary.

The dev repo imports no provider SDK, so it cannot catch backend-specific
error types directly.  Concrete clients in the secure repo translate
their provider's errors into the types defined here, which is what lets
dev-side code distinguish "this run is misconfigured" from "this one
report didn't parse".
"""

from __future__ import annotations


class LLMConfigurationError(Exception):
    """The request is invalid for this backend and retrying is pointless.

    Raised by concrete clients for non-retryable failures that are a
    property of the configuration rather than of the individual report:
    an unsupported parameter or value, an unknown deployment, or an
    authentication failure.

    Pipeline code deliberately does **not** swallow this.  Per-report
    tolerance exists so that one malformed narrative cannot abort a
    batch; applying it to a misconfiguration instead produces a run that
    completes "successfully" with every extraction empty, which is
    indistinguishable downstream from legitimate absence of data.

    Retryable or transient failures — rate limits, timeouts, 5xx — must
    *not* use this type.  Let them raise whatever the provider raises so
    the existing per-report handling still applies.
    """
