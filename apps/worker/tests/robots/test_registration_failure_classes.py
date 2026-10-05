"""The platform classifies a failed REGISTER_ROBOT_RUN Job by its recorded
exception class *name* (ADR-008 §5.2, ``sceneops_core.robots
.registration_failures``). The classification lives in core as strings; this
test is what ties those strings to the real exception classes, so a renamed or
newly added registration error cannot silently become "transient"."""

from __future__ import annotations

import inspect

import pytest

from sceneops_core.robots import manifest as manifest_module
from sceneops_core.robots.registration_failures import (
    PERMANENT_REGISTRATION_ERROR_TYPES,
    RegistrationFailureClass,
    classify_registration_failure,
)
from sceneops_worker.robots import registration as registration_module


def _exception_classes(module, base):
    return {
        cls.__name__
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, base) and cls.__module__ == module.__name__
    }


def test_every_registration_error_class_is_permanent():
    """None of the registration errors is retryable: each says the manifest,
    the recording or canonical state must change first. (The abstract base is
    never raised itself; only its subclasses are.)"""
    base = registration_module.RobotRunRegistrationError
    worker_errors = _exception_classes(registration_module, base) - {base.__name__}

    assert worker_errors  # the base class and its subclasses were found
    assert worker_errors <= PERMANENT_REGISTRATION_ERROR_TYPES


def test_every_manifest_error_class_is_permanent():
    manifest_errors = _exception_classes(
        manifest_module, manifest_module.RobotRunManifestError
    )

    assert {
        "RobotRunManifestError",
        "UnsupportedRobotRunManifestVersionError",
        "NonCanonicalRobotRunManifestError",
    } <= manifest_errors
    assert manifest_errors <= PERMANENT_REGISTRATION_ERROR_TYPES


def test_every_permanent_name_resolves_to_a_real_exception_class():
    resolvable = (
        _exception_classes(registration_module, Exception)
        | _exception_classes(manifest_module, Exception)
        | {"ValidationError"}  # pydantic's, raised by Job parameter validation
    )

    assert PERMANENT_REGISTRATION_ERROR_TYPES <= resolvable


def test_pydantic_validation_error_is_named_as_classified():
    from pydantic import ValidationError

    assert ValidationError.__name__ in PERMANENT_REGISTRATION_ERROR_TYPES


@pytest.mark.parametrize(
    "error_type",
    [
        "OSError",
        "ConnectionError",
        "ArtifactReadError",
        "TimeoutError",
        "Whatever",
        None,
    ],
)
def test_everything_else_is_transient(error_type):
    assert (
        classify_registration_failure(error_type) == RegistrationFailureClass.TRANSIENT
    )
