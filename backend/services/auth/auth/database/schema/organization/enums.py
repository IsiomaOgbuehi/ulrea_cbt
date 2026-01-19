from enum import Enum

class OrganizationType(str, Enum):
    SCHOOL = 'school'
    COMPANY = 'company'
    NGO = 'ngo'
    GOVERNMENT = 'government'
    CERTIFICATION = 'certification'
    RECRUITER = 'recruiter'
    OTHERS = 'others'