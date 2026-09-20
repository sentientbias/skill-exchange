"""Pydantic request/response schemas for the REST API.

Free-text input fields carry max_length caps (see api/request_size_guard.py
for the coarse body-size guard). The caps are generous relative to real
usage — they exist so malformed or hostile payloads fail fast with a 422
instead of reaching nacl/JSON parsing or the database.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

# Ed25519 material: 64-byte signature -> 88 chars standard base64;
# 32-byte public key -> 64 hex chars. Bands (not exact lengths) so clients
# that add stray whitespace still validate — core.signing strips it.
_SIG_MIN, _SIG_MAX = 80, 100
_PUBKEY_MIN, _PUBKEY_MAX = 56, 72


class AccountCreate(BaseModel):
    handle: str = Field(..., max_length=64, examples=["curiouscirkits"])
    display_name: str = Field(..., max_length=200, examples=["Curious Cirkits"])
    referred_by: Optional[str] = Field(
        default=None,
        max_length=64,
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
    name: str = Field(default="default", max_length=100, examples=["laptop"])


class KeyOut(BaseModel):
    id: str
    key_prefix: str
    name: str
    created_at: Any
    last_used_at: Optional[Any] = None
    revoked: bool
    api_key: Optional[str] = None  # present ONLY on creation responses


class SkillPublish(BaseModel):
    name: str = Field(..., max_length=200)
    slug: str = Field(..., max_length=128)
    description: str = Field(..., max_length=2000)
    category: str = Field(default="general", max_length=100)
    version: str = Field(default="1.0.0", max_length=50)
    # store.create_skill enforces 50..200_000 chars; the schema cap mirrors it
    # so oversized content fails at the API boundary (422) with a clear error.
    skill_md: str = Field(
        ..., max_length=200_000, description="Full SKILL.md content")
    manifest: dict[str, Any] = Field(default_factory=dict)
    signature: str = Field(
        ..., min_length=_SIG_MIN, max_length=_SIG_MAX,
        description="Base64 ed25519 signature")
    public_key: str = Field(
        ..., min_length=_PUBKEY_MIN, max_length=_PUBKEY_MAX,
        description="Hex ed25519 public key")


class VersionPublish(BaseModel):
    version: str = Field(..., max_length=50)
    skill_md: str = Field(..., max_length=200_000)
    manifest: dict[str, Any] = Field(default_factory=dict)
    signature: str = Field(
        ..., min_length=_SIG_MIN, max_length=_SIG_MAX)
    public_key: str = Field(
        ..., min_length=_PUBKEY_MIN, max_length=_PUBKEY_MAX)


class RatingIn(BaseModel):
    stars: int = Field(..., ge=1, le=5)
    comment: str = Field(default="", max_length=5000)


class InstallIn(BaseModel):
    slug: str = Field(..., max_length=128)
    version: str = Field(default="latest", max_length=50)
    client: str = Field(default="rest", max_length=100)


class ModerateIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=5000)


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
