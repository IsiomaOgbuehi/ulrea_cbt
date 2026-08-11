from enum import Enum


class ExamStatus(str, Enum):
    DRAFT = "draft"                   # teacher building it
    PENDING_APPROVAL = "pending"      # submitted to admin for review
    APPROVED = "approved"             # admin approved, not yet started
    ACTIVE = "active"                 # currently running
    CLOSED = "closed"                 # exam window ended (end_time passed or manually closed)
    REJECTED = "rejected"             # admin rejected, back to teacher


class AssignmentStatus(str, Enum):
    ASSIGNED = "assigned"            # student is assigned the exam but has not started it
    STARTED = "started"               # student has opened/entered the exam
    SUBMITTED = "submitted"           # student has completed and submitted the exam
    MISSED = "missed"                 # student did not start before deadline or window closed



# For audit log purposes
class ExamAction(str, Enum):
    # lifecycle
    CREATED = "created"                           # exam was created
    UPDATED = "updated"                           # exam details/settings were modified
    DELETED = "deleted"                           # exam was deleted
    RESET = "reset"

    # review flow
    SUBMITTED_FOR_APPROVAL = "submitted_for_approval"  # teacher submitted exam for admin review
    APPROVED = "approved"                         # admin approved the exam
    REJECTED = "rejected"                         # admin rejected the exam

    # scheduling / execution
    SCHEDULED = "scheduled"                       # exam was scheduled with a start/end time
    RESCHEDULED = "rescheduled"                   # exam schedule was changed
    STARTED = "started"                           # exam became active
    PAUSED = "paused"                             # exam was temporarily suspended
    COMPLETED = "completed"                       # exam was closed/completed

    # exam composition
    ITEM_ADDED = "item_added"                     # question/item added to the exam
    ITEM_REMOVED = "item_removed"                 # question/item removed from the exam
    SECTION_ADDED = "section_added"               # section added to the exam
    SECTION_UPDATED = "section_updated"           # section details modified

    # assignment
    ASSIGNED_TO_STUDENT = "assigned_to_student"   # exam assigned to a student
    UNASSIGNED_FROM_STUDENT = "unassigned_from_student"  # assignment removed from a student

    # student activity
    STUDENT_STARTED = "student_started"           # student began an exam attempt
    STUDENT_SUBMITTED = "student_submitted"       # student submitted an attempt
    STUDENT_AUTO_SUBMITTED = "student_auto_submitted"  # system auto-submitted attempt after timeout

    SECTION_REMOVED = "section_removed"              # section deleted
    ASSIGNED_TO_COHORT = "assigned_to_cohort"        # assigned to an entire cohort/class
    UNASSIGNED_FROM_COHORT = "unassigned_from_cohort"# cohort assignment removed
    REOPENED = "reopened"                            # closed exam reopened
    SETTINGS_UPDATED = "settings_updated"            # exam settings changed
