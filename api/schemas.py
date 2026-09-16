"""Pydantic request/response schemas for the REST API."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    handle: str = Field(..., examples=["curiouscirkits"])
    display_name: str = Field(..., examples=["Curious Cirkits"])
    referred_by: Optional[str] = Field(
        default=None,
        description="Handle of the existing publisher who referred this account",
        examples=["zuckbot"],
    )


class AccountOut(BaseModel):
    id: str
    handle: str
    display_name: str
    is_moderator: bool
    created_at: Any
    api_key: Optional[str] = None  # present ONLY on creation responses


class KeyCreate(BaseModel):
    name: str = Field(default="default", examples=["laptop"])


class KeyOut(BaseModel):
    id: str
    key_prefix: str
    name: str
    created_at: Any
    last_used_at: Optional[Any] = None
    revoked: bool
    api_key: Optional[str] = None  # present ONLY on creation responses


class SkillPublish(BaseModel):
    name: str
    slug: str
    description: str
    category: str = "general"
    version: str = "1.0.0"
    skill_md: str = Field(..., description="Full SKILL.md content")
    manifest: dict[str, Any] = Field(default_factory=dict)
    signature: str = Field(..., description="Base64 ed25519 signature")
    public_key: str = Field(..., description="Hex ed25519 public key")


class VersionPublish(BaseModel):
    version: str
    skill_md: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    signature: str
    public_key: str


class RatingIn(BaseModel):
    stars: int = Field(..., ge=1, le=5)
    comment: str = ""


class InstallIn(BaseModel):
    slug: str
    version: str = "latest"
    client: str = "rest"


class ModerateIn(BaseModel):
    approve: bool
    note: str = ""


class ReferralOut(BaseModel):
    id: str
    referrer_handle: str
    referred_handle: str
    status: str
    created_at: Any
    converted_at: Optional[Any] = None
    pass_id: Optional[str] = None
    expires_at: Optional[Any] = None


class ProPassOut(BaseModel):
    pass_id: str
    token: str
    issued_at: Any
    expires_at: Any
