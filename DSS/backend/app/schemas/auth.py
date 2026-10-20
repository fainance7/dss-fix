from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    # Для веб-интерфейса токены выдаются в httpOnly-cookie и в теле отсутствуют (H-1).
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class AccessTokenResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"


class RoleInfo(BaseModel):
    name: str
    region_id: str | None = None
    region_name: str | None = None

    model_config = {"from_attributes": True}


class DirectionInfo(BaseModel):
    id: str
    code: str
    name: str


class MeResponse(BaseModel):
    id: str
    username: str
    email: str
    is_active: bool
    roles: list[RoleInfo]
    directions: list[DirectionInfo] = []

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    email: str | None = None
    iin: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ECPChallengeResponse(BaseModel):
    challenge_token: str
    nonce: str  # base64-encoded nonce to send to NCALayer for signing


class ECPLoginRequest(BaseModel):
    challenge_token: str  # JWT returned by GET /auth/ecp-challenge
    cms_signature: str    # base64 CMS SignedData (detached) from NCALayer
