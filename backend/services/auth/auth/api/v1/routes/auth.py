from typing import Annotated
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from auth.api_models.login_response import LoginResponse, Token, User
from auth.api.v1.auth_routes import AuthRoutes
from auth.database.database import SessionDep
from auth.dependencies.user_dependencies import authenticate_user, get_current_active_user
from auth.dependencies.auth_dependencies import ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token
from datetime import datetime, timedelta, timezone
from auth.api_models import SignUp
from auth.database.schema import OrganizationModel
# from database.schema.hero import Hero, HeroModel, HeroRead

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

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f'{AuthRoutes.API_VERSION.value}{AuthRoutes.BASE_ROUTE.value}{AuthRoutes.LOGIN.value}')

router = APIRouter(
    prefix=AuthRoutes.BASE_ROUTE.value,
    tags=['auth'],
    responses={401: {'message': 'Unauthorized'}}
)

@router.post(AuthRoutes.LOGIN.value, response_model=Token)
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

@router.get(AuthRoutes.TOKEN.value)
async def get_token(token: Annotated[str, Depends(oauth2_scheme)]):
    return {'token': token}

@router.post(AuthRoutes.SIGNUP.value)
async def signup(signup_data: SignUp, session: SessionDep):
    organization = OrganizationModel.model_validate(signup_data.organization)

    session.add(organization)
    session.commit()
    session.refresh(organization)
    return organization



@router.get("/users/me/", response_model=User)
async def read_users_me(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    return current_user


# @router.post("/heroes/", response_model=HeroModel)
# async def create_hero(hero: Hero, session: SessionDep):
    
#     db_hero = HeroModel.model_validate(hero)
#     session.add(db_hero)
#     session.commit()
#     session.refresh(db_hero)
#     return db_hero

# @router.put("/heroes/{hero_id}", response_model=HeroRead)
# async def update_hero_put(
#     hero_id: int,
#     hero: Hero,
#     session: SessionDep,
# ):
#     db_hero = session.get(HeroModel, hero_id)
#     if not db_hero:
#         raise HTTPException(status.HTTP_404_NOT_FOUND, detail={'message': 'Hero not Found'})

#     for key, value in hero.model_dump().items():
#         setattr(db_hero, key, value)

#     session.commit()
#     session.refresh(db_hero)
#     return db_hero

# @router.patch("/heroes/{hero_id}", response_model=HeroRead)
# def update_hero_patch(
#     hero_id: int,
#     hero: Hero,
#     session: SessionDep,
# ):
#     db_hero = session.get(HeroModel, hero_id)
#     if not db_hero:
#         raise HTTPException(404)

#     hero_data = hero.model_dump(exclude_unset=True)
#     for key, value in hero_data.items():
#         setattr(db_hero, key, value)

#     session.commit()
#     session.refresh(db_hero)
#     return db_hero