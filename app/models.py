import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict

class WebhookMessage(BaseModel):
    message_id: str = Field(..., min_length=1)
    from_: str = Field(..., alias="from")
    to: str
    ts: str
    text: Optional[str] = Field(None, max_length=4096)

    @field_validator('from_', 'to')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not re.match(r'^\+[1-9]\d{1,14}$', v):
            raise ValueError('Must be valid phone number in E.164 format (+ followed by country code and digits)')
        return v

    @field_validator('ts')
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        try:
            # Handle ISO-8601 with Z or timezone offset
            cleaned_ts = v.replace('Z', '+00:00')
            datetime.fromisoformat(cleaned_ts)
        except Exception:
            raise ValueError('Invalid ISO-8601 timestamp')
        return v

    model_config = ConfigDict(populate_by_name=True)


class MessageResponse(BaseModel):
    message_id: str
    from_: str = Field(..., alias="from")
    to: str
    ts: str
    text: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)

class MessagesListResponse(BaseModel):
    data: list[MessageResponse]
    total: int
    limit: int
    offset: int

class SenderCount(BaseModel):
    from_: str = Field(..., alias="from")
    count: int

    model_config = ConfigDict(populate_by_name=True)

class StatsResponse(BaseModel):
    total_messages: int
    senders_count: int
    messages_per_sender: list[SenderCount]
    first_message_ts: Optional[str]
    last_message_ts: Optional[str]
