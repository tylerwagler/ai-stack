#!/bin/bash
# rotate-logs.sh - Rotate llama-proxy logs
# Version: 1.0
# Usage: ./rotate-logs.sh
# Recommended: Add to crontab for daily execution

LOG_DIR="/home/tyler/ai-stack/llama-proxy/logs"
RETENTION_DAYS=30
ARCHIVE_AFTER_DAYS=7

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=== Log Rotation Starting ==="
echo "Started at: $(date)"
echo "Log directory: $LOG_DIR"
echo ""

# Check if directory exists
if [ ! -d "$LOG_DIR" ]; then
    echo -e "${YELLOW}WARNING: Log directory not found: $LOG_DIR${NC}"
    exit 1
fi

# Archive old logs (older than 7 days)
echo "Archiving logs older than $ARCHIVE_AFTER_DAYS days..."
ARCHIVED=0
while IFS= read -r -d '' file; do
    if [ -f "$file" ] && [[ "$file" == *.log ]] && [[ "$file" != *.gz ]]; then
        gzip "$file"
        echo "  Archived: $(basename "$file")"
        ARCHIVED=$((ARCHIVED + 1))
    fi
done < <(find "$LOG_DIR" -name "*.log" -type f -mtime +$ARCHIVE_AFTER_DAYS -print0 2>/dev/null)

if [ $ARCHIVED -eq 0 ]; then
    echo "  No logs to archive"
else
    echo "  Archived $ARCHIVED log files"
fi

# Delete archived logs older than retention period
echo ""
echo "Deleting archived logs older than $RETENTION_DAYS days..."
DELETED=0
while IFS= read -r -d '' file; do
    rm "$file"
    echo "  Deleted: $(basename "$file")"
    DELETED=$((DELETED + 1))
done < <(find "$LOG_DIR" -name "*.log.gz" -type f -mtime +$RETENTION_DAYS -print0 2>/dev/null)

if [ $DELETED -eq 0 ]; then
    echo "  No logs to delete"
else
    echo "  Deleted $DELETED archived log files"
fi

# Show current log directory size
echo ""
echo "Current log directory usage:"
du -sh "$LOG_DIR"
echo ""
echo "Log file summary:"
ls -lh "$LOG_DIR" | tail -10

echo ""
echo -e "${GREEN}Log rotation complete${NC}"
echo "Completed at: $(date)"
