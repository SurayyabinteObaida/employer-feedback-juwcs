import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings


def send_magic_link_email(to_email: str, magic_link_url: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your sign-in link — JUW Employer Portal"
    msg["From"] = settings.email_from
    msg["To"] = to_email

    html = f"""
        <p>Hello,</p>
        <p>Use the link below to sign in to the JUW employer portal.
        This link expires in {settings.magic_link_expire_minutes} minutes
        and can only be used once.</p>
        <p><a href="{magic_link_url}">Sign in to the employer portal</a></p>
        <p>If you did not request this, you can ignore this email.</p>
    """
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, [to_email], msg.as_string())