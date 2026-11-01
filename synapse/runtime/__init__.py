"""Lazy public facade; implementation modules load when an exported name is used."""

from __future__ import annotations

from importlib import import_module as _import_module

_EXPORTS = {'DATA_EGRESS_POLICIES': ('synapse.runtime.contracts', 'DATA_EGRESS_POLICIES'),
 'EXECUTION_SCOPES': ('synapse.runtime.contracts', 'EXECUTION_SCOPES'),
 'NETWORK_SCOPES': ('synapse.runtime.contracts', 'NETWORK_SCOPES'),
 'PROVIDER_PROGRESS_STATES': ('synapse.runtime.contracts', 'PROVIDER_PROGRESS_STATES'),
 'PreparedRequest': ('synapse.runtime.contracts', 'PreparedRequest'),
 'RUNTIME_PROGRESS_STATES': ('synapse.runtime.contracts', 'RUNTIME_PROGRESS_STATES'),
 'RuntimeAdapter': ('synapse.runtime.contracts', 'RuntimeAdapter'),
 'RuntimeAdapterError': ('synapse.runtime.contracts', 'RuntimeAdapterError'),
 'RuntimeErrorKind': ('synapse.runtime.contracts', 'RuntimeErrorKind'),
 'RuntimeObservation': ('synapse.runtime.contracts', 'RuntimeObservation'),
 'RuntimeProgressEvent': ('synapse.runtime.contracts', 'RuntimeProgressEvent'),
 'RuntimeProgressEvidence': ('synapse.runtime.contracts', 'RuntimeProgressEvidence'),
 'RuntimeRequest': ('synapse.runtime.contracts', 'RuntimeRequest'),
 'RuntimeResponse': ('synapse.runtime.contracts', 'RuntimeResponse'),
 'is_runtime_adapter': ('synapse.runtime.contracts', 'is_runtime_adapter')}

__all__ = ['DATA_EGRESS_POLICIES',
 'EXECUTION_SCOPES',
 'NETWORK_SCOPES',
 'PROVIDER_PROGRESS_STATES',
 'RUNTIME_PROGRESS_STATES',
 'PreparedRequest',
 'RuntimeAdapter',
 'RuntimeAdapterError',
 'RuntimeErrorKind',
 'RuntimeObservation',
 'RuntimeProgressEvent',
 'RuntimeProgressEvidence',
 'RuntimeRequest',
 'RuntimeResponse',
 'is_runtime_adapter']


def __getattr__(name: str):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(_import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
