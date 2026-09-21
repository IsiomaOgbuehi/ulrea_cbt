from enum import Enum


class ViolationType(str, Enum):
    TAB_SWITCH = "tab_switch"
    FULLSCREEN_EXIT = "fullscreen_exit"
    COPY_PASTE = "copy_paste"
    RIGHT_CLICK = "right_click"
    DEV_TOOLS_OPENED = "dev_tools_opened"
    MULTIPLE_FACES_DETECTED = "multiple_faces_detected"
    NO_FACE_DETECTED = "no_face_detected"
    WINDOW_BLUR = "window_blur"
    OTHER = "other"   # fallback — pairs with `description` + `metadata_` for anything not yet enumerated


class ViolationSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"