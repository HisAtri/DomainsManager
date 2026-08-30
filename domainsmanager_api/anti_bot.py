"""Small, operation-bound anti-bot challenges."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from altcha import create_challenge, verify_solution
from captcha.image import ImageCaptcha
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_api.dependencies import get_session
from domainsmanager_api.global_setting_registry import GLOBAL_SETTING_BY_KEY
from domainsmanager_api.secret_settings import decrypt_secret
from domainsmanager_persistence.models import GlobalSetting

Operation = Literal["register", "login", "create_domain", "refresh_domain"]
router = APIRouter(prefix="/anti-bot", tags=["Anti-bot"])
_COSTS = {"easy": 1_000, "medium": 5_000, "hard": 15_000}


class CaptchaChallenge(BaseModel):
    operation: Operation
    image: str
    token: str


class PowChallenge(BaseModel):
    operation: Literal["create_domain", "refresh_domain"]
    challenge: dict


def _fernet(request: Request) -> Fernet:
    secret = request.app.state.settings.jwt_secret_key.get_secret_value()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


async def config(session: AsyncSession, request: Request) -> dict[str, object]:
    keys = ("anti_bot_mode", "captcha_rotate", "captcha_offset", "captcha_warp", "pow_difficulty", "turnstile_site_key", "turnstile_secret_key")
    rows = {key: await session.get(GlobalSetting, key) for key in keys}
    result: dict[str, object] = {}
    for key in keys:
        row, definition = rows[key], GLOBAL_SETTING_BY_KEY[key]
        if row is None:
            value = definition.default(request.app.state.settings)
        elif definition.secret:
            value = decrypt_secret(row.value, request.app.state.settings.configuration_encryption_key)
        elif definition.kind == "boolean":
            value = row.value == "true"
        else:
            value = row.value
        result[key] = value
    return result


def reject() -> None:
    raise HTTPException(422, detail={"code": "anti_bot_verification_failed", "message": "Anti-bot verification failed"})


@router.get("/captcha", response_model=CaptchaChallenge)
async def captcha(operation: Operation, request: Request, session: AsyncSession = Depends(get_session)) -> CaptchaChallenge:
    settings = await config(session, request)
    if settings["anti_bot_mode"] != "image_captcha" or operation not in {"register", "login"}:
        raise HTTPException(404)
    answer = "".join(secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5))
    image = ImageCaptcha(width=180, height=60).generate_image(answer)
    if settings["captcha_rotate"]:
        image = image.rotate(secrets.randbelow(15) - 7, fillcolor="white")
    if settings["captcha_offset"]:
        image = image.transform(image.size, Image.Transform.AFFINE, (1, 0, secrets.randbelow(9) - 4, 0, 1, secrets.randbelow(7) - 3), fillcolor="white")
    if settings["captcha_warp"]:
        image = image.transform(image.size, Image.Transform.PERSPECTIVE, (1, .04, 0, .02, 1, 0, 0, 0), fillcolor="white")
    payload = json.dumps({"a": answer, "o": operation, "e": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())}).encode()
    token = _fernet(request).encrypt(payload).decode()
    output = io.BytesIO(); image.save(output, format="PNG")
    return CaptchaChallenge(operation=operation, image="data:image/png;base64," + base64.b64encode(output.getvalue()).decode(), token=token)


@router.get("/pow", response_model=PowChallenge)
async def pow(operation: Literal["create_domain", "refresh_domain"], request: Request, session: AsyncSession = Depends(get_session)) -> PowChallenge:
    settings = await config(session, request)
    if settings["anti_bot_mode"] != "image_captcha":
        raise HTTPException(404)
    challenge = create_challenge(algorithm="PBKDF2/SHA-256", cost=_COSTS[str(settings["pow_difficulty"])], hmac_secret=request.app.state.settings.jwt_secret_key.get_secret_value(), expires_at=datetime.now(UTC) + timedelta(minutes=5), data={"operation": operation})
    return PowChallenge(operation=operation, challenge=challenge.to_dict())


async def verify(request: Request, session: AsyncSession, operation: Operation, *, captcha_token: str | None = None, captcha_answer: str | None = None, pow_payload: str | None = None, turnstile_token: str | None = None) -> None:
    settings = await config(session, request)
    mode = settings["anti_bot_mode"]
    if mode == "disabled": return
    if mode == "image_captcha":
        if operation in {"register", "login"}:
            try:
                data = json.loads(_fernet(request).decrypt((captcha_token or "").encode()).decode())
                if data["o"] != operation or data["e"] < datetime.now(UTC).timestamp() or not secrets.compare_digest(data["a"], (captcha_answer or "").upper()): reject()
            except (InvalidToken, ValueError, KeyError, TypeError): reject()
        else:
            result = verify_solution(pow_payload or "", request.app.state.settings.jwt_secret_key.get_secret_value())
            if not result.verified: reject()
        return
    if not settings["turnstile_secret_key"] or not turnstile_token: reject()
    response = await httpx.AsyncClient(timeout=5).post("https://challenges.cloudflare.com/turnstile/v0/siteverify", data={"secret": settings["turnstile_secret_key"], "response": turnstile_token, "remoteip": request.client.host if request.client else ""})
    if not response.json().get("success") or response.json().get("action") != operation: reject()
