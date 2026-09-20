"""
--------------------------------------------------------
Sends plain-text notification emails via Gmail SMTP.

Requires two environment variables to be set before starting the
backend (never hardcode real credentials in this file):

    SMTP_EMAIL         - the Gmail address to send FROM
    SMTP_APP_PASSWORD  - a 16-character Gmail "App Password"
                          (NOT your normal Gmail password)

How to get an App Password:
  1. Turn on 2-Step Verification on the Gmail account:
     https://myaccount.google.com/security
  2. Go to https://myaccount.google.com/apppasswords
  3. Create an app password (name it e.g. "HostelOps") and copy the
     16-character code it gives you.
  4. Set the two environment variables before running the server, e.g.
     (macOS/Linux):
         export SMTP_EMAIL="youraddress@gmail.com"
         export SMTP_APP_PASSWORD="the16charcode"
     (Windows PowerShell):
         $env:SMTP_EMAIL="youraddress@gmail.com"
         $env:SMTP_APP_PASSWORD="the16charcode"
--------------------------------------------------------
"""

import os
import smtplib
from email.mime.text import MIMEText


def send_email(to_email, subject, body):
    """Sends a plain-text email. Raises RuntimeError with a clear message
    if SMTP_EMAIL / SMTP_APP_PASSWORD aren't configured, or the real
    smtplib exception if sending itself fails (bad app password, etc.)."""
    sender = os.environ.get("SMTP_EMAIL")
    app_password = os.environ.get("SMTP_APP_PASSWORD")
    if not sender or not app_password:
        raise RuntimeError(
            "Email is not configured: set the SMTP_EMAIL and SMTP_APP_PASSWORD "
            "environment variables (see the top of email_utils.py for how to "
            "generate a Gmail App Password) before starting the server."
        )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, [to_email], msg.as_string())