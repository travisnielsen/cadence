"""
Application Insights observability configuration for the Data Agent API.

This module configures Azure Monitor/Application Insights for monitoring agent
performance, tracing requests, and collecting metrics.

Uses centralized ``Settings`` for all configuration. Relevant settings:

- ``enable_instrumentation``: Gate flag (default: False)
- ``applicationinsights_connection_string``: Azure Monitor connection string
- ``enable_sensitive_data``: Include prompts/responses in traces (default: False)
"""

import logging

from config.settings import get_settings

logger = logging.getLogger(__name__)


def is_observability_enabled() -> bool:
    """Check if OpenTelemetry observability is enabled."""
    return get_settings().enable_instrumentation


def configure_observability() -> None:
    """Configure Application Insights observability if enabled.

    Reads all values from ``Settings`` rather than ``os.getenv()``.
    Requires ``applicationinsights_connection_string`` to be set.
    """
    settings = get_settings()

    if not settings.enable_instrumentation:
        logger.info("Observability disabled (enable_instrumentation is false)")
        return

    try:
        connection_string = settings.applicationinsights_connection_string

        if connection_string:
            _configure_azure_monitor(
                connection_string, enable_sensitive=settings.enable_sensitive_data
            )
        else:
            logger.warning(
                "enable_instrumentation=true but applicationinsights_connection_string not set. "
                "Observability will not be configured."
            )

    except ImportError as e:
        logger.warning("Azure Monitor packages not available: %s", e)
    except (RuntimeError, ValueError, OSError):
        logger.exception("Failed to configure Azure Monitor")


def _configure_azure_monitor(connection_string: str, *, enable_sensitive: bool) -> None:
    """Configure Azure Monitor for production telemetry."""
    try:
        from agent_framework.observability import (  # noqa: PLC0415
            create_resource,
            enable_instrumentation,
        )
        from azure.monitor.opentelemetry import (  # type: ignore[import-not-found]  # noqa: PLC0415
            configure_azure_monitor,
        )

        # Configure Azure Monitor with instrumentation options
        # Enable azure_sdk to trace Azure AI Foundry/Inference calls
        configure_azure_monitor(
            connection_string=connection_string,
            resource=create_resource(),
            enable_live_metrics=True,
            instrumentation_options={
                "azure_sdk": {"enabled": True},  # Trace Azure SDK calls (AI Foundry)
                "fastapi": {"enabled": True},  # Trace FastAPI requests
                "requests": {"enabled": True},  # Trace HTTP requests
                "urllib3": {"enabled": True},  # Trace urllib3 requests
            },
        )

        # Enable Agent Framework instrumentation for workflow/executor tracing
        # Note: This may cause "Failed to detach context" warnings in SSE streaming
        # scenarios, but the spans are still captured and exported correctly.
        enable_instrumentation(enable_sensitive_data=enable_sensitive)

        # Suppress the noisy context detach error logs (they're harmless warnings)
        logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)

        logger.info(
            "OpenTelemetry configured with Azure Monitor (sensitive_data=%s)", enable_sensitive
        )

    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry not installed. "
            "Install with: pip install 'data-agent-api[observability]'"
        )
