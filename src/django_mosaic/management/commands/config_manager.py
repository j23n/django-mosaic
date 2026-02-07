"""
Configuration manager for deployment settings.

Handles loading, saving, and gathering deployment configuration with TOML persistence.
Requires Python 3.11+ for tomllib.
"""

import tomllib
from pathlib import Path


# Default configuration values
DEFAULT_INSTALL_PATH = '/var/www/mosaic'
DEFAULT_APP_NAME = 'mosaic'
DEFAULT_GUNICORN_WORKERS = 2
DEFAULT_WSGI_MODULE = 'website.wsgi:application'
DEFAULT_URL_CONF = 'website.urls'
DEFAULT_SSH_USER = 'root'
DEFAULT_SSH_KEY = '~/.ssh/id_rsa'


class ConfigManager:
    """Manages deployment configuration with TOML file persistence."""

    def __init__(self, config_file=None):
        """
        Initialize ConfigManager.

        Args:
            config_file: Path to config file. Defaults to .deployment-config.toml in project root.
        """
        if config_file:
            self.config_file = Path(config_file)
        else:
            # Default to project root
            self.config_file = Path.cwd() / '.deployment-config.toml'

    def load_from_file(self):
        """
        Load configuration from TOML file.

        Returns:
            dict: Configuration dict, or empty dict if file doesn't exist.
        """
        if not self.config_file.exists():
            return {}

        try:
            with open(self.config_file, 'rb') as f:
                return tomllib.load(f)
        except Exception as e:
            print(f"Warning: Failed to load config from {self.config_file}: {e}")
            return {}

    def save_to_file(self, config):
        """
        Save configuration to TOML file (simple writer for flat structures).

        Args:
            config: Configuration dict to save.
        """
        try:
            # Only save non-sensitive persistent config
            config_to_save = {
                'host': config.get('host'),
                'user': config.get('user'),
                'ssh_key': config.get('ssh_key'),
                'install_path': config.get('install_path'),
                'app_name': config.get('app_name'),
                'domain': config.get('domain'),
                'email': config.get('email'),
                'gunicorn_workers': config.get('gunicorn_workers'),
                'wsgi_module': config.get('wsgi_module'),
                'url_conf': config.get('url_conf'),
            }

            # Remove None values
            config_to_save = {k: v for k, v in config_to_save.items() if v is not None}

            # Write simple TOML format
            with open(self.config_file, 'w') as f:
                f.write("# Mosaic Deployment Configuration\n")
                f.write("# Auto-generated - edit with caution\n\n")

                for key, value in sorted(config_to_save.items()):
                    if isinstance(value, str):
                        # Escape quotes and backslashes
                        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
                        f.write(f'{key} = "{escaped}"\n')
                    elif isinstance(value, int):
                        f.write(f'{key} = {value}\n')

            print(f"✓ Configuration saved to {self.config_file}")

        except Exception as e:
            print(f"Warning: Failed to save config to {self.config_file}: {e}")

    def gather_interactively(self, existing_config=None, required_fields=None, stdout=None):
        """
        Gather configuration interactively with prompts.

        Args:
            existing_config: Existing config dict to use as defaults.
            required_fields: List of field names to gather. If None, gathers all setup fields.
            stdout: Django command stdout for styled output.

        Returns:
            dict: Complete configuration.
        """
        if existing_config is None:
            existing_config = {}

        config = existing_config.copy()

        # Determine which fields to gather
        if required_fields is None:
            # Default: all setup fields
            required_fields = [
                'host', 'user', 'ssh_key', 'domain', 'install_path', 'email',
                'app_name', 'gunicorn_workers', 'wsgi_module', 'url_conf'
            ]

        # Define field prompts and defaults
        field_config = {
            'host': {
                'prompt': 'VPS hostname or IP',
                'validator': lambda x: len(x.strip()) > 0,
                'error': 'Host cannot be empty'
            },
            'user': {
                'prompt': 'SSH user',
                'default': DEFAULT_SSH_USER
            },
            'ssh_key': {
                'prompt': 'SSH private key path',
                'default': DEFAULT_SSH_KEY,
                'validator': lambda x: Path(x).expanduser().exists(),
                'error': 'SSH key file does not exist'
            },
            'domain': {
                'prompt': 'Domain name (e.g., blog.example.com)',
                'validator': lambda x: '.' in x and ' ' not in x,
                'error': 'Invalid domain format',
                'required': True
            },
            'install_path': {
                'prompt': 'Installation path',
                'default': DEFAULT_INSTALL_PATH
            },
            'email': {
                'prompt': 'Email for SSL certificate notifications',
                'validator': lambda x: '@' in x and '.' in x.split('@')[1],
                'error': 'Invalid email format',
                'required': True
            },
            'app_name': {
                'prompt': 'Application name',
                'default': DEFAULT_APP_NAME
            },
            'gunicorn_workers': {
                'prompt': 'Number of Gunicorn workers',
                'default': str(DEFAULT_GUNICORN_WORKERS),
                'validator': lambda x: x.isdigit() and int(x) > 0,
                'error': 'Must be a positive integer'
            },
            'wsgi_module': {
                'prompt': 'WSGI module',
                'default': DEFAULT_WSGI_MODULE,
                'validator': lambda x: ':' in x and all(part.strip() for part in x.split(':')),
                'error': 'Must be in format "module.path:application"'
            },
            'url_conf': {
                'prompt': 'URL configuration module',
                'default': DEFAULT_URL_CONF,
                'validator': lambda x: len(x.strip()) > 0 and ' ' not in x,
                'error': 'Must be a valid Python module path'
            },
        }

        # Gather each required field
        for field in required_fields:
            if field in config and config[field]:
                # Already have value (from file or CLI), skip prompt
                continue

            field_info = field_config.get(field, {})
            prompt = field_info.get('prompt', field)
            default = field_info.get('default')
            validator = field_info.get('validator')
            error_msg = field_info.get('error', 'Invalid input')
            required = field_info.get('required', False)

            # Get input with validation
            config[field] = self._get_input_required(
                prompt=prompt,
                default=default,
                validator=validator,
                error_msg=error_msg,
                required=required,
                stdout=stdout
            )

        return config

    def _get_input_required(self, prompt, default=None, validator=None, error_msg='Invalid input', required=False, stdout=None):
        """
        Get input with validation, retry on invalid input.

        Args:
            prompt: Prompt text to display
            default: Default value if user enters nothing
            validator: Function to validate input (returns True if valid)
            error_msg: Error message to display on validation failure
            required: If True, empty input not allowed even without validator
            stdout: Django command stdout for styled output

        Returns:
            str: Validated user input
        """
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
                        if stdout:
                            stdout.write(stdout.style.ERROR(f'  ✗ {error_msg}. Please try again.'))
                        else:
                            print(f'✗ {error_msg}. Please try again.')
                except Exception:
                    if stdout:
                        stdout.write(stdout.style.ERROR(f'  ✗ {error_msg}. Please try again.'))
                    else:
                        print(f'✗ {error_msg}. Please try again.')
            else:
                if user_input or not required:
                    return user_input
                else:
                    if stdout:
                        stdout.write(stdout.style.ERROR('  ✗ This field is required. Please try again.'))
                    else:
                        print('✗ This field is required. Please try again.')

    def get_config(self, cli_args=None, required_fields=None, stdout=None):
        """
        Get complete configuration by merging file, CLI args, and prompts.

        Priority: CLI args > saved config > interactive prompts

        Args:
            cli_args: Dict of CLI arguments to override saved values
            required_fields: List of field names required. If None, gathers all setup fields.
            stdout: Django command stdout for styled output

        Returns:
            dict: Complete configuration
        """
        # Start with saved config
        og_config = self.load_from_file()
        config = og_config

        # Override with CLI args
        if cli_args:
            config.update({k: v for k, v in cli_args.items() if v is not None})

        # Prompt for missing required fields
        config = self.gather_interactively(
            existing_config=config,
            required_fields=required_fields,
            stdout=stdout
        )

        # Save configuration for future use
        if og_config != config:
            print('\n💾 Saving configuration...')
            self.save_to_file(config)

        return config
