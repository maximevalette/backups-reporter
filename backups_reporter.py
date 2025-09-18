#!/usr/bin/env python3
"""
Backups Reporter - Monitor and report on Borg repositories and S3 buckets
"""

import os
import sys
import yaml
import logging
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
import requests
import boto3
from botocore.exceptions import ClientError


@dataclass
class BackupEntry:
    source: str
    name: str
    timestamp: datetime
    size: Optional[int] = None
    type: str = "unknown"


class WebhookNotifier:
    def __init__(self, webhooks: List[str]):
        self.webhooks = webhooks
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'BackupsReporter/1.0'})

    def ping(self, status: str = "start", message: str = ""):
        for webhook in self.webhooks:
            try:
                if "healthchecks.io" in webhook or webhook.endswith("/start") or webhook.endswith("/fail"):
                    if status == "start":
                        url = webhook if webhook.endswith("/start") else f"{webhook}/start"
                    elif status == "fail":
                        url = webhook if webhook.endswith("/fail") else f"{webhook}/fail"
                    else:
                        url = webhook.replace("/start", "").replace("/fail", "")

                    response = self.session.post(url, data=message.encode('utf-8'), timeout=10)
                    response.raise_for_status()
                else:
                    payload = {"status": status, "message": message, "timestamp": datetime.now(timezone.utc).isoformat()}
                    response = self.session.post(webhook, json=payload, timeout=10)
                    response.raise_for_status()

                logging.info(f"Webhook notification sent successfully to {webhook}")
            except requests.RequestException as e:
                logging.error(f"Failed to send webhook notification to {webhook}: {e}")


class BorgRepository:
    def __init__(self, config: Dict[str, Any]):
        self.name = config['name']
        self.repository = config['repository']
        self.passphrase = config.get('passphrase')
        self.calculate_sizes = config.get('calculate_sizes', True)
        self.ssh_strict_host_key_checking = config.get('ssh_strict_host_key_checking', False)
        self.ssh_known_hosts_file = config.get('ssh_known_hosts_file')
        self.mount_point = None

    def mount(self) -> bool:
        try:
            self.mount_point = tempfile.mkdtemp(prefix=f"borg_{self.name}_")

            env = os.environ.copy()
            if self.passphrase:
                env['BORG_PASSPHRASE'] = self.passphrase

            # Configure SSH options for remote repositories
            if self.repository.startswith(('ssh://', 'user@')):
                ssh_options = []

                if not self.ssh_strict_host_key_checking:
                    ssh_options.extend(['-o', 'StrictHostKeyChecking=no'])
                    ssh_options.extend(['-o', 'UserKnownHostsFile=/dev/null'])
                elif self.ssh_known_hosts_file:
                    ssh_options.extend(['-o', f'UserKnownHostsFile={self.ssh_known_hosts_file}'])

                if ssh_options:
                    env['BORG_RSH'] = f"ssh {' '.join(ssh_options)}"

            cmd = ['borg', 'mount', self.repository, self.mount_point]
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)

            if result.returncode == 0:
                logging.info(f"Successfully mounted Borg repository {self.name} at {self.mount_point}")
                return True
            else:
                logging.error(f"Failed to mount Borg repository {self.name}: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logging.error(f"Timeout while mounting Borg repository {self.name}")
            return False
        except Exception as e:
            logging.error(f"Error mounting Borg repository {self.name}: {e}")
            return False

    def unmount(self):
        if self.mount_point and os.path.exists(self.mount_point):
            try:
                subprocess.run(['borg', 'umount', self.mount_point], timeout=60)
                os.rmdir(self.mount_point)
                logging.info(f"Unmounted Borg repository {self.name}")
            except Exception as e:
                logging.error(f"Error unmounting Borg repository {self.name}: {e}")

    def _calculate_directory_size(self, directory_path: Path) -> int:
        """Calculate the total size of a directory recursively."""
        total_size = 0
        file_count = 0

        try:
            # Use os.walk for better performance on large directories
            import os
            for root, dirs, files in os.walk(directory_path):
                for file in files:
                    try:
                        file_path = os.path.join(root, file)
                        file_size = os.path.getsize(file_path)
                        total_size += file_size
                        file_count += 1

                        # Log progress every 10000 files for very large archives
                        if file_count % 10000 == 0:
                            logging.info(f"Processed {file_count:,} files, current size: {total_size:,} bytes")

                    except (OSError, PermissionError):
                        # Skip files that can't be accessed
                        continue

            logging.info(f"Archive scan complete: {file_count:,} files, total size: {total_size:,} bytes")

        except Exception as e:
            logging.warning(f"Error calculating size for {directory_path}: {e}")

        return total_size

    def list_archives(self, limit: int = 10) -> List[BackupEntry]:
        if not self.mount_point:
            return []

        entries = []
        try:
            for item in Path(self.mount_point).iterdir():
                if item.is_dir():
                    stat = item.stat()

                    # Conditionally calculate directory size
                    archive_size = None
                    if self.calculate_sizes:
                        logging.info(f"Calculating size for Borg archive: {item.name}")
                        archive_size = self._calculate_directory_size(item)
                        logging.info(f"Archive {item.name} size: {archive_size} bytes")

                    entries.append(BackupEntry(
                        source=f"borg:{self.name}",
                        name=item.name,
                        timestamp=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        size=archive_size,
                        type="borg_archive"
                    ))

            entries.sort(key=lambda x: x.timestamp, reverse=True)
            return entries[:limit]
        except Exception as e:
            logging.error(f"Error listing archives from Borg repository {self.name}: {e}")
            return []


class S3Bucket:
    def __init__(self, config: Dict[str, Any]):
        self.name = config['name']
        self.bucket = config['bucket']
        self.prefix = config.get('prefix', '')
        self.region = config.get('region', 'us-east-1')
        self.access_key = config.get('access_key')
        self.secret_key = config.get('secret_key')
        self.endpoint_url = config.get('endpoint_url')

    def list_objects(self, limit: int = 10) -> List[BackupEntry]:
        try:
            session_kwargs = {}
            if self.access_key and self.secret_key:
                session_kwargs.update({
                    'aws_access_key_id': self.access_key,
                    'aws_secret_access_key': self.secret_key
                })

            session = boto3.Session(**session_kwargs)

            client_kwargs = {'region_name': self.region}
            if self.endpoint_url:
                client_kwargs['endpoint_url'] = self.endpoint_url

            s3 = session.client('s3', **client_kwargs)

            paginator = s3.get_paginator('list_objects_v2')
            page_kwargs = {'Bucket': self.bucket}
            if self.prefix:
                page_kwargs['Prefix'] = self.prefix

            entries = []
            for page in paginator.paginate(**page_kwargs):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        entries.append(BackupEntry(
                            source=f"s3:{self.name}",
                            name=obj['Key'],
                            timestamp=obj['LastModified'],
                            size=obj['Size'],
                            type="s3_object"
                        ))

            entries.sort(key=lambda x: x.timestamp, reverse=True)
            return entries[:limit]
        except ClientError as e:
            logging.error(f"Error listing objects from S3 bucket {self.name}: {e}")
            return []
        except Exception as e:
            logging.error(f"Unexpected error listing objects from S3 bucket {self.name}: {e}")
            return []


class EmailReporter:
    def __init__(self, config: Dict[str, Any]):
        self.smtp_server = config['smtp_server']
        self.smtp_port = config.get('smtp_port', 587)
        self.username = config.get('username')
        self.password = config.get('password')
        self.from_email = config['from_email']
        self.to_emails = config['to_emails']
        self.use_tls = config.get('use_tls', True)

    def send_report(self, entries: List[BackupEntry]):
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Backups Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            msg['From'] = self.from_email
            msg['To'] = ', '.join(self.to_emails)

            html_content = self._generate_html_report(entries)
            html_part = MIMEText(html_content, 'html')
            msg.attach(html_part)

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(msg)

            logging.info(f"Report sent successfully to {len(self.to_emails)} recipients")
        except Exception as e:
            logging.error(f"Failed to send email report: {e}")
            raise

    def _generate_html_report(self, entries: List[BackupEntry]) -> str:
        def format_size(size: Optional[int]) -> str:
            if size is None:
                return "N/A"
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if size < 1024.0:
                    return f"{size:.1f} {unit}"
                size /= 1024.0
            return f"{size:.1f} PB"

        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Backups Report</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1 {{ color: #333; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
                th {{ background-color: #f2f2f2; font-weight: bold; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                .borg {{ color: #0066cc; }}
                .s3 {{ color: #ff6600; }}
                .timestamp {{ font-family: monospace; }}
                .size {{ text-align: right; }}
            </style>
        </head>
        <body>
            <h1>Backups Report</h1>
            <p>Generated on: {timestamp}</p>
            <p>Total entries: {total_entries}</p>

            <table>
                <thead>
                    <tr>
                        <th>Source</th>
                        <th>Name</th>
                        <th>Timestamp (UTC)</th>
                        <th>Size</th>
                        <th>Type</th>
                    </tr>
                </thead>
                <tbody>
        """.format(
            timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            total_entries=len(entries)
        )

        for entry in entries:
            source_class = 'borg' if entry.source.startswith('borg:') else 's3'
            html += """
                    <tr>
                        <td class="{source_class}">{source}</td>
                        <td>{name}</td>
                        <td class="timestamp">{timestamp}</td>
                        <td class="size">{size}</td>
                        <td>{entry_type}</td>
                    </tr>
            """.format(
                source_class=source_class,
                source=entry.source,
                name=entry.name,
                timestamp=entry.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                size=format_size(entry.size),
                entry_type=entry.type
            )

        html += """
                </tbody>
            </table>
        </body>
        </html>
        """
        return html


class BackupsReporter:
    def __init__(self, config_path: str):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        self.setup_logging()
        self.webhook_notifier = None
        if 'webhooks' in self.config:
            self.webhook_notifier = WebhookNotifier(self.config['webhooks'])

    def setup_logging(self):
        log_level = self.config.get('log_level', 'INFO').upper()
        logging.basicConfig(
            level=getattr(logging, log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    def run(self):
        if self.webhook_notifier:
            self.webhook_notifier.ping("start", "Backups report generation started")

        try:
            entries = []
            limit_per_source = self.config.get('entries_per_source', 10)

            # Process Borg repositories
            borg_repos = []
            for repo_config in self.config.get('borg_repositories', []):
                repo = BorgRepository(repo_config)
                if repo.mount():
                    borg_repos.append(repo)
                    entries.extend(repo.list_archives(limit_per_source))

            # Process S3 buckets
            for bucket_config in self.config.get('s3_buckets', []):
                bucket = S3Bucket(bucket_config)
                entries.extend(bucket.list_objects(limit_per_source))

            # Sort all entries by timestamp
            entries.sort(key=lambda x: x.timestamp, reverse=True)

            # Limit total entries
            max_entries = self.config.get('max_total_entries', 100)
            entries = entries[:max_entries]

            # Send email report
            if 'email' in self.config:
                reporter = EmailReporter(self.config['email'])
                reporter.send_report(entries)

            # Cleanup Borg mounts
            for repo in borg_repos:
                repo.unmount()

            if self.webhook_notifier:
                self.webhook_notifier.ping("success", f"Backups report generated successfully with {len(entries)} entries")

            logging.info(f"Backups report generation completed successfully with {len(entries)} entries")

        except Exception as e:
            logging.error(f"Error during backups report generation: {e}")
            if self.webhook_notifier:
                self.webhook_notifier.ping("fail", f"Backups report generation failed: {str(e)}")
            raise


def main():
    # Default config file path
    default_config = "config.yaml"

    if len(sys.argv) > 2:
        print("Usage: backups_reporter.py [config_file]")
        print("If no config file is specified, 'config.yaml' will be used.")
        sys.exit(1)

    config_file = sys.argv[1] if len(sys.argv) == 2 else default_config

    if not os.path.exists(config_file):
        print(f"Config file not found: {config_file}")
        if config_file == default_config:
            print("Create a config.yaml file or specify a different config file path.")
        sys.exit(1)

    try:
        reporter = BackupsReporter(config_file)
        reporter.run()
    except Exception as e:
        logging.error(f"Application failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()