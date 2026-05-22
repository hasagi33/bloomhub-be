"""
core/enums.py
─────────────────────────────────────────────────────────────────────────────
Single source of truth for every Django TextChoices / IntegerChoices class
used across the BloomHub backend.

Organisation
~~~~~~~~~~~~
  1. User / Employment
  2. Employee Documents  (CV, external links)
  3. Documents           (company-wide document vault)
  4. Assets
  5. Employee Profile Change-History
  6. Project Assignments
  7. Leave Management
  8. Performance Reviews
  9. Onboarding / Offboarding

Import convention
~~~~~~~~~~~~~~~~~
  from core.enums import ReviewStatus, DocumentCategory   # direct, explicit
"""

from django.db import models

# ──────────────────────────────────────────────────────────────────────────────
# 1. User / Employment
# ──────────────────────────────────────────────────────────────────────────────


class EmploymentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ON_LEAVE = "on_leave", "On Leave"
    INACTIVE = "inactive", "Inactive"


class OrgChartEventKind(models.TextChoices):
    HIRE = "hire", "Hire"
    PROMOTE = "promote", "Promote"
    REASSIGN = "reassign", "Reassign"
    LEAVE = "leave", "Leave"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Employee Documents  (per-employee CV / external files)
# ──────────────────────────────────────────────────────────────────────────────


class EmployeeDocumentType(models.TextChoices):
    """Type of document stored against an employee record (CV, etc.)."""

    CV = "cv", "CV"
    OTHER = "other", "Other"


class EmployeeDocumentSourceType(models.TextChoices):
    FILE = "file", "File"
    EXTERNAL_LINK = "external_link", "External Link"


class EmployeeDocumentProviderType(models.TextChoices):
    INTERNAL = "internal", "Internal"
    CANVA = "canva", "Canva"
    OTHER = "other", "Other"


# ──────────────────────────────────────────────────────────────────────────────
# 3. Documents  (company-wide document vault)
# ──────────────────────────────────────────────────────────────────────────────


class DocumentCategory(models.TextChoices):
    CONTRACTS = "contracts", "Contracts"
    POLICIES = "policies", "Policies"
    AGREEMENTS = "agreements", "Agreements"
    COMPLIANCE = "compliance", "Compliance"
    ONBOARDING = "onboarding", "Onboarding"
    TRAINING = "training", "Training"
    BENEFITS = "benefits", "Benefits"
    OTHER = "other", "Other"


class DocumentSignatureStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SIGNED = "signed", "Signed"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"
    NOT_REQUIRED = "not_required", "Not Required"


class DocumentAccessRole(models.TextChoices):
    """Roles that may be granted access to a document via ``allowed_roles``."""

    EMPLOYEE = "employee", "Employee"
    MANAGER = "manager", "Manager"
    HR = "hr", "HR"
    ADMIN = "admin", "Admin"


class DocumentSignerStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SIGNED = "signed", "Signed"
    REJECTED = "rejected", "Rejected"
    NOT_SENT = "notsent", "Not Sent"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Assets
# ──────────────────────────────────────────────────────────────────────────────


class AssetStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    MAINTENANCE = "maintenance", "Maintenance"
    RETIRED = "retired", "Retired"
    LOST = "lost", "Lost"
    RETURNED = "returned", "Returned"
    DAMAGED = "damaged", "Damaged"


class AssetCondition(models.TextChoices):
    EXCELLENT = "excellent", "Excellent"
    GOOD = "good", "Good"
    FAIR = "fair", "Fair"
    POOR = "poor", "Poor"
    DAMAGED = "damaged", "Damaged"


# ──────────────────────────────────────────────────────────────────────────────
# 5. Employee Profile Change-History
# ──────────────────────────────────────────────────────────────────────────────


class TrackedField(models.TextChoices):
    ROLE = "role", "Role"
    SALARY = "salary", "Salary"
    CPF_LEVEL = "cpf_level", "CPF Level"
    DEPARTMENT = "department", "Department"
    MANAGER_IDS = "manager_ids", "Manager IDs"
    EMPLOYMENT_STATUS = "employment_status", "Employment Status"
    CAREER_LEVEL = "career_level", "Career Level"
    START_DATE = "start_date", "Start Date"


# ──────────────────────────────────────────────────────────────────────────────
# 6. Project Assignments
# ──────────────────────────────────────────────────────────────────────────────


class ProjectAssignmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    ON_HOLD = "on_hold", "On Hold"


class ProjectType(models.TextChoices):
    CLIENT = "client", "Client"
    INTERNAL = "internal", "Internal"


class ProjectStatus(models.TextChoices):
    PLANNED = "planned", "Planned"
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On Hold"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    ARCHIVED = "archived", "Archived"


class ProjectStage(models.TextChoices):
    INTAKE = "intake", "Proposal request"
    SCOPING = "scoping", "Scoping"
    TRIAGE = "triage", "Triage"
    ESTIMATION = "estimation", "Estimation"
    REVIEW_APPROVAL = "review_approval", "Review & approval"
    PROPOSAL_SENT = "proposal_sent", "Proposal sent"
    KICKOFF = "kickoff", "Team assembly & kick-off"
    DELIVERY = "delivery", "Delivery"


# ──────────────────────────────────────────────────────────────────────────────
# 7. Leave Management
# ──────────────────────────────────────────────────────────────────────────────


class LeaveType(models.TextChoices):
    VACATION = "vacation", "Vacation"
    SICK = "sick", "Sick Leave"
    WFH = "wfh", "Work From Home"
    PERSONAL = "personal", "Personal"
    MATERNITY = "maternity", "Maternity"
    PATERNITY = "paternity", "Paternity"
    BEREAVEMENT = "bereavement", "Bereavement"
    UNPAID = "unpaid", "Unpaid Leave"


class LeaveRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    LEAD_APPROVED = "lead_approved", "Lead Approved"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class LeaveWorkflowStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_REVIEW = "in_review", "In Review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


# ──────────────────────────────────────────────────────────────────────────────
# 8. Performance Reviews
# ──────────────────────────────────────────────────────────────────────────────


class ReviewType(models.TextChoices):
    QUARTERLY = "quarterly", "Quarterly Review"
    MID_YEAR = "mid_year", "Mid-Year Review"
    ANNUAL = "annual", "Annual Review"
    PROBATION = "probation", "Probation Review"
    CUSTOM = "custom", "Custom Review"


class ReviewStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class ReviewOutcome(models.TextChoices):
    EXCEEDS_EXPECTATIONS = "exceeds_expectations", "Exceeds Expectations"
    MEETS_EXPECTATIONS = "meets_expectations", "Meets Expectations"
    PARTIALLY_MEETS = "partially_meets", "Partially Meets Expectations"
    NEEDS_IMPROVEMENT = "needs_improvement", "Needs Improvement"
    UNSATISFACTORY = "unsatisfactory", "Unsatisfactory"


class ReviewNoteVisibility(models.TextChoices):
    SHARED = "shared", "Shared"
    PRIVATE = "private", "Private"


class ActionPointStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class ReminderType(models.TextChoices):
    UPCOMING = "upcoming", "Upcoming"
    DUE_TODAY = "due_today", "Due Today"
    OVERDUE = "overdue", "Overdue"


class ReviewEventType(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    SCHEDULED = "scheduled", "Scheduled"
    RESCHEDULED = "rescheduled", "Rescheduled"
    STATUS_CHANGED = "status_changed", "Status Changed"
    OUTCOME_UPDATED = "outcome_updated", "Outcome Updated"
    NOTE_ADDED = "note_added", "Note Added"
    NOTE_UPDATED = "note_updated", "Note Updated"
    ACTION_POINT_ADDED = "action_point_added", "Action Point Added"
    ACTION_POINT_UPDATED = "action_point_updated", "Action Point Updated"
    ATTACHMENT_ADDED = "attachment_added", "Attachment Added"
    ATTACHMENT_REMOVED = "attachment_removed", "Attachment Removed"
    REMINDER_GENERATED = "reminder_generated", "Reminder Generated"
    REMINDER_READ = "reminder_read", "Reminder Read"


# ──────────────────────────────────────────────────────────────────────────────
# 9. Onboarding / Offboarding
# ──────────────────────────────────────────────────────────────────────────────


class ChecklistType(models.TextChoices):
    ONBOARDING = "onboarding", "Onboarding"
    OFFBOARDING = "offboarding", "Offboarding"


class TaskRole(models.TextChoices):
    HR = "HR", "HR"
    IT = "IT", "IT"
    MANAGER = "Manager", "Manager"


class ChecklistInstanceStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In Progress"
    DONE = "done", "Done"


class ChecklistTaskStatus(models.TextChoices):
    TODO = "todo", "To Do"
    IN_PROGRESS = "in_progress", "In Progress"
    DONE = "done", "Done"


# ──────────────────────────────────────────────────────────────────────────────
# 9.5 Training & Development
# ──────────────────────────────────────────────────────────────────────────────


class ConferenceCourseRegistrationStatus(models.TextChoices):
    REGISTERED = "registered", "Registered"
    ATTENDED = "attended", "Attended"
    CANCELLED = "cancelled", "Cancelled"


# ──────────────────────────────────────────────────────────────────────────────
# 9.6 Internal Mobility & Promotions
# ──────────────────────────────────────────────────────────────────────────────


class JobListingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class ApplicationStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under Review"
    SHORTLISTED = "shortlisted", "Shortlisted"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"
    ACCEPTED = "accepted", "Accepted"


# ──────────────────────────────────────────────────────────────────────────────
# 10. Document Templates
# ──────────────────────────────────────────────────────────────────────────────


class TemplateCategory(models.TextChoices):
    CONTRACT = "contract", "Contract"
    POLICY = "policy", "Policy"
    AGREEMENT = "agreement", "Agreement"
    ONBOARDING = "onboarding", "Onboarding"
    COMPLIANCE = "compliance", "Compliance"
    TRAINING = "training", "Training"
    BENEFITS = "benefits", "Benefits"
    OTHER = "other", "Other"


class TemplateFieldType(models.TextChoices):
    TEXT = "text", "Text"
    DATE = "date", "Date"
    NUMBER = "number", "Number"
    DROPDOWN = "dropdown", "Dropdown"
    CHECKBOX = "checkbox", "Checkbox"
    USER_SELECT = "user_select", "User Select"


class TemplateVisibility(models.TextChoices):
    PRIVATE = "private", "Private"
    SHARED = "shared", "Shared"


class TemplateStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    INACTIVE = "inactive", "Inactive"


# ──────────────────────────────────────────────────────────────────────────────
# 11. API Error Codes
# ──────────────────────────────────────────────────────────────────────────────


class ErrorCode(models.TextChoices):
    NOT_FOUND = "NOT_FOUND", "Not Found"
    FORBIDDEN = "FORBIDDEN", "Forbidden"
    SYSTEM_TEMPLATE_IMMUTABLE = "SYSTEM_TEMPLATE_IMMUTABLE", "System Template Immutable"
    VALIDATION_ERROR = "VALIDATION_ERROR", "Validation Error"
    DUPLICATE_NAME = "DUPLICATE_NAME", "Duplicate Name"
