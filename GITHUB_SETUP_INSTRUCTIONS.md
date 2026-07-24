# GitHub Account Setup Instructions for Palindrome Research Labs

## Phase 1: Create GitHub Account

### Step 1: Access GitHub Signup

**Option A: Use <HOSTNAME> Sandbox Browser**
1. Open browser to: http://<IP_ADDRESS>:8080
2. Navigate to: https://github.com/signup

**Option B: Use Local Browser**
1. Navigate to: https://github.com/signup

### Step 2: Create Email Address

**Recommended: Use Gmail**
1. Go to https://mail.google.com
2. Create account: `palindromeresearch` or `palindrome.research.labs`
3. Suggested email: `palindromeresearch@gmail.com`
4. Save credentials securely

**Alternative: Use Temp Email Service**
- https://temp-mail.org
- https://10minutemail.com
- Note: Temp emails may cause issues with 2FA later

### Step 3: Fill Out Registration Form

**Account Details:**
- **Email**: [email created above]
- **Username**: `palindromerl` (first choice)
  - Alternative: `palindrome-research-labs`
  - Alternative: `palindromeresearch`
- **Password**: [Generate strong password]
  - Use password manager or: `openssl rand -base64 32`

### Step 4: Verify Email

1. Check inbox for verification email from GitHub
2. Click verification link
3. Complete verification process

### Step 5: Complete Profile

1. **Name**: Palindrome Research Labs
2. **Company**: Palindrome Research Labs
3. **Bio**: "Research lab focused on AI agents and language models"
4. **Location**: (optional)
5. **Website**: (optional - can add later)

### Step 6: Setup 2FA (Optional but Recommended)

**If enabling 2FA:**
1. Go to Settings → Password and authentication → Two-factor authentication
2. Choose authenticator app (e.g., Google Authenticator, Authy)
3. **IMPORTANT**: Save recovery codes securely
4. Store in: `~/.openclaw/workspace/credentials/github-palindromerl-2fa-recovery.txt`

### Step 7: Create Personal Access Token

GitHub now requires tokens for git operations:

1. Go to: Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. **Token name**: `agent8088-repo-access`
4. **Expiration**: 90 days or No expiration
5. **Scopes** (select these):
   - [x] `repo` (full control of private repositories)
   - [x] `workflow` (if using GitHub Actions)
6. Click "Generate token"
7. **COPY THE TOKEN IMMEDIATELY** (you won't see it again)
8. Save to: `~/.openclaw/workspace/credentials/github-palindromerl-token.txt`

## Phase 2: Create Repository

### Step 1: Create New Repository

1. Click the "+" icon in top-right → "New repository"
2. **Repository name**: `agent8088`
3. **Description**: "AI agent with fine-tuned tool-calling capabilities"
4. **Visibility**: ✅ **Private**
5. **Do NOT initialize** with README, .gitignore, or license (we already have these)
6. Click "Create repository"

### Step 2: Note Repository URL

You'll see a URL like:
```
https://github.com/palindromerl/agent8088.git
```

Copy this for the next step.

## Phase 3: Push Code to GitHub

### Run These Commands:

```bash
cd /tmp/agent8088-repo

# Add GitHub remote
git remote add origin https://github.com/palindromerl/agent8088.git

# Push to GitHub using token authentication
# When prompted for username: palindromerl
# When prompted for password: [paste your personal access token]
git push -u origin main
```

**Alternative: Use Token in URL** (more convenient but visible in shell history)
```bash
cd /tmp/agent8088-repo

# Replace YOUR_TOKEN with your actual token
git remote add origin https://YOUR_TOKEN@github.com/palindromerl/agent8088.git
git push -u origin main
```

### Verify Push

1. Go to: https://github.com/palindromerl/agent8088
2. Verify files are present
3. Check that README.md is rendered correctly

## Phase 4: Configure Repository Settings

### Repository Settings

1. Go to: https://github.com/palindromerl/agent8088/settings
2. **General**:
   - Description: "AI agent with fine-tuned tool-calling capabilities"
   - Topics: Add these tags:
     - `ai-agent`
     - `llm`
     - `tool-calling`
     - `qwen`
     - `lora`
     - `fine-tuning`

3. **Features** (disable if not needed):
   - [ ] Wikis (uncheck)
   - [ ] Issues (uncheck unless you want issue tracking)
   - [ ] Discussions (uncheck)

4. **Branch Protection** (optional but recommended):
   - Go to Settings → Branches
   - Add rule for `main` branch:
     - [x] Require pull request reviews before merging
     - [x] Dismiss stale pull request approvals when new commits are pushed

## Phase 5: Document Credentials

### Save to: `~/.openclaw/workspace/credentials/github-palindromerl.md`

```markdown
# GitHub - Palindrome Research Labs

## Account Information
- **Username**: palindromerl
- **Email**: [email used]
- **Password**: [password]
- **Profile URL**: https://github.com/palindromerl

## Two-Factor Authentication
- **Status**: [Enabled/Disabled]
- **Recovery Codes**: See `github-palindromerl-2fa-recovery.txt`
- **Authenticator App**: [Google Authenticator/Authy/etc.]

## Personal Access Token
- **Token Name**: agent8088-repo-access
- **Token**: See `github-palindromerl-token.txt`
- **Scopes**: repo, workflow
- **Expiration**: [date or "No expiration"]
- **Created**: 2026-05-29

## Repositories
- **agent8088**: https://github.com/palindromerl/agent8088
  - Visibility: Private
  - Description: AI agent with fine-tuned tool-calling capabilities
  - Topics: ai-agent, llm, tool-calling, qwen, lora, fine-tuning

## Notes
- Keep personal access token secret
- Don't commit token to git repositories
- Rotate token every 90 days for security
- Use token for git operations (not password)
```

## Verification Checklist

- [ ] GitHub account created: `palindromerl`
- [ ] Email verified
- [ ] Personal access token generated and saved
- [ ] Repository created: `agent8088`
- [ ] Repository set to **Private**
- [ ] Code pushed to repository
- [ ] README.md displays correctly
- [ ] Topics added
- [ ] Credentials documented in `~/.openclaw/workspace/credentials/`
- [ ] 2FA recovery codes saved (if enabled)

## Troubleshooting

### Authentication Failed During Push

If you get "Authentication failed" when pushing:

1. Make sure you're using the **personal access token** as password, not your account password
2. GitHub no longer accepts account passwords for git operations
3. Generate a new token if needed (see Step 7 above)

### Username Already Taken

If `palindromerl` is taken, try:
- `palindrome-research-labs`
- `palindromeresearch`
- `palindrome-labs`
- `palindromeRL`

### Repository Already Exists

If you accidentally created a repository with a README:
1. Delete the repository
2. Create a new one **without** initializing any files
3. Push from `/tmp/agent8088-repo`

## Quick Reference

**Repository URL**: https://github.com/palindromerl/agent8088  
**Clone Command**: `git clone https://github.com/palindromerl/agent8088.git`  
**Push Command**: `git push origin main`  
**Token Usage**: Username: `palindromerl`, Password: `[your-token]`

---

Created: 2026-05-29  
Status: Ready for execution
