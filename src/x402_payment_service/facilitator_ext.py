from typing import Any, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from x402.facilitator import FacilitatorClient
from x402.types import PaymentRequirements, SettleResponse, VerifyResponse


class EIP2612Permit(BaseModel):
    owner: str
    spender: str
    value: str
    nonce: int
    deadline: str
    signature: str

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class EIP2612Transfer(BaseModel):
    from_: str = Field(alias="from")
    to: str
    amount: str

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class EIP2612PaymentPayload(BaseModel):
    permit: EIP2612Permit
    transfer: EIP2612Transfer

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class FacilitatorClientExt(FacilitatorClient):
    async def verify_eip2612(
        self,
        payload: EIP2612PaymentPayload,
        payment_requirements: PaymentRequirements,
        x402_version: int = 1,
    ) -> VerifyResponse:
        headers = {"Content-Type": "application/json"}

        if self.config.get("create_headers"):
            custom_headers = await self.config["create_headers"]()
            headers.update(custom_headers.get("verify", {}))

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.config['url']}/verify",
                json={
                    "x402Version": x402_version,
                    "paymentPayload": {
                        "x402Version": x402_version,
                        "scheme": "exact",
                        "network": payment_requirements.network,
                        "payload": payload.model_dump(by_alias=True),
                    },
                    "paymentRequirements": payment_requirements.model_dump(
                        by_alias=True, exclude_none=True
                    ),
                },
                headers=headers,
                follow_redirects=True,
            )

            return VerifyResponse(**response.json())

    async def settle_eip2612(
        self,
        payload: EIP2612PaymentPayload,
        payment_requirements: PaymentRequirements,
        x402_version: int = 1,
    ) -> SettleResponse:
        headers = {"Content-Type": "application/json"}

        if self.config.get("create_headers"):
            custom_headers = await self.config["create_headers"]()
            headers.update(custom_headers.get("settle", {}))

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.config['url']}/settle",
                json={
                    "x402Version": x402_version,
                    "paymentPayload": {
                        "x402Version": x402_version,
                        "scheme": "exact",
                        "network": payment_requirements.network,
                        "payload": payload.model_dump(by_alias=True),
                    },
                    "paymentRequirements": payment_requirements.model_dump(
                        by_alias=True, exclude_none=True
                    ),
                },
                headers=headers,
                follow_redirects=True,
            )

            return SettleResponse(**response.json())

    @staticmethod
    def is_eip2612_payload(raw_payload: dict[str, Any]) -> bool:
        payload_data = raw_payload.get("payload", {})
        return "permit" in payload_data and "transfer" in payload_data

    @staticmethod
    def parse_eip2612_payload(
        raw_payload: dict[str, Any],
    ) -> Optional[EIP2612PaymentPayload]:
        try:
            payload_data = raw_payload.get("payload", {})
            if "permit" not in payload_data or "transfer" not in payload_data:
                return None
            permit = EIP2612Permit(**payload_data["permit"])
            transfer = EIP2612Transfer(**payload_data["transfer"])
            return EIP2612PaymentPayload(permit=permit, transfer=transfer)
        except Exception:
            return None