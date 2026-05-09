# AI BlackBox Landing Page - Firebase Hosting Deployment Guide

## Prerequisites
- Google Cloud account
- Firebase CLI installed (already done: v15.3.1)

## Step 1: Login to Firebase

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Apps/landing-page
firebase login
```

This will open a browser window for Google authentication.

## Step 2: Create Firebase Project

Option A: Create via Firebase Console (Recommended)
1. Go to https://console.firebase.google.com/
2. Click "Add project"
3. Name it "ai-blackbox-landing" (or your preferred name)
4. Disable Google Analytics (optional for landing page)
5. Wait for project creation

Option B: Create via CLI
```bash
firebase projects:create ai-blackbox-landing
```

## Step 3: Link Project

If you used a different project name, update .firebaserc:
```json
{
  "projects": {
    "default": "your-project-name"
  }
}
```

Or run:
```bash
firebase use --add
```
And select your project.

## Step 4: Deploy

```bash
firebase deploy --only hosting
```

You'll get a URL like: https://ai-blackbox-landing.web.app

## Step 5: Custom Domain (After Purchase)

1. Go to Firebase Console > Hosting
2. Click "Add custom domain"
3. Enter your domain (e.g., aiblackbox.com)
4. Firebase will provide DNS records to add:
   - A record pointing to Firebase IP
   - TXT record for verification
5. Add these records in Google Cloud DNS (or your registrar)
6. Wait for SSL certificate provisioning (usually 24-48 hours)

## Project Structure

```
landing-page/
├── index.html      # Main landing page
├── style.css       # Styles
├── app.js          # JavaScript
├── firebase.json   # Firebase configuration
├── .firebaserc     # Project association
├── server.py       # Local dev server (not deployed)
└── DEPLOYMENT.md   # This file
```

## Useful Commands

```bash
# Preview before deploying
firebase hosting:channel:deploy preview

# View deployment history
firebase hosting:sites:list

# Rollback to previous version
firebase hosting:rollback

# Check current project
firebase projects:list
```

## Pricing

Firebase Hosting free tier includes:
- 10 GB storage
- 360 MB/day data transfer
- Custom domain + SSL
- Global CDN

More than enough for a landing page!

## Troubleshooting

**"Permission denied" error:**
```bash
firebase login --reauth
```

**Project not found:**
```bash
firebase use --clear
firebase use --add
```

**SSL certificate pending:**
- Wait 24-48 hours after adding DNS records
- Verify DNS propagation: https://dnschecker.org
