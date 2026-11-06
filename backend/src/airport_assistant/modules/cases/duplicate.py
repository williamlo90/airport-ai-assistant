"""Exact-reference advisory lookup; closure still revalidates the target."""
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DuplicateSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)
    membership_id: UUID = Field(strict=False)
    expected_version: int = Field(ge=1)
    public_reference: str = Field(min_length=37, max_length=37)

    @field_validator('public_reference')
    @classmethod
    def canonical_reference(cls, value):
        if re.fullmatch(r'CASE-[a-f0-9]{32}', value) is None:
            raise ValueError('Exact case reference required.')
        return value
