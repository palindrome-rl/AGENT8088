#!/bin/bash
#
# Helper script to push agent8088 to GitHub
# Usage: ./push-to-github.sh [username] [token]
#
#

set -e

REPO_DIR="/tmp/agent8088-repo"
GITHUB_USERNAME="${1:-palindromerl}"
GITHUB_TOKEN="${2}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Agent8088 GitHub Push Script ===${NC}\n"

# Check if we're in the right directory
if [ ! -f "$REPO_DIR/agent8088" ]; then
    echo -e "${RED}Error: agent8088 not found in $REPO_DIR${NC}"
    echo "Please run this script from the repository directory or check the path."
    exit 1
fi

cd "$REPO_DIR"

# Check if git is initialized
if [ ! -d ".git" ]; then
    echo -e "${RED}Error: Git repository not initialized${NC}"
    echo "Run: git init && git add . && git commit -m 'Initial commit'"
    exit 1
fi

# Check if token is provided
if [ -z "$GITHUB_TOKEN" ]; then
    echo -e "${YELLOW}GitHub Personal Access Token not provided${NC}"
    echo ""
    echo "Usage: $0 [username] [token]"
    echo ""
    echo "To get a token:"
    echo "1. Go to: https://github.com/settings/tokens"
    echo "2. Generate new token (classic)"
    echo "3. Select 'repo' scope"
    echo "4. Copy the token and use it here"
    echo ""
    echo -e "${YELLOW}Attempting interactive push...${NC}"
    echo "You will be prompted for username and password (use token as password)"
    REMOTE_URL="https://github.com/${GITHUB_USERNAME}/agent8088.git"
else
    echo -e "${GREEN}✓ Token provided${NC}"
    REMOTE_URL="https://${GITHUB_TOKEN}@github.com/${GITHUB_USERNAME}/agent8088.git"
fi

# Remove existing origin if present
if git remote | grep -q '^origin$'; then
    echo -e "${YELLOW}Removing existing origin remote...${NC}"
    git remote remove origin
fi

# Add remote
echo -e "${GREEN}Adding GitHub remote: ${GITHUB_USERNAME}/agent8088${NC}"
git remote add origin "$REMOTE_URL"

# Verify we're on main branch
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo -e "${YELLOW}Renaming branch to 'main'...${NC}"
    git branch -M main
fi

# Show what will be pushed
echo ""
echo -e "${GREEN}Files to be pushed:${NC}"
git log --oneline --decorate -1
echo ""
git diff --stat main origin/main 2>/dev/null || echo "(Initial push - no remote branch yet)"
echo ""

# Push
echo -e "${GREEN}Pushing to GitHub...${NC}"
if git push -u origin main; then
    echo ""
    echo -e "${GREEN}✓ Successfully pushed to GitHub!${NC}"
    echo ""
    echo "Repository URL: https://github.com/${GITHUB_USERNAME}/agent8088"
    echo ""
    echo "Next steps:"
    echo "1. Visit: https://github.com/${GITHUB_USERNAME}/agent8088/settings"
    echo "2. Set repository to Private"
    echo "3. Add description: 'AI agent with fine-tuned tool-calling capabilities'"
    echo "4. Add topics: ai-agent, llm, tool-calling, qwen, lora"
    echo ""
    echo "To clone on another machine:"
    echo "  git clone https://github.com/${GITHUB_USERNAME}/agent8088.git"
    echo ""
else
    echo ""
    echo -e "${RED}✗ Push failed${NC}"
    echo ""
    echo "Common issues:"
    echo "1. Repository doesn't exist - create it at: https://github.com/new"
    echo "2. Invalid token - generate new one at: https://github.com/settings/tokens"
    echo "3. Token lacks 'repo' scope - regenerate with correct permissions"
    echo "4. Repository already initialized - delete and recreate without README"
    echo ""
    exit 1
fi
