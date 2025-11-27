from typing import Annotated
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from ....models.login_response import LoginResponse, Token, User
from ..auth_routes import AuthRoutes

fake_users_db = {
    "johndoe": {
        "username": "johndoe",
        "full_name": "John Doe",
        "email": "johndoe@example.com",
        # "hashed_password": "fakehashedsecret",
        "disabled": False,
    },
    "alice": {
        "username": "alice",
        "full_name": "Alice Wonderson",
        "email": "alice@example.com",
        # "hashed_password": "fakehashedsecret2",
        "disabled": True,
    },
}

app = FastAPI()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl='token')

router = APIRouter(
    prefix=AuthRoutes.base_route.value,
    tags=['auth'],
    responses={401: {'message': 'Unauthorized'}}
)

@router.post(AuthRoutes.login.value, response_model=Token)
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user_dict = fake_users_db.get(form_data.username)
    if not user_dict:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    user = User(**user_dict)
    # hashed_password = fake_hash_password(form_data.password)
    # if not hashed_password == user.hashed_password:
    #     raise HTTPException(status_code=400, detail="Incorrect username or password")

    return Token(access_token=user.username) # {"access_token": user.username, "token_type": "bearer"}

@router.get(AuthRoutes.token.value)
async def get_token(token: Annotated[str, Depends(oauth2_scheme)]):
    return {'token': token}