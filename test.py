"""
Standalone mail test for the OBE feedback panel. Standard library only.

Tests, in order:
  1. Raw TCP reachability to smtp.gmail.com on 587 and 465, over IPv4 and IPv6
  2. SMTP 587 (STARTTLS): connect, login, send
  3. SMTP 465 (SSL): connect, login, send
  4. Brevo HTTPS API send (only if BREVO_API_KEY is set)

Reads SMTP_USER, SMTP_PASSWORD, BREVO_API_KEY, MAIL_FROM from the environment.
Anything missing is prompted for.

Usage:
  python test_mail.py recipient@example.com
"""

import getpass
import json
import os
import smtplib
import socket
import ssl
import sys
import urllib.error
import urllib.request
from email.mime.text import MIMEText

HOST = "smtp.gmail.com"
TIMEOUT = 12


def env_or_prompt(name, secret=False, optional=False):
    val = os.environ.get(name, "").strip()
    if val:
        return val
    if optional:
        return ""
    prompt = f"{name}: "
    return (getpass.getpass(prompt) if secret else input(prompt)).strip()


def line(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  ->  {detail}" if detail else ""))


def tcp_test(port, family, fam_name):
    try:
        infos = socket.getaddrinfo(HOST, port, family, socket.SOCK_STREAM)
    except socket.gaierror as e:
        line(f"TCP {fam_name} :{port}", False, f"no {fam_name} address ({e})")
        return
    addr = infos[0][4]
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT)
            s.connect(addr)
            if port == 465:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(s, server_hostname=HOST) as ss:
                    banner = ss.recv(200).decode(errors="replace").strip()
            else:
                banner = s.recv(200).decode(errors="replace").strip()
        line(f"TCP {fam_name} :{port} ({addr[0]})", banner.startswith("220"), banner[:70] or "no banner")
    except Exception as e:
        line(f"TCP {fam_name} :{port} ({addr[0]})", False, repr(e))


def build_msg(sender, to, via):
    msg = MIMEText(f"Test email from the OBE feedback panel server, sent via {via}.", "plain")
    msg["Subject"] = f"OBE panel mail test ({via})"
    msg["From"] = sender
    msg["To"] = to
    return msg


def smtp_test(port, user, pw, to):
    via = f"SMTP {port}"
    try:
        if port == 465:
            s = smtplib.SMTP_SSL(HOST, 465, timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(HOST, 587, timeout=TIMEOUT)
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
        s.login(user, pw)
        s.sendmail(user, [to], build_msg(user, to, via).as_string())
        s.quit()
        line(via, True, f"sent to {to}")
    except smtplib.SMTPAuthenticationError as e:
        line(via, False, f"connected, but login rejected (wrong user/app password): {e.smtp_code}")
    except Exception as e:
        line(via, False, repr(e))


def brevo_test(key, sender, to):
    payload = {
        "sender": {"email": sender, "name": "JUW CSSE OBE"},
        "to": [{"email": to}],
        "subject": "OBE panel mail test (Brevo API)",
        "textContent": "Test email from the OBE feedback panel server, sent via Brevo API.",
    }
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode(),
        headers={"api-key": key, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            line("Brevo API", r.status in (200, 201), r.read().decode()[:120])
    except urllib.error.HTTPError as e:
        line("Brevo API", False, f"HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        line("Brevo API", False, repr(e))


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_mail.py recipient@example.com")
        sys.exit(1)
    to = sys.argv[1]

    print("== 1. Network reachability ==")
    for port in (587, 465):
        tcp_test(port, socket.AF_INET, "IPv4")
        tcp_test(port, socket.AF_INET6, "IPv6")

    print("\n== 2-3. Gmail SMTP ==")
    user = env_or_prompt("SMTP_USER")
    pw = env_or_prompt("SMTP_PASSWORD", secret=True).replace(" ", "")
    smtp_test(587, user, pw, to)
    smtp_test(465, user, pw, to)

    print("\n== 4. Brevo API ==")
    key = env_or_prompt("BREVO_API_KEY", optional=True)
    if key:
        brevo_test(key, os.environ.get("MAIL_FROM", user), to)
    else:
        print("[SKIP] BREVO_API_KEY not set")


if __name__ == "__main__":
    main()