from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, SQLModel, Field

class OrganizationSettingsBase(SQLModel):
    org_id: UUID = Field(
        foreign_key="organizations.id",
        index=True,
        nullable=False,
    )
    version: int = Field(default=1)
    settings: dict = Field(
        sa_column=Column(JSONB, nullable=False)
    )
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrganizationSettingsModel(OrganizationSettingsBase, table=True):
    __tablename__ = 'organization_settings'
    id: UUID = Field(default_factory=uuid4, primary_key=True)



# class OrganizationSettings(BaseModel):
#     branding: dict = {}
#     localization: dict = {}
#     security: dict = {}
#     features: dict = {}
#     academic: dict = {}
#     assessment: dict = {}
#     notifications: dict = {}
#     integrations: dict = {}
#     billing: dict = {}
#     limits: dict = {}

"""
FULL SETTINGS DEFINITION (Recommended)
🎨 1. Branding
"branding": {
  "logo_url": "https://cdn.org/logo.png",
  "primary_color": "#0A3D62",
  "secondary_color": "#1E3799",
  "custom_domain": "cbt.school.edu",
  "email_sender_name": "CBT School",
  "email_sender_address": "noreply@school.edu"
}

🌍 2. Localization
"localization": {
  "timezone": "Africa/Lagos",
  "language": "en",
  "date_format": "DD/MM/YYYY",
  "currency": "NGN"
}

🔐 3. Security & Access Control
"security": {
  "password_policy": {
    "min_length": 8,
    "require_uppercase": true,
    "require_numbers": true,
    "require_special_chars": false
  },
  "two_factor_auth": {
    "enabled": true,
    "methods": ["email", "authenticator"]
  },
  "session_timeout_minutes": 60,
  "ip_whitelist": []
}

🧪 4. Feature Flags (CRITICAL)
"features": {
  "cbt_enabled": true,
  "practice_mode": true,
  "proctoring": false,
  "ai_question_generation": false,
  "result_analytics": true,
  "offline_mode": false
}


🔥 This allows per-organization feature toggling without redeploying code.

🎓 5. Academic Structure
"academic": {
  "grading_system": "percentage",
  "pass_mark": 50,
  "levels": ["JSS1", "JSS2", "SS1", "SS2", "SS3"],
  "subjects": ["Math", "English", "Biology"],
  "academic_year": {
    "start": "2025-01-01",
    "end": "2025-12-31"
  }
}

📝 6. Assessment / CBT Rules
"assessment": {
  "default_duration_minutes": 60,
  "allow_review": true,
  "shuffle_questions": true,
  "shuffle_options": true,
  "negative_marking": false,
  "auto_submit_on_timeout": true,
  "max_attempts": 1
}

📣 7. Notifications
"notifications": {
  "email": {
    "enabled": true,
    "exam_reminders": true,
    "result_notifications": true
  },
  "sms": {
    "enabled": false
  },
  "in_app": {
    "enabled": true
  }
}

🔌 8. Integrations
"integrations": {
  "payment": {
    "provider": "paystack",
    "public_key": "pk_live_xxx",
    "enabled": true
  },
  "lms": {
    "enabled": false
  },
  "webhooks": []
}

💳 9. Billing / Subscription
"billing": {
  "plan": "pro",
  "status": "active",
  "renewal_date": "2025-12-31",
  "trial_ends_at": null
}

🚦 10. Limits & Quotas (Monetization Gold)
"limits": {
  "max_users": 500,
  "max_exams_per_month": 50,
  "storage_mb": 10240,
  "concurrent_exams": 5
}
"""