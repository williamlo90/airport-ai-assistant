"""Server-owned requirements for private observations and their derived content.

Labels describe requirements, never prove that an actor currently meets them.
Persistence adapters must load them with the owning row's trusted tenant.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from airport_assistant.modules.identity.ports import SourceGrantProvider
from airport_assistant.modules.source.grants import SCOPES
from airport_assistant.modules.source.models import Booking, Policy, SourceRecord, Transaction


class ResourceSecurityLabel(SourceRecord):
    source: Literal["reference"]
    resource_type: Literal["booking", "transaction"]
    reference: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    required_source_scopes: tuple[Literal["booking:read", "payment_status:read", "payment_details:read"], ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def valid_scopes(self):
        scopes = frozenset(self.required_source_scopes)
        if len(scopes) != len(self.required_source_scopes) or not scopes <= SCOPES[self.resource_type]:
            raise ValueError("Invalid resource scope.")
        return self


@dataclass(frozen=True)
class SecurityLabels:
    tenant_id: UUID
    labels: tuple[ResourceSecurityLabel, ...]

    def __post_init__(self):
        if not isinstance(self.tenant_id, UUID) or type(self.labels) is not tuple or not 1 <= len(self.labels) <= 64:
            raise ValueError("Private source content requires bounded labels and a tenant.")
        if any(type(label) is not ResourceSecurityLabel for label in self.labels):
            raise ValueError("Validated labels required.")
        keys = [(label.resource_type, label.reference) for label in self.labels]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate resource labels must be merged.")

    def to_records(self) -> list[dict]:
        return [{**label.model_dump(mode="json"),
                 "required_source_scopes": sorted(label.required_source_scopes)}
                for label in sorted(self.labels, key=lambda item: (item.resource_type, item.reference))]

    @classmethod
    def from_records(cls, tenant_id: UUID, records: list[dict]) -> "SecurityLabels":
        """Internal repository boundary, not a client/model input schema."""
        if type(records) is not list or not 1 <= len(records) <= 64:
            raise ValueError("Missing or oversized private labels.")
        bundles = []
        for record in records:
            if type(record) is not dict or type(record.get("required_source_scopes")) is not list:
                raise ValueError("Malformed stored label.")
            label = ResourceSecurityLabel.model_validate(
                {**record, "required_source_scopes": tuple(record["required_source_scopes"])})
            bundles.append(cls(tenant_id, (label,)))
        return merge_labels(*bundles)


def _label(kind, reference, scopes):
    return ResourceSecurityLabel(source="reference", resource_type=kind, reference=reference,
                                 required_source_scopes=tuple(sorted(scopes)))


def labels_for_observation(tenant_id: UUID, observation: Booking | Transaction) -> SecurityLabels:
    """Whole DTO labeling: no selective field projection or client-supplied scopes.

    A transaction's visible booking link carries a separate booking requirement.
    """
    if type(observation) is Booking:
        labels = (_label("booking", observation.reference, {"booking:read"}),)
    elif type(observation) is Transaction:
        labels = (_label("transaction", observation.reference, observation.allowed_scopes),)
        if observation.booking_reference is not None:
            labels += (_label("booking", observation.booking_reference, {"booking:read"}),)
    else:
        raise ValueError("Source booking or transaction observation required.")
    return SecurityLabels(tenant_id, labels)


def labels_for_policy(tenant_id: UUID, booking: Booking, policy: Policy) -> SecurityLabels:
    if (type(booking) is not Booking or type(policy) is not Policy
            or booking.policy_reference != policy.reference or booking.service_type != policy.service_type):
        raise ValueError("Policy must match the observed booking version and service.")
    return labels_for_observation(tenant_id, booking)


def merge_labels(*bundles: SecurityLabels) -> SecurityLabels:
    """Monotone union: adding content can only add requirements."""
    if not bundles or any(type(bundle) is not SecurityLabels for bundle in bundles):
        raise ValueError("Validated private labels required.")
    tenant_id = bundles[0].tenant_id
    scopes = {}
    for bundle in bundles:
        if bundle.tenant_id != tenant_id:
            raise ValueError("Cannot combine tenants.")
        for label in bundle.labels:
            scopes.setdefault((label.resource_type, label.reference), set()).update(label.required_source_scopes)
    return SecurityLabels(tenant_id, tuple(_label(kind, ref, required) for (kind, ref), required in sorted(scopes.items())))


class PrivateSourceAccess:
    """Additional gate after object ACL; no staff/admin or participant bypass.

    The caller passes the intended customer's trusted principal. Re-run at each
    operation; a successful return must not be persisted as an authorization.
    """

    def __init__(self, grants: SourceGrantProvider):
        self._grants = grants

    async def allows(self, *, tenant_id: UUID, principal_id: UUID, labels: SecurityLabels) -> bool:
        if not isinstance(tenant_id, UUID) or not isinstance(principal_id, UUID):
            raise ValueError("Trusted customer identity required.")
        if type(labels) is not SecurityLabels or labels.tenant_id != tenant_id:
            return False
        for label in labels.labels:
            granted = await self._grants.has_grants(tenant_id=tenant_id, principal_id=principal_id,
                resource_type=label.resource_type, resource_reference=label.reference,
                required_scopes=frozenset(label.required_source_scopes))
            if granted is not True:
                return False
        return True
