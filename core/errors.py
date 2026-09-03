"""Shared exception hierarchy for the PDF editor core.

Every module raises exceptions from this hierarchy rather than defining
its own ad-hoc exception classes. This lets the UI layer build a single,
consistent error-display system instead of handling N different
exception types from N different modules. See SPEC.md section 6.3.
"""

from __future__ import annotations


class PDFEditorError(Exception):
    """Base class for all application-specific errors."""


class CorruptDocumentError(PDFEditorError):
    """Raised when a PDF cannot be parsed or is structurally invalid."""


class OperationError(PDFEditorError):
    """Raised when an Operation fails to apply, invert, or undo/redo."""


class ConversionError(PDFEditorError):
    """Raised when an external conversion tool (LibreOffice, Ghostscript,
    Tesseract, etc.) fails, times out, or is unavailable on this machine.
    """


class SecurityError(PDFEditorError):
    """Raised for encryption/decryption failures, permission violations,
    network-lockdown violations, or secure-delete failures.
    """


class PluginLoadError(PDFEditorError):
    """Raised when a plugin fails to load or register correctly."""


class PluginCompatibilityError(PDFEditorError):
    """Raised when a plugin's declared compatible_core_version range does
    not match the running core version.
    """


class SchemaVersionError(PDFEditorError):
    """Raised when deserializing an Operation, Workflow, or plugin
    manifest whose schema_version is unsupported by the running core.
    """


class OperationCancelledError(PDFEditorError):
    """Raised when the user cancels a running operation.

    A genuinely new category rather than an `OperationError`: nothing
    went wrong, so callers that report failures to the user should stay
    quiet for this one. Phase 6d (docs/GUI_PLAN.md §3.5) raises it from
    an operation's progress callback, which is what makes cancellation
    cooperative - the operation unwinds through its normal error path
    and the half-written output is simply never adopted, since every
    operation writes to a *new* working file and the session only swaps
    to it on success.
    """
