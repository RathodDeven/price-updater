#!/usr/bin/env bash
set -e

git init
git add .
git commit -m "Initial backend scaffold for price updater"

echo "Now create an empty GitHub repo, then run:"
echo "git branch -M main"
echo "git remote add origin <YOUR_GITHUB_REPO_URL>"
echo "git push -u origin main"
