# API Testing Guide

Complete guide for testing all 29 eviStream API endpoints.

---

## 🧪 Test Script: `test_all_endpoints.py`

**Comprehensive automated testing script that:**
- Tests all 29 API endpoints in logical workflow order
- Creates test data automatically
- Shows detailed colored output
- Tracks test statistics (passed/failed/skipped)
- Can be run repeatedly

---

## 🚀 Quick Start

### 1. Start All Services

**Terminal 1 - Redis:**
```bash
redis-server
```

**Terminal 2 - FastAPI Backend:**
```bash
cd /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream/backend
conda activate topics
python -m app.main
```

**Terminal 3 - Celery Worker:**
```bash
cd /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream/backend
conda activate topics
celery -A app.workers.celery_app worker --loglevel=info
```

### 2. Run Tests

**Terminal 4 - Test Script:**
```bash
cd /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream/backend
conda activate topics
python test_all_endpoints.py
```

---

## 📋 What Gets Tested

### Authentication (3 endpoints)
1. ✓ POST `/api/v1/auth/register` - User registration
2. ✓ POST `/api/v1/auth/login` - Login and get JWT token
3. ✓ GET `/api/v1/auth/me` - Get current user info

### Projects (5 endpoints)
4. ✓ POST `/api/v1/projects` - Create project
5. ✓ GET `/api/v1/projects` - List all projects
6. ✓ GET `/api/v1/projects/{id}` - Get specific project
7. ✓ PUT `/api/v1/projects/{id}` - Update project
8. ✓ DELETE `/api/v1/projects/{id}` - Delete project

### Documents (4 endpoints)
9. ✓ POST `/api/v1/documents/upload` - Upload PDF
10. ✓ GET `/api/v1/documents` - List documents
11. ✓ GET `/api/v1/documents/{id}` - Get document
12. ✓ DELETE `/api/v1/documents/{id}` - Delete document

### Forms (5 endpoints)
13. ✓ POST `/api/v1/forms` - Create form (triggers code generation)
14. ✓ GET `/api/v1/forms` - List forms
15. ✓ GET `/api/v1/forms/{id}` - Get form
16. ✓ PUT `/api/v1/forms/{id}` - Update form
17. ✓ DELETE `/api/v1/forms/{id}` - Delete form

### Extractions (4 endpoints)
18. ✓ POST `/api/v1/extractions` - Create extraction
19. ✓ GET `/api/v1/extractions` - List extractions
20. ✓ GET `/api/v1/extractions/{id}` - Get extraction

### Results (5 endpoints)
21. ✓ GET `/api/v1/results` - List all results
22. ✓ GET `/api/v1/results/{id}` - Get specific result
23. ✓ GET `/api/v1/results/{id}/export?format=json` - Export as JSON
24. ✓ GET `/api/v1/results/{id}/export?format=csv` - Export as CSV

**Total: 24 endpoints tested** (cleanup endpoints are optional)

---

## 📊 Expected Output

```
================================================================================
                  eviStream Backend - Complete API Testing Suite
================================================================================

================================================================================
                              HEALTH CHECK
================================================================================

[TEST] GET /health
  Status: 200
  Response: {"status":"healthy"}...

✓ Health check passed - Backend is running!

================================================================================
                    1. AUTHENTICATION - REGISTRATION
================================================================================

[TEST] POST /auth/register
  Status: 201
  Response: {"id":"...","email":"test_user_api@example.com"}...

✓ User registered successfully!

... (continues for all endpoints) ...

================================================================================
                              TEST SUMMARY
================================================================================

Total Tests:   24
Passed:        22
Failed:        0
Skipped:       2

Pass Rate:     91.7%

🎉 ALL TESTS PASSED! 🎉
```

---

## ⚙️ Configuration

Edit the script to customize:

```python
# Base URL (if running on different host/port)
BASE_URL = "http://localhost:8000/api/v1"

# Test user credentials
TEST_USER = {
    "email": "test_user_api@example.com",
    "password": "TestPassword123!",
    "full_name": "API Test User"
}

# Test project
TEST_PROJECT = {
    "name": "API Test Project",
    "description": "Project created by automated API testing script"
}
```

---

## 🎨 Output Features

### Color Coding:
- 🟦 **Blue** - Test being run
- 🟢 **Green** - Success messages
- 🔴 **Red** - Error messages
- 🟡 **Yellow** - Warnings and info
- 🟣 **Purple** - Section headers

### Test Status:
- ✓ - Test passed
- ✗ - Test failed
- ⚠ - Test skipped (dependencies missing)
- ℹ - Information message

---

## 🔍 Test Details

### What Each Test Does:

#### 1. Health Check
- Verifies backend is running
- No authentication needed

#### 2-3. Authentication
- Registers new user (or skips if exists)
- Logs in and obtains JWT token
- Gets user profile

#### 4-8. Projects
- Creates test project
- Lists all projects
- Gets specific project details
- Updates project description
- (Optional) Deletes project

#### 9-12. Documents
- Uploads a dummy PDF file
- Triggers PDF processing job
- Lists documents
- Gets document status
- (Optional) Deletes document

#### 13-17. Forms
- Creates extraction form
- Triggers code generation job
- Lists forms
- Gets form status and schema
- Updates form
- (Optional) Deletes form

#### 18-20. Extractions
- Creates extraction job (needs completed form)
- Triggers extraction worker
- Lists extractions
- Gets extraction status

#### 21-24. Results
- Lists extraction results
- Gets specific result
- Exports result as JSON
- Exports result as CSV

---

## 🐛 Troubleshooting

### "Cannot connect to backend"
**Problem:** Backend not running

**Fix:**
```bash
cd backend
python -m app.main
```

### "Login failed"
**Problem:** User already exists with different password

**Fix:** Either:
1. Change `TEST_USER` email in script
2. Delete user from Supabase database
3. Use correct password

### "Create extraction failed with 400"
**Problem:** Form code generation not complete yet

**Fix:**
1. Wait for code generation to finish (30-60s)
2. Check form status: `GET /api/v1/forms/{id}`
3. Look for `"status": "completed"` and `"schema_name": "..."`
4. Check Celery worker logs

### "Upload document failed"
**Problem:** Storage directory doesn't exist

**Fix:**
```bash
mkdir -p storage/uploads/pdfs
mkdir -p storage/processed/extracted_pdfs
```

### Tests skipped
**Problem:** Previous test failed, dependencies missing

**Fix:**
1. Check which test failed
2. Fix that issue first
3. Re-run tests

---

## 📝 Manual Testing with curl

If you prefer manual testing:

### 1. Register User
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "password123",
    "full_name": "Test User"
  }'
```

### 2. Login
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "password123"
  }'

# Save the token
TOKEN="<your_access_token_here>"
```

### 3. Create Project
```bash
curl -X POST http://localhost:8000/api/v1/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Project",
    "description": "Test project"
  }'
```

### 4. Upload Document
```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "project_id=<project_id>" \
  -F "file=@/path/to/paper.pdf"
```

### 5. Create Form
```bash
curl -X POST http://localhost:8000/api/v1/forms \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<project_id>",
    "form_name": "Patient Population",
    "form_description": "Extract patient data",
    "fields": [
      {
        "field_name": "sample_size",
        "field_type": "number",
        "description": "Number of patients",
        "required": true
      }
    ]
  }'
```

---

## 🌐 Using Swagger UI

The easiest way to test manually:

1. **Open Swagger UI:**
   ```
   http://localhost:8000/api/docs
   ```

2. **Authenticate:**
   - Click "Authorize" button (top right)
   - Login to get token
   - Enter token in format: `Bearer <your_token>`

3. **Test Endpoints:**
   - Expand any endpoint
   - Click "Try it out"
   - Fill in parameters
   - Click "Execute"
   - See response below

---

## 📈 Advanced Testing

### Test Specific Endpoints Only

Edit the script's `run_all_tests()` method:

```python
def run_all_tests(self):
    # Comment out tests you don't want to run
    self.test_health_check()
    self.test_register()
    self.test_login()
    # self.test_create_project()  # Skip this
    # ... etc
```

### Add Custom Tests

Add new test methods:

```python
def test_my_custom_endpoint(self):
    """Test my custom endpoint."""
    print_section("MY CUSTOM TEST")

    response = requests.get(
        f"{BASE_URL}/my/endpoint",
        headers=self.headers
    )

    if response.status_code == 200:
        print_success("Custom test passed!")
        self.update_stats(True)
        return True
    else:
        print_error("Custom test failed!")
        self.update_stats(False)
        return False
```

### Save Test Results

Redirect output to file:

```bash
python test_all_endpoints.py > test_results.txt 2>&1
```

Or save colored output:

```bash
python test_all_endpoints.py | tee test_results.txt
```

---

## ✅ Success Criteria

**All tests should pass when:**
- ✅ Redis is running
- ✅ FastAPI backend is running
- ✅ Celery worker is running
- ✅ Supabase database is configured
- ✅ All required directories exist
- ✅ No previous test data conflicts

**Some tests may be skipped if:**
- ⚠ Background jobs haven't completed yet
- ⚠ Dependencies from previous tests failed
- ⚠ Optional cleanup is disabled

---

## 🎯 Next Steps After Testing

Once all tests pass:

1. **Build Frontend** - Connect React app to these APIs
2. **Load Test** - Test with multiple concurrent users
3. **Integration Tests** - Add pytest integration tests
4. **Deploy** - Deploy to production environment
5. **Monitor** - Set up logging and monitoring

---

## 📞 Support

If tests fail:
1. Check all services are running
2. Check Celery worker logs for errors
3. Check FastAPI logs for errors
4. Verify Supabase configuration
5. Review error messages in test output

---

**Happy Testing! 🧪**
