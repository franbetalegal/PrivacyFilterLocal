"""Create a GitHub release for Privacy Filter.

Usage:
    python create_release.py

Reads version from VERSION file and changelog from CHANGELOG.md.
Requires GITHUB_TOKEN environment variable.
"""
import json
import os
import re
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

REPO = "franbetalegal/PrivacyFilterLocal"
SCRIPT_DIR = Path(__file__).parent
VERSION_FILE = SCRIPT_DIR / "VERSION"
CHANGELOG_FILE = SCRIPT_DIR / "CHANGELOG.md"
ENV_FILE = SCRIPT_DIR / ".env"


def _token_from_env_file():
    """Read GITHUB_TOKEN from a local .env file, if present.

    Tolerates ``export`` prefixes, surrounding quotes and Windows CRLF line
    endings. Never logs the value.
    """
    if not ENV_FILE.is_file():
        return ""
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "GITHUB_TOKEN":
            return value.strip().strip('"').strip("'").strip()
    return ""


def get_token():
    """Get the GitHub token from the environment, falling back to .env."""
    token = os.environ.get("GITHUB_TOKEN", "") or _token_from_env_file()
    if not token:
        print("Error: GITHUB_TOKEN not set (environment or .env).")
        print("Set it with:")
        print('  $env:GITHUB_TOKEN = "your-token"   (PowerShell)')
        print("  or add GITHUB_TOKEN=your-token to a .env file")
        print("  python create_release.py")
        exit(1)
    return token


def get_version():
    """Read version from VERSION file."""
    if not VERSION_FILE.is_file():
        print(f"Error: {VERSION_FILE} not found.")
        exit(1)
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def get_changelog(version):
    """Extract changelog section for the given version."""
    if not CHANGELOG_FILE.is_file():
        return "No changelog available."

    content = CHANGELOG_FILE.read_text(encoding="utf-8")

    # Find section for this version: ## [X.Y.Z] - YYYY-MM-DD
    pattern = rf"## \[{re.escape(version)}\].*?(?=## \[|\Z)"
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        return f"Version {version} release."

    section = match.group(0).strip()

    # Remove the header line and "How to Update" section
    lines = section.split("\n")
    body_lines = []
    skip = False
    for line in lines:
        if line.startswith("## ["):
            continue
        if "How to Update" in line or "### Automatic Update" in line or "### Manual Update" in line:
            skip = True
            continue
        if skip and line.startswith("## "):
            skip = False
        if not skip:
            body_lines.append(line)

    return "\n".join(body_lines).strip() or f"Version {version} release."


def create_release(token, version, changelog):
    """Create a GitHub release."""
    tag = f"v{version}"
    title = f"v{version}"
    body = f"## What's New\n\n{changelog}\n\n---\n\n## Installation\n\nDownload and run `install.bat`."

    data = json.dumps({
        "tag_name": tag,
        "target_commitish": "main",
        "name": title,
        "body": body,
        "draft": False,
        "prerelease": False,
    }).encode("utf-8")

    req = Request(
        f"https://api.github.com/repos/{REPO}/releases",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "PrivacyFilter-App",
        },
    )

    try:
        with urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            print(f"Release created: {result['html_url']}")
    except URLError as e:
        print(f"Network error: {e}")
        exit(1)
    except Exception as e:
        print(f"Error: {e}")
        exit(1)


if __name__ == "__main__":
    token = get_token()
    version = get_version()
    changelog = get_changelog(version)
    print(f"Creating release for v{version}...")
    create_release(token, version, changelog)
