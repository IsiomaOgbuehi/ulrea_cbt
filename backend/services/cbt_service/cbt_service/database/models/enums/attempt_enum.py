from enum import Enum

class AttemptStatus(str, Enum):
    STARTED = 'started'
    SUBMITTED = 'submitted'
    SCORED = 'scored'
    RESET = 'reset'
