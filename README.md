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

Runs ruff, black, schema generation, and pytest for changed test files; commit is
blocked if they fail. Run the full pytest suite manually before pushing larger
changes.

```bash
pre-commit install
pre-commit install --hook-type commit-msg
```

### Parallel tests

`pytest` now runs in parallel by default via `pytest-xdist`.

```bash
pytest
```

The repo-local shard runner still exists if you want file-level sharding across
subprocesses:

```bash
python scripts/run_pytest_parallel.py -j 4
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
- Loads 184 permissions from `permissions.csv`
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

## Performance Reviews Module

Structured performance evaluations with automated workflows and comprehensive audit trail.

### Features

- **Review scheduling & management**: Create quarterly, mid-year, annual, probation, or custom reviews with flexible scheduling
- **Shared & private notes**: Employees and reviewers can add secure notes with visibility controls
- **Action points tracking**: Define and track action items with progress, due dates, and ownership
- **Document attachments**: Upload supporting documents (reviews, feedback, forms)
- **CPF & performance fields**: Track CPF progression levels and performance scores (0-100)
- **Reminders**: Automated reminders at configurable intervals before scheduled reviews
- **Complete audit trail**: History events for all changes (review created, updated, notes added, status changed, etc.)
- **Role-based permissions**: Staff/admin users and permission-gated direct reports can manage reviews

### API Endpoints

All endpoints require authentication (`Authorization: Bearer <token>`).

#### Main Review Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/performance-reviews/` | GET, POST | List reviews (filtered by permissions) or create a new review |
| `/api/performance-reviews/{id}/` | GET, PUT, PATCH, DELETE | Retrieve, update, or delete a specific review |
| `/api/performance-reviews/{id}/status/` | PATCH | Update review status (scheduled → in_progress → completed/cancelled) |
| `/api/performance-reviews/summary/` | GET | Get summary metrics (count, status breakdown, recent activity) |

#### Nested Resources (within `/api/performance-reviews/{id}/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/performance-reviews/{id}/notes/` | GET, POST | List/create notes (shared or private) |
| `/api/performance-reviews/{id}/notes/{note_id}/` | GET, PUT, PATCH, DELETE | Manage individual notes |
| `/api/performance-reviews/{id}/action-points/` | GET, POST | List/create action points with tracking |
| `/api/performance-reviews/{id}/action-points/{action_point_id}/` | GET, PUT, PATCH, DELETE | Manage individual action points |
| `/api/performance-reviews/{id}/attachments/` | GET, POST | List/upload document attachments |
| `/api/performance-reviews/{id}/attachments/{attachment_id}/` | GET, DELETE | Download or remove attachments |
| `/api/performance-reviews/{id}/history/` | GET | View full audit trail of changes |

### Example Usage

#### Create a Performance Review

```bash
curl -X POST http://localhost:8000/api/performance-reviews/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "review_type": "quarterly",
    "employee": 2,
    "reviewer": 1,
    "scheduled_date": "2026-05-31",
    "period_start": "2026-01-01",
    "period_end": "2026-03-31",
    "title": "Q1 2026 Review",
    "reminder_offsets_days": [7, 1]
  }'
```

#### Add a Shared Note

```bash
curl -X POST http://localhost:8000/api/performance-reviews/1/notes/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Great work on the project delivery!",
    "visibility": "shared"
  }'
```

#### Add an Action Point

```bash
curl -X POST http://localhost:8000/api/performance-reviews/1/action-points/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Complete advanced Python course",
    "description": "Improve async programming skills",
    "owner": 2,
    "due_date": "2026-06-30"
  }'
```

#### Upload Attachment

```bash
curl -X POST http://localhost:8000/api/performance-reviews/1/attachments/ \
  -H "Authorization: Bearer <token>" \
  -F "file=@feedback.pdf" \
  -F "description=Q1 feedback document"
```

### Interactive API Documentation

View the complete Performance Reviews API documentation:

- **Swagger UI:** http://localhost:8000/api/schema/swagger-ui/ (search for "Performance Reviews")
- **ReDoc:** http://localhost:8000/api/schema/redoc/ (Performance Reviews section)
- **Raw OpenAPI:** http://localhost:8000/api/schema/

---

## Training & Development Module

Comprehensive training and development management for employee skill growth and certificate tracking.

### Features

- **Training Entries**: Log courses, conferences, workshops, webinars, and certifications with cost tracking for budget management
- **Certificates**: Store and manage earned certificates with issue and expiration dates
- **Peer Sessions**: Record peer-to-peer learning sessions with optional duration and incentive linking
- **Training Budget**: Manage annual training budget allocation and spending per employee per fiscal year
- **Cost Tracking**: Automatically track budget consumption through training entries
- **Audit Trail**: Complete created_at/updated_at timestamps on all training records

### Models

#### TrainingEntry
- Employee reference
- Course title and provider
- Training type (course, conference, workshop, webinar, certification, other)
- Training date (required) and optional completion timestamp
- Optional cost for budget tracking
- Description field for notes

#### Certificate
- Employee reference
- Certificate title and issuer
- Uploaded certificate file (stored in R2/Cloudflare)
- Issue date and optional expiration date
- Expiration status property for compliance tracking

#### PeerSession
- Employee reference
- Session topic and date
- Optional duration in minutes
- Placeholder for future incentive program linking
- Description field

#### TrainingBudget
- Employee and fiscal year (unique constraint)
- Allocated budget amount
- Used budget amount (updated through TrainingEntry costs)
- Calculated properties: remaining_budget, budget_percentage_used

### Database Schema

All models follow BloomHub conventions with proper indexes, metadata fields, and cascade deletion. Migration: `core/0028_certificate_peersession_trainingbudget_trainingentry.py`

### API Endpoints

All endpoints require authentication (`Authorization: Bearer <token>`).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/training-entries/` | GET, POST | List training entries (filtered by employee) or create new entry |
| `/api/training-entries/{id}/` | GET, PUT, PATCH, DELETE | Retrieve, update, or delete a training entry |
| `/api/certificates/` | GET, POST | List certificates or upload new certificate |
| `/api/certificates/{id}/` | GET, PUT, PATCH, DELETE | Manage individual certificates |
| `/api/peer-sessions/` | GET, POST | List peer sessions or create new session |
| `/api/peer-sessions/{id}/` | GET, PUT, PATCH, DELETE | Manage individual peer sessions |
| `/api/training-budgets/` | GET | List training budgets (filtered by employee) |
| `/api/training-budgets/{id}/` | GET | Retrieve specific training budget with usage |

### Filtering & Searching

- **TrainingEntry**: Filter by `training_type`, `year` (from training_date). Search by `course_title`, `provider`, `description`
- **Certificate**: Filter by `status` (active, expired). Search by `certificate_title`, `issuer`
- **Pagination**: Default 20 items per page, configurable via `?page_size=`

### Interactive API Documentation

View the complete Training Module API documentation:

- **Swagger UI:** http://localhost:8000/api/schema/swagger-ui/ (search for "Training")
- **ReDoc:** http://localhost:8000/api/schema/redoc/ (Training section)
- **Raw OpenAPI:** http://localhost:8000/api/schema/

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
| `python manage.py spectacular --file schema.yaml` | Generate OpenAPI schema using drf-spectacular |

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

## Jira OAuth 2.0 (per-user 3LO)

BloomHub supports per-user Atlassian OAuth (3LO) for Jira so each employee imports
worklogs through their own access token. The legacy admin API token (configured at
`/api/time-integrations/jira/settings/`) is still used as a fallback for users who
have not connected.

### 1. Register an Atlassian OAuth 2.0 (3LO) app

1. Go to <https://developer.atlassian.com/console/myapps/> and create an
   **OAuth 2.0 integration** (3LO).
2. Add the **Jira API** as a permission and grant the following scopes:
   - `read:jira-work`
   - `read:jira-user`
   - `read:me`
   - `offline_access` (required to receive a refresh token)
3. Under **Authorization**, set the **Callback URL** to the frontend route that will
   forward the `code` + `state` back to the BloomHub API:
   `https://<frontend-host>/oauth/jira/callback`
4. Copy the **Client ID** and **Client Secret** into your `.env`.

### 2. Configure environment variables

```
JIRA_OAUTH_CLIENT_ID=...
JIRA_OAUTH_CLIENT_SECRET=...
JIRA_OAUTH_REDIRECT_URI=https://<frontend-host>/oauth/jira/callback
JIRA_TOKEN_ENCRYPTION_KEY=<long-random-string>
```

`JIRA_TOKEN_ENCRYPTION_KEY` encrypts the access/refresh tokens at rest (Fernet via
`core.services.credential_encryption`). It is **required in production**.

### 3. API surface (under `/api/time-integrations/jira/oauth/`)

All endpoints require JWT/session authentication.

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/authorize/` | Returns `{ authorize_url, state }`. FE redirects user to `authorize_url`. |
| POST   | `/callback/`  | Body: `{ code, state }`. Exchanges code, fetches profile + cloud_id, stores encrypted tokens, returns the connection status payload. |
| GET    | `/status/`    | Returns `{ connected, jira_account_id, jira_email, jira_display_name, cloud_id, site_url, connected_at, token_expires_at, scopes }`. |
| DELETE | `/connection/`| Deletes the local `JiraUserConnection` for the current user (Atlassian 3LO has no remote revoke endpoint). |

One-shot user-triggered sync (outside the OAuth subtree):

| Method | Path | Purpose |
|--------|------|---------|
| POST   | `/api/time-integrations/jira/sync/` | Body (all optional): `date_from`, `date_to` (ISO dates). Pulls + commits the authenticated user's Jira worklogs via their OAuth token. Defaults to last 30 days, or since `last_synced_at` if set. Returns `{ date_from, date_to, counts, batch_id, last_synced_at }`. 401 with `code = "jira_reauth_required"` if no connection or refresh failed. |

### 4. Behaviour

- On callback, the backend exchanges the code at `https://auth.atlassian.com/oauth/token`,
  picks the first site from `accessible-resources` as the user's `cloud_id` + `site_url`,
  and pulls profile data from `https://api.atlassian.com/me`.
- A matching `JiraUserMapping` (jira_account_id → employee) is created automatically
  if the user does not yet have one.
- Worklog imports (`/time-imports/jira/preview/` + `/commit/`) prefer the per-user
  token (base = `https://api.atlassian.com/ex/jira/{cloud_id}`) when
  `filters.employee_id` has a connection, and fall back to the admin token otherwise.
- Access tokens are refreshed transparently when ≤ 60 s from expiry. If Atlassian
  rejects the refresh, the stored tokens are cleared and
  `JiraReauthRequired` propagates as **HTTP 401** with `code = "jira_reauth_required"`,
  signalling the FE to prompt the user to reconnect.
- OAuth `state` is single-use, scoped to the requesting user, and expires after 10 minutes.

## Tempo OAuth 2.0 (per-user)

BloomHub also supports per-user Tempo Cloud OAuth so each employee imports worklogs
through their own Tempo access token. The legacy admin Tempo API token (configured at
`/api/time-integrations/tempo/settings/`) is still used as a fallback for users who
have not connected.

### 1. Register a Tempo OAuth 2.0 application

1. In your Atlassian site, open **Tempo** (Timesheets).
2. Tempo → gear icon → **Settings** → **API integration** → **OAuth 2.0 Applications**
   (or **Data Access** → **OAuth 2.0 Applications** in newer Tempo).
3. **New Application**:
   - **Application type**: `OAuth 2.0`
   - **Redirect URIs**: register all envs (local + dev + prod):
     - `http://localhost:3000/oauth/tempo/callback`
     - `https://bloomhub-fe-dev.vercel.app/oauth/tempo/callback`
   - **Access**: enable Worklogs view access, plus any extra Tempo namespaces
     you plan to import. Do not add `scope` to the authorize URL; current Tempo
     OAuth derives access from the app configuration.
4. Copy the **Client ID** and **Client Secret**.

### 2. Configure environment variables

```
TEMPO_OAUTH_CLIENT_ID=...
TEMPO_OAUTH_CLIENT_SECRET=...
TEMPO_OAUTH_REDIRECT_URI=http://localhost:3000/oauth/tempo/callback
TEMPO_OAUTH_JIRA_URL=https://<your-site>.atlassian.net
```

Tokens are encrypted at rest using the same `JIRA_TOKEN_ENCRYPTION_KEY` /
`CREDENTIAL_ENCRYPTION_KEY` / `SECRET_KEY` chain as Jira.

### 3. API surface (under `/api/time-integrations/tempo/oauth/`)

All endpoints require JWT/session authentication.

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/authorize/` | Returns `{ authorize_url, state }`. FE redirects user to `authorize_url`. |
| POST   | `/callback/`  | Body: `{ code, state }`. Exchanges code, stores encrypted tokens, returns the connection status payload. |
| GET    | `/status/`    | Returns `{ connected, tempo_account_id, tempo_email, tempo_display_name, base_url, connected_at, token_expires_at, scopes }`. |
| DELETE | `/connection/`| Deletes the local `TempoUserConnection` for the current user. |

### 4. Behaviour

- Backend exchanges the code at `https://api.tempo.io/oauth/token/`.
- Worklog imports (`/time-imports/tempo/preview/` + `/commit/`) prefer the per-user
  token when `filters.employee_id` has a Tempo connection, and fall back to the admin
  Tempo API token otherwise.
- Access tokens are refreshed transparently when ≤ 60 s from expiry. If Tempo rejects
  the refresh, the stored tokens are cleared and `TempoReauthRequired` propagates as
  **HTTP 401** with `code = "tempo_reauth_required"`, signalling the FE to prompt the
  user to reconnect.
- OAuth `state` is single-use, scoped to the requesting user, expires after 10 minutes.
