#!/usr/bin/env python3
"""Create a GitHub Release with ZIP asset.

Usage:
    python create_release.py 1.2.0 --changelog "## What's New\n• Feature X"
    python create_release.py 1.2.0 --changelog-file CHANGELOG.md
    python create_release.py 1.2.0 --dry-run
"""

import argparse
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError


GITHUB_REPO = "franbetalegal/PrivacyFilterLocal"
PROJECT_DIR = Path(__file__).parent


def get_token() -> str:
    """Get GitHub token from environment or user input."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        token = input("GitHub token: ").strip()
    return token


def update_version_file(version: str) -> None:
    """Update the VERSION file."""
    version_file = PROJECT_DIR / "VERSION"
    version_file.write_text(f"{version}\n", encoding="utf-8")
    print(f"[OK] VERSION updated to {version}")


def create_zip(version: str) -> Path:
    """Create a ZIP file of the project."""
    temp_dir = Path(tempfile.mkdtemp(prefix="opf_release_"))
    zip_path = temp_dir / f"PrivacyFilterLocal-v{version}.zip"
    
    # Files/dirs to include
    include_list = [
        "app_local.py",
        "app_update.py",
        "start.bat",
        "install.bat",
        "install.ps1",
        "README.md",
        "VERSION",
        "CHANGELOG.md",
        "create_release.py",
        "privacy-filter/",
    ]
    
    # Files/dirs to exclude
    exclude_list = {
        ".git",
        "__pycache__",
        "*.pyc",
        ".update_temp",
        "*.zip",
    }
    
    print("[...] Creating ZIP archive...")
    
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item_name in include_list:
            item_path = PROJECT_DIR / item_name
            
            if item_path.is_file():
                # Check if excluded
                skip = False
                for exclude in exclude_list:
                    if exclude.startswith("*"):
                        if item_path.name.endswith(exclude[1:]):
                            skip = True
                            break
                if not skip:
                    zf.write(item_path, item_name)
                    print(f"  + {item_name}")
            
            elif item_path.is_dir():
                # Add all files in directory
                for file_path in item_path.rglob("*"):
                    if file_path.is_dir():
                        continue
                    
                    # Check if excluded
                    skip = False
                    for exclude in exclude_list:
                        if exclude.startswith("*"):
                            if file_path.name.endswith(exclude[1:]):
                                skip = True
                                break
                        elif exclude in file_path.parts:
                            skip = True
                            break
                    
                    if not skip:
                        arcname = str(file_path.relative_to(PROJECT_DIR))
                        zf.write(file_path, arcname)
                        print(f"  + {arcname}")
    
    print(f"[OK] ZIP created: {zip_path}")
    return zip_path


def create_github_release(
    version: str,
    changelog: str,
    zip_path: Path,
    token: str,
    dry_run: bool = False,
) -> bool:
    """Create a GitHub Release via API."""
    
    tag_name = f"v{version}"
    release_name = f"Version {version}"
    
    print(f"\n[...] Creating GitHub Release: {tag_name}")
    
    if dry_run:
        print("[DRY RUN] Would create release with:")
        print(f"  Tag: {tag_name}")
        print(f"  Name: {release_name}")
        print(f"  Changelog: {changelog[:100]}...")
        print(f"  ZIP: {zip_path.name}")
        return True
    
    # Create release
    release_data = json.dumps({
        "tag_name": tag_name,
        "name": release_name,
        "body": changelog,
        "draft": False,
        "prerelease": False,
    }).encode("utf-8")
    
    req = Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases",
        data=release_data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "PrivacyFilter-Release",
            "Content-Type": "application/json",
        },
    )
    
    try:
        with urlopen(req, timeout=30) as response:
            release_info = json.loads(response.read().decode("utf-8"))
            upload_url = release_info["upload_url"].split("{")[0]
            release_id = release_info["id"]
            print(f"[OK] Release created (ID: {release_id})")
    except URLError as exc:
        print(f"[ERROR] Failed to create release: {exc}")
        return False
    
    # Upload ZIP asset
    print(f"[...] Uploading {zip_path.name}...")
    
    asset_url = f"{upload_url}?name={zip_path.name}"
    zip_data = zip_path.read_bytes()
    
    req = Request(
        asset_url,
        data=zip_data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "PrivacyFilter-Release",
            "Content-Type": "application/zip",
        },
    )
    
    try:
        with urlopen(req, timeout=120) as response:
            asset_info = json.loads(response.read().decode("utf-8"))
            print(f"[OK] Asset uploaded: {asset_info.get('browser_download_url', '')}")
    except URLError as exc:
        print(f"[ERROR] Failed to upload asset: {exc}")
        return False
    
    print(f"\n[OK] Release published: https://github.com/{GITHUB_REPO}/releases/tag/{tag_name}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Create a GitHub Release")
    parser.add_argument("version", help="Version number (e.g., 1.2.0)")
    parser.add_argument("--changelog", help="Changelog text (markdown)")
    parser.add_argument("--changelog-file", help="Path to changelog file")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually create the release")
    parser.add_argument("--token", help="GitHub token (or set GITHUB_TOKEN env var)")
    
    args = parser.parse_args()
    
    # Validate version format
    try:
        parts = [int(x) for x in args.version.split(".")]
        if len(parts) != 3:
            raise ValueError
    except ValueError:
        print("[ERROR] Invalid version format. Use: MAJOR.MINOR.PATH (e.g., 1.2.0)")
        sys.exit(1)
    
    # Get changelog
    changelog = ""
    if args.changelog:
        changelog = args.changelog.replace("\\n", "\n")
    elif args.changelog_file:
        changelog_file = Path(args.changelog_file)
        if changelog_file.is_file():
            changelog = changelog_file.read_text(encoding="utf-8")
        else:
            print(f"[ERROR] Changelog file not found: {changelog_file}")
            sys.exit(1)
    else:
        print("[ERROR] Provide --changelog or --changelog-file")
        sys.exit(1)
    
    # Get token
    token = args.token or get_token()
    if not token and not args.dry_run:
        print("[ERROR] GitHub token required")
        sys.exit(1)
    
    print(f"=" * 50)
    print(f"  Creating Release: v{args.version}")
    print(f"=" * 50)
    
    # Update VERSION file
    update_version_file(args.version)
    
    # Create ZIP
    zip_path = create_zip(args.version)
    
    # Create GitHub Release
    success = create_github_release(
        version=args.version,
        changelog=changelog,
        zip_path=zip_path,
        token=token,
        dry_run=args.dry_run,
    )
    
    # Cleanup
    zip_path.parent.rmdir()
    
    if success:
        print("\n[OK] Release process complete!")
    else:
        print("\n[ERROR] Release process failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
