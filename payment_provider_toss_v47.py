"""Toss Payments adapter boundary for Coin Pattern.
No live credentials or live API calls are included in this development package.
"""
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class PaymentConfig:
    provider: str = "tosspayments"
    base_url: str = "https://api.tosspayments.com"
    secret_key: str = os.environ.get("COIN_PATTERN_TOSS_SECRET_KEY", "")
    client_key: str = os.environ.get("COIN_PATTERN_TOSS_CLIENT_KEY", "")

class TossPaymentsProvider:
    def __init__(self, config=None):
        self.config = config or PaymentConfig()
    def configured(self):
        return bool(self.config.secret_key and self.config.client_key)
    def create_billing_customer(self, customer_key: str):
        raise NotImplementedError("Connect only after merchant review/contract and production security review.")
    def charge_subscription(self, billing_key: str, customer_key: str, amount: int, order_id: str):
        raise NotImplementedError("Live billing intentionally disabled in Coin Pattern v47.")
    def cancel_subscription(self, provider_subscription_id: str):
        raise NotImplementedError("Provider-specific cancellation is intentionally disabled in v47.")
