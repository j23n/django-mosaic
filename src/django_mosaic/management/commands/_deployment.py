"""
Deployment handler for mosaic blog.

This module is called via the mosaic command:
    python manage.py mosaic deployment setup
    python manage.py mosaic deployment status

Not meant to be called directly.
"""

from django.core.management.utils import get_random_secret_key
from django.conf import settings as django_settings
from fabric import Connection
import subprocess
import tempfile
import os
import secrets
from pathlib import Path

from .config_manager import ConfigManager


class DeploymentHandler:
    """Handles deployment operations for mosaic blog"""

    def __init__(self, stdout, style):
        """
        Initialize deployment handler.

        Args:
            stdout: Output stream for writing messages
            style: Django command style object for colored output
        """
        self.stdout = stdout
        self.style = style

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

    def _run(self, conn, cmd, description=None, **kwargs):
        """Execute conn.run() with display and confirmation"""
        if description and self.explain_mode:
            self.stdout.write(f"\n{description}")
        self.stdout.write(f"  $ {cmd}")

        if not self.auto_mode:
            response = input("  Execute? [Y/n]: ").strip()
            if response.lower() == 'n':
                raise Exception("Deployment cancelled by user")

        if self.dry_run:
            return None

        return conn.run(cmd, **kwargs)

    def _sudo(self, conn, cmd, description=None, **kwargs):
        """Execute conn.sudo() with display and confirmation"""
        if description and self.explain_mode:
            self.stdout.write(f"\n{description}")
        self.stdout.write(self.style.WARNING(f"  $ sudo {cmd}"))

        if not self.auto_mode:
            response = input("  Execute? [Y/n]: ").strip()
            if response.lower() == 'n':
                raise Exception("Deployment cancelled by user")

        if self.dry_run:
            return None

        return conn.sudo(cmd, **kwargs)

    def _put(self, conn, local_path, remote_path, description=None):
        """Upload file with content display and confirmation"""
        if description and self.explain_mode:
            self.stdout.write(f"\n{description}")

        local_file = Path(local_path)
        file_size = local_file.stat().st_size if local_file.exists() else 0

        self.stdout.write(f"  📤 {local_path} → {remote_path}")

        # Show content for non-archive files
        if not remote_path.endswith(('.tar.gz', '.tar', '.zip', '.tgz')):
            try:
                with open(local_path, 'r') as f:
                    content = f.read()

                self.stdout.write(self.style.SUCCESS("\n  Content:"))
                self.stdout.write("  " + "─" * 70)
                for i, line in enumerate(content.split('\n'), 1):
                    self.stdout.write(f"  {i:3d} | {line}")
                self.stdout.write("  " + "─" * 70)
            except (UnicodeDecodeError, IsADirectoryError):
                # Binary file
                self.stdout.write(f"  (Binary file, {file_size} bytes)")
        else:
            # Archive - just show size
            size_mb = file_size / (1024 * 1024)
            self.stdout.write(f"  (Archive, {size_mb:.1f} MB)")

        if not self.auto_mode:
            response = input("  Upload? [Y/n]: ").strip()
            if response.lower() == 'n':
                raise Exception("Deployment cancelled by user")

        if self.dry_run:
            return None

        # Use rsync instead of SFTP for better compatibility
        rsync_cmd = ['rsync', '-avz']

        # Add SSH options
        ssh_opts = []
        if hasattr(self, 'config') and self.config.get('ssh_key'):
            ssh_key_path = os.path.expanduser(self.config['ssh_key'])
            ssh_opts.append(f'-i {ssh_key_path}')

        if ssh_opts:
            rsync_cmd.extend(['-e', f'ssh {" ".join(ssh_opts)}'])

        # Add source and destination
        rsync_cmd.extend([
            local_path,
            f"{conn.user}@{conn.host}:{remote_path}"
        ])

        result = subprocess.run(rsync_cmd, check=True, capture_output=True, text=True)
        return result

    # =========================================================================
    # SETUP COMMAND
    # =========================================================================

    def run_setup(self, options):
        """Main setup flow - interactive deployment"""
        self.stdout.write(self.style.SUCCESS('=== Mosaic Deployment Helper ===\n'))

        # Set mode flags
        self.auto_mode = options.get('auto', False)
        self.explain_mode = options.get('explain', False)
        self.dry_run = options.get('dry_run', False)

        if self.dry_run:
            self.stdout.write(self.style.WARNING('🔍 Dry run mode: no commands will be executed\n'))

        conn = None
        config_manager = ConfigManager()

        try:
            # Step 1: Gather configuration
            config = config_manager.get_config(stdout=self.stdout)

            # Store config as instance variable for use in helper methods
            self.config = config

            # Generate secret key (not saved to file)
            config['secret_key'] = get_random_secret_key()

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

            # Step 4: Configure firewall
            self.stdout.write('\n🔥 Configuring firewall...')
            try:
                self.setup_firewall(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 5: Transfer project files to VPS
            self.stdout.write('\n📦 Transferring project files...')
            try:
                self.transfer_project_files(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 6: Build Docker image on VPS
            self.stdout.write('\n🐳 Building Docker image on VPS...')
            try:
                self.build_docker_image_remote(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 7: Generate and upload configuration files
            self.stdout.write('\n⚙️  Setting up configuration...')
            try:
                self.setup_configuration(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 8: Create systemd services
            self.stdout.write('\n🔧 Creating systemd services...')
            try:
                self.setup_systemd_services(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 9: Configure nginx
            self.stdout.write('\n🌐 Configuring nginx...')
            try:
                self.setup_nginx(conn, config)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Failed: {str(e)}'))
                return

            # Step 10: Set up SSL with certbot
            self.stdout.write('\n🔒 Setting up SSL certificate...')
            try:
                self.setup_ssl(conn, config)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'  ⚠ Warning: {str(e)}'))
                # Continue even if SSL fails - it can be set up later

            # Step 11: Start services
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
        self._sudo(conn, 'apt-get update', description='Updating package lists')
        self._sudo(conn, 'apt-get install -y docker.io nginx certbot python3-certbot-nginx ufw',
                   description='Installing Docker, nginx, certbot, and UFW firewall')
        self._sudo(conn, 'systemctl enable docker', description='Enabling Docker service')
        self._sudo(conn, 'systemctl start docker', description='Starting Docker service')

        self.stdout.write(self.style.SUCCESS('\n  ✓ Dependencies installed'))

    def setup_firewall(self, conn, config):
        """Configure UFW firewall"""
        # Allow SSH (critical - do this first to avoid lockout)
        self._sudo(conn, 'ufw allow 22/tcp', description='Allowing SSH (port 22)')

        # Allow HTTP and HTTPS
        self._sudo(conn, 'ufw allow 80/tcp', description='Allowing HTTP (port 80)')
        self._sudo(conn, 'ufw allow 443/tcp', description='Allowing HTTPS (port 443)')

        # Set default policies
        self._sudo(conn, 'ufw default deny incoming', description='Setting default deny for incoming')
        self._sudo(conn, 'ufw default allow outgoing', description='Setting default allow for outgoing')

        # Enable UFW
        self._sudo(conn, 'ufw --force enable', description='Enabling UFW firewall')

        self.stdout.write(self.style.SUCCESS('\n  ✓ Firewall configured'))

    def transfer_project_files(self, conn, config):
        """Transfer project files to VPS"""
        install_path = config['install_path']
        build_path = f"{install_path}/build"

        # Create build directory on VPS
        self._sudo(conn, f'mkdir -p {build_path}', description='Creating build directory on VPS')

        # Make directory user owned
        self._sudo(conn, f'chown -R {config["user"]} {build_path}', description='Give user permissions for the build directory')

        # Create temporary directory for files to transfer
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Render and save Dockerfile
            dockerfile_content = self.load_template('Dockerfile')
            dockerfile_content = self.render_template(dockerfile_content, config)
            with open(tmpdir / 'Dockerfile', 'w') as f:
                f.write(dockerfile_content)

            # Copy .dockerignore
            dockerignore_content = self.load_template('.dockerignore')
            with open(tmpdir / '.dockerignore', 'w') as f:
                f.write(dockerignore_content)

            # Render and save entrypoint script
            entrypoint_content = self.load_template('docker-entrypoint.sh')
            with open(tmpdir / 'docker-entrypoint.sh', 'w') as f:
                f.write(entrypoint_content)

            # Transfer Dockerfile, .dockerignore, and entrypoint
            self._put(conn, str(tmpdir / 'Dockerfile'), f'{build_path}/Dockerfile',
                      description='Uploading Dockerfile')
            self._put(conn, str(tmpdir / '.dockerignore'), f'{build_path}/.dockerignore',
                      description='Uploading .dockerignore')
            self._put(conn, str(tmpdir / 'docker-entrypoint.sh'), f'{build_path}/docker-entrypoint.sh',
                      description='Uploading Docker entrypoint script')

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
        remote_tar = f'/tmp/{config["app_name"]}-project.tar.gz'
        self._put(conn, tar_file, remote_tar, description='Uploading project archive')

        # Extract on VPS
        self._run(conn, f'tar xzf {remote_tar} -C {build_path}',
                  description='Extracting project files on VPS')
        self._run(conn, f'rm {remote_tar}', description='Cleaning up remote tar file')

        # Cleanup local tar
        os.unlink(tar_file)

        self.stdout.write(self.style.SUCCESS('  ✓ Project files transferred'))

    def build_docker_image_remote(self, conn, config):
        """Build Docker image on the VPS"""
        build_path = f"{config['install_path']}/build"

        # Build the image
        result = self._sudo(
            conn,
            f'docker build -t {config["app_name"]}:latest {build_path}',
            description='Building Docker image on VPS (this may take a few minutes)',
            warn=True
        )

        if not self.dry_run and not result.ok:
            self.stdout.write(self.style.ERROR('\n  ✗ Docker build failed'))
            raise Exception('Docker build failed')

        self.stdout.write(self.style.SUCCESS('\n  ✓ Docker image built on VPS'))

    def setup_configuration(self, conn, config):
        """Generate and upload .env and settings.py"""
        install_path = config['install_path']

        # Create installation directory
        self._sudo(conn, f'mkdir -p {install_path}', description='Creating installation directory')
        self._sudo(conn, f'mkdir -p {install_path}/media', description='Creating media directory')
        self._sudo(conn, f'mkdir -p {install_path}/static', description='Creating static files directory')

        self._sudo(conn, f'chown -R {config["user"]} {install_path}', description='Giving the user permissions for the install directory')


        # Create empty database file if it doesn't exist (required for Docker volume mount)
        self._run(conn, f'touch {install_path}/db.sqlite3', description='Creating database file')

        # Check if .env already exists and reuse SECRET_KEY if it does
        result = self._run(conn, f'cat {install_path}/.env 2>/dev/null | grep "^SECRET_KEY="',
                          description='Checking for existing SECRET_KEY', warn=True, hide=True)
        if not self.dry_run and result.ok and result.stdout.strip():
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

        self._put(conn, env_temp, f'{install_path}/.env', description='Uploading .env configuration')
        os.unlink(env_temp)

        # Upload backup script
        backup_content = self.load_template('backup.sh')
        backup_content = self.render_template(backup_content, config)

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(backup_content)
            backup_temp = f.name

        self._put(conn, backup_temp, f'{install_path}/backup.sh', description='Uploading backup script')
        self._run(conn, f'chmod +x {install_path}/backup.sh', description='Making backup script executable')
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

            self._put(conn, temp_file, f'/tmp/{service_name}',
                     description=f'Uploading {service_name}')
            self._sudo(conn, f'mv /tmp/{service_name} /etc/systemd/system/{service_name}',
                      description=f'Installing {service_name}')
            os.unlink(temp_file)

        self._sudo(conn, 'systemctl daemon-reload', description='Reloading systemd daemon')
        self.stdout.write(self.style.SUCCESS('\n  ✓ Systemd services created'))

    def setup_nginx(self, conn, config):
        """Configure nginx as reverse proxy"""
        nginx_content = self.load_template('nginx.conf')
        nginx_content = self.render_template(nginx_content, config)

        site_name = config['app_name']

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(nginx_content)
            temp_file = f.name

        self._put(conn, temp_file, f'/tmp/{site_name}', description='Uploading nginx configuration')
        self._sudo(conn, f'mv /tmp/{site_name} /etc/nginx/sites-available/{site_name}',
                  description='Installing nginx site configuration')
        self._sudo(conn, f'ln -sf /etc/nginx/sites-available/{site_name} /etc/nginx/sites-enabled/{site_name}',
                  description='Enabling nginx site')
        os.unlink(temp_file)

        # Test nginx config
        result = self._sudo(conn, 'nginx -t', description='Testing nginx configuration', warn=True, hide=True)
        if not self.dry_run and not result.ok:
            self.stdout.write(self.style.ERROR('\n  ✗ Nginx configuration test failed'))
            raise Exception('Nginx configuration is invalid')

        # Reload nginx to apply changes
        self._sudo(conn, 'systemctl reload nginx', description='Reloading nginx')

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

        result = self._sudo(conn, cmd, description='Obtaining SSL certificate with certbot', warn=True)
        if not self.dry_run:
            if result.ok:
                self.stdout.write(self.style.SUCCESS('\n  ✓ SSL certificate obtained'))
            else:
                self.stdout.write(self.style.WARNING('\n  ⚠ SSL setup failed (you may need to configure DNS first)'))

    def start_services(self, conn, config):
        """Start all services"""
        # Enable and restart app service (restart ensures config changes are applied)
        self._sudo(conn, 'systemctl enable mosaic-app.service', description='Enabling mosaic app service')
        self._sudo(conn, 'systemctl restart mosaic-app.service', description='Starting mosaic app service')

        # Enable and restart backup timer
        self._sudo(conn, 'systemctl enable mosaic-backup.timer', description='Enabling backup timer')
        self._sudo(conn, 'systemctl restart mosaic-backup.timer', description='Starting backup timer')

        # Reload nginx
        self._sudo(conn, 'systemctl reload nginx', description='Reloading nginx')

        self.stdout.write(self.style.SUCCESS('\n  ✓ Services started'))

    # =========================================================================
    # STATUS COMMAND
    # =========================================================================

    def check_status(self, options):
        """Check deployment status on VPS"""
        self.stdout.write(self.style.SUCCESS('=== Deployment Status ===\n'))

        # Load config using ConfigManager
        config_manager = ConfigManager()
        
        # For status, we need fewer fields than setup
        required_fields = ['host', 'user', 'ssh_key', 'install_path', 'app_name', 'domain']
        config = config_manager.get_config(
            required_fields=required_fields,
            stdout=self.stdout
        )

        try:
            conn = self.test_ssh_connection(config)

            # Check configuration files
            self.stdout.write('\n📄 Configuration Files:')
            self.check_config_files(conn, config)

            # Check Docker
            self.stdout.write('\n🐳 Docker:')
            self.check_docker_status(conn, config)

            # Check systemd services
            self.stdout.write('\n⚙️  Services:')
            self.check_services_status(conn)

            # Check nginx
            self.stdout.write('\n🌐 Nginx:')
            self.check_nginx_status(conn)

            # Check application health (includes SSL certificate check)
            self.stdout.write('\n🏥 Application Health & SSL:')
            self.check_application_health(conn, config)

            # Check disk space
            self.stdout.write('\n💾 Disk Space:')
            self.check_disk_status(conn)

            # Check last backup
            self.stdout.write('\n📦 Database Backup:')
            self.check_backup_status(conn, config)

            # Check for recent errors
            self.stdout.write('\n⚠️  Recent Errors:')
            self.check_recent_errors(conn, config)

            conn.close()

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to check status: {e}'))


    def check_config_files(self, conn, config):
        """Check if critical configuration files exist"""
        install_path = config.get('install_path', '/var/www/mosaic')
        app_name = config.get('app_name', 'mosaic')

        files_to_check = [
            (f'{install_path}/.env', '.env file'),
            (f'{install_path}/backup.sh', 'backup.sh script'),
            (f'/etc/systemd/system/mosaic-app.service', 'mosaic-app.service'),
            (f'/etc/systemd/system/mosaic-backup.service', 'mosaic-backup.service'),
            (f'/etc/systemd/system/mosaic-backup.timer', 'mosaic-backup.timer'),
            (f'/etc/nginx/sites-available/{app_name}', 'nginx site config'),
            (f'/etc/nginx/sites-enabled/{app_name}', 'nginx site symlink'),
        ]

        for file_path, description in files_to_check:
            result = conn.run(f'test -e {file_path}', warn=True, hide=True)
            if result.ok:
                # Check if backup.sh is executable
                if 'backup.sh' in file_path:
                    exec_result = conn.run(f'test -x {file_path}', warn=True, hide=True)
                    if exec_result.ok:
                        self.stdout.write(self.style.SUCCESS(f'  ✓ {description} exists and is executable'))
                    else:
                        self.stdout.write(self.style.WARNING(f'  ⚠ {description} exists but is not executable'))
                else:
                    self.stdout.write(self.style.SUCCESS(f'  ✓ {description} exists'))
            else:
                self.stdout.write(self.style.ERROR(f'  ✗ {description} missing'))

    def check_docker_status(self, conn, config):
        """Check if Docker is running and show container status"""
        app_name = config.get('app_name', 'mosaic')

        # Check if container is running
        result = conn.sudo(f'docker ps --filter "name={app_name}" --format "{{{{.Names}}}}|{{{{.Status}}}}|{{{{.Image}}}}"', warn=True, hide=True)
        if result.ok and result.stdout.strip():
            container_info = result.stdout.strip().split('|')
            if len(container_info) >= 3:
                name, status, image = container_info[0], container_info[1], container_info[2]
                self.stdout.write(self.style.SUCCESS(f'  ✓ Container running: {name}'))
                self.stdout.write(f'    Status: {status}')
                self.stdout.write(f'    Image: {image}')
            else:
                self.stdout.write(self.style.SUCCESS('  ✓ Container running'))
        else:
            self.stdout.write(self.style.ERROR('  ✗ Container not running'))

            # Check if container exists but is stopped
            stopped = conn.run(f'docker ps -a --filter "name={app_name}" --format "{{{{.Names}}}}"', warn=True, hide=True)
            if stopped.ok and stopped.stdout.strip():
                self.stdout.write(self.style.WARNING(f'    ⚠ Container exists but is stopped'))

        # Check if image exists
        image_result = conn.run(f'docker images {app_name}:latest --format "{{{{.ID}}}}|{{{{.CreatedAt}}}}"', warn=True, hide=True)
        if image_result.ok and image_result.stdout.strip():
            image_info = image_result.stdout.strip().split('|')
            if len(image_info) >= 2:
                self.stdout.write(f'  Image: {app_name}:latest (created {image_info[1]})')
        else:
            self.stdout.write(self.style.WARNING(f'  ⚠ Image {app_name}:latest not found'))

    def check_services_status(self, conn):
        """Check systemd service status"""
        services = ['mosaic-app.service', 'mosaic-backup.service', 'mosaic-backup.timer']
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

    def check_application_health(self, conn, config):
        """Check if application is responding to HTTP requests"""
        domain = config.get('domain')
        if not domain:
            self.stdout.write(self.style.WARNING('  ⚠ No domain configured, skipping health check'))
            return

        # Try HTTPS first, then HTTP
        for protocol in ['https', 'http']:
            result = conn.run(
                f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 5 {protocol}://{domain}/',
                warn=True,
                hide=True
            )
            if result.ok:
                status_code = result.stdout.strip()
                if status_code.startswith('2') or status_code.startswith('3'):
                    self.stdout.write(self.style.SUCCESS(f'  ✓ Application responding ({protocol.upper()} {status_code})'))

                    # If HTTPS is working, also check certificate expiry
                    if protocol == 'https':
                        cert_info = conn.run(
                            f'echo | openssl s_client -servername {domain} -connect {domain}:443 2>/dev/null | '
                            f'openssl x509 -noout -dates',
                            warn=True,
                            hide=True
                        )
                        if cert_info.ok and cert_info.stdout.strip():
                            for line in cert_info.stdout.strip().split('\n'):
                                if 'notAfter' in line:
                                    expiry = line.replace('notAfter=', '').strip()
                                    self.stdout.write(f'  ✓ SSL certificate expires: {expiry}')

                    return
                else:
                    self.stdout.write(self.style.WARNING(f'  ⚠ Application returned {protocol.upper()} {status_code}'))
                    return

        self.stdout.write(self.style.ERROR(f'  ✗ Application not responding at {domain}'))

    def check_disk_status(self, conn):
        """Check disk space"""
        result = conn.run('df -h /', hide=True)
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            self.stdout.write(f'  {lines[1]}')

    def check_backup_status(self, conn, config):
        """Check last backup time"""
        install_path = config.get('install_path', '/var/www/mosaic')
        result = conn.run(f'ls -t {install_path}/backups/hourly/db-*.sqlite3 2>/dev/null | head -1', warn=True, hide=True)
        if result.ok and result.stdout.strip():
            latest = result.stdout.strip().split('/')[-1]
            self.stdout.write(self.style.SUCCESS(f'  ✓ Latest backup: {latest}'))

            # Count backups
            for tier in ['hourly', 'daily', 'weekly', 'monthly']:
                count_result = conn.run(f'ls -1 {install_path}/backups/{tier}/db-*.sqlite3 2>/dev/null | wc -l', warn=True, hide=True)
                if count_result.ok:
                    count = count_result.stdout.strip()
                    self.stdout.write(f'    {tier.capitalize()}: {count} backups')
        else:
            self.stdout.write(self.style.WARNING('  ⚠ No backups found'))

    def check_recent_errors(self, conn, config):
        """Check for recent errors in logs"""
        app_name = config.get('app_name', 'mosaic')
        has_errors = False

        # Check Docker container logs for errors
        docker_result = conn.run(
            f'docker logs {app_name} --tail 50 2>&1 | grep -iE "(error|exception|fatal|critical)" | head -5',
            warn=True,
            hide=True
        )
        if docker_result.ok and docker_result.stdout.strip():
            self.stdout.write(self.style.WARNING('  ⚠ Recent errors in Docker logs:'))
            for line in docker_result.stdout.strip().split('\n')[:3]:
                self.stdout.write(f'    {line[:100]}')
            has_errors = True

        # Check systemd service failures
        systemd_result = conn.run(
            'journalctl -u mosaic-app.service --since "1 hour ago" --no-pager -p err 2>/dev/null | tail -5',
            warn=True,
            hide=True
        )
        if systemd_result.ok and systemd_result.stdout.strip():
            self.stdout.write(self.style.WARNING('  ⚠ Recent systemd errors:'))
            for line in systemd_result.stdout.strip().split('\n')[:3]:
                self.stdout.write(f'    {line[:100]}')
            has_errors = True

        # Check nginx error log
        nginx_result = conn.run(
            'tail -50 /var/log/nginx/error.log 2>/dev/null | grep -iE "error|crit|alert|emerg" | tail -5',
            warn=True,
            hide=True
        )
        if nginx_result.ok and nginx_result.stdout.strip():
            self.stdout.write(self.style.WARNING('  ⚠ Recent nginx errors:'))
            for line in nginx_result.stdout.strip().split('\n')[:3]:
                self.stdout.write(f'    {line[:100]}')
            has_errors = True

        if not has_errors:
            self.stdout.write(self.style.SUCCESS('  ✓ No recent errors found'))
