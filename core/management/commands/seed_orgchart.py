"""
Idempotent seed for org-chart module (BHB-511).

Creates 10 departments (with palette), 30 roles, 30 employees with full
profile fields + manager chain, and 6 projects with active assignments.

Usage:
    python manage.py seed_orgchart
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.enums import (
    EmploymentStatus,
    ProjectAssignmentStatus,
    ProjectType,
)
from core.models import (
    Department,
    Project,
    ProjectAssignment,
    Role,
    UserProfile,
)

User = get_user_model()


DEPARTMENTS = [
    ("Engineering", "#4f46e5", "#eef2ff"),
    ("Design", "#ea580c", "#fff7ed"),
    ("Product", "#7c3aed", "#f5f3ff"),
    ("People", "#16a34a", "#f0fdf4"),
    ("Sales", "#e11d48", "#fff1f2"),
    ("Marketing", "#d97706", "#fffbeb"),
    ("Customer Success", "#0891b2", "#ecfeff"),
    ("Operations", "#475569", "#f1f5f9"),
    ("Finance", "#059669", "#ecfdf5"),
    ("Executive", "#171717", "#f3f4f6"),
]

DEPARTMENT_ALIASES = {
    "C-Suite": "Executive",
    "Institute": "Operations",
}


ROLES = [
    "CEO",
    "CTO",
    "CFO",
    "CPO",
    "CRO",
    "Head of People",
    "Head of Design",
    "Head of Operations",
    "Engineering Manager",
    "DevOps Lead",
    "Mobile Lead",
    "QA Lead",
    "Web Tech Lead",
    "Senior Product Manager",
    "Marketing Manager",
    "Customer Success Lead",
    "Finance Manager",
    "Senior Backend Engineer",
    "Backend Engineer",
    "Frontend Engineer",
    "Junior Frontend Engineer",
    "DevOps Engineer",
    "Android Engineer",
    "Brand Designer",
    "Product Designer",
    "People Partner",
    "Recruiter",
    "Account Executive",
    "Data Analyst",
    "IT Support Specialist",
]


# (seed_id, first, last, email_local, role, dept, manager_email_local,
#  status, is_remote, location, start_date)
EMPLOYEES = [
    (
        1,
        "Hana",
        "Mehić",
        "hana",
        "CEO",
        "Executive",
        None,
        "active",
        False,
        "Sarajevo",
        "2018-01-15",
    ),
    (
        2,
        "Senad",
        "Halilović",
        "senad",
        "CTO",
        "Engineering",
        "hana",
        "active",
        False,
        "Sarajevo",
        "2018-04-02",
    ),
    (
        3,
        "Almir",
        "Begović",
        "almir",
        "CFO",
        "Finance",
        "hana",
        "active",
        False,
        "Sarajevo",
        "2019-02-01",
    ),
    (
        4,
        "Lana",
        "Bukvić",
        "lana",
        "CPO",
        "Product",
        "hana",
        "active",
        False,
        "Sarajevo",
        "2020-03-10",
    ),
    (
        5,
        "Mirsad",
        "Karić",
        "mirsad",
        "CRO",
        "Sales",
        "hana",
        "active",
        False,
        "Banja Luka",
        "2020-09-21",
    ),
    (
        12,
        "Aida",
        "Salihović",
        "aida",
        "Head of People",
        "People",
        "hana",
        "active",
        False,
        "Sarajevo",
        "2019-06-01",
    ),
    (
        18,
        "Asmin",
        "Bašić",
        "asmin",
        "Engineering Manager",
        "Engineering",
        "senad",
        "active",
        False,
        "Sarajevo",
        "2020-09-15",
    ),
    (
        31,
        "Ahmed",
        "Burić",
        "ahmed",
        "DevOps Lead",
        "Engineering",
        "senad",
        "active",
        False,
        "Mostar",
        "2022-11-07",
    ),
    (
        67,
        "Mirza",
        "Kovač",
        "mirza",
        "Mobile Lead",
        "Engineering",
        "senad",
        "on_leave",
        False,
        "Banja Luka",
        "2021-08-23",
    ),
    (
        71,
        "Lejla",
        "Ibrahimović",
        "lejla",
        "QA Lead",
        "Engineering",
        "senad",
        "active",
        False,
        "Sarajevo",
        "2024-01-08",
    ),
    (
        141,
        "Damir",
        "Hamzić",
        "damir",
        "Web Tech Lead",
        "Engineering",
        "senad",
        "active",
        False,
        "Sarajevo",
        "2020-12-10",
    ),
    (
        56,
        "Dženana",
        "Hodžić",
        "dzenana",
        "Head of Design",
        "Design",
        "lana",
        "active",
        False,
        "Sarajevo",
        "2023-04-10",
    ),
    (
        104,
        "Sanja",
        "Đurić",
        "sanja",
        "Senior Product Manager",
        "Product",
        "lana",
        "active",
        False,
        "Sarajevo",
        "2021-04-19",
    ),
    (
        148,
        "Selma",
        "Karić",
        "selma",
        "Marketing Manager",
        "Marketing",
        "mirsad",
        "active",
        False,
        "Sarajevo",
        "2022-05-30",
    ),
    (
        159,
        "Adisa",
        "Smajić",
        "adisa",
        "Customer Success Lead",
        "Customer Success",
        "mirsad",
        "active",
        False,
        "Sarajevo",
        "2023-06-12",
    ),
    (
        174,
        "Ena",
        "Krupalija",
        "ena",
        "Finance Manager",
        "Finance",
        "almir",
        "active",
        False,
        "Sarajevo",
        "2021-01-25",
    ),
    (
        185,
        "Tarik",
        "Pehlić",
        "tarik.p",
        "Head of Operations",
        "Operations",
        "hana",
        "active",
        False,
        "Sarajevo",
        "2020-11-04",
    ),
    (
        42,
        "Tarik",
        "Mujanović",
        "tarik",
        "Senior Backend Engineer",
        "Engineering",
        "asmin",
        "active",
        False,
        "Sarajevo",
        "2022-03-01",
    ),
    (
        92,
        "Vedad",
        "Memić",
        "vedad",
        "Backend Engineer",
        "Engineering",
        "asmin",
        "active",
        False,
        "Sarajevo",
        "2024-09-02",
    ),
    (
        152,
        "Muhamed",
        "Begić",
        "muhamed",
        "Backend Engineer",
        "Engineering",
        "asmin",
        "on_leave",
        False,
        "Tuzla",
        "2021-10-04",
    ),
    (
        23,
        "Hanan",
        "Bajramović",
        "hanan",
        "Frontend Engineer",
        "Engineering",
        "damir",
        "active",
        False,
        "Sarajevo",
        "2023-02-13",
    ),
    (
        125,
        "Tarik",
        "Selimović",
        "tarik.s",
        "Junior Frontend Engineer",
        "Engineering",
        "damir",
        "active",
        True,
        "Sarajevo (remote)",
        "2026-03-15",
    ),
    (
        181,
        "Jasmin",
        "Hodžić",
        "jasmin",
        "DevOps Engineer",
        "Engineering",
        "ahmed",
        "active",
        False,
        "Sarajevo",
        "2019-11-08",
    ),
    (
        112,
        "Kemal",
        "Hadžić",
        "kemal",
        "Android Engineer",
        "Engineering",
        "mirza",
        "active",
        True,
        "Mostar (remote)",
        "2022-07-25",
    ),
    (
        118,
        "Maja",
        "Kapetanović",
        "maja",
        "Brand Designer",
        "Design",
        "dzenana",
        "active",
        False,
        "Sarajevo",
        "2024-03-11",
    ),
    (
        201,
        "Nadina",
        "Šabanović",
        "nadina",
        "Product Designer",
        "Design",
        "dzenana",
        "active",
        False,
        "Sarajevo",
        "2024-11-04",
    ),
    (
        84,
        "Amila",
        "Halilović",
        "amila",
        "People Partner",
        "People",
        "aida",
        "active",
        False,
        "Sarajevo",
        "2024-06-17",
    ),
    (
        133,
        "Ivana",
        "Petrović",
        "ivana",
        "Recruiter",
        "People",
        "aida",
        "active",
        False,
        "Banja Luka",
        "2023-11-20",
    ),
    (
        79,
        "Edin",
        "Šabić",
        "edin",
        "Account Executive",
        "Sales",
        "mirsad",
        "active",
        False,
        "Tuzla",
        "2023-09-04",
    ),
    (
        98,
        "Nedim",
        "Pašić",
        "nedim",
        "Data Analyst",
        "Operations",
        "tarik.p",
        "active",
        False,
        "Sarajevo",
        "2026-02-03",
    ),
    (
        167,
        "Boris",
        "Tomić",
        "boris",
        "IT Support Specialist",
        "Operations",
        "tarik.p",
        "active",
        False,
        "Sarajevo",
        "2024-08-19",
    ),
]


# (project_name, status, member_email_locals)
PROJECTS = [
    ("Project Atlas", "active", ["tarik", "asmin", "vedad", "senad", "hanan", "damir"]),
    (
        "Mercury Internal Tools",
        "active",
        ["asmin", "hanan", "damir", "tarik.s", "jasmin"],
    ),
    ("Horizon Mobile App", "active", ["mirza", "kemal", "senad", "lejla"]),
    ("Orion Data Warehouse", "on_hold", ["ahmed", "nedim", "tarik.p", "boris"]),
    ("Phoenix Billing Engine", "active", ["muhamed", "tarik", "ena", "almir"]),
    ("Pioneer QA Automation", "active", ["lejla", "ahmed", "jasmin"]),
    (
        "Brand Refresh 2026",
        "active",
        ["dzenana", "maja", "nadina", "lana", "sanja", "selma"],
    ),
    ("Hiring Pipeline Q2", "active", ["aida", "amila", "ivana", "hana"]),
    ("Sales Enablement", "active", ["mirsad", "edin", "adisa"]),
]


class Command(BaseCommand):
    help = "Idempotent org-chart seed: depts, roles, employees, projects."

    @transaction.atomic
    def handle(self, *args, **options):
        self._merge_dept_aliases()
        depts = self._seed_departments()
        roles = self._seed_roles()
        profiles_by_email = self._seed_employees(depts, roles)
        self._link_managers(profiles_by_email)
        self._seed_projects(profiles_by_email)
        self.stdout.write(
            self.style.SUCCESS(
                f"Org-chart seed complete: "
                f"{len(depts)} depts, {len(roles)} roles, "
                f"{len(profiles_by_email)} employees, "
                f"{len(PROJECTS)} projects."
            )
        )

    # ── Departments ────────────────────────────────────────────────────

    def _merge_dept_aliases(self):
        for old_name, new_name in DEPARTMENT_ALIASES.items():
            old = Department.objects.filter(name=old_name).first()
            if not old:
                continue
            new, _ = Department.objects.get_or_create(name=new_name)
            UserProfile.objects.filter(department_fk=old).update(department_fk=new)
            UserProfile.objects.filter(department=old_name).update(department=new_name)
            old.delete()

    def _seed_departments(self) -> dict[str, Department]:
        out: dict[str, Department] = {}
        for name, color, color_soft in DEPARTMENTS:
            dept, _ = Department.objects.update_or_create(
                name=name,
                defaults={"color": color, "color_soft": color_soft},
            )
            out[name] = dept
        return out

    # ── Roles ──────────────────────────────────────────────────────────

    def _seed_roles(self) -> dict[str, Role]:
        out: dict[str, Role] = {}
        for name in ROLES:
            role, _ = Role.objects.get_or_create(name=name)
            out[name] = role
        return out

    # ── Employees ──────────────────────────────────────────────────────

    def _phone_for(self, seed_id: int) -> str:
        return f"+387 61 200 {seed_id:03d}"

    def _email_for(self, local: str) -> str:
        return f"{local}@bloomteq.com"

    def _seed_employees(
        self,
        depts: dict[str, Department],
        roles: dict[str, Role],
    ) -> dict[str, UserProfile]:
        out: dict[str, UserProfile] = {}
        for (
            seed_id,
            first,
            last,
            local,
            role_name,
            dept_name,
            _mgr,
            status,
            is_remote,
            location,
            start_date,
        ) in EMPLOYEES:
            email = self._email_for(local)
            status_enum = (
                EmploymentStatus.ON_LEAVE
                if status == "on_leave"
                else EmploymentStatus.ACTIVE
            )

            user, _ = User.objects.update_or_create(
                username=email,
                defaults={
                    "email": email,
                    "first_name": first,
                    "last_name": last,
                    "is_active": True,
                },
            )

            profile, _ = UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "full_name": f"{first} {last}",
                    "email_address": email,
                    "phone_number": self._phone_for(seed_id),
                    "address": location,
                    "start_date": date.fromisoformat(start_date),
                    "hire_date": date.fromisoformat(start_date),
                    "department": dept_name,
                    "department_fk": depts[dept_name],
                    "role": roles[role_name],
                    "employment_status": status_enum,
                    "is_remote": is_remote,
                },
            )
            out[local] = profile
        return out

    def _link_managers(self, by_email: dict[str, UserProfile]) -> None:
        for (
            _id,
            _first,
            _last,
            local,
            _role,
            _dept,
            mgr_local,
            _status,
            _remote,
            _loc,
            _start,
        ) in EMPLOYEES:
            profile = by_email[local]
            if mgr_local is None:
                profile.primary_manager = None
                profile.save(update_fields=["primary_manager"])
                profile.managers.clear()
                continue
            manager = by_email[mgr_local]
            profile.primary_manager = manager
            profile.save(update_fields=["primary_manager"])
            profile.managers.set([manager])

    # ── Projects ───────────────────────────────────────────────────────

    def _seed_projects(self, by_email: dict[str, UserProfile]) -> None:
        for name, status, members in PROJECTS:
            project, _ = Project.objects.update_or_create(
                name=name,
                defaults={
                    "status": status,
                    "project_type": (
                        ProjectType.INTERNAL
                        if "Internal" in name or "QA" in name
                        else ProjectType.CLIENT
                    ),
                    "client": "Bloomteq" if "Internal" in name else "Acme Corp",
                    "start_date": date(2024, 1, 1),
                },
            )

            member_profiles = [by_email[m] for m in members]
            alloc = max(1, 100 // max(1, len(member_profiles)))

            existing = {a.user_profile_id: a for a in project.assignments.all()}
            keep_ids = {p.id for p in member_profiles}

            for p in member_profiles:
                ProjectAssignment.objects.update_or_create(
                    project=project,
                    user_profile=p,
                    defaults={
                        "start_date": date(2024, 1, 1),
                        "end_date": None,
                        "status": ProjectAssignmentStatus.ACTIVE,
                        "allocation_percentage": alloc,
                    },
                )
            # Remove assignments not in current member list.
            for uid, assignment in existing.items():
                if uid not in keep_ids:
                    assignment.delete()
