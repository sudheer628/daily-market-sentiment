import os
import smtplib
import ssl
from email.message import EmailMessage
import logging

logger = logging.getLogger("daily_market_sentiment.emailer")


def send_email(subject: str, body: str) -> bool:
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_APP_PASSWORD")
    recipient = os.getenv("EMAIL_RECIPIENT")
    smtp_server = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "465"))

    if not (sender and password and recipient):
        logger.warning("Email configuration missing; skipping email send.")
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context) as server:
            server.login(sender, password)
            server.send_message(msg)
        logger.info("Email sent: %s -> %s", sender, recipient)
        return True
    except Exception as exc:
        logger.exception("Failed to send email: %s", exc)
        return False
