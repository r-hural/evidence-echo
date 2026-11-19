"""Email channel: SMTP with STARTTLS or SSL; text and HTML parts, the report attached."""
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from evidence_echo.notify import NotifyError

NO_PASSWORD = ("SMTP password missing: set it in the plugin settings "
               "(Evidence Echo → SMTP password; a Gmail app password works)")
REQUIRED = ("host", "port", "user", "from", "to")


def _default_factory(host: str, port: int, security: str):
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    return smtplib.SMTP(host, port, timeout=30)


def build(channel: dict, subject: str, text: str, html: str, attachment: Path | None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = channel["from"]
    msg["To"] = channel["to"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if attachment is not None and Path(attachment).is_file():
        path = Path(attachment)
        msg.add_attachment(path.read_bytes(), maintype="text", subtype="html", filename=path.name)
    return msg


def send(channel: dict, password: str, subject: str, text: str, html: str,
         attachment: Path | None = None, smtp_factory=None) -> None:
    missing = [k for k in REQUIRED if not channel.get(k)]
    if missing:
        raise NotifyError(f"email channel is missing {', '.join(missing)} "
                          "(set it with: ev schedule channel email ...)")
    if not password:
        raise NotifyError(NO_PASSWORD)
    security = channel.get("security", "starttls")
    factory = smtp_factory or _default_factory
    msg = build(channel, subject, text, html, attachment)
    try:
        server = factory(channel["host"], int(channel["port"]), security)
        try:
            if security != "ssl":
                server.starttls(context=ssl.create_default_context())
            server.login(channel["user"], password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise NotifyError(f"email failed: {str(exc).replace(password, '***')}") from None
