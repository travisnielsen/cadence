"""
Application Insights observability configuration for the Data Agent API.

This module configures Azure Monitor/Application Insights for monitoring agent
performance, tracing requests, and collecting metrics.

Environment variables:
- ENABLE_INSTRUMENTATION: Set to "true" to enable tracing (default: false)
- APPLICATIONINSIGHTS_CONNECTION_STRING: Azure Monitor connection string (required when enabled)
- ENABLE_SENSITIVE_DATA: Set to "true" to log prompts/responses (default: false)
"""

import logging
import os

logger = logging.getLogger(__name__)


def is_observability_enabled() -> bool:
    """Check if OpenTelemetry observability is enabled."""
    return os.getenv("ENABLE_INSTRUMENTATION", "false").lower() == "true"


def configure_observability() -> None:
    """
    Configure Application Insights observability if enabled.

    Requires APPLICATIONINSIGHTS_CONNECTION_STRING to be set.
    """
    if not is_observability_enabled():
        logger.info("Observability disabled (ENABLE_INSTRUMENTATION != true)")
        return

    try:
        azure_monitor_connection = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")

        if azure_monitor_connection:
            _configure_azure_monitor(azure_monitor_connection)
        else:
            logger.warning(
                "ENABLE_INSTRUMENTATION=true but APPLICATIONINSIGHTS_CONNECTION_STRING not set. "
                "Observability will not be configured."
            )

    except ImportError as e:
        logger.warning("Azure Monitor packages not available: %s", e)
    except (RuntimeError, ValueError, OSError) as e:
        logger.error("Failed to configure Azure Monitor: %s", e)


def _configure_azure_monitor(connection_string: str) -> None:
    """Configure Azure Monitor for production telemetry."""
    try:
        from azure.monitor.opentelemetry import (  # type: ignore[import-not-found]
            configure_azure_monitor,
        )
        from agent_framework.observability import create_resource, enable_instrumentation

        enable_sensitive = os.getenv("ENABLE_SENSITIVE_DATA", "false").lower() == "true"

        # Configure Azure Monitor with instrumentation options
        # Enable azure_sdk to trace Azure AI Foundry/Inference calls
        configure_azure_monitor(
            connection_string=connection_string,
            resource=create_resource(),
            enable_live_metrics=True,
            instrumentation_options={
                "azure_sdk": {"enabled": True},  # Trace Azure SDK calls (AI Foundry)
                "fastapi": {"enabled": True},    # Trace FastAPI requests
                "requests": {"enabled": True},   # Trace HTTP requests
                "urllib3": {"enabled": True},    # Trace urllib3 requests
            },
        )
        
        # Enable Agent Framework instrumentation for workflow/executor tracing
        # Note: This may cause "Failed to detach context" warnings in SSE streaming
        # scenarios, but the spans are still captured and exported correctly.
        enable_instrumentation(enable_sensitive_data=enable_sensitive)
        
        # Suppress the noisy context detach error logs (they're harmless warnings)
        logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)
        
        logger.info(
            "OpenTelemetry configured with Azure Monitor (sensitive_data=%s)",
            enable_sensitive
        )

    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry not installed. "
            "Install with: pip install 'data-agent-api[observability]'"
        )
