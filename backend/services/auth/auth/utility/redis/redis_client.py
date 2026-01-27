# redis_client.py
import redis
from dotenv import load_dotenv
import os

load_dotenv()

redis_client = redis.Redis.from_url(
    os.getenv('REDIS_URL'),
    decode_responses=True
)