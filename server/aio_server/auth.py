"""Optional HTTP Basic auth (AIO_AUTH=user:password) for servers reachable beyond localhost."""

import base64
import binascii
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

# Browsers fetch the web app manifest and its icons without credentials, so those stay public.
PUBLIC_PATHS = ("/manifest.json", "/icons/")


def add_basic_auth(app: FastAPI, credentials: str) -> None:
    user, _, password = credentials.partition(":")
    if not user or not password:
        raise ValueError("AIO_AUTH must look like user:password")
    expected_user, expected_password = user.encode(), password.encode()

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if request.url.path.startswith(PUBLIC_PATHS) or _authorized(request, expected_user, expected_password):
            return await call_next(request)
        return PlainTextResponse("Sign in to use Aio.", status_code=401,
                                 headers={"WWW-Authenticate": 'Basic realm="Aio", charset="UTF-8"'})


def _authorized(request: Request, user: bytes, password: bytes) -> bool:
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        given_user, _, given_password = base64.b64decode(encoded, validate=True).partition(b":")
    except (binascii.Error, ValueError):
        return False
    # Compare both parts every time so timing doesn't reveal which one was wrong.
    user_ok = secrets.compare_digest(given_user, user)
    password_ok = secrets.compare_digest(given_password, password)
    return user_ok and password_ok
