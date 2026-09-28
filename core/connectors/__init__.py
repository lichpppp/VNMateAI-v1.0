"""
core/connectors/__init__.py
===========================
Enterprise Connectors Package — Phase 59 Universal Enterprise Connector Hub.

Exports all concrete adapters and the base class.
"""

from __future__ import annotations

from core.connectors.base_connector import (
    BaseConnector,
    ConnectorConfig,
    ConnectorResult,
    CONNECTOR_DEFAULTS,
    CONNECTOR_ENV_MAP,
    invalidate_config_cache,
    load_connector_settings,
)

from core.connectors.aws_connector import AWSConnector, aws_connector
from core.connectors.oci_connector import OCIConnector, oci_connector
from core.connectors.paperless_connector import PaperlessConnector, paperless_connector
from core.connectors.einvoice_connector import EInvoiceConnector, einvoice_connector

# Registry of all available connectors
CONNECTOR_REGISTRY: dict = {
    "aws": aws_connector,
    "oci": oci_connector,
    "paperless": paperless_connector,
    "einvoice": einvoice_connector,
}

# Risk level mapping for Zero-Trust integration
# Level 1-2: Auto-approved | Level 3-5: Require HITL approval
CONNECTOR_RISK_LEVELS: dict = {
    "aws:billing_summary": 1,
    "aws:instance_status": 1,
    "oci:instance_list": 1,
    "oci:cloud_metrics": 1,
    "paperless:search": 1,
    "paperless:details": 1,
    "paperless:download": 2,
    "einvoice:daily_summary": 1,
    "einvoice:search": 1,
    "einvoice:details": 1,
}

__all__ = [
    # Base
    "BaseConnector",
    "ConnectorConfig",
    "ConnectorResult",
    # Concrete
    "AWSConnector",
    "aws_connector",
    "OCIConnector",
    "oci_connector",
    "PaperlessConnector",
    "paperless_connector",
    "EInvoiceConnector",
    "einvoice_connector",
    # Registry
    "CONNECTOR_REGISTRY",
    "CONNECTOR_RISK_LEVELS",
    "CONNECTOR_ENV_MAP",
    "CONNECTOR_DEFAULTS",
    # Cấu hình runtime
    "load_connector_settings",
    "invalidate_config_cache",
]