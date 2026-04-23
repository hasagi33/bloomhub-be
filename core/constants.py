# core/constants.py

REGISTER_FIELDS = [
    "username",
    "email",
    "password",
    "password_confirm",
    "first_name",
    "last_name",
    "avatar",
    "avatar_url",
]

REGISTER_EXTRA_KWARGS = {
    "email": {"required": True},
}

EMPLOYEE_PROFILE_FIELDS = [
    "id",
    "username",
    "employee_id",
    "email",
    "first_name",
    "last_name",
    "full_name",
    "email_address",
    "department",
    "role",
    "role_name",
    "managers",
    "manager_names",
    "start_date",
    "phone_number",
    "emergency_contact_phone",
    "address",
    "employment_status",
    "avatar",
    "is_active",
    "cpf_level",
    "career_level",
    "salary",
    "current_salary",
    "tech_tags",
    "permissions_bitmap",
    "assigned_projects",
]

EMPLOYEE_PROFILE_READ_ONLY_FIELDS = [
    "id",
    "username",
    "is_active",
    "permissions_bitmap",
]

EMPLOYEE_PROFILE_FILTERSET_FIELDS = [
    "role__name",
    "department",
    "is_active",
    "employment_status",
]

EMPLOYEE_PROFILE_SEARCH_FIELDS = [
    "full_name",
    "email_address",
    "user__username",
    "employee_id",
]

EMPLOYEE_PROFILE_ORDERING_FIELDS = ["full_name", "start_date", "created_at"]
