# x402 Payment Service

A Python package for handling x402 payment verification and settlement.

## Installation

Install directly from GitHub:

```bash
pip install git+https://github.com/second-state/x402-payment-service.git
```

Or with uv:

```bash
uv add git+https://github.com/second-state/x402-payment-service.git
```

## Usage

```python
from flask import Flask, request
from x402_payment_service import PaymentService

app = Flask(__name__)

# Configuration
NETWORK = "base-sepolia"  # or "base" for mainnet
ADDRESS = "0xYourWalletAddress"
FACILITATOR_URL = "https://x402f1.secondstate.io"

@app.route("/premium")
async def premium_content():
    # Create payment service
    payment_service = PaymentService(
        app_name="My App",
        app_logo="/static/logo.png",
        headers=request.headers,
        resource_url=request.url,
        price=0.01,  # $0.01 USD
        description="Access to premium content",
        network=NETWORK,
        pay_to_address=ADDRESS,
        facilitator_url=FACILITATOR_URL,
        max_timeout_seconds=60
    )

    # Parse and validate payment header
    success, payment, selected_requirements, parse_error = payment_service.parse()
    if not success:
        return payment_service.response(parse_error)

    # Verify payment
    is_valid, verify_error = await payment_service.verify(
        payment, selected_requirements, "premium"
    )
    if not is_valid:
        return payment_service.response(verify_error)

    # Settle payment
    success, tx_hash, tx_network, settle_error = await payment_service.settle(
        payment, selected_requirements, "premium"
    )
    if not success:
        return payment_service.response(settle_error)

    # Generate transaction link
    tx_link = PaymentService.generate_transaction_link(tx_hash, tx_network)

    # Return premium content after successful payment
    return f"<h1>Premium Content</h1><p>Transaction: {tx_link}</p>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

## API Reference

### PaymentService

#### Constructor Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `app_name` | str | Application name for paywall display |
| `app_logo` | str | Application logo URL for paywall display |
| `headers` | dict | Request headers dictionary |
| `resource_url` | str | The resource URL being accessed |
| `price` | float | Price in USD |
| `description` | str | Description of the payment |
| `network` | str | Network name (`base-sepolia` or `base`) |
| `pay_to_address` | str | Address to receive payment |
| `facilitator_url` | str | Facilitator service URL |
| `max_timeout_seconds` | int | Maximum timeout for payment (default: 60) |
| `token_config` | dict | Optional. Custom token configuration for non-USDC tokens (see below) |

#### Custom Token Configuration (EIP-2612)

For custom ERC-20 tokens instead of USDC, provide the `token_config` parameter:

```python
payment_service = PaymentService(
    # ... other parameters
    token_config={
        "address": "0xYourTokenContractAddress",
        "decimals": 18,
        "name": "MyToken",
        "version": "1",
    }
)
```

| Field | Type | Description |
|-------|------|-------------|
| `address` | str | Token contract address (required) |
| `decimals` | int | Token decimals (default: 18) |
| `name` | str | Token name for EIP-712 domain |
| `version` | str | Token version for EIP-712 (default: "1") |

#### Methods

- `parse()` - Parse and validate payment header from request
- `verify(payment, requirements, order_id)` - Verify payment using facilitator (async)
- `settle(payment, requirements, order_id)` - Settle payment on blockchain (async)
- `response(error)` - Create 402 response with payment requirements
- `generate_transaction_link(tx_hash, network)` - Generate blockchain explorer link (static)

## License

MIT
