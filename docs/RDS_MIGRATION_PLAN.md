# AWS RDS Migration Plan

**Status:** Planned for before production launch
**Current:** Using Supabase for Phase 1 testing
**Target:** AWS RDS PostgreSQL for production

---

## Why Migrate to RDS?

1. ✅ **AWS Ecosystem** - Everything in one place (ECS, S3, Redis, RDS)
2. ✅ **HIPAA Compliance** - Healthcare data regulations
3. ✅ **Cost at Scale** - More economical for production workloads
4. ✅ **Full Control** - Fine-tune PostgreSQL configuration
5. ✅ **No Vendor Lock-in** - Standard PostgreSQL

---

## Timeline

**Phase 1 (Current):** Supabase for testing (2-3 weeks)
**Phase 2:** Migrate to RDS before production deployment (2-3 hours work)

---

## Changes Required

### 1. Infrastructure Setup (AWS Console or Terraform)

**Create RDS Instance:**
```hcl
# Terraform example (or use AWS Console)
resource "aws_db_instance" "evistream" {
  identifier           = "evistream-production"
  engine               = "postgres"
  engine_version       = "15.4"
  instance_class       = "db.t3.micro"  # Start small, scale up
  allocated_storage    = 20
  storage_encrypted    = true

  db_name  = "evistream"
  username = "evistream_admin"
  password = random_password.db_password.result

  vpc_security_group_ids = [aws_security_group.db.id]
  db_subnet_group_name   = aws_db_subnet_group.private.name

  backup_retention_period = 7
  skip_final_snapshot     = false
  final_snapshot_identifier = "evistream-final-${timestamp()}"

  tags = {
    Name        = "evistream-production"
    Environment = "production"
  }
}
```

**Cost:** ~$15-30/month for t3.micro

---

### 2. Code Changes

#### A. Add SQLAlchemy Dependencies

**backend/requirements.txt:**
```txt
# Add these lines
sqlalchemy==2.0.25
psycopg2-binary==2.9.9
alembic==1.13.1

# Remove (or keep for backward compatibility during migration)
# supabase==2.3.0
```

#### B. Create Database Models

**backend/app/models/database.py:**
```python
"""
SQLAlchemy database models.
"""

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Integer, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    projects = relationship("Project", back_populates="user")


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="projects")
    documents = relationship("Document", back_populates="project")
    forms = relationship("Form", back_populates="project")


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    unique_filename = Column(String(255))
    s3_pdf_path = Column(String(512))
    s3_markdown_path = Column(String(512))
    processing_status = Column(String(50), default="pending")
    processing_error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="documents")


class Form(Base):
    __tablename__ = "forms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    form_name = Column(String(255), nullable=False)
    form_description = Column(Text)
    fields = Column(JSON, nullable=False)
    status = Column(String(50), default="draft")
    schema_name = Column(String(255))
    task_dir = Column(String(512))
    statistics = Column(JSON)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="forms")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"))
    job_type = Column(String(50), nullable=False)
    status = Column(String(50), default="pending")
    progress = Column(Integer, default=0)
    celery_task_id = Column(String(255))
    input_data = Column(JSON)
    result_data = Column(JSON)
    error_message = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
```

#### C. Update Database Connection

**backend/app/database.py:**
```python
"""
Database connection and session management.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

from app.config import settings

# Create engine
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.DEBUG
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """
    Dependency for getting database session.

    Usage:
        @router.get("/users")
        def get_users(db: Session = Depends(get_db)):
            return db.query(User).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """
    Context manager for database session.

    Usage:
        with get_db_context() as db:
            user = db.query(User).first()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

#### D. Update Configuration

**backend/app/config.py:**
```python
# Replace Supabase settings with:
DATABASE_URL: str  # Format: postgresql://user:pass@host:5432/dbname

# Remove these:
# SUPABASE_URL: str
# SUPABASE_KEY: str
# SUPABASE_SERVICE_KEY: str
```

**backend/.env:**
```bash
# Replace Supabase with RDS
DATABASE_URL=postgresql://evistream_admin:your-password@your-rds-endpoint.rds.amazonaws.com:5432/evistream

# Remove Supabase vars
# SUPABASE_URL=...
# SUPABASE_KEY=...
# SUPABASE_SERVICE_KEY=...
```

#### E. Update Auth Endpoints

**Before (Supabase):**
```python
from supabase import create_client

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# Register
result = supabase.table("users").insert({
    "email": user_data.email,
    "hashed_password": hashed_password,
    "full_name": user_data.full_name,
    "is_active": True
}).execute()
user = result.data[0]
```

**After (SQLAlchemy):**
```python
from sqlalchemy.orm import Session
from app.models.database import User
from app.dependencies import get_db

# Register
@router.post("/register")
async def register(user_data: UserRegister, db: Session = Depends(get_db)):
    # Check if user exists
    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    user = User(
        email=user_data.email,
        hashed_password=auth_service.hash_password(user_data.password),
        full_name=user_data.full_name,
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Generate token
    token = auth_service.create_access_token(user.id)
    return Token(access_token=token, token_type="bearer", user_id=user.id)
```

#### F. Update Dependencies

**backend/app/dependencies.py:**
```python
from sqlalchemy.orm import Session
from app.database import get_db

# Add database dependency (already shown above)
# Update get_current_user to optionally accept db session
```

---

### 3. Database Migrations with Alembic

**Initialize Alembic:**
```bash
cd backend
alembic init alembic
```

**Configure alembic.ini:**
```ini
sqlalchemy.url = postgresql://user:pass@host:5432/dbname
```

**Create initial migration:**
```bash
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

**For future schema changes:**
```bash
alembic revision --autogenerate -m "Add new table"
alembic upgrade head
```

---

### 4. Data Migration (If Needed)

If you have test data in Supabase to migrate:

```python
# migration_script.py
from supabase import create_client
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.database import User

# Connect to both
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
db = SessionLocal()

# Migrate users
users = supabase.table("users").select("*").execute()
for user_data in users.data:
    user = User(
        id=user_data["id"],
        email=user_data["email"],
        hashed_password=user_data["hashed_password"],
        full_name=user_data.get("full_name"),
        is_active=user_data.get("is_active", True),
        created_at=user_data["created_at"]
    )
    db.add(user)

db.commit()
```

---

## Migration Checklist

- [ ] Create AWS RDS instance
- [ ] Configure security groups (allow ECS access)
- [ ] Update requirements.txt
- [ ] Create database.py with SQLAlchemy models
- [ ] Update config.py
- [ ] Refactor auth endpoints
- [ ] Initialize Alembic
- [ ] Create initial migration
- [ ] Test locally with RDS
- [ ] Migrate any existing data
- [ ] Update all CRUD endpoints
- [ ] Update tests
- [ ] Deploy to production

---

## Testing After Migration

```bash
# 1. Update .env with RDS connection
DATABASE_URL=postgresql://...

# 2. Run migrations
alembic upgrade head

# 3. Start server
python -m app.main

# 4. Test endpoints
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/v1/auth/register ...
```

---

## Rollback Plan

If migration fails:

1. Keep Supabase credentials in .env as backup
2. Git revert to previous commit
3. Restart server with Supabase
4. Debug RDS issues separately

---

## Estimated Effort

**Total time:** 2-3 hours

- Infrastructure setup: 30 min
- Code refactoring: 1-2 hours
- Testing: 30 min
- Data migration (if needed): 30 min

---

## When to Migrate?

**Trigger points:**
- ✅ Phase 1 testing complete
- ✅ Authentication working
- ✅ Ready to implement CRUD endpoints
- ✅ Before production deployment
- ✅ Need HIPAA compliance
- ✅ Supabase costs exceeding RDS

**Recommended:** Migrate after Phase 1 testing, before implementing full CRUD operations.

---

## Benefits After Migration

1. ✅ All infrastructure on AWS
2. ✅ Better performance (VPC networking)
3. ✅ HIPAA compliance ready
4. ✅ Full PostgreSQL control
5. ✅ Cost-effective at scale
6. ✅ Standard ORM patterns (SQLAlchemy)
7. ✅ Easy to add other ORMs or tools

---

## Questions?

When you're ready to migrate, I'll help with:
- Creating the RDS instance
- Refactoring all endpoints
- Setting up Alembic migrations
- Testing the migration
- Deploying to production

For now, continue testing with Supabase! 🚀
