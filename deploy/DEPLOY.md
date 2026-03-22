# eviStream Demo Deployment Guide

Complete guide to deploy eviStream on a single AWS EC2 instance. Designed to be executed by Claude Code on the EC2 instance.

---

## What to Copy to EC2

```bash
# From your dev machine, copy these 3 things:
scp -r backend/ frontend/ CLAUDE.md user@<EC2_IP>:~/evistream/
```

EC2 directory structure:
```
~/evistream/
├── CLAUDE.md
├── backend/
│   ├── deploy/        ← setup scripts, nginx, redis configs, this file
│   ├── app/
│   ├── core/
│   └── ...
└── frontend/
```

---

## EC2 Instance Requirements

| Setting | Value |
|---------|-------|
| Instance type | `t3.xlarge` (4 vCPU, 16 GB RAM) |
| AMI | Ubuntu 22.04 LTS |
| Storage | 50 GB gp3 EBS |
| Security group | Inbound: 22 (SSH), 80 (HTTP), 443 (HTTPS) |
| Networking | Allocate and attach an Elastic IP |

Estimated cost: ~$120/month on-demand, ~$50/month with spot instance.

---

## Phase 1: Manual Setup (do this before Claude Code)

SSH into your EC2 instance and run these commands:

```bash
# 1. Install Node.js 22
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# 2. Verify
node --version   # should be v22.x
npm --version    # should be 11.x

# 3. Install Claude Code
npm install -g @anthropic-ai/claude-code

# 4. Copy backend/, frontend/, CLAUDE.md to ~/evistream/ (scp or git clone)
cd ~/evistream

# 5. Launch Claude Code
claude
```

---

## Phase 2: Claude Code Prompt

Copy-paste this into Claude Code on the EC2 instance. Fill in your actual values first:

```
Follow backend/deploy/DEPLOY.md to set up eviStream on this EC2 instance. Here are my configuration values:

EC2_PUBLIC_IP: <YOUR_ELASTIC_IP>

SUPABASE_URL: <your-supabase-url>
SUPABASE_KEY: <your-anon-key>
SUPABASE_SERVICE_KEY: <your-service-role-key>

AWS_ACCESS_KEY_ID: <your-aws-key>
AWS_SECRET_ACCESS_KEY: <your-aws-secret>
S3_BUCKET: evistream-production

ANTHROPIC_API_KEY: <your-anthropic-key>
OPENAI_API_KEY: <your-openai-key>
GEMINI_API_KEY: <your-gemini-key>

Execute the following steps in order:

1. Run the setup script: sudo bash backend/deploy/setup.sh
2. Configure environment files with my values above
3. Update nginx config with my EC2 IP
4. Start the stack with: bash backend/deploy/start.sh
5. Verify everything works

IMPORTANT: Always run "source ~/.bashrc && conda activate topics" before any Python/backend commands.
```

---

## Phase 3: Detailed Setup Steps (for Claude Code to execute)

### Step 1: Run Setup Script

```bash
sudo bash backend/deploy/setup.sh
```

This installs: system packages, Miniconda, Python 3.11 conda env (`topics`), Node.js 22, all pip dependencies (CPU-only), npm dependencies, Redis (port 6380), and Nginx.

Expected time: 10-20 minutes (mostly pip install).

### Step 2: Create Backend Environment File

```bash
cp backend/.env.example backend/.env
```

Then edit `backend/.env` with real values:

```
ENVIRONMENT=development
DEBUG=true
SECRET_KEY=<generate with: openssl rand -hex 32>
REFRESH_SECRET_KEY=<generate with: openssl rand -hex 32>

SUPABASE_URL=<real-value>
SUPABASE_KEY=<real-value>
SUPABASE_SERVICE_KEY=<real-value>

AWS_ACCESS_KEY_ID=<real-value>
AWS_SECRET_ACCESS_KEY=<real-value>
AWS_REGION=us-east-1
S3_BUCKET=evistream-production

ANTHROPIC_API_KEY=<real-value>
OPENAI_API_KEY=<real-value>
GEMINI_API_KEY=<real-value>

REDIS_URL=redis://localhost:6380/0
REDIS_HOST=localhost
REDIS_PORT=6380
REDIS_DB=0
REDIS_CACHE_DB=1
REDIS_SESSION_DB=2
CELERY_BROKER_URL=redis://localhost:6380/0
CELERY_RESULT_BACKEND=redis://localhost:6380/1

FRONTEND_URL=http://<EC2_PUBLIC_IP>
BACKEND_CORS_ORIGINS=["http://<EC2_PUBLIC_IP>","http://localhost:3000"]

LOG_LEVEL=INFO
CLOUDWATCH_ENABLED=false
MAX_UPLOAD_SIZE=104857600
```

### Step 3: Create Frontend Environment File

```bash
cp frontend/.env.local.example frontend/.env.local
```

Edit `frontend/.env.local`:
```
NEXT_PUBLIC_API_URL=http://<EC2_PUBLIC_IP>
```

### Step 4: Update Nginx Config with EC2 IP

```bash
sudo sed -i 's/YOUR_EC2_PUBLIC_IP/<EC2_PUBLIC_IP>/g' /etc/nginx/sites-available/evistream
sudo nginx -t && sudo systemctl restart nginx
```

### Step 5: Start the Stack

```bash
source ~/.bashrc && conda activate topics
cd ~/evistream
bash backend/deploy/start.sh
```

This starts a tmux session `evistream` with:
- Pane 0: Frontend (Next.js dev server on port 3000)
- Pane 1: Backend (Celery workers + FastAPI on port 8001)

To re-attach later: `tmux attach -t evistream`

### Step 6: Verify

```bash
# Backend health check
curl http://localhost:8001/health

# Frontend check
curl -s http://localhost:3000 | head -5

# Nginx proxy check (from outside)
curl http://<EC2_PUBLIC_IP>/health
```

Then open `http://<EC2_PUBLIC_IP>` in a browser.

---

## End-to-End Verification Checklist

- [ ] `http://<IP>/health` returns 200
- [ ] Login page loads at `http://<IP>/login`
- [ ] Can register a new user
- [ ] Can login
- [ ] Can create a project
- [ ] Can upload a PDF document
- [ ] Can create or select a form
- [ ] Can run an extraction
- [ ] Can view extraction results

---

## Architecture (Single Instance)

```
Browser → Nginx (port 80)
            ├── / → Next.js (port 3000)
            ├── /api/ → FastAPI (port 8001)
            └── /ws/ → FastAPI WebSocket (port 8001)

FastAPI → Redis (port 6380) → Celery Workers
       → Supabase (cloud PostgreSQL)
       → AWS S3 (file storage)
       → LLM APIs (Anthropic, OpenAI, Gemini)
```

---

## Maintenance

### Restart the stack
```bash
bash backend/deploy/stop.sh
source ~/.bashrc && conda activate topics
bash backend/deploy/start.sh
```

### View logs
```bash
tmux attach -t evistream
# Ctrl+B then arrow keys to switch panes
# Ctrl+B then D to detach
```

### Update code
```bash
bash backend/deploy/stop.sh
# Copy updated backend/ and frontend/ from dev machine
source ~/.bashrc && conda activate topics
cd backend && pip install -r requirements.lock.txt
cd ../frontend && npm install
cd .. && bash backend/deploy/start.sh
```

### Check worker status
```bash
tmux attach -t evistream
# Look at Pane 1 for worker logs
# Worker logs also in backend/logs/
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Redis connection refused | `sudo systemctl status redis-server` — check port 6380 |
| CORS errors in browser | Verify `FRONTEND_URL` in `backend/.env` matches the URL in your browser exactly |
| 502 Bad Gateway from nginx | Backend not running. `tmux attach -t evistream` and check Pane 1 |
| Workers not processing jobs | Check worker pane in tmux. Look for import errors or Redis connection issues |
| `torch` import error | Ensure CPU version installed. Run: `pip install torch==2.7.0+cpu --extra-index-url https://download.pytorch.org/whl/cpu` |
| pip install fails | Try `pip install --no-cache-dir -r requirements.lock.txt`. May need more disk space. |
| Frontend shows blank page | Check browser console. Likely `NEXT_PUBLIC_API_URL` not set correctly in `frontend/.env.local` |
| Can't reach from browser | Check EC2 security group allows inbound HTTP (port 80). Check Elastic IP is attached. |
| Backend starts but health check fails | Check `backend/.env` has valid `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` |

---

## Environment Summary

| Component | Version | Port |
|-----------|---------|------|
| Python | 3.11.11 | — |
| Node.js | 22.x | — |
| FastAPI (backend) | 0.128.0 | 8001 |
| Next.js (frontend) | 15.5.12 | 3000 |
| Redis | latest | 6380 |
| Nginx | latest | 80 |
| Celery workers | 5.3.6 | — |

### Python packages: 390+ (see `backend/requirements.lock.txt`)
### Node packages: 25 (see `frontend/package.json`)

---

## What's NOT Included (Demo Simplifications)

- No HTTPS/SSL (add with `sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx`)
- No Docker containerization
- No load balancing (single instance)
- No monitoring (Flower, Prometheus, Grafana)
- No CI/CD pipeline
- No admin role system
- No Supabase Row Level Security (RLS)
- No Redis persistence tuning
