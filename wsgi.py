"""Ponto de entrada do servidor (gunicorn wsgi:app). Rode com UM processo e várias threads."""
from app import create_app
from app.processor import recover_stuck
from app.tokens import start_background_jobs

app = create_app()
recover_stuck(app.config["DATABASE_PATH"])
start_background_jobs(app)
