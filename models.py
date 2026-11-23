from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class ReviewTone(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tone: ReviewTone
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=500)
    reasons: list[str] = Field(max_length=5)
    recommended_action: str = Field(min_length=1, max_length=500)
    draft: str = Field(min_length=1, max_length=1200)
    needs_review: bool


class RemoteReview(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    text: str = Field(min_length=3, max_length=3000)
    created_at: datetime
    status: str = "new"


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=3, max_length=3000)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    consent_processing: bool
    consent_publication: bool = False
    website: str = Field(default="", max_length=100)
