"""
Authentication endpoints for user registration and login.
"""

import logging
from fastapi import APIRouter, HTTPException, status, Depends, Request
from supabase import create_client
from uuid import UUID

from app.models.schemas import UserRegister, UserLogin, Token, TokenWithRefresh, UserResponse
from app.services.auth_service import auth_service
from app.config import settings
from app.dependencies import get_current_user
from app.rate_limit import limiter
from postgrest.exceptions import APIError as PostgrestAPIError

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


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
