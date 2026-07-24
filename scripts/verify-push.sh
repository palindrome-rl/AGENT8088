#!/bin/bash
#
# Verify GitHub Push Success
# Run after pushing to validate deployment
#

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

GITHUB_USERNAME="${1}"

if [ -z "$GITHUB_USERNAME" ]; then
    echo -e "${YELLOW}Usage: $0 [username]${NC}"
    echo "Example: $0 palindromerl"
    echo ""
    read -p "Enter GitHub username: " GITHUB_USERNAME
fi

REPO_URL="https://github.com/${GITHUB_USERNAME}/agent8088"
API_URL="https://api.github.com/repos/${GITHUB_USERNAME}/agent8088"

echo -e "${GREEN}=== GitHub Repository Verification ===${NC}"
echo ""
echo "Repository: $REPO_URL"
echo ""

# Check if repository exists
echo -e "${YELLOW}Checking repository status...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL")

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Repository exists${NC}"
    
    # Get repository details
    REPO_DATA=$(curl -s "$API_URL")
    
    # Parse JSON (requires jq, but we'll use grep/sed as fallback)
    if command -v jq &> /dev/null; then
        PRIVATE=$(echo "$REPO_DATA" | jq -r '.private')
        DESCRIPTION=$(echo "$REPO_DATA" | jq -r '.description')
        DEFAULT_BRANCH=$(echo "$REPO_DATA" | jq -r '.default_branch')
        PUSHED_AT=$(echo "$REPO_DATA" | jq -r '.pushed_at')
        
        echo ""
        echo "Repository Details:"
        echo "  Visibility: $([ "$PRIVATE" = "true" ] && echo -e "${GREEN}Private ✓${NC}" || echo -e "${RED}Public ✗${NC}")"
        echo "  Description: $DESCRIPTION"
        echo "  Default Branch: $DEFAULT_BRANCH"
        echo "  Last Push: $PUSHED_AT"
    else
        # Fallback without jq
        if echo "$REPO_DATA" | grep -q '"private":true'; then
            echo -e "  Visibility: ${GREEN}Private ✓${NC}"
        else
            echo -e "  Visibility: ${RED}Public ✗${NC}"
        fi
    fi
    
    echo ""
    echo -e "${GREEN}=== Verification Complete ===${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Visit: $REPO_URL"
    echo "2. Verify files are present"
    echo "3. Check Privacy: Settings → Danger Zone → Change visibility"
    echo "4. Update description if needed"
    echo "5. Save credentials to: ~/.openclaw/workspace/credentials/github-${GITHUB_USERNAME}.md"
    echo ""
    
elif [ "$HTTP_CODE" = "404" ]; then
    echo -e "${RED}✗ Repository not found${NC}"
    echo ""
    echo "Possible issues:"
    echo "1. Repository name incorrect"
    echo "2. Username incorrect"
    echo "3. Repository not yet created"
    echo "4. Repository is private (you're not authenticated)"
    echo ""
    echo "To check manually:"
    echo "  Visit: $REPO_URL"
    echo ""
    
elif [ "$HTTP_CODE" = "403" ]; then
    echo -e "${YELLOW}⚠ Repository exists but access restricted${NC}"
    echo "This is normal for private repositories viewed without authentication."
    echo ""
    echo "To verify:"
    echo "  Visit: $REPO_URL"
    echo "  (Log in if prompted)"
    echo ""
    
else
    echo -e "${RED}✗ Unexpected response: HTTP $HTTP_CODE${NC}"
    echo ""
    echo "Check manually:"
    echo "  Visit: $REPO_URL"
    echo ""
fi

# Check local git status
echo ""
echo -e "${YELLOW}Local Repository Status:${NC}"
cd "$(dirname "$0")"

if git rev-parse --git-dir > /dev/null 2>&1; then
    echo "  Branch: $(git branch --show-current)"
    echo "  Commits: $(git rev-list --count HEAD)"
    echo "  Remote: $(git remote get-url origin 2>/dev/null || echo 'Not set')"
    echo ""
    
    # Check if remote is tracking
    if git branch -vv | grep -q '\[origin/'; then
        echo -e "  ${GREEN}✓ Tracking remote branch${NC}"
    else
        echo -e "  ${YELLOW}⚠ Not tracking remote branch${NC}"
    fi
else
    echo -e "  ${RED}✗ Not a git repository${NC}"
fi

echo ""
