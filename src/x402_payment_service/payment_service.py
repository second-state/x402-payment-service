"""Payment service for handling x402 payment verification and settlement."""

import json
import logging
from decimal import Decimal
from enum import Enum
from typing import Optional, Union

from x402.common import find_matching_payment_requirements, x402_VERSION
from x402.encoding import safe_base64_decode
from x402.facilitator import FacilitatorConfig
from x402.paywall import get_paywall_html, is_browser_request
from x402.types import (PaymentPayload, PaymentRequirements, PaywallConfig,
                        x402PaymentRequiredResponse)

from .facilitator_ext import (NATIVE_TOKEN_ADDRESS, EIP2612PaymentPayload,
                              FacilitatorClientExt, NativePaymentPayload)
from .paywall_adapter import get_paywall_adapter_script

logger = logging.getLogger(__name__)

# ERC-3009 (TransferWithAuthorization) token registry
# Each token maps network -> {address, decimals, name (EIP-712 domain name), version (EIP-712 domain version)}
EIP3009_TOKENS = {
    "usdc": {
        "base": {
            "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "decimals": 6,
            "name": "USDC",
            "version": "2",
        },
        "base-sepolia": {
            "address": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            "decimals": 6,
            "name": "USDC",
            "version": "2",
        },
    },
    "kii": {
        "base": {
            "address": "0x0c59d37a843d2632AE93BA2eb4253e426CAC038C",
            "decimals": 6,
            "name": "KII",
            "version": "1",
        },
        "base-sepolia": {
            "address": "0xb3f5d498D8Ef4E91d2c95AfDF711b66Cee6A49f3",
            "decimals": 6,
            "name": "KII",
            "version": "1",
        },
    },
}


class PaymentScheme(str, Enum):
    """Supported payment schemes for x402 payments."""

    ERC3009 = "erc3009"  # USDC TransferWithAuthorization
    EIP2612 = "eip2612"  # ERC-20 Permit
    NATIVE = "native"    # Native token (ETH, AVAX, etc.)


PaymentPayloadUnion = Union[PaymentPayload,
                            EIP2612PaymentPayload, NativePaymentPayload]


class PaymentService:
    """Service class for handling x402 payment operations.

    Supports both ERC-3009 (USDC TransferWithAuthorization) and
    EIP-2612 (Permit) payment flows.
    """

    def __init__(
        self,
        app_name: str,
        app_logo: str,
        headers: dict,
        resource_url: str,
        price: float,
        description: str,
        network: str,
        pay_to_address: str,
        facilitator_url: str,
        max_timeout_seconds: int = 60,
        token_config: Optional[dict] = None,
        native_token: bool = False,
        eip3009_token: str = "usdc",
    ):
        """Initialize PaymentService.

        Args:
            app_name: Application name for paywall display
            app_logo: Application logo URL for paywall display
            headers: Request headers dictionary
            resource_url: The resource URL being accessed
            price: Price of the product
            description: Description of the payment
            network: Network name (e.g., 'base-sepolia', 'base')
            pay_to_address: Address to receive payment
            facilitator_url: Facilitator service URL
            max_timeout_seconds: Maximum timeout for payment
            token_config: Optional custom token configuration dict with keys:
                - address: Token contract address
                - decimals: Token decimals (default 18)
                - name: Token name for EIP-712 domain
                - symbol: Token symbol for display
                - version: Token version for EIP-712 domain (default "1")
            native_token: If True, accept native token payments (ETH, AVAX, etc.)
            eip3009_token: ERC-3009 token to use for default payment path.
                Must be a key in EIP3009_TOKENS (e.g., 'usdc', 'kii').
        """
        self.app_name = app_name
        self.app_logo = app_logo
        self.headers = dict(headers)
        self.resource_url = resource_url
        self.price = price
        self.description = description
        self.network = network
        self.pay_to_address = pay_to_address
        self.facilitator_url = facilitator_url
        self.token_config = token_config
        self.native_token = native_token
        self.eip3009_token = eip3009_token
        self.paywall_config = PaywallConfig(
            app_name=self.app_name,
            app_logo=self.app_logo,
        )
        self.max_timeout_seconds = max_timeout_seconds

        # Create payment requirements on initialization
        self.payment_requirements = self._create_payment_requirements()
        self.facilitator_config = FacilitatorConfig(url=self.facilitator_url)

        # Use extended client for EIP-2612 support
        self.facilitator = FacilitatorClientExt(self.facilitator_config)
        self._scheme: PaymentScheme = PaymentScheme.ERC3009

    def _find_matching_requirements_eip2612(
        self, payload: EIP2612PaymentPayload
    ) -> Optional[PaymentRequirements]:
        """Find matching payment requirements for EIP-2612 payload."""
        for req in self.payment_requirements:
            if req.pay_to.lower() == payload.transfer.to.lower():
                return req
        return None

    def _find_matching_requirements_native(
        self, payload: NativePaymentPayload
    ) -> Optional[PaymentRequirements]:
        """Find matching payment requirements for native token payload."""
        for req in self.payment_requirements:
            if req.pay_to.lower() == payload.to.lower():
                return req
        return None

    def _inject_paywall_adapter(self, html: str) -> str:
        """Inject paywall adapter script for custom token support."""
        token_config_json = None

        if self.token_config:
            if self.native_token:
                symbol = self.token_config.get("symbol", "ETH")
                decimals = self.token_config.get("decimals", 18)
                name = self.token_config.get("name", symbol)
                token_config_json = json.dumps({
                    "symbol": symbol,
                    "decimals": decimals,
                    "name": name,
                    "native": True
                })
            elif self.token_config.get("address"):
                symbol = self.token_config.get("symbol", "TOKEN")
                address = self.token_config["address"]
                decimals = self.token_config.get("decimals", 18)
                name = self.token_config.get("name", symbol)
                version = self.token_config.get("version", "1")
                token_config_json = json.dumps({
                    "symbol": symbol,
                    "address": address,
                    "decimals": decimals,
                    "name": name,
                    "version": version
                })
        elif self.eip3009_token.lower() != "usdc":
            # Non-USDC ERC-3009 token: inject adapter to replace
            # "USDC" text and amounts in the upstream paywall UI
            token_key = self.eip3009_token.lower()
            network_tokens = EIP3009_TOKENS.get(token_key, {})
            token_info = network_tokens.get(self.network, {})
            if token_info:
                token_config_json = json.dumps({
                    "symbol": token_info["name"],
                    "address": token_info["address"],
                    "decimals": token_info["decimals"],
                    "name": token_info["name"],
                    "version": token_info["version"],
                })

        if not token_config_json:
            return html

        adapter_script = get_paywall_adapter_script()
        display_amount = f"{self.price:.10f}".rstrip('0').rstrip('.')
        head_injection = f'''
<script>
window.__x402_token = {token_config_json};
window.__x402_display_amount = {display_amount};
</script>
'''
        body_injection = f'''
<script>
{adapter_script}
</script>
'''
        html = html.replace("</head>", f"{head_injection}</head>")
        if "</body>" in html:
            html = html.replace("</body>", f"{body_injection}</body>")
        else:
            html += body_injection
        return html

    def _create_payment_requirements(self) -> list[PaymentRequirements]:
        """Create payment requirements for the payment.

        Returns:
            List of PaymentRequirements
        """
        if self.native_token:
            decimals = 18
            if self.token_config and self.token_config.get("decimals"):
                decimals = self.token_config["decimals"]
            atomic_amount = int(Decimal(str(self.price))
                                * Decimal(10**decimals))
            return [
                PaymentRequirements(
                    scheme="native",
                    network=self.network,
                    asset=NATIVE_TOKEN_ADDRESS,
                    max_amount_required=str(atomic_amount),
                    resource=self.resource_url,
                    description=self.description,
                    mime_type="text/html",
                    pay_to=self.pay_to_address,
                    max_timeout_seconds=self.max_timeout_seconds,
                    extra={},
                    output_schema={},
                )
            ]

        if self.token_config and self.token_config.get("address"):
            decimals = self.token_config.get("decimals", 18)
            atomic_amount = int(Decimal(str(self.price))
                                * Decimal(10**decimals))
            eip712_domain = {}
            if self.token_config.get("name"):
                eip712_domain["name"] = self.token_config["name"]
                eip712_domain["version"] = self.token_config.get(
                    "version", "1")
            return [
                PaymentRequirements(
                    scheme="exact",
                    network=self.network,
                    asset=self.token_config["address"],
                    max_amount_required=str(atomic_amount),
                    resource=self.resource_url,
                    description=self.description,
                    mime_type="text/html",
                    pay_to=self.pay_to_address,
                    max_timeout_seconds=self.max_timeout_seconds,
                    extra=eip712_domain,
                    output_schema={},
                )
            ]

        token_key = self.eip3009_token.lower()
        if token_key not in EIP3009_TOKENS:
            raise ValueError(
                f"Unsupported ERC-3009 token: '{self.eip3009_token}'. "
                f"Supported tokens: {list(EIP3009_TOKENS.keys())}"
            )
        network_tokens = EIP3009_TOKENS[token_key]
        if self.network not in network_tokens:
            raise ValueError(
                f"Token '{self.eip3009_token}' is not available on network '{self.network}'. "
                f"Available networks: {list(network_tokens.keys())}"
            )
        token_info = network_tokens[self.network]

        decimals = token_info["decimals"]
        atomic_amount = int(Decimal(str(self.price)) * Decimal(10**decimals))
        asset_address = token_info["address"]
        eip712_domain = {
            "name": token_info["name"],
            "version": token_info["version"],
        }

        return [
            PaymentRequirements(
                scheme="exact",
                network=self.network,
                asset=asset_address,
                max_amount_required=str(atomic_amount),
                resource=self.resource_url,
                description=self.description,
                mime_type="text/html",
                pay_to=self.pay_to_address,
                max_timeout_seconds=self.max_timeout_seconds,
                extra=eip712_domain,
                output_schema={},
            )
        ]

    def response(self, error: str) -> tuple:
        """Create a 402 response with payment requirements.

        Args:
            error: Error message to include in response

        Returns:
            Tuple of (response_content, status_code)
        """
        if is_browser_request(self.headers):
            html_content = get_paywall_html(
                error, self.payment_requirements, self.paywall_config
            )
            html_content = self._inject_paywall_adapter(html_content)
            return html_content, 400
        else:
            response_data = x402PaymentRequiredResponse(
                x402_version=x402_VERSION,
                accepts=self.payment_requirements,
                error=error,
            ).model_dump(by_alias=True)
            return response_data, 402

    def parse(self) -> tuple[bool, Optional[PaymentPayloadUnion], Optional[PaymentRequirements], Optional[str]]:
        """Parse and validate payment header.

        Returns:
            Tuple of (success, payment_payload, selected_requirements, error_message)
            If successful, error_message is None
        """
        payment_header = self.headers.get(
            "X-Payment", "") or self.headers.get("X-PAYMENT", "")

        if not payment_header:
            return False, None, None, "No X-PAYMENT header provided"

        logger.info(f"Received X-PAYMENT header: {payment_header}")

        try:
            payment_dict = json.loads(safe_base64_decode(payment_header))

            # Check if this is a native token payload
            if FacilitatorClientExt.is_native_payload(payment_dict):
                logger.info("Detected native token payload")
                native_payload = FacilitatorClientExt.parse_native_payload(
                    payment_dict)
                if not native_payload:
                    logger.error("Failed to parse native token payload")
                    return False, None, None, "Invalid native token payment payload"

                self._scheme = PaymentScheme.NATIVE
                selected_payment_requirements = self._find_matching_requirements_native(
                    native_payload)
                if not selected_payment_requirements:
                    logger.error(
                        "No matching payment requirements found for native token")
                    return False, native_payload, None, "No matching payment requirements found"

                logger.info(
                    f"Parsed native token payload: tx_hash={native_payload.tx_hash}, from={native_payload.from_}")
                return True, native_payload, selected_payment_requirements, None

            # Check if this is an EIP-2612 payload
            elif FacilitatorClientExt.is_eip2612_payload(payment_dict):
                logger.info("Detected EIP-2612 payload")
                eip2612_payload = FacilitatorClientExt.parse_eip2612_payload(
                    payment_dict)
                if not eip2612_payload:
                    logger.error("Failed to parse EIP-2612 payload")
                    return False, None, None, "Invalid EIP-2612 payment payload"

                self._scheme = PaymentScheme.EIP2612
                selected_payment_requirements = self._find_matching_requirements_eip2612(
                    eip2612_payload)
                if not selected_payment_requirements:
                    logger.error(
                        "No matching payment requirements found for EIP-2612")
                    return False, eip2612_payload, None, "No matching payment requirements found"

                logger.info(
                    f"Parsed EIP-2612 payload: permit owner={eip2612_payload.permit.owner}")
                return True, eip2612_payload, selected_payment_requirements, None
            else:
                # Standard ERC-3009 payload
                payment = PaymentPayload(**payment_dict)
                logger.info(f"Decoded payment payload: {payment}")

                selected_payment_requirements = find_matching_payment_requirements(
                    self.payment_requirements, payment
                )
                if not selected_payment_requirements:
                    logger.error("No matching payment requirements found")
                    return False, payment, None, "No matching payment requirements found"

                self._scheme = PaymentScheme.ERC3009
                logger.info(
                    f"Selected payment requirements: {selected_payment_requirements}")
                return True, payment, selected_payment_requirements, None

        except Exception as e:
            logger.error(f"Failed to decode payment header: {e}")
            return False, None, None, f"Invalid payment header format: {e}"

    async def verify(
        self,
        payment: PaymentPayloadUnion,
        requirements: PaymentRequirements,
        order_id: str
    ) -> tuple[bool, Optional[str]]:
        """Verify a payment using the facilitator.

        Args:
            payment: Payment payload to verify
            requirements: Payment requirements to verify against
            order_id: Order ID for logging

        Returns:
            Tuple of (is_valid, error_message)
            If valid, error_message is None
        """
        try:
            if self._scheme == PaymentScheme.NATIVE:
                verify_response = await self.facilitator.verify_native(
                    payment, requirements  # type: ignore
                )
            elif self._scheme == PaymentScheme.EIP2612:
                verify_response = await self.facilitator.verify_eip2612(
                    payment, requirements  # type: ignore
                )
            else:
                verify_response = await self.facilitator.verify(
                    payment, requirements  # type: ignore
                )
        except Exception as e:
            logger.error(f"Payment verification failed ({order_id}): {e}")
            return False, f"Payment verification failed: {e}"

        if not verify_response.is_valid:
            error_reason = verify_response.invalid_reason or "Unknown error"
            logger.error(
                f"Payment verification failed ({order_id}): {error_reason}")
            return False, f"Payment verification failed: {error_reason}"

        logger.info(
            f"Payment verified successfully ({order_id}): {verify_response}")
        return True, None

    async def settle(
        self,
        payment: PaymentPayloadUnion,
        requirements: PaymentRequirements,
        order_id: str
    ) -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """Settle a payment using the facilitator.

        Args:
            payment: Payment payload to settle
            requirements: Payment requirements
            order_id: Order ID for logging

        Returns:
            Tuple of (success, tx_hash, network, error_message)
            If successful, error_message is None
        """
        try:
            if self._scheme == PaymentScheme.NATIVE:
                settle_response = await self.facilitator.settle_native(
                    payment, requirements  # type: ignore
                )
            elif self._scheme == PaymentScheme.EIP2612:
                settle_response = await self.facilitator.settle_eip2612(
                    payment, requirements  # type: ignore
                )
            else:
                settle_response = await self.facilitator.settle(
                    payment, requirements  # type: ignore
                )

            logger.info(f"Settle response ({order_id}): {settle_response}")

            if not settle_response.success:
                error_reason = settle_response.error_reason or "Unknown error"
                logger.error(
                    f"Payment settlement not success ({order_id}): {error_reason}")
                return False, None, None, f"Payment settlement not success: {error_reason}"

            logger.info(f"Payment settled successfully ({order_id})")
            return True, settle_response.transaction, settle_response.network, None

        except Exception as e:
            logger.error(f"Payment settlement failed ({order_id}): {e}")
            return False, None, None, f"Payment settlement failed: {e}"

    @staticmethod
    def generate_transaction_link(tx_hash: Optional[str], network: str) -> str:
        """Generate blockchain explorer link for a transaction.

        Args:
            tx_hash: Transaction hash
            network: Network name

        Returns:
            Transaction explorer URL or empty string if no hash
        """
        if not tx_hash:
            return ''

        explorers = {
            "base-sepolia": f"https://sepolia.basescan.org/tx/{tx_hash}",
            "base": f"https://basescan.org/tx/{tx_hash}",
        }

        return explorers.get(network, '')
