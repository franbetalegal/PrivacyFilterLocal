"""Cooperative cancellation for work running on the inference thread pool.

The pool has a single worker, so a job nobody is waiting for any more still
holds it: a user who cancels a 230-page scan, or whose browser gave up, used to
leave the server OCRing and running the model on it for minutes while the next
file queued behind. Python cannot interrupt a thread from outside, so the long
stages check in at safe points instead.

Usage: the route creates a :class:`threading.Event` per request and passes it to
:func:`server.inference.run_blocking` / :func:`server.inference.redact`, which
bind it to the worker thread for the duration of the call. Code deep inside a
stage then calls :func:`check` without having to thread the event through every
signature. Outside a bound call, :func:`check` does nothing.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar

# The cancel event of the request being handled. Set by the route for the
# duration of the request; :func:`server.inference.run_blocking` picks it up
# from here, so a route does not have to pass it to each of its many calls.
request_event: ContextVar[threading.Event | None] = ContextVar(
    "request_event", default=None
)


class Cancelled(BaseException):
    """The request this work belonged to has been abandoned.

    A ``BaseException`` on purpose: extraction and OCR are full of broad
    ``except Exception`` fallbacks ("this page failed, try the next way"), and
    each of them would otherwise swallow the cancellation and carry on.
    """


_local = threading.local()


def bind(event: threading.Event | None) -> None:
    _local.event = event


def check() -> None:
    """Raise :class:`Cancelled` if the current job has been cancelled."""
    event = getattr(_local, "event", None)
    if event is not None and event.is_set():
        raise Cancelled()
