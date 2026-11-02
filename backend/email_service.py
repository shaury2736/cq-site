import os
import smtplib
import ssl
from email.message import EmailMessage


class EmailServiceError(Exception):
    pass


def send_otp_email(email, otp):
    host = os.environ.get("SMTP_HOST")
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("MAIL_FROM")
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError as error:
        raise EmailServiceError("Email service is not configured") from error

    if not all((host, username, password, sender)):
        raise EmailServiceError("Email service is not configured")

    message = EmailMessage()
    message["Subject"] = "Verify your Cinema Quotient email"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        f"Your Cinema Quotient verification code is {otp}. "
        "It expires in 10 minutes. If you did not request this, ignore this email."
    )

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise EmailServiceError("Could not deliver verification email") from error