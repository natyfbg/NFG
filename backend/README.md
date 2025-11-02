# 🏋️‍♂️ NattyFit (NFG) — Workout & Recipe Platform  
*A modern Flask + MongoDB web app built for fitness trainers, creators, and enthusiasts.*

---

## 🌟 Overview

**NattyFit (NFG)** is a fitness-focused web application built with **Flask**, **MongoDB**, and **Bootstrap 5**.  
It allows users to **browse, search, and manage workouts and recipes** through a responsive interface — with an admin dashboard for managing content and dynamic filtering for workouts.

This project is designed to be:
- ✅ Lightweight and portable (works locally or in Docker)
- ✅ Ready for production (deployable on **Render**, with persistent MongoDB)
- ✅ Scalable for future extensions (authentication, user dashboards, media uploads)

---

## 🧱 Tech Stack

| Layer | Technology | Purpose |
|-------|-------------|----------|
| **Backend** | Flask (Python 3.11) | RESTful routes, templates, admin logic |
| **Database** | MongoDB 7 | Document-based storage for workouts & recipes |
| **Auth** | Flask-Login | Admin authentication |
| **Forms** | Flask-WTF + CSRFProtect | Secure forms with CSRF protection |
| **Frontend** | Jinja2 Templates + Bootstrap 5 | Responsive UI, quick rendering |
| **Deployment** | Docker + Render | Portable & cloud-native |
| **Server** | Gunicorn | Production-grade WSGI HTTP server |

---

## 🚀 Quick Start

You can run **NattyFit** in two main ways:

### 🧩 Option 1 — Local Python environment (developer-friendly)
```bash
# From repo root
cd backend

# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and configure environment
cp .env.example .env

# 4. Seed database (loads demo workouts & recipes)
python seed.py

# 5. Run app
python app.py


[//]: # (To Run App)

[//]: # ()
[//]: # (# 1&#41; Make sure your .env is at the repo root &#40;NFG/.env&#41;)

[//]: # (# 2&#41; Build and start)

[//]: # (docker compose up -d --build)

[//]: # ()
[//]: # (# 3&#41; Seed sample data &#40;optional, but handy&#41;)

[//]: # (docker compose run --rm app python seed.py)

[//]: # ()
[//]: # (# 4&#41; Tail logs &#40;optional, to see requests/errors&#41;)

[//]: # (docker compose logs -f app)
