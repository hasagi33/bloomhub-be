"""
Use localhost in the runserver startup message instead of 127.0.0.1.

By setting default_addr = "localhost", the server binds to localhost (resolving to
127.0.0.1) and the built-in startup message shows "Starting development server at
http://localhost:8000/".
"""

from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticFilesRunserverCommand,
)


class Command(StaticFilesRunserverCommand):
    help = (
        "Starts a lightweight web server for development and also serves static files. "
        "Shows http://localhost:8000/ in the startup message."
    )

    # Show localhost instead of 127.0.0.1 when no address is specified
    default_addr = "localhost"
