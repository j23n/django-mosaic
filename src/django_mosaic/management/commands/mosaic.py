"""
Django management command for mosaic operations.

Usage:
    python manage.py mosaic deployment setup
    python manage.py mosaic deployment update
    python manage.py mosaic deployment status
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Mosaic blog management commands"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(
            dest="command", required=True, help="Available commands"
        )

        # Deployment command
        deployment_parser = subparsers.add_parser(
            "deployment", help="Deployment operations"
        )
        deployment_subparsers = deployment_parser.add_subparsers(
            dest="subcommand", required=True
        )

        # Shared flags for setup and update
        def add_deploy_flags(parser):
            parser.add_argument("--host", help="VPS hostname or IP")
            # default None (not "root") so an unset flag doesn't clobber a saved
            # non-root user; the "root" fallback is applied by ConfigManager.
            parser.add_argument("--user", default=None, help="SSH user (default: root)")
            parser.add_argument("--domain", help="Domain name for the blog")
            parser.add_argument(
                "--auto",
                action="store_true",
                help="Run without per-step confirmation prompts (executes everything)",
            )
            parser.add_argument(
                "--explain",
                action="store_true",
                help="Show descriptions for each operation",
            )
            parser.add_argument(
                "--dry-run",
                action="store_true",
                help="Show what would be done without executing",
            )

        # Deployment: setup
        setup = deployment_subparsers.add_parser(
            "setup",
            help="Full deployment setup (system deps, firewall, nginx, SSL, app)",
        )
        add_deploy_flags(setup)

        # Deployment: update
        update = deployment_subparsers.add_parser(
            "update",
            help="Quick app update (transfer files, rebuild image, restart service)",
        )
        add_deploy_flags(update)

        # Deployment: status
        status = deployment_subparsers.add_parser(
            "status", help="Check deployment status"
        )
        status.add_argument("--host", help="VPS hostname or IP")
        status.add_argument("--user", default=None, help="SSH user (default: root)")

    def handle(self, *args, **options):
        command = options.get("command")
        subcommand = options.get("subcommand")

        if command == "deployment":
            try:
                from ._deployment import DeploymentHandler
            except ImportError as e:
                raise CommandError(
                    "Deployment tooling requires the optional 'deploy' extra. "
                    "Install it with: pip install 'django-mosaic[deploy]'"
                ) from e
            handler = DeploymentHandler(stdout=self.stdout, style=self.style)

            if subcommand == "setup":
                handler.run_setup(options)
            elif subcommand == "update":
                handler.run_update(options)
            elif subcommand == "status":
                handler.check_status(options)
            else:
                self.stdout.write(
                    self.style.ERROR(f"Unknown deployment subcommand: {subcommand}")
                )
        else:
            self.stdout.write(self.style.ERROR(f"Unknown command: {command}"))
