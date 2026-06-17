from enum import Enum

class OrganizationType(str, Enum):
    SCHOOL = "school"
    UNIVERSITY = "university"
    CORPORATE = "corporate"
    EXAM_BODY = "exam_body"        # WAEC, NECO, JAMB, etc.
    RECRUITMENT = "recruitment"
    CERTIFICATION = "certification"
    GOVERNMENT = "government"
    DEFAULT = "default"
    OTHER = "other"


class OrganizationVisibility(str, Enum):
    PRIVATE = "private"     # INSTITUTION — only members see it
    PUBLIC = "public"       # EXAM_BODY — discoverable by anyone