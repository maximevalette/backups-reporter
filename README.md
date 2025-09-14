# Backups Reporter

A Python script that monitors and reports on backup status from multiple sources including Borg repositories and S3 buckets. It generates HTML email reports and sends webhook notifications compatible with healthchecks.io.

## Features

- **Borg Repository Monitoring**: Mount and list archives from one or more Borg repositories
- **S3 Bucket Monitoring**: List objects from one or more S3 buckets (including S3-compatible storage like MinIO)
- **HTML Email Reports**: Generate and send comprehensive HTML reports via SMTP
- **Webhook Notifications**: Send start/success/failure notifications to webhooks (healthchecks.io compatible)
- **Flexible Configuration**: YAML-based configuration for all settings
- **Docker Support**: Containerized deployment with multi-architecture support
- **Automated Builds**: GitHub Actions workflow for ARM64 and AMD64 images

## Requirements

- Python 3.8+
- Borg Backup (when monitoring Borg repositories)
- AWS CLI or boto3 credentials (when monitoring S3 buckets)

## Installation

### Local Installation

1. Clone the repository:
```bash
git clone https://github.com/maximevalette/backups-reporter.git
cd backups-reporter
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Copy and customize the configuration:
```bash
cp config.yaml.example config.yaml
# Edit config.yaml with your settings
```

### Docker Installation

Pull the pre-built image:
```bash
docker pull maximevalette/backups-reporter:latest
```

Or build locally:
```bash
docker build -t backups-reporter .
```

## Configuration

Create a `config.yaml` file based on the example. The configuration supports:

### Basic Settings

- `log_level`: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `max_total_entries`: Maximum total entries in the report
- `entries_per_source`: Entries to fetch from each source

### Webhook Notifications

```yaml
webhooks:
  - "https://hc-ping.com/your-uuid-here"  # healthchecks.io
  - "https://your-webhook.com/endpoint"   # Custom webhook
```

For healthchecks.io, the script will automatically append `/start` for start notifications and `/fail` for failure notifications.

### Borg Repositories

```yaml
borg_repositories:
  - name: "home-backup"
    repository: "/path/to/borg/repo"
    passphrase: "your-passphrase"  # Optional, can use BORG_PASSPHRASE env var
```

### S3 Buckets

```yaml
s3_buckets:
  - name: "production-backups"
    bucket: "my-backup-bucket"
    prefix: "backups/"  # Optional
    region: "us-east-1"
    access_key: "your-key"     # Optional, can use AWS CLI/IAM
    secret_key: "your-secret"  # Optional, can use AWS CLI/IAM
    endpoint_url: "https://custom-s3-endpoint.com"  # For S3-compatible storage
```

### Email Configuration

```yaml
email:
  smtp_server: "smtp.gmail.com"
  smtp_port: 587
  use_tls: true
  username: "your-email@gmail.com"
  password: "your-app-password"
  from_email: "backups-reporter@yourcompany.com"
  to_emails:
    - "admin@yourcompany.com"
```

## Usage

### Command Line

```bash
python backup_reporter.py config.yaml
```

### Docker

```bash
# Using bind mount for configuration
docker run -v $(pwd)/config.yaml:/app/config.yaml maximevalette/backups-reporter:latest

# Using environment variables for sensitive data
docker run \
  -e BORG_PASSPHRASE="your-passphrase" \
  -e AWS_ACCESS_KEY_ID="your-key" \
  -e AWS_SECRET_ACCESS_KEY="your-secret" \
  -v $(pwd)/config.yaml:/app/config.yaml \
  maximevalette/backups-reporter:latest
```

### Docker Compose

```yaml
version: '3.8'
services:
  backups-reporter:
    image: maximevalette/backups-reporter:latest
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./borg-repos:/borg-repos:ro  # If using local Borg repos
    environment:
      - BORG_PASSPHRASE=your-passphrase
      - AWS_ACCESS_KEY_ID=your-key
      - AWS_SECRET_ACCESS_KEY=your-secret
    restart: unless-stopped
```

### Cron Job

Add to your crontab for regular reports:

```bash
# Daily at 6 AM
0 6 * * * /usr/bin/python3 /path/to/backup_reporter.py /path/to/config.yaml

# Weekly on Sunday at 8 AM
0 8 * * 0 /usr/bin/python3 /path/to/backup_reporter.py /path/to/config.yaml
```

## Environment Variables

The following environment variables are supported:

- `BORG_PASSPHRASE`: Borg repository passphrase
- `AWS_ACCESS_KEY_ID`: AWS access key
- `AWS_SECRET_ACCESS_KEY`: AWS secret key
- `AWS_DEFAULT_REGION`: Default AWS region

## Report Format

The HTML email report includes:

- Report generation timestamp
- Total number of entries
- Sortable table with:
  - Source (Borg repository or S3 bucket name)
  - Entry name (archive name or object key)
  - Timestamp (UTC)
  - Size (formatted for S3 objects, N/A for Borg archives)
  - Type (borg_archive or s3_object)

## Webhook Notifications

The script sends three types of webhook notifications:

1. **Start**: Sent when the report generation begins
2. **Success**: Sent when the report is generated and sent successfully
3. **Failure**: Sent when an error occurs

For healthchecks.io URLs, the script automatically handles the correct endpoints. For custom webhooks, a JSON payload is sent with `status`, `message`, and `timestamp` fields.

## Error Handling

- Failed Borg mounts are logged but don't stop execution
- S3 access errors are logged per bucket
- Email failures stop execution and trigger failure webhooks
- All mounted Borg repositories are properly unmounted on exit

## Security Considerations

- Store sensitive credentials in environment variables rather than config files
- Use IAM roles when running in AWS environments
- Use app-specific passwords for email providers like Gmail
- Ensure Borg repositories are accessible with appropriate permissions

## Logging

Logs are written to stdout/stderr with configurable levels. In Docker, logs can be viewed with:

```bash
docker logs <container_name>
```

## Troubleshooting

### Common Issues

1. **Borg mount fails**: Check repository path and passphrase
2. **S3 access denied**: Verify credentials and bucket permissions
3. **Email fails**: Check SMTP settings and app passwords
4. **Docker permissions**: Ensure volumes are mounted correctly

### Debug Mode

Set `log_level: DEBUG` in your configuration for verbose logging.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues and questions:

1. Check the troubleshooting section
2. Review logs with DEBUG level enabled
3. Open an issue on GitHub with:
   - Configuration (redacted of sensitive data)
   - Error messages
   - Log output