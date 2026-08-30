import os
import tempfile

# Must be set before any app module is imported (app.config reads it at import time).
_TMP = tempfile.mkdtemp(prefix="ha_db_explorer_test_")
os.environ["APP_DATA"] = _TMP
os.environ["DOCKER_SOCK"] = "/nonexistent/docker.sock"
