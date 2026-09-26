"""Multi-key NovelAI gateway utilities.

This package provides a small FastAPI-based gateway layer built on top of the
SDK. It is intended for platform/operator usage rather than direct end-user SDK
consumption.

The actual FastAPI app lives in :mod:`novelai.gateway.app`. It is intentionally
not imported here so the base SDK can still be imported in environments where
FastAPI is not installed.
"""

__all__ = []