"""Broker integration module — modular design for multi-broker support."""

from .base import BrokerService, BrokerAuthStatus, ContractInfo, OrderRequest, OrderResult

__all__ = [
    "BrokerService",
    "BrokerAuthStatus",
    "ContractInfo",
    "OrderRequest",
    "OrderResult",
]
