import os

if os.getenv('APP_MODE') == 'azure':
    from app.cloud_api import app
else:
    from app.main import app
