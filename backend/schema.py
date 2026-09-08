from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExtractedFact(BaseModel):
    """What the model returns per fact, before document-level grounding fields are attached."""

    page_number: int = Field(
        description="The exact page number (from the labeled page blocks) this fact was read from."
    )
    source_quote: str = Field(
        description="Verbatim or near-verbatim quote from that page supporting this fact."
    )
    entity: str
    metric: str
    value: str
    unit: Optional[str] = None
    time_period: Optional[str] = None
    scope: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    skip_reason: Optional[str] = Field(
        default=None,
        description=(
            "Set only when source_quote is a partial/nearest-available span rather than one that "
            "fully supports the claim (e.g. a labeled value split from the header/period that "
            "identifies it). One sentence explaining what's missing, e.g. 'row label and value are "
            "on separate lines with no adjacent context tying them to a specific quarter'. Null when "
            "source_quote fully supports the fact."
        ),
    )
    attributes_json: Optional[str] = Field(
        default=None,
        description=(
            "A JSON object, encoded as a string, for anything document-specific that doesn't fit "
            'the fixed fields above (e.g. \'{"segment": "Express Parcel", "restated": true}\'). '
            'Use "{}" if nothing extra applies.'
        ),
    )


class Fact(BaseModel):
    """Fact schema per PRD section 4. attributes is the escape hatch for anything
    document-specific that doesn't fit the fixed fields -- new keys show up per
    document type without a schema migration."""

    fact_id: str
    document_id: str
    page_number: int
    source_quote: str
    extracted_at: str

    entity: str
    metric: str
    value: str
    unit: Optional[str] = None
    time_period: Optional[str] = None
    scope: Optional[str] = None
    confidence: float
    skip_reason: Optional[str] = None

    attributes: dict = Field(default_factory=dict)


class RelationshipJudgment(BaseModel):
    """PRD section 5 step 5: the model's classification of one candidate pair."""

    relation: Literal["corroborates", "contradicts", "reconcilable_context", "unrelated"]
    explanation: str = Field(
        description=(
            "One paragraph citing what in each fact's source_quote supports this "
            "classification. Must explicitly weigh scope/qualifier differences (e.g. "
            "consolidated vs standalone) before concluding contradicts -- a numeric gap "
            "fully explained by differing scope is reconcilable_context, not a contradiction."
        )
    )
