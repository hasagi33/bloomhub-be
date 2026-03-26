# BloomHub Backend (Django)

Django backend with tests, formatting, and commit convention `[BHB-XX]`.

---

## Local setup

### Prerequisites

- Python 3.11+ (3.12 recommended)

### Run locally

```bash
git clone <repo-url>
cd BloomHub-be
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env .env
```

Edit `.env`:

- **SQLite (easiest):** leave `DATABASE_URL` unset. The app uses `db.sqlite3` in the project root.
- **Local Postgres:** set `DATABASE_URL=postgres://user:password@localhost:5432/yourdb` (see below to run Postgres).

### Local PostgreSQL (optional)

Use Postgres when you need the same DB as staging/production (e.g. Neon). Pick one option.

**With Docker (Mac / Linux / Windows)** — same everywhere:

```bash
docker run -d --name bloomhub-db -e POSTGRES_USER=bloomhub -e POSTGRES_PASSWORD=bloomhub -e POSTGRES_DB=bloomhub -p 5432:5432 postgres:16
```

Then in `.env`:

```env
DATABASE_URL=postgres://bloomhub:bloomhub@localhost:5432/bloomhub
```

Stop/start later: `docker stop bloomhub-db` / `docker start bloomhub-db`.

**Without Docker**

| OS | How to install | Start / create DB |
|----|----------------|-------------------|
| **macOS** | `brew install postgresql@16` then `brew services start postgresql@16` | `createdb bloomhub` (uses your OS user; set `DATABASE_URL=postgres://localhost:5432/bloomhub` or add a user with `createuser`) |
| **Linux (Debian/Ubuntu)** | `sudo apt update && sudo apt install postgresql postgresql-contrib` | `sudo -u postgres createuser -P bloomhub` then `sudo -u postgres createdb -O bloomhub bloomhub`; `DATABASE_URL=postgres://bloomhub:<password>@localhost:5432/bloomhub` |
| **Linux (RHEL/Fedora)** | `sudo dnf install postgresql-server postgresql-contrib` then `sudo postgresql-setup --initdb` and `sudo systemctl start postgresql` | Same as Debian: create user and DB, then set `DATABASE_URL`. |
| **Windows** | [Postgres installer](https://www.postgresql.org/download/windows/) or `winget install PostgreSQL.PostgreSQL` | After install, create DB via pgAdmin or `psql -U postgres -c "CREATE USER bloomhub WITH PASSWORD 'bloomhub'; CREATE DATABASE bloomhub OWNER bloomhub;"` then `DATABASE_URL=postgres://bloomhub:bloomhub@localhost:5432/bloomhub`. |

Then:

**SQLite** (no `DATABASE_URL`):

```bash
python manage.py migrate
python manage.py runserver
```

**PostgreSQL** (with `DATABASE_URL` set): the app uses django-tenants. Run schema migrations and create the public tenant so **localhost** and **127.0.0.1** both work:

```bash
python manage.py migrate_schemas --shared
python manage.py setup_public_tenant
python manage.py runserver
```

Open http://localhost:8000/ or http://127.0.0.1:8000/

The root URL (`/`) provides API information and available endpoints. Interactive API docs:

- **Swagger UI:** http://localhost:8000/api/schema/swagger-ui/
- **ReDoc:** http://localhost:8000/api/schema/redoc/
- **OpenAPI schema:** http://localhost:8000/api/schema/

## Troubleshooting

### "No tenant for hostname localhost" (404 when using Postgres locally)

When using PostgreSQL, the app uses django-tenants and resolves tenants by hostname. Run once so localhost is available:

```bash
python manage.py setup_public_tenant
```

This creates the public tenant and domains for `localhost` and `127.0.0.1`. Safe to run multiple times.

### 404 on deployed dev (e.g. bloomhub-be.onrender.com)

With Postgres + django-tenants, every hostname must be registered as a domain for the public tenant. On Render free tier (no Shell/Pre-Deploy), use one of these:

1. **GitHub Actions (recommended):** In the repo go to **Settings → Secrets and variables → Actions**, add a secret **DEV_PUBLIC_DOMAIN** with value `bloomhub-be.onrender.com`. Push to `main` (or re-run the Deploy Dev workflow); the workflow will run `setup_public_tenant --domain bloomhub-be.onrender.com` and the 404 will go away.

2. **One-off from your machine:** Copy the dev database URL from Render (Environment), then locally run:
   ```bash
   DATABASE_URL='postgres://...' python manage.py setup_public_tenant --domain bloomhub-be.onrender.com
   ```
   The domain is stored in the DB, so you only need to do this once per hostname.


---

## Authentication API

The backend uses JWT (JSON Web Tokens) for authentication via Django REST Framework.

### Endpoints

- `POST /api/auth/register/` - Register a new user
- `POST /api/auth/login/` - Login with email/password and get tokens
- `POST /api/auth/refresh/` - Refresh access token using refresh token
- `POST /api/auth/logout/` - Logout (blacklist refresh token)
- `GET /api/auth/profile/` - Get current user profile (requires authentication)
- `POST /api/admin/upload-role-permissions/` - Upload CSV to manage role permissions (admin only)

### Authentication

Include the access token in the `Authorization` header:
```
Authorization: Bearer <access_token>
```

### Example Usage

#### Register
```bash
curl -X POST http://127.0.0.1:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@example.com",
    "password": "password123",
    "password_confirm": "password123",
    "first_name": "Test",
    "last_name": "User"
  }'
```

#### Login
```bash
curl -X POST http://127.0.0.1:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "password123"
  }'
```

Response includes `access`, `refresh` tokens, and `user` data.

#### Refresh Token
```bash
curl -X POST http://127.0.0.1:8000/api/auth/refresh/ \
  -H "Content-Type: application/json" \
  -d '{"refresh": "<refresh_token>"}'
```

#### Access Protected Endpoint
```bash
curl -H "Authorization: Bearer <access_token>" \
  http://127.0.0.1:8000/api/auth/profile/
```

#### Upload Role Permissions (Admin Only)
```bash
curl -X POST http://127.0.0.1:8000/api/admin/upload-role-permissions/ \
  -H "Authorization: Bearer <admin_access_token>" \
  -F "file=@example.csv"
```

---

## Employee Profile Module

The backend includes a comprehensive employee profile system with role-based access.

### Features

- **Role-based authentication**: Employee, Manager, HR Admin, Super Admin
- **Extended profiles**: Personal info (address, emergency contact, birthday), career progression (CPF level), tech stacks
- **Document management**: CV uploads with versioning
- **Tracking**: Project assignments, equipment assignments, salary history (HR/Admin only)
- **Audit**: Change logs for key fields (role, salary, CPF level)

### Loading Data

- Load base permissions: `python manage.py load_permissions permissions.csv`
- Load role permissions: `python manage.py load_role_permissions example.csv`
- Manage all data via Django Admin at `/admin/`

### Pre-commit (optional)

Runs ruff, black, and pytest on every commit; commit is blocked if they fail.

```bash
pre-commit install
pre-commit install --hook-type commit-msg
```

---

## Role & Permission Management System

BloomHub implements a comprehensive role-based access control (RBAC) system with dynamic permission management through CSV uploads.

### Core Components

#### Data Models
- **Permission**: Module-specific actions (183 total permissions across Employee Profiles, Vacations, Reviews, etc.)
- **Role**: Named permission sets (e.g., "EMP", "SUPER_ADMIN") with many-to-many permission relationships
- **UserProfile**: User roles + additional bitmap-based permissions for fine-grained control

#### Permission Storage
- **Roles**: Many-to-many relationship (unlimited permissions per role)
- **Users**: 64-bit bitmap for additional permissions beyond role assignments
- **Combined checking**: Users inherit role permissions + have personal overrides

### CSV Upload System

#### File Format
```csv
role_id,module_name,feature_action,permission,operation_type
EMP,Employee Profiles,view_own_profile,YES,override
EMP,Vacations,submit_leave_request,YES,add
```

#### Supported Operations

##### OVERRIDE (Replace Entire Role)
- **Purpose**: Complete role redefinition
- **Action**: Replaces all role permissions with CSV YES entries
- **Use Case**: Full role reset from scratch

##### ADD (Grant Additional Permissions)
- **Purpose**: Add permissions without removing existing ones
- **Action**: Adds YES permissions to current role permissions
- **Use Case**: Granting new access rights

##### REMOVE (Revoke Specific Permissions)
- **Purpose**: Remove specific permissions
- **Action**: Removes YES permissions from the role
- **Use Case**: Revoking access to specific features

##### MERGE (Intelligent Update)
- **Purpose**: Selective permission updates
- **Action**: Updates permissions listed in CSV, preserves others
- **Use Case**: Targeted permission changes with customization preservation

### API Endpoints

#### Upload Role Permissions
```bash
POST /api/admin/upload-role-permissions/
Authorization: Bearer <admin_token>
Content-Type: multipart/form-data

file: <csv_file>
```

**Response**:
```json
{
  "message": "Role permissions uploaded and processed successfully",
  "file_path": "uploads/role_permissions/example.csv"
}
```

**Requirements**:
- Admin access (`is_staff` or `is_superuser`)
- CSV format with required columns
- File stored in `media/uploads/role_permissions/`

### Management Commands

#### Load Base Permissions
```bash
python manage.py load_permissions permissions.csv
```
- Loads 183 permissions from `permissions.csv`
- Creates Permission objects with auto-assigned bit positions

#### Load Role Permissions
```bash
python manage.py load_role_permissions example.csv
```
- Processes role-permission assignments
- Supports all operations: override, add, remove, merge
- Updates role permission relationships

### Setup Instructions

1. **Load Base Permissions**:
   ```bash
   python manage.py load_permissions permissions.csv
   ```

2. **Load Role Permissions**:
   ```bash
   python manage.py load_role_permissions example.csv
   ```

3. **Create Admin User**:
   ```bash
   python manage.py createsuperuser
   ```

4. **Assign SUPER_ADMIN Role** (via Django Admin or shell):
   ```python
   from core.models import UserProfile, Role
   user = User.objects.get(username='admin')
   role = Role.objects.get(name='SUPER_ADMIN')
   user.profile.role = role
   user.profile.save()
   ```

### Permission Checking

In views/serializers, check permissions like:
```python
from core.models import Permission

def some_view(request):
    view_permission = Permission.objects.get(
        module_name='Employee Profiles',
        feature_action='view_own_profile'
    )
    
    if request.user.profile.has_permission(view_permission):
        # Allow access
        return Response(data)
    else:
        return Response({'error': 'Permission denied'}, status=403)
```

### File Storage

- **Directory**: `media/uploads/role_permissions/`
- **Access**: `/media/uploads/role_permissions/filename.csv`
- **Production**: Configured for cloud storage (R2/Cloudflare)

### Security Features

- **Admin-only uploads**: Requires staff or superuser status
- **File validation**: CSV format and required columns
- **Error handling**: Detailed error messages for invalid data
- **Audit trail**: Upload history preserved in media directory

### Example Usage

#### Create Custom Role
1. Create CSV with desired permissions:
   ```csv
   role_id,module_name,feature_action,permission,operation_type
   MANAGER,Employee Profiles,view_team_profiles,YES,override
   MANAGER,Vacations,approve_team_requests,YES,override
   ```

2. Upload via API or command:
   ```bash
   python manage.py load_role_permissions custom_role.csv
   ```

#### Add Permission to Existing Role
```csv
role_id,module_name,feature_action,permission,operation_type
EMP,New Module,new_feature,YES,add
```

#### Remove Permission from Role
```csv
role_id,module_name,feature_action,permission,operation_type
EMP,Old Module,old_feature,YES,remove
```

This system provides flexible, granular permission management while maintaining performance and security.

---

## PR labels (GitStream)

Labels are applied automatically by [gitStream](https://gitstream.cm) based on the PR contents:

| Label | Meaning |
|-------|--------|
| `invalid-pr-title` | PR title does not follow `[BHB-XX] description` (e.g. `[BHB-42] Add user auth`) |
| `missing-tests` | No test-related files in the PR (no `test_*`, `*_test.py`, or paths under `tests/`) |
| `deleted-files` | PR includes one or more file deletions |
| `docs-only` | All changed files are documentation only |
| `migrations` | PR touches migration files under `migrations/` and also includes test files |
| `migrations + missing-tests` | PR has migration files but no test files (only one of these two applies per PR) |
| `python` | PR includes at least one `.py` file |

---

## Scripts

| Command | Description |
|--------|-------------|
| `ruff check .` | Lint |
| `black .` / `black --check .` | Format / check format |
| `pytest` | Run tests |
| `python manage.py load_permissions <csv_file>` | Load base permissions from CSV (expects `module_name,feature_action` headers) |
| `python manage.py load_role_permissions <csv_file>` | Load role permissions from CSV with operations (override/add/remove/merge) |
| `python manage.py spectacular --file schema.yml` | Generate OpenAPI schema using drf-spectacular |

   user.profile.save()
   `

### Permission Checking

In views/serializers, check permissions like:
`python
from core.models import Permission

def some_view(request):
    view_permission = Permission.objects.get(
        module_name='Employee Profiles',
        feature_action='view_own_profile'
    )
    
    if request.user.profile.has_permission(view_permission):
        # Allow access
        return Response(data)
    else:
        return Response({'error': 'Permission denied'}, status=403)
``n
### File Storage

- **Directory**: media/uploads/role_permissions/`n- **Access**: /media/uploads/role_permissions/filename.csv`n- **Production**: Configured for cloud storage (R2/Cloudflare)

### Security Features

- **Admin-only uploads**: Requires staff or superuser status
- **File validation**: CSV format and required columns
- **Error handling**: Detailed error messages for invalid data
- **Audit trail**: Upload history preserved in media directory

### Example Usage

#### Create Custom Role
1. Create CSV with desired permissions:
   `csv
   role_id,module_name,feature_action,permission,operation_type
   MANAGER,Employee Profiles,view_team_profiles,YES,override
   MANAGER,Vacations,approve_team_requests,YES,override
   `

2. Upload via API or command:
   `ash
   python manage.py load_role_permissions custom_role.csv
   `

#### Add Permission to Existing Role
`csv
role_id,module_name,feature_action,permission,operation_type
EMP,New Module,new_feature,YES,add
`

#### Remove Permission from Role
`csv
role_id,module_name,feature_action,permission,operation_type
EMP,Old Module,old_feature,YES,remove
`

This system provides flexible, granular permission management while maintaining performance and security.

---

## Scripts

| Command | Description |
|--------|-------------|
| 
uff check . | Lint |
| lack . / lack --check . | Format / check format |
| pytest | Run tests |
| python manage.py load_permissions <csv_file> | Load base permissions from CSV (expects module_name,feature_action headers) |
| python manage.py load_role_permissions <csv_file> | Load role permissions from CSV with operations (override/add/remove/merge) |

---

## PR labels (GitStream)

Labels are applied automatically by [gitStream](https://gitstream.cm) based on the PR contents:

| Label | Meaning |
|-------|--------|
| invalid-pr-title | PR title does not follow [BHB-XX] description (e.g. [BHB-42] Add user auth) |
| missing-tests | No test-related files in the PR (no 	est_*, *_test.py, or paths under 	ests/) |
| deleted-files | PR includes one or more file deletions |
| docs-only | All changed files are documentation only |
| migrations | PR touches migration files under migrations/ and also includes test files |
| migrations + missing-tests | PR has migration files but no test files (only one of these two applies per PR) |
| python | PR includes at least one .py file |
