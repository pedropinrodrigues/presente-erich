from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TelegramInviteResponse(BaseModel):
    invite_id: uuid.UUID
    deep_link: str
    share_link: str
    expires_at: datetime
    status: str


class TelegramInviteListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invite_id: uuid.UUID
    status: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class AccountResponse(BaseModel):
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    display_name: str | None
    locale: str | None
    timezone: str | None
    status: str
    platform_admin: bool
    invitation_policy: str
