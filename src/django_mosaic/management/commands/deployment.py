"""
Django management command to deploy mosaic to a VPS.

Usage:
    python manage.py deployment setup
    python manage.py deployment status
"""

from django.core.management.base import BaseCommand
from django.core.management.utils import get_random_secret_key
from django.conf import settings as django_settings
from fabric import Connection
import subprocess
import tempfile
import os
import secrets
from pathlib import Path


class Command(BaseCommand):
    help = 'Deploy mosaic blog to a VPS'

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest='subcommand', required=True)

        # Setup subcommand
        setup = subparsers.add_parser('setup', help='Deploy the blog to VPS')
        setup.add_argument('--host', help='VPS hostname or IP')
        setup.add_argument('--user', default='root', help='SSH user (default: root)')
        setup.add_argument('--domain', help='Domain name for the blog')

        # Status subcommand
        status = subparsers.add_parser('status', help='Check deployment status')
        status.add_argument('--host', help='VPS hostname or IP')
        status.add_argument('--user', default='root', help='SSH user')

    def handle(self, *args, **options):
        if options['subcommand'] == 'setup':
            self.run_setup(options)
        elif options['subcommand'] == 'status':
            self.check_status(options)

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def get_template_dir(self):
        """Get the deployment templates directory"""
        return Path(__file__).parent.parent.parent / 'conf' / 'deployment'

    def load_template(self, filename):
        """Load a template file"""
        template_path = self.get_template_dir() / filename
        with open(template_path, 'r') as f:
            return f.read()

    def render_template(self, template_content, config):
        """Replace placeholders in template with config values"""
        replacements = {
            '{{APP_NAME}}': config['app_name'],
            '{{DOMAIN}}': config['domain'],
            '{{INSTALL_PATH}}': config['install_path'],
            '{{GUNICORN_WORKERS}}': str(config['gunicorn_workers']),
            '{{WSGI_MODULE}}': config['wsgi_module'],
            '{{URL_CONF}}': config['url_conf'],
            '{{SECRET_KEY}}': config['secret_key'],
            '{{EMAIL}}': config['email'],
        }

        result = template_content
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        return result

    # =========================================================================
    # SETUP COMMAND
    # =========================================================================

    def run_setup(self, options):
        """Main setup flow - interactive deployment"""
        self.stdout.write(self.style.SUCCESS('=== Mosaic Deployment Helper ===\n'))

        conn = None

        try:
            # Step 1: Gather configuration
            config = self.gather_config(options)

            # Step 2: Test SSH connection
            self.stdout.write('\n📡 Testing SSH connection...')
            try:
                conn = self.test_ssh_connection(config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 3: Install system dependencies
            self.stdout.write('\n📦 Installing system dependencies...')
            try:
                self.install_system_dependencies(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 4: Transfer project files to VPS
            self.stdout.write('\n📦 Transferring project files...')
            try:
                self.transfer_project_files(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 5: Build Docker image on VPS
            self.stdout.write('\n🐳 Building Docker image on VPS...')
            try:
                self.build_docker_image_remote(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 6: Generate and upload configuration files
            self.stdout.write('\n⚙️  Setting up configuration...')
            try:
                self.setup_configuration(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 7: Create systemd services
            self.stdout.write('\n🔧 Creating systemd services...')
            try:
                self.setup_systemd_services(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 8: Configure nginx
            self.stdout.write('\n🌐 Configuring nginx...')
            try:
                self.setup_nginx(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 9: Set up SSL with certbot
            self.stdout.write('\n🔒 Setting up SSL certificate...')
            try:
                self.setup_ssl(conn, config)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'  ⚠ Warning: {str(e)}'))
                # Continue even if SSL fails - it can be set up later

            # Step 10: Start services
            self.stdout.write('\n🚀 Starting services...')
            try:
                self.start_services(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            self.stdout.write(self.style.SUCCESS(f'\n✅ Deployment complete! Visit https://{config["domain"]}'))

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\n\n⚠ Deployment cancelled by user'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n\n✗ Deployment failed: {str(e)}'))
        finally:
            if conn:
                conn.close()

    def gather_config(self, options):
        """Interactive prompts to gather deployment configuration"""
        config = {}

        # SSH connection details
        config['host'] = self._get_input_required(
            'VPS hostname or IP',
            validator=lambda x: len(x.strip()) > 0,
            error_msg='Host cannot be empty'
        )

        config['user'] = input('SSH user [root]: ') or 'root'

        config['ssh_key'] = self._get_input_required(
            'SSH private key path',
            default="~/.ssh/id_rsa",
            validator=lambda x: Path(x).expanduser().exists(),
            error_msg='SSH key file does not exist'
        )

        # Site details
        config['domain'] = self._get_input_required(
            'Domain name (e.g., blog.example.com)',
            validator=lambda x: '.' in x and ' ' not in x,
            error_msg='Invalid domain format'
        )

        config['install_path'] = input('Installation path [/var/www/mosaic]: ') or '/var/www/mosaic'

        # Email for Let's Encrypt
        config['email'] = self._get_input_required(
            'Email for SSL certificate notifications',
            validator=lambda x: '@' in x and '.' in x.split('@')[1],
            error_msg='Invalid email format'
        )

        # App configuration
        config['app_name'] = input('Application name [mosaic]: ') or 'mosaic'

        config['gunicorn_workers'] = self._get_input_required(
            'Number of Gunicorn workers',
            default='2',
            validator=lambda x: x.isdigit() and int(x) > 0,
            error_msg='Must be a positive integer'
        )

        # Django project configuration
        config['wsgi_module'] = self._get_input_required(
            'WSGI module',
            default="website.wsgi:application",
            validator=lambda x: ':' in x and all(part.strip() for part in x.split(':')),
            error_msg='Must be in format "module.path:application"'
        )

        config['url_conf'] = self._get_input_required(
            'URL configuration module',
            default="website.urls",
            validator=lambda x: len(x.strip()) > 0 and ' ' not in x,
            error_msg='Must be a valid Python module path'
        )

        # Generate secret key
        config['secret_key'] = get_random_secret_key()

        return config

    def _get_input_required(self, prompt, default=None, validator=None, error_msg='Invalid input'):
        """Get input with validation, retry on invalid input"""
        prompt_text = f'{prompt}: ' if not default else f'{prompt} [{default}]: '

        while True:
            user_input = input(prompt_text).strip()

            # Use default if provided and input is empty
            if not user_input and default:
                user_input = default

            # Validate
            if validator:
                try:
                    if validator(user_input):
                        return user_input
                    else:
                        self.stdout.write(self.style.ERROR(f'  ✗ {error_msg}. Please try again.'))
                except Exception:
                    self.stdout.write(self.style.ERROR(f'  ✗ {error_msg}. Please try again.'))
            else:
                if user_input:
                    return user_input
                else:
                    self.stdout.write(self.style.ERROR('  ✗ This field is required. Please try again.'))

    def test_ssh_connection(self, config):
        """Test SSH connection and return Connection object"""
        connect_kwargs = {}
        if config.get('ssh_key'):
            # Expand tilde to home directory
            ssh_key_path = os.path.expanduser(config['ssh_key'])
            connect_kwargs['key_filename'] = ssh_key_path

        conn = Connection(
            host=config['host'],
            user=config['user'],
            connect_kwargs=connect_kwargs,
        )
        result = conn.run('echo "Connection successful"', hide=True)
        self.stdout.write(self.style.SUCCESS(f'  ✓ Connected to {config["host"]}'))
        return conn

    def install_system_dependencies(self, conn, config):
        """Install Docker, nginx, certbot on the VPS"""
        commands = [
            'apt-get update',
            'apt-get install -y docker.io nginx certbot python3-certbot-nginx',
            'systemctl enable docker',
            'systemctl start docker',
        ]

        for cmd in commands:
            self.stdout.write(f'  Running: {cmd}')
            conn.sudo(cmd, hide=True)

        self.stdout.write(self.style.SUCCESS('  ✓ Dependencies installed'))

    def transfer_project_files(self, conn, config):
        """Transfer project files to VPS"""
        install_path = config['install_path']
        build_path = f"{install_path}/build"

        # Create build directory on VPS
        conn.run(f'mkdir -p {build_path}')

        # Create temporary directory for files to transfer
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Render and save Dockerfile
            dockerfile_content = self.load_template('Dockerfile')
            dockerfile_content = self.render_template(dockerfile_content, config)
            with open(tmpdir / 'Dockerfile', 'w') as f:
                f.write(dockerfile_content)

            # Render and save entrypoint script
            entrypoint_content = self.load_template('docker-entrypoint.sh')
            with open(tmpdir / 'docker-entrypoint.sh', 'w') as f:
                f.write(entrypoint_content)

            # Transfer Dockerfile and entrypoint
            conn.put(str(tmpdir / 'Dockerfile'), f'{build_path}/Dockerfile')
            conn.put(str(tmpdir / 'docker-entrypoint.sh'), f'{build_path}/docker-entrypoint.sh')

        # Create tar of current project (excluding venv, cache, etc.)
        project_root = Path.cwd()
        tar_file = f'/tmp/{config["app_name"]}-project.tar.gz'

        self.stdout.write('  Creating project archive...')
        subprocess.run([
            'tar', 'czf', tar_file,
            '--exclude=.venv',
            '--exclude=venv',
            '--exclude=__pycache__',
            '--exclude=*.pyc',
            '--exclude=.git',
            '--exclude=node_modules',
            '--exclude=.pytest_cache',
            '--exclude=staticfiles',
            '--exclude=media',
            '.'
        ], cwd=project_root, check=True)

        # Transfer project archive
        self.stdout.write('  Uploading project files...')
        remote_tar = f'/tmp/{config["app_name"]}-project.tar.gz'
        conn.put(tar_file, remote_tar)

        # Extract on VPS
        self.stdout.write('  Extracting project files...')
        conn.run(f'tar xzf {remote_tar} -C {build_path}')
        conn.run(f'rm {remote_tar}')

        # Cleanup local tar
        os.unlink(tar_file)

        self.stdout.write(self.style.SUCCESS('  ✓ Project files transferred'))

    def build_docker_image_remote(self, conn, config):
        """Build Docker image on the VPS"""
        build_path = f"{config['install_path']}/build"

        self.stdout.write('  Building Docker image (this may take a few minutes)...')

        # Build the image
        result = conn.run(
            f'cd {build_path} && docker build -t {config["app_name"]}:latest .',
            warn=True,
            hide=True
        )

        if not result.ok:
            self.stdout.write(self.style.ERROR('  ✗ Docker build failed'))
            raise Exception('Docker build failed')

        self.stdout.write(self.style.SUCCESS('  ✓ Docker image built on VPS'))

    def setup_configuration(self, conn, config):
        """Generate and upload .env and settings.py"""
        install_path = config['install_path']

        # Create installation directory
        conn.run(f'mkdir -p {install_path}')
        conn.run(f'mkdir -p {install_path}/db')
        conn.run(f'mkdir -p {install_path}/media')
        conn.run(f'mkdir -p {install_path}/static')

        # Check if .env already exists and reuse SECRET_KEY if it does
        result = conn.run(f'cat {install_path}/.env 2>/dev/null | grep "^SECRET_KEY="', warn=True, hide=True)
        if result.ok and result.stdout.strip():
            # Extract and reuse existing secret key
            existing_key = result.stdout.strip().split('=', 1)[1].strip().strip('"').strip("'")
            if existing_key:
                config['secret_key'] = existing_key
                self.stdout.write('  ℹ Reusing existing SECRET_KEY')

        # Generate .env file
        env_content = self.load_template('.env.template')
        env_content = self.render_template(env_content, config)

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(env_content)
            env_temp = f.name

        conn.put(env_temp, f'{install_path}/.env')
        os.unlink(env_temp)

        # Upload backup script
        backup_content = self.load_template('backup.sh')
        backup_content = self.render_template(backup_content, config)

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(backup_content)
            backup_temp = f.name

        conn.put(backup_temp, f'{install_path}/backup.sh')
        conn.run(f'chmod +x {install_path}/backup.sh')
        os.unlink(backup_temp)

        self.stdout.write(self.style.SUCCESS('  ✓ Configuration files uploaded'))

    def setup_systemd_services(self, conn, config):
        """Create systemd service and timer for app and backups"""
        services = [
            ('mosaic-app.service', 'mosaic-app.service'),
            ('mosaic-backup.service', 'mosaic-backup.service'),
            ('mosaic-backup.timer', 'mosaic-backup.timer'),
        ]

        for template_name, service_name in services:
            content = self.load_template(template_name)
            content = self.render_template(content, config)

            with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                f.write(content)
                temp_file = f.name

            conn.put(temp_file, f'/tmp/{service_name}')
            conn.sudo(f'mv /tmp/{service_name} /etc/systemd/system/{service_name}')
            os.unlink(temp_file)

        conn.sudo('systemctl daemon-reload')
        self.stdout.write(self.style.SUCCESS('  ✓ Systemd services created'))

    def setup_nginx(self, conn, config):
        """Configure nginx as reverse proxy"""
        nginx_content = self.load_template('nginx.conf')
        nginx_content = self.render_template(nginx_content, config)

        site_name = config['app_name']

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(nginx_content)
            temp_file = f.name

        conn.put(temp_file, f'/tmp/{site_name}')
        conn.sudo(f'mv /tmp/{site_name} /etc/nginx/sites-available/{site_name}')
        conn.sudo(f'ln -sf /etc/nginx/sites-available/{site_name} /etc/nginx/sites-enabled/{site_name}')
        os.unlink(temp_file)

        # Test nginx config
        result = conn.sudo('nginx -t', warn=True, hide=True)
        if not result.ok:
            self.stdout.write(self.style.ERROR('  ✗ Nginx configuration test failed'))
            raise Exception('Nginx configuration is invalid')

        # Reload nginx to apply changes
        conn.sudo('systemctl reload nginx')

        self.stdout.write(self.style.SUCCESS('  ✓ Nginx configured and reloaded'))

    def setup_ssl(self, conn, config):
        """Set up SSL certificate with certbot"""
        domain = config['domain']
        email = config['email']

        cmd = (
            f'certbot --nginx '
            f'--non-interactive '
            f'--agree-tos '
            f'--email {email} '
            f'-d {domain} '
            f'--keep-until-expiring '  # Only obtain new cert if current one is expiring
            f'--expand'  # Allow expanding certificate with additional domains
        )

        result = conn.sudo(cmd, warn=True)
        if result.ok:
            self.stdout.write(self.style.SUCCESS('  ✓ SSL certificate obtained'))
        else:
            self.stdout.write(self.style.WARNING('  ⚠ SSL setup failed (you may need to configure DNS first)'))

    def start_services(self, conn, config):
        """Start all services"""
        # Enable and restart app service (restart ensures config changes are applied)
        conn.sudo('systemctl enable mosaic-app.service')
        conn.sudo('systemctl restart mosaic-app.service')

        # Enable and restart backup timer
        conn.sudo('systemctl enable mosaic-backup.timer')
        conn.sudo('systemctl restart mosaic-backup.timer')

        # Reload nginx
        conn.sudo('systemctl reload nginx')

        self.stdout.write(self.style.SUCCESS('  ✓ Services started'))

    # =========================================================================
    # STATUS COMMAND
    # =========================================================================

    def check_status(self, options):
        """Check deployment status on VPS"""
        self.stdout.write(self.style.SUCCESS('=== Deployment Status ===\n'))

        # Gather minimal config
        config = {}

        # SSH connection details
        config['host'] = self._get_input_required(
            'VPS hostname or IP',
            validator=lambda x: len(x.strip()) > 0,
            error_msg='Host cannot be empty'
        )

        config['user'] = input('SSH user [root]: ') or 'root'

        config['ssh_key'] = self._get_input_required(
            'SSH private key path',
            default="~/.ssh/id_rsa",
            validator=lambda x: Path(x).expanduser().exists(),
            error_msg='SSH key file does not exist'
        )

        try:
            conn = self.test_ssh_connection(config)

            # Check Docker
            self.stdout.write('\n🐳 Docker:')
            self.check_docker_status(conn)

            # Check systemd services
            self.stdout.write('\n⚙️  Services:')
            self.check_services_status(conn)

            # Check nginx
            self.stdout.write('\n🌐 Nginx:')
            self.check_nginx_status(conn)

            # Check SSL certificate
            self.stdout.write('\n🔒 SSL Certificate:')
            self.check_ssl_status(conn)

            # Check disk space
            self.stdout.write('\n💾 Disk Space:')
            self.check_disk_status(conn)

            # Check last backup
            self.stdout.write('\n📦 Database Backup:')
            self.check_backup_status(conn)

            conn.close()

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to check status: {e}'))


    def check_docker_status(self, conn):
        """Check if Docker is running and show container status"""
        result = conn.run('docker ps --filter name=mosaic', warn=True, hide=True)
        if result.ok and result.stdout.strip():
            self.stdout.write(self.style.SUCCESS('  ✓ Container running'))
        else:
            self.stdout.write(self.style.ERROR('  ✗ Container not running'))

    def check_services_status(self, conn):
        """Check systemd service status"""
        services = ['mosaic-app.service', 'mosaic-backup.timer']
        for service in services:
            result = conn.run(f'systemctl is-active {service}', warn=True, hide=True)
            if result.ok and 'active' in result.stdout:
                self.stdout.write(self.style.SUCCESS(f'  ✓ {service} active'))
            else:
                self.stdout.write(self.style.ERROR(f'  ✗ {service} inactive'))

    def check_nginx_status(self, conn):
        """Check nginx status"""
        result = conn.run('systemctl is-active nginx', warn=True, hide=True)
        if result.ok and 'active' in result.stdout:
            self.stdout.write(self.style.SUCCESS('  ✓ Nginx active'))
        else:
            self.stdout.write(self.style.ERROR('  ✗ Nginx inactive'))

    def check_ssl_status(self, conn):
        """Check SSL certificate status"""
        result = conn.run('certbot certificates', warn=True, hide=True)
        if result.ok and 'VALID' in result.stdout:
            self.stdout.write(self.style.SUCCESS('  ✓ Certificate valid'))
        else:
            self.stdout.write(self.style.WARNING('  ⚠ No valid certificate found'))

    def check_disk_status(self, conn):
        """Check disk space"""
        result = conn.run('df -h /', hide=True)
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            self.stdout.write(f'  {lines[1]}')

    def check_backup_status(self, conn):
        """Check last backup time"""
        result = conn.run('ls -t /var/www/mosaic/backups/hourly/db-*.sqlite3 2>/dev/null | head -1', warn=True, hide=True)
        if result.ok and result.stdout.strip():
            latest = result.stdout.strip().split('/')[-1]
            self.stdout.write(self.style.SUCCESS(f'  ✓ Latest backup: {latest}'))

            # Count backups
            for tier in ['hourly', 'daily', 'weekly', 'monthly']:
                count_result = conn.run(f'ls -1 /var/www/mosaic/backups/{tier}/db-*.sqlite3 2>/dev/null | wc -l', warn=True, hide=True)
                if count_result.ok:
                    count = count_result.stdout.strip()
                    self.stdout.write(f'    {tier.capitalize()}: {count} backups')
        else:
            self.stdout.write(self.style.WARNING('  ⚠ No backups found'))
