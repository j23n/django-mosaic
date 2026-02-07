#!/bin/bash
set -e

BACKUP_DIR="{{INSTALL_PATH}}/backups"
DB_FILE="{{INSTALL_PATH}}/db.sqlite3"

# Create backup directories
mkdir -p "$BACKUP_DIR"/{hourly,daily,weekly,monthly}

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DATESTAMP=$(date +%Y%m%d)
WEEKSTAMP=$(date +%Y-W%V)
MONTHSTAMP=$(date +%Y-%m)

# Create hourly backup
cp "$DB_FILE" "$BACKUP_DIR/hourly/db-$TIMESTAMP.sqlite3"

# Rotate hourly backups
# If it's the first backup of the day (midnight hour), promote to daily
if [ "$(date +%H)" = "00" ]; then
    cp "$DB_FILE" "$BACKUP_DIR/daily/db-$DATESTAMP.sqlite3"
fi

# If it's Monday midnight, promote to weekly
if [ "$(date +%u)" = "1" ] && [ "$(date +%H)" = "00" ]; then
    cp "$DB_FILE" "$BACKUP_DIR/weekly/db-$WEEKSTAMP.sqlite3"
fi

# If it's the 1st of the month at midnight, promote to monthly
if [ "$(date +%d)" = "01" ] && [ "$(date +%H)" = "00" ]; then
    cp "$DB_FILE" "$BACKUP_DIR/monthly/db-$MONTHSTAMP.sqlite3"
fi

# Cleanup old backups
# Keep last 24 hourly backups
find "$BACKUP_DIR/hourly" -name "db-*.sqlite3" -type f | sort -r | tail -n +25 | xargs -r rm

# Keep last 7 daily backups
find "$BACKUP_DIR/daily" -name "db-*.sqlite3" -type f | sort -r | tail -n +8 | xargs -r rm

# Keep last 4 weekly backups
find "$BACKUP_DIR/weekly" -name "db-*.sqlite3" -type f | sort -r | tail -n +5 | xargs -r rm

# Keep last 12 monthly backups
find "$BACKUP_DIR/monthly" -name "db-*.sqlite3" -type f | sort -r | tail -n +13 | xargs -r rm

echo "Backup completed: $TIMESTAMP"
echo "Hourly: $(ls -1 "$BACKUP_DIR/hourly" | wc -l) backups"
echo "Daily: $(ls -1 "$BACKUP_DIR/daily" | wc -l) backups"
echo "Weekly: $(ls -1 "$BACKUP_DIR/weekly" | wc -l) backups"
echo "Monthly: $(ls -1 "$BACKUP_DIR/monthly" | wc -l) backups"
