from enum import Enum


class ItemBankRoutes(str, Enum):
    API_VERSION = "/api/v1"

    # Subjects
    SUBJECTS = "/subjects"
    SUBJECT_BY_ID = "/subjects/{subject_id}"
    ASSIGN_SUBJECT = "/subjects/{subject_id}/assign"
    UNASSIGN_SUBJECT = "/subjects/{subject_id}/assign/{user_id}"

    # Items
    ITEMS = "/items"
    ITEM_BY_ID = "/items/{item_id}"
    BULK_UPLOAD = "/items/bulk"
    BULK_UPLOAD_TEMPLATE = "/items/bulk/template"
