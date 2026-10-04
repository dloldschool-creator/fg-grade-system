"""Sending parent notices by email (spec §78.4).

One school sending account (a Gmail address and its app password, held by
the ICT Coordinator), configured through the deployment's secrets — the
same environment variables every other secret here uses, never the
database or the code:

    NOTICE_EMAIL_ADDRESS       the sending account, e.g. fgnmhs.shs.notices@gmail.com
    NOTICE_EMAIL_APP_PASSWORD  its 16-character Google app password
    NOTICE_SMTP_HOST           optional, default smtp.gmail.com
    NOTICE_SMTP_PORT           optional, default 465 (implicit TLS)

The adviser's name is the display name and their address is Reply-To, so
a parent's reply reaches the adviser. Nobody is copied (§78.4).

One connection is opened per batch and reused, and a dropped connection is
reopened once before the message is given up on.
"""

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 465
TIMEOUT_SECONDS = 30


class MailNotConfigured(RuntimeError):
    pass


class MailAuthError(RuntimeError):
    """The account refused the login — nothing else in the batch can go
    either, so the sender stops rather than failing every learner."""


@dataclass(frozen=True)
class MailSettings:
    address: str
    app_password: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @classmethod
    def from_env(cls) -> "MailSettings | None":
        address = os.environ.get("NOTICE_EMAIL_ADDRESS", "").strip()
        password = os.environ.get("NOTICE_EMAIL_APP_PASSWORD", "").replace(" ", "")
        if not address or not password:
            return None
        return cls(
            address=address,
            app_password=password,
            host=os.environ.get("NOTICE_SMTP_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST,
            port=int(os.environ.get("NOTICE_SMTP_PORT", DEFAULT_PORT)),
        )


def build_message(
    settings: MailSettings,
    *,
    to: str,
    sender_name: str,
    reply_to: str | None,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    attachment_name: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((sender_name, settings.address))
    message["To"] = to
    if reply_to:
        message["Reply-To"] = reply_to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=settings.address.split("@")[-1])
    message.set_content(body)
    if attachment is not None:
        message.add_attachment(
            attachment, maintype="application", subtype="pdf", filename=attachment_name
        )
    return message


class Mailer:
    """`with Mailer(settings) as mailer: mailer.send(message)`."""

    def __init__(self, settings: MailSettings | None):
        if settings is None:
            raise MailNotConfigured("The school's sending email account isn't set up yet.")
        self.settings = settings
        self._smtp = None

    def _connect(self):
        smtp = smtplib.SMTP_SSL(self.settings.host, self.settings.port, timeout=TIMEOUT_SECONDS)
        try:
            smtp.login(self.settings.address, self.settings.app_password)
        except smtplib.SMTPAuthenticationError as exc:
            smtp.close()
            raise MailAuthError(
                "The sending account refused the login — check the app password."
            ) from exc
        self._smtp = smtp

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, *exc):
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except smtplib.SMTPException:
                self._smtp.close()
        self._smtp = None

    def send(self, message: EmailMessage) -> None:
        try:
            self._smtp.send_message(message)
        except smtplib.SMTPServerDisconnected:
            self._connect()
            self._smtp.send_message(message)
