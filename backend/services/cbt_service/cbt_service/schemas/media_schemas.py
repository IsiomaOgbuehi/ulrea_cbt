from pydantic import BaseModel


class ImageUploadRead(BaseModel):
    url: str            # public URL to embed in question / option HTML
    key: str            # storage key (path inside the bucket / media folder)
    content_type: str   # detected from the file's bytes, not the client's claim
    size: int           # bytes
