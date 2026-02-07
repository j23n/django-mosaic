# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-02-07

### Added
- Automated deployment system with `python manage.py mosaic deployment setup` command
- VPS deployment with Docker containerization
- Nginx reverse proxy configuration with rate limiting
- Automatic SSL certificate provisioning via Let's Encrypt/certbot
- UFW firewall configuration
- Systemd service management for application and backups
- Automated hourly database backups with retention policy (hourly/daily/weekly/monthly)
- Deployment status checker with `python manage.py mosaic deployment status`
- Persistent deployment configuration saved to `.deployment-config.toml`
- Interactive deployment wizard with validation
- Deployment modes: `--auto`, `--explain`, `--dry-run`
- Health checks for services, Docker containers, SSL certificates, and application availability

