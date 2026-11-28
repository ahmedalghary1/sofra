import os
from .container_env import configure_environment

configure_environment(os.environ)
from .settings import *  # noqa: E402,F403
