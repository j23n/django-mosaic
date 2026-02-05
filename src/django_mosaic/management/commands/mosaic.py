"""
Django management command for mosaic operations.

Usage:
    python manage.py mosaic deployment setup
    python manage.py mosaic deployment status
"""

from django.core.management.base import BaseCommand
import sys
from ._deployment import Command as DeploymentCommand


class Command(BaseCommand):
    help = 'Mosaic blog management commands'

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')

        # Deployment command
        deployment_parser = subparsers.add_parser('deployment', help='Deployment operations')
        deployment_subparsers = deployment_parser.add_subparsers(dest='subcommand', required=True)

        # Deployment: setup
        setup = deployment_subparsers.add_parser('setup', help='Deploy the blog to VPS')
        setup.add_argument('--host', help='VPS hostname or IP')
        setup.add_argument('--user', default='root', help='SSH user (default: root)')
        setup.add_argument('--domain', help='Domain name for the blog')
        setup.add_argument('--auto', action='store_true', help='Run without confirmations (print commands only)')
        setup.add_argument('--explain', action='store_true', help='Show descriptions for each operation')
        setup.add_argument('--dry-run', action='store_true', help='Show what would be done without executing')

        # Deployment: status
        status = deployment_subparsers.add_parser('status', help='Check deployment status')
        status.add_argument('--host', help='VPS hostname or IP')
        status.add_argument('--user', default='root', help='SSH user')

    def handle(self, *args, **options):
        command = options.get('command')

        if command == 'deployment':
            deployment_cmd = DeploymentCommand(stdout=self.stdout, stderr=self.stderr, no_color=self.style.NO_COLOR)
            deployment_cmd.handle(*args, **options)
        else:
            self.stdout.write(self.style.ERROR(f'Unknown command: {command}'))
