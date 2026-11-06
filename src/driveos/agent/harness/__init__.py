"""Agent harness: OpenRouter tool loop with a payment firewall, context budgets and a persistent session.

The package never imports the wallet, authority or biometric layers; the model sees consent only as the
boolean ``checkout_open``.
"""
