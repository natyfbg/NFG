# NattyFit – Workouts & Recipes (Flask + MongoDB)

## Quick start
```bash

# from backend/
docker-compose build          # rebuild image (picks up new requirements.txt)
docker-compose up -d app      # restart the app container
# if you changed seed fields and want fresh data:
# docker-compose run --rm app python seed.py



python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # edit if needed
python seed.py
python app.py
```
Open http://127.0.0.1:5000

## What’s inside
- `app.py` – Flask app with routes: home, /workouts, /recipes, /search
- `templates/base.html` – Bootstrap layout + navbar + search bar
- Reuses your `home.html`, `workouts.html`, `recipes.html`, `quick_options.html`
- `seed.py` – loads a few sample workouts & recipes
- `requirements.txt`, `.env.example`

## Next steps
- Add forms or an admin area to create/edit workouts and recipes
- Add user auth later (Flask-Login / JWT)
- Switch to async Motor if needed for heavy workloads


