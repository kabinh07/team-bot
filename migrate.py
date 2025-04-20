# migrate.py
import os
os.system("alembic revision --autogenerate -m 'autogen'")
os.system("alembic upgrade head")