
from typing import Annotated

from fastapi import Depends

from auth.api.v1.routes.auth import oauth2_scheme


def get_user(db, username: str):
    pass
    # if username in db:
    #     user_dict = db[username]
    #     return UserInDB(**user_dict)

def authenticate_user(fake_db, username: str, password: str):
    pass
    # user = get_user(fake_db, username)
    # if not user:
    #     return False
    # if not verify_password(password, user.hashed_password):
    #     return False
    # return user

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    pass
    # credentials_exception = HTTPException(
    #     status_code=status.HTTP_401_UNAUTHORIZED,
    #     detail="Could not validate credentials",
    #     headers={"WWW-Authenticate": "Bearer"},
    # )
    # try:
    #     payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    #     username = payload.get("sub")
    #     if username is None:
    #         raise credentials_exception
    #     token_data = TokenData(username=username)
    # except InvalidTokenError:
    #     raise credentials_exception
    # user = get_user(fake_users_db, username=token_data.username)
    # if user is None:
    #     raise credentials_exception
    # return user



# async def get_current_active_user(
#     current_user: Annotated[User, Depends(get_current_user)],
# ):
#     if current_user.disabled:
#         raise HTTPException(status_code=400, detail="Inactive user")
#     return current_user