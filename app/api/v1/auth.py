"""
Authentication endpoints for user registration and login.
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, status, Depends, Request
from supabase import create_client
from uuid import UUID
import httpx

from app.models.schemas import (
    UserRegister, UserLogin, Token, TokenWithRefresh, UserResponse,
    ForgotPasswordRequest, ResetPasswordRequest,
    UserProfileUpdate, ChangePasswordRequest,
)
from app.services.auth_service import auth_service
from app.config import settings
from app.dependencies import get_current_user
from app.rate_limit import limiter
from postgrest.exceptions import APIError as PostgrestAPIError

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def _send_password_reset_email(to_email: str, full_name: str | None, reset_url: str) -> None:
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not configured — skipping password reset email to %s", to_email)
        return
    name = full_name or "there"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:40px 24px;color:#111">
      <p style="font-size:20px;font-weight:600;margin:0 0 24px">Reset your password</p>
      <p style="color:#555;margin:0 0 24px">Hi {name}, click the button below to choose a new password.
         This link expires in 15 minutes.</p>
      <a href="{reset_url}"
         style="display:inline-block;background:#0a0a0a;color:#fff;text-decoration:none;
                padding:12px 24px;border-radius:6px;font-size:14px;font-weight:500">
        Reset password
      </a>
      <p style="color:#9ca3af;font-size:12px;margin:32px 0 0">
        If you didn't request this, you can safely ignore this email.
      </p>
    </div>
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={"from": settings.EMAIL_FROM, "to": [to_email],
                      "subject": "Reset your eviStreams password", "html": html},
            )
            resp.raise_for_status()
    except Exception:
        logger.exception("Failed to send password reset email to %s", to_email)


@router.post("/register", response_model=TokenWithRefresh, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, user_data: UserRegister):
    """
    Register a new user.

    - **email**: User email address
    - **password**: User password (min 8 characters, must contain uppercase and digit)
    - **full_name**: Optional full name
    """
    try:
        # Check if user already exists
        existing = supabase.table("users").select("id").eq("email", user_data.email).execute()
        if existing.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

        # Hash password
        hashed_password = auth_service.hash_password(user_data.password)

        # Create user in database
        result = supabase.table("users").insert({
            "email": user_data.email,
            "hashed_password": hashed_password,
            "full_name": user_data.full_name,
            "is_active": True
        }).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user"
            )

        user = result.data[0]
        user_id = UUID(user["id"])
        user_role = user.get("role", "user")

        # Generate tokens
        access_token = auth_service.create_access_token(user_id, role=user_role)
        refresh_token = auth_service.create_refresh_token(user_id)

        logger.info(f"Registration successful: user={user_data.email}")
        return TokenWithRefresh(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user_id=user_id
        )

    except HTTPException:
        raise
    except PostgrestAPIError as e:
        logger.error(f"Supabase API error during registration: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database service error"
        )
    except Exception as e:
        logger.exception("Error during user registration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/login", response_model=TokenWithRefresh)
@limiter.limit("10/minute")
async def login(request: Request, credentials: UserLogin):
    """
    Login with email and password.

    Returns JWT access token and refresh token on success.
    """
    try:
        # Get user from database — only select needed columns
        result = supabase.table("users").select(
            "id, email, hashed_password, is_active, role"
        ).eq("email", credentials.email).execute()

        # Timing-safe: always run bcrypt even if user not found
        hashed_password = result.data[0]["hashed_password"] if result.data else None
        password_valid = auth_service.verify_password_timing_safe(
            credentials.password, hashed_password
        )

        if not result.data or not password_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password"
            )

        user = result.data[0]

        # Check if user is active
        if not user.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive"
            )

        user_id = UUID(user["id"])
        user_role = user.get("role", "user")

        # Generate tokens
        access_token = auth_service.create_access_token(user_id, role=user_role)
        refresh_token = auth_service.create_refresh_token(user_id)

        logger.info(f"Login successful: user={credentials.email} role={user_role}")
        return TokenWithRefresh(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user_id=user_id
        )

    except HTTPException:
        raise
    except PostgrestAPIError as e:
        logger.error(f"Supabase API error during login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database service error"
        )
    except Exception as e:
        logger.exception("Error during login")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/demo", response_model=TokenWithRefresh)
@limiter.limit("20/minute")
async def demo_login(request: Request):
    """
    Zero-login demo access. Issues a session for the shared read/write demo
    account so reviewers can explore the live system with no credentials.

    Returns 404 when demo mode is disabled.
    """
    if not settings.DEMO_MODE_ENABLED or not settings.DEMO_USER_ID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo mode is not enabled"
        )
    try:
        result = supabase.table("users").select("id, is_active, role")\
            .eq("id", settings.DEMO_USER_ID).execute()
        if not result.data or not result.data[0].get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Demo account is unavailable"
            )

        user_id = UUID(settings.DEMO_USER_ID)
        user_role = result.data[0].get("role", "user")

        access_token = auth_service.create_access_token(user_id, role=user_role)
        refresh_token = auth_service.create_refresh_token(user_id)

        logger.info("Demo session issued for demo user %s", user_id)
        return TokenWithRefresh(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user_id=user_id
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error issuing demo session")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/refresh", response_model=TokenWithRefresh)
@limiter.limit("30/minute")
async def refresh_access_token(request: Request):
    """
    Exchange a refresh token for a new access token and a rotated refresh token.

    Send the refresh token in the Authorization header as Bearer token.
    """
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing refresh token"
            )

        refresh_token = auth_header.split(" ", 1)[1]
        user_id = auth_service.verify_refresh_token(refresh_token)

        # Verify user still exists and is active
        result = supabase.table("users").select("id, is_active, role").eq("id", str(user_id)).execute()
        if not result.data or not result.data[0].get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials"
            )

        user_role = result.data[0].get("role", "user")

        # Issue new access token and rotate the refresh token
        access_token = auth_service.create_access_token(user_id, role=user_role)
        new_refresh_token = auth_service.create_refresh_token(user_id)

        logger.info("Token refreshed for user %s", user_id)

        return TokenWithRefresh(
            access_token=access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
            user_id=user_id
        )

    except HTTPException:
        raise
    except PostgrestAPIError as e:
        logger.error(f"Supabase API error during token refresh: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database service error"
        )
    except Exception as e:
        logger.exception("Error during token refresh")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def logout(request: Request):
    """
    Logout endpoint. Client must discard stored tokens on receipt.
    Future enhancement: add server-side token blacklist here.
    """
    return None


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
async def forgot_password(request: Request, body: ForgotPasswordRequest):
    """
    Request a password reset link. Always returns 200 to prevent user enumeration.
    """
    try:
        raw_token, token_hash, expires_at = auth_service.create_password_reset_token()

        result = supabase.table("users").select("id, email, full_name").eq("email", body.email).execute()
        if result.data:
            user = result.data[0]
            supabase.table("users").update({
                "password_reset_token": token_hash,
                "password_reset_expires_at": expires_at.isoformat(),
            }).eq("id", user["id"]).execute()

            frontend_url = settings.FRONTEND_URL or "https://evistreams.com"
            reset_url = f"{frontend_url}/reset-password?token={raw_token}"
            await _send_password_reset_email(user["email"], user.get("full_name"), reset_url)

        return {"message": "If that email is registered, you'll receive a reset link shortly."}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error during forgot-password request")
        return {"message": "If that email is registered, you'll receive a reset link shortly."}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def reset_password(request: Request, body: ResetPasswordRequest):
    """
    Reset password using a valid reset token.
    """
    try:
        token_hash = auth_service.hash_reset_token(body.token)

        result = supabase.table("users").select(
            "id, password_reset_token, password_reset_expires_at"
        ).eq("password_reset_token", token_hash).execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

        user = result.data[0]
        expires_raw = user.get("password_reset_expires_at", "")
        expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))

        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

        new_hash = auth_service.hash_password(body.new_password)
        supabase.table("users").update({
            "hashed_password": new_hash,
            "password_reset_token": None,
            "password_reset_expires_at": None,
        }).eq("id", user["id"]).execute()

        logger.info("Password reset for user id=%s", user["id"])
        return {"message": "Password reset successfully."}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error during password reset")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(user_id: UUID = Depends(get_current_user)):
    """
    Get current user information.

    Requires authentication.
    """
    try:
        # Only select columns the response needs — never include hashed_password
        result = supabase.table("users").select(
            "id, email, full_name, is_active, role, created_at"
        ).eq("id", str(user_id)).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        return UserResponse(**result.data[0])

    except HTTPException:
        raise
    except PostgrestAPIError as e:
        logger.error(f"Supabase API error fetching user info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database service error"
        )
    except Exception as e:
        logger.exception("Error fetching user info")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.patch("/me", response_model=UserResponse)
@limiter.limit("10/minute")
async def update_current_user(
    request: Request,
    body: UserProfileUpdate,
    user_id: UUID = Depends(get_current_user),
):
    """Update the current user's profile (full_name, email)."""
    try:
        updates: dict = {}
        if body.full_name is not None:
            updates["full_name"] = body.full_name.strip() or None
        if body.email is not None:
            new_email = body.email.lower()
            existing = supabase.table("users").select("id").eq("email", new_email).neq("id", str(user_id)).execute()
            if existing.data:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")
            updates["email"] = new_email

        if not updates:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

        supabase.table("users").update(updates).eq("id", str(user_id)).execute()

        result = supabase.table("users").select(
            "id, email, full_name, is_active, role, created_at"
        ).eq("id", str(user_id)).execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        logger.info("Profile updated for user id=%s fields=%s", user_id, list(updates.keys()))
        return UserResponse(**result.data[0])

    except HTTPException:
        raise
    except PostgrestAPIError as e:
        logger.error(f"Supabase API error during profile update: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database service error")
    except Exception:
        logger.exception("Error updating profile")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.post("/change-password", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Change the current user's password. Requires current password verification."""
    try:
        result = supabase.table("users").select("id, hashed_password").eq("id", str(user_id)).execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        current_hash = result.data[0]["hashed_password"]
        if not auth_service.verify_password_timing_safe(body.current_password, current_hash):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

        new_hash = auth_service.hash_password(body.new_password)
        supabase.table("users").update({"hashed_password": new_hash}).eq("id", str(user_id)).execute()

        logger.info("Password changed for user id=%s", user_id)
        return {"message": "Password changed successfully."}

    except HTTPException:
        raise
    except PostgrestAPIError as e:
        logger.error(f"Supabase API error during password change: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database service error")
    except Exception:
        logger.exception("Error changing password")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.get("/users/search")
async def search_users(
    q: str = "",
    limit: int = 8,
    _user_id: UUID = Depends(get_current_user),
):
    """Search registered users by email or name. Used for project member invites."""
    q = q.strip()
    if len(q) < 2:
        return []
    try:
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        result = supabase.table("users")\
            .select("id, email, full_name")\
            .or_(f"email.ilike.%{q}%,full_name.ilike.%{q}%")\
            .limit(limit)\
            .execute()
        return result.data or []
    except Exception:
        logger.exception("Error searching users")
        return []
