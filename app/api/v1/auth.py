"""
Authentication endpoints for user registration and login.
"""

from fastapi import APIRouter, HTTPException, status, Depends
from supabase import create_client
from uuid import UUID

from app.models.schemas import UserRegister, UserLogin, Token, UserResponse
from app.services.auth_service import auth_service
from app.config import settings
from app.dependencies import get_current_user


router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


# Handle CORS preflight requests
@router.options("/register")
@router.options("/login")
@router.options("/me")
async def options_handler():
    """Handle CORS preflight requests"""
    return {"status": "ok"}


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserRegister):
    """
    Register a new user.

    - **email**: User email address
    - **password**: User password (min 8 characters)
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

        # Generate access token
        access_token = auth_service.create_access_token(user_id)

        return Token(
            access_token=access_token,
            token_type="bearer",
            user_id=user_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/login", response_model=Token)
async def login(credentials: UserLogin):
    """
    Login with email and password.

    Returns JWT access token on success.
    """
    try:
        # Get user from database
        result = supabase.table("users").select("*").eq("email", credentials.email).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password"
            )

        user = result.data[0]

        # Verify password
        if not auth_service.verify_password(credentials.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password"
            )

        # Check if user is active
        if not user.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive"
            )

        user_id = UUID(user["id"])

        # Generate access token
        access_token = auth_service.create_access_token(user_id)

        return Token(
            access_token=access_token,
            token_type="bearer",
            user_id=user_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(user_id: UUID = Depends(get_current_user)):
    """
    Get current user information.

    Requires authentication.
    """
    try:
        result = supabase.table("users").select("*").eq("id", str(user_id)).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        return UserResponse(**result.data[0])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
