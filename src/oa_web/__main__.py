"""``python -m oa_web <command>`` — Django's manage.py for the web UI
(e.g. ``createsuperuser``, ``changepassword``, ``migrate``)."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oa_web.settings")
    import django
    from django.core.management import call_command, execute_from_command_line

    # Keep Django's own tables current so `createsuperuser` etc. just work
    # on a fresh checkout (the tracker database is not involved).
    if len(sys.argv) > 1 and sys.argv[1] not in ("migrate", "help", "check"):
        django.setup()
        call_command("migrate", interactive=False, verbosity=0)
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
