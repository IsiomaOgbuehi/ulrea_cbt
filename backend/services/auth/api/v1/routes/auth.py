from typing import Annotated
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from models.login_response import LoginResponse, Token, User
from api.v1.auth_routes import AuthRoutes
from database.database import SessionDep
from dependencies.user_dependencies import authenticate_user, get_current_active_user
from dependencies.auth_dependencies import ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token
from datetime import datetime, timedelta, timezone

fake_users_db = {
    "johndoe": {
        "username": "johndoe",
        "full_name": "John Doe",
        "email": "johndoe@example.com",
        "hashed_password": "$argon2id$v=19$m=65536,t=3,p=4$wagCPXjifgvUFBzq4hqe3w$CYaIb8sB+wtD+Vu/P4uod1+Qof8h+1g7bbDlBID48Rc",
        "disabled": False,
    },
    "alice": {
        "username": "alice",
        "full_name": "Alice Wonderson",
        "email": "alice@example.com",
        "hashed_password": "fakehashedsecret2",
        "disabled": True,
    },
}

app = FastAPI()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f'{AuthRoutes.api_version.value}{AuthRoutes.base_route.value}{AuthRoutes.login.value}')

router = APIRouter(
    prefix=AuthRoutes.base_route.value,
    tags=['auth'],
    responses={401: {'message': 'Unauthorized'}}
)

@router.post(AuthRoutes.login.value, response_model=Token)
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    
    user = authenticate_user(fake_users_db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )

    return Token(access_token=access_token)

@router.get(AuthRoutes.token.value)
async def get_token(token: Annotated[str, Depends(oauth2_scheme)]):
    return {'token': token}

@router.get("/users/me/", response_model=User)
async def read_users_me(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    return current_user