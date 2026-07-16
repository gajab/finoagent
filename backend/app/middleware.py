"""Custom middleware for session-based authentication."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from .auth import COOKIE_NAME, unsign_session_id, validate_session
from .database import async_session
from .models import User
from sqlalchemy import select


class SessionMiddleware(BaseHTTPMiddleware):
    """
    Reads the session_id cookie on every request, validates the session
    in the database, and attaches the user to request.state.user.

    If the session is expired or invalid, clears the cookie.
    This middleware does NOT block unauthenticated requests — that is the
    job of the `get_current_user` dependency on individual routes.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.user = None

        token = request.cookies.get(COOKIE_NAME)
        clear_cookie = False

        if token:
            raw_session_id = unsign_session_id(token)
            if raw_session_id is None:
                clear_cookie = True
            else:
                async with async_session() as db:
                    session = await validate_session(db, raw_session_id)
                    if session is None:
                        clear_cookie = True
                    else:
                        result = await db.execute(select(User).where(User.id == session.user_id))
                        user = result.scalar_one_or_none()
                        if user:
                            request.state.user = user
                        else:
                            clear_cookie = True

        response = await call_next(request)

        if clear_cookie:
            response.delete_cookie(COOKIE_NAME, path="/")

        return response
