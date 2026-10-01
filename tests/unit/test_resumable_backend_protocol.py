"""Resumable backends extend the core protocol without changing it."""

from __future__ import annotations

from pixelgym.backends.base import Backend, ResumableBackend
from pixelgym.backends.fake import FakeBackend
from pixelgym.grounding.v5.backend import V5FakeBackend

RESUME_METHODS = {"checkpoint", "restore", "environment_resume_record", "verify_resume_record"}


def test_core_backend_protocol_does_not_require_resume_methods() -> None:
    assert not RESUME_METHODS & set(dir(Backend))
    assert RESUME_METHODS <= set(dir(ResumableBackend))


def test_fake_backends_satisfy_resumable_protocol() -> None:
    for backend in (FakeBackend(), V5FakeBackend()):
        try:
            assert isinstance(backend, Backend)
            assert isinstance(backend, ResumableBackend)
        finally:
            backend.close()


def test_osworld_backend_class_declares_resume_methods() -> None:
    # Instantiating OSWorldBackend needs a provider; check the class surface only.
    from pixelgym.backends.osworld import OSWorldBackend

    assert RESUME_METHODS <= set(dir(OSWorldBackend))
