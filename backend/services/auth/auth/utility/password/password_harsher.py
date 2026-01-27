from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()


class PasswordHasher:

    @staticmethod
    def create(password: str):
        return password_hash.hash(password=password)
    
    @staticmethod
    def verify(plain_password, hashed_password):
        return password_hash.verify(plain_password, hashed_password)