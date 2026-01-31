#!/bin/bash

# Modernized Update Script for AI Stack with Volume Backup & GHCR Support
# Version: 1.2
# Usage: ./update.sh [--restore] [--check-only] [--dry-run]

# Configuration
BACKUP_DIR="./backups"
COMPOSE_FILE="docker-compose.yml"
WEBUI_VOLUME="open-webui_data"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
MAX_BACKUPS=7
LLAMA_CACHE_VOLUME="ai-stack_llama_cache"
PRUNE_MODELS=("nomic-ai_*" "unsloth_Qwen*")
MODELS=(
    "https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/resolve/main/GLM-4.7-Flash-Q4_K_M.gguf|unsloth_GLM-4.7-Flash-GGUF_GLM-4.7-Flash-Q4_K_M.gguf"
    "https://huggingface.co/unsloth/Nemotron-3-Nano-30B-A3B-GGUF/resolve/main/Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf|unsloth_Nemotron-3-Nano-30B-A3B-GGUF_Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf"
)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Function to display help
display_help() {
    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  --restore       Restore the latest volume backup"
    echo "  --check-only    Only check for updates, don't apply them"
    echo "  --dry-run       Check for updates without pulling new images"
    echo "  --help, -h      Display this help message"
    exit 0
}

# Parse arguments
RESTORE_MODE=false
CHECK_ONLY=false
DRY_RUN=false
INTERACTIVE=true

if [ $# -gt 0 ]; then
    INTERACTIVE=false
    while [[ $# -gt 0 ]]; do
        case $1 in
            --restore)
                RESTORE_MODE=true
                shift
                ;;
            --check-only)
                CHECK_ONLY=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --help|-h)
                display_help
                ;;
            *)
                echo -e "${RED}✗ Unknown argument: $1${NC}"
                display_help
                ;;
        esac
    done
fi

mkdir -p "$BACKUP_DIR"

# =============================================
# HELPER FUNCTIONS
# =============================================

get_image_info() {
    local image=$1
    local fmt="{{.Id}}|{{index .Config.Labels \"org.opencontainers.image.revision\"}}|{{index .Config.Labels \"org.opencontainers.image.created\"}}"
    docker inspect -f "$fmt" "$image" 2>/dev/null
}

format_id() {
    echo "$1" | cut -d':' -f2 | cut -c1-12
}

format_date() {
    echo "$1" | cut -d'T' -f1
}

cleanup_backups() {
    echo -e "${BLUE}🧹 Pruning old backups (Keeping latest $MAX_BACKUPS)...${NC}"
    ls -t "$BACKUP_DIR/${WEBUI_VOLUME}_"*.tar.gz 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs -I {} rm -f "{}"
}

# =============================================
# VOLUME BACKUP FUNCTIONS
# =============================================

backup_volume() {
    local volume_name=$1
    local backup_path="$BACKUP_DIR/${volume_name}_$TIMESTAMP.tar.gz"
    
    echo -e "${GREEN}Creating backup of volume $volume_name...${NC}"
    
    docker run --rm \
        -v "$volume_name:/data" \
        -v "$(pwd)/$BACKUP_DIR:/backup" \
        busybox tar -czf "/backup/${volume_name}_$TIMESTAMP.tar.gz" -C /data .
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Volume backed up to $backup_path${NC}"
        cleanup_backups
    else
        echo -e "${RED}✗ Volume backup failed${NC}"
        return 1
    fi
}

restore_volume() {
    local volume_name=$1
    echo -e "${GREEN}Looking for latest backup for $volume_name...${NC}"
    
    LATEST_BACKUP=$(ls -t "$BACKUP_DIR/${volume_name}_"*.tar.gz 2>/dev/null | head -1)
    
    if [ -z "$LATEST_BACKUP" ]; then
        echo -e "${RED}✗ No backups found for $volume_name${NC}"
        return 1
    fi
    
    echo -e "${YELLOW}Warning: This will OVERWRITE existing data in $volume_name. Continue? (y/n)${NC}"
    read -r response
    if [[ ! "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        echo "Restore cancelled."
        return 0
    fi

    echo -e "${YELLOW}Stopping services...${NC}"
    docker compose down
    
    echo -e "${GREEN}Restoring from $LATEST_BACKUP...${NC}"
    docker run --rm \
        -v "$volume_name:/data" \
        -v "$(pwd)/$BACKUP_DIR:/backup" \
        busybox sh -c "rm -rf /data/* && tar -xzf /backup/$(basename "$LATEST_BACKUP") -C /data"
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Restore successful${NC}"
        echo -e "${YELLOW}Restarting services...${NC}"
        docker compose up -d
    else
        echo -e "${RED}✗ Restore failed${NC}"
        return 1
    fi
}

prune_llama_cache() {
    echo -e "${BLUE}🧹 Checking for removed models in $LLAMA_CACHE_VOLUME...${NC}"
    
    local patterns=""
    for pattern in "${PRUNE_MODELS[@]}"; do
        patterns="$patterns $pattern"
    done

    if [ -n "$patterns" ]; then
        docker run --rm \
            -v "$LLAMA_CACHE_VOLUME:/cache" \
            busybox sh -c "cd /cache && rm -f $patterns"
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ Pruning complete.${NC}"
        else
            echo -e "${YELLOW}! Pruning encountered an error (some files may not exist).${NC}"
        fi
    fi
}

# =============================================
# UPDATE FUNCTIONS
# =============================================

check_for_updates() {
    local mode_text="Checking"
    [ "$DRY_RUN" = true ] && mode_text="Dry-running update check"
    
    echo -e "${BLUE}🔍 $mode_text...${NC}"
    local updated=0
    
    # Map images to services
    local image_mapping
    image_mapping=$(docker compose config | awk '/^  [a-zA-Z0-9_-]+:/ {svc=$1; gsub(/:/, "", svc)} /image:/ {print $2, svc}')
    
    # Get unique images
    local images
    images=$(echo "$image_mapping" | cut -d' ' -f1 | sort -u)
    
    for image in $images; do
        # Find which services use this image
        local affecting_services
        affecting_services=$(echo "$image_mapping" | grep -F "$image " | cut -d' ' -f2- | xargs)
        
        echo -e "${BLUE}Image: $image${NC}"
        echo -e "  Affects: $affecting_services"
        
        # Get current info 
        local current_info=$(get_image_info "$image")
        local current_id=$(echo "$current_info" | cut -d'|' -f1)
        local current_rev=$(echo "$current_info" | cut -d'|' -f2)
        local current_date=$(echo "$current_info" | cut -d'|' -f3)
        
        # Pull (unless in dry run)
        if [ "$DRY_RUN" = false ]; then
            docker pull -q "$image" >/dev/null 2>&1
        fi
        
        # Get latest info
        local latest_info=$(get_image_info "$image")
        local latest_id=$(echo "$latest_info" | cut -d'|' -f1)
        local latest_rev=$(echo "$latest_info" | cut -d'|' -f2)
        local latest_date=$(echo "$latest_info" | cut -d'|' -f3)
        
        if [ "$current_id" != "$latest_id" ]; then
            echo -e "  ${YELLOW}Update Available!${NC}"
            echo -e "  Current: $(format_id "$current_id") [Rev: ${current_rev:-N/A}] ($(format_date "$current_date"))"
            echo -e "  Latest:  $(format_id "$latest_id") [Rev: ${latest_rev:-N/A}] ($(format_date "$latest_date"))"
            updated=1
        else
            echo -e "  ${GREEN}Up to date.${NC} ($(format_id "$current_id"))"
        fi
        echo ""
    done
    return $updated
}

check_model_updates() {
    echo -e "${BLUE}🧠 Checking for Model Updates...${NC}"
    local updated=0

    for model_entry in "${MODELS[@]}"; do
        local url="${model_entry%%|*}"
        local filename="${model_entry##*|}"
        local etag_file="/cache/${filename}.etag"
        
        # Fetch headers string once
        local headers=$(curl -s -I -L "$url")

        # Extract potential remote hashes
        local remote_linked_etag=$(echo "$headers" | grep -i "x-linked-etag" | awk '{print $2}' | tr -d '"\r' | head -n 1)
        local remote_std_etag=$(echo "$headers" | grep -i "^etag:" | awk '{print $2}' | tr -d '"\r' | head -n 1)

        # Get local ETag via Docker to access the volume
        local local_etag=$(docker run --rm -v "${LLAMA_CACHE_VOLUME}:/cache" busybox cat "$etag_file" 2>/dev/null | tr -d '"')

        echo -e "${BLUE}Model: ${filename}${NC}"
        
        if [ -z "$local_etag" ]; then
            echo -e "  ${YELLOW}Local ETag not found (New Model?)${NC}"
            echo -e "  Remote (Linked): ${remote_linked_etag}"
            echo -e "  Remote (Std):    ${remote_std_etag}"
        elif [ "$local_etag" == "$remote_linked_etag" ] || [ "$local_etag" == "$remote_std_etag" ]; then
            echo -e "  ${GREEN}Up to date.${NC} (Hash verified)"
        else
            # Note: Local definition might use sha256 while remote uses git-lfs-sha256. 
            # We alert on mismatch but user must confirm.
            echo -e "  ${YELLOW}Potential Update Available / Hash Mismatch${NC}"
            echo -e "  Local:      ${local_etag:0:12}..."
            echo -e "  Remote LNK: ${remote_linked_etag:0:12}..."
            echo -e "  Remote STD: ${remote_std_etag:0:12}..."
            echo -e "  ${YELLOW}To update: Delete the file in /root/.cache/llama.cpp volume and restart.${NC}"
            updated=1
        fi
        echo ""
    done
    return $updated
}

apply_updates() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}Dry-run complete. No changes applied.${NC}"
        return 0
    fi

    backup_volume "$WEBUI_VOLUME" || exit 1
    echo -e "${GREEN}Applying updates and waiting for health checks...${NC}"
    docker compose up -d --remove-orphans
    
    prune_llama_cache
    
    echo -ne "${YELLOW}Waiting for services to stabilize...${NC}"
    for i in {1..40}; do
        if ! docker compose ps | grep -q "(health: starting)"; then
            echo -e "\n${GREEN}✓ All services are healthy!${NC}"
            break
        fi
        echo -ne "."
        sleep 10
        if [ $i -eq 40 ]; then
            echo -e "\n${RED}✗ Timeout waiting for health. Check 'docker compose ps'.${NC}"
        fi
    done

    docker image prune -f >/dev/null 2>&1
}

# =============================================
# MAIN INTERACTIVE LOOP
# =============================================

if [ "$INTERACTIVE" = true ]; then
    echo -e "${BLUE}=== AI Stack Maintenance ===${NC}"
    check_for_updates
    UPDATES_AVAILABLE=$?
    
    check_model_updates
    MODEL_UPDATES=$?

    if [ $UPDATES_AVAILABLE -eq 1 ]; then
        echo -e "${YELLOW}Updates are available for your stack!${NC}"
        echo "What would you like to do?"
        echo -e "  [${GREEN}U${NC}]pdate now"
        echo -e "  [${BLUE}B${NC}]ackup only"
        echo -e "  [${RED}R${NC}]estore from latest backup"
        echo -e "  [${NC}E${NC}]xit"
        read -p "Choice: " choice
        case $choice in
            [uU]*) apply_updates ;;
            [bB]*) backup_volume "$WEBUI_VOLUME" ;;
            [rR]*) restore_volume "$WEBUI_VOLUME" ;;
            *) exit 0 ;;
        esac
    else
        echo -e "${GREEN}Everything is up to date.${NC}"
        echo ""
        echo "Options:"
        echo -e "  [${BLUE}B${NC}]ackup now"
        echo -e "  [${YELLOW}R${NC}]estore from latest backup"
        echo -e "  [${NC}E${NC}]xit"
        read -p "Choice: " choice
        case $choice in
            [bB]*) backup_volume "$WEBUI_VOLUME" ;;
            [rR]*) restore_volume "$WEBUI_VOLUME" ;;
            *) exit 0 ;;
        esac
    fi
else
    # Non-interactive mode
    if [ "$RESTORE_MODE" = true ]; then
        restore_volume "$WEBUI_VOLUME"
    elif [ "$CHECK_ONLY" = true ]; then
        check_for_updates
        [ $? -eq 1 ] && echo -e "${YELLOW}Updates are available.${NC}" || echo -e "${GREEN}Stack is up to date.${NC}"
    elif [ "$DRY_RUN" = true ]; then
        check_for_updates
    else
        check_for_updates
        if [ $? -eq 1 ]; then
            apply_updates
        else
            echo -e "✓ No updates found."
        fi
    fi
fi

echo -e "${GREEN}Process completed.${NC}"
