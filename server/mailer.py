"""Sending email - password reset codes - over SMTP. Standard library only.

Set these as environment variables (Vercel -> Settings -> Environment Variables,
then redeploy):

  SMTP_HOST      smtp.gmail.com for Gmail
  SMTP_PORT      587 (STARTTLS, the default) or 465 (SSL)
  SMTP_USER      the address that sends, e.g. a dedicated Gmail account
  SMTP_PASSWORD  for Gmail, a 16-character *app password* - never the account's
                 own password (Google account -> Security -> App passwords)
  MAIL_FROM      optional; defaults to SMTP_USER

On your own computer, with nothing set, codes are printed in the server's
console instead, so the reset flow can be tried without sending real email.
The hosted site never does that: there, unset means "not set up".
"""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

SENDER_NAME = "Leave Planner"
TIMEOUT = 15


class MailError(Exception):
    """Sending failed. The message is safe to show an admin: it never holds the password."""


class Mailer:
    def __init__(self, env=None, console=False):
        env = os.environ if env is None else env
        self.host = (env.get("SMTP_HOST") or "").strip()
        self.user = (env.get("SMTP_USER") or "").strip()
        self.password = env.get("SMTP_PASSWORD") or ""
        self.sender = (env.get("MAIL_FROM") or "").strip() or self.user
        try:
            self.port = int(env.get("SMTP_PORT") or 587)
        except ValueError:
            self.port = 587
        self.console = console

    @property
    def configured(self):
        """Real email is set up."""
        return bool(self.host and self.user and self.password and self.sender)

    @property
    def available(self):
        """Codes can reach people - by email, or (locally only) the console."""
        return self.configured or self.console

    def describe(self):
        """For admins: where codes go, without any secret."""
        if self.configured:
            return "Sending from %s via %s" % (self.sender, self.host)
        return "Testing on this computer — codes are printed in the server console, not emailed" if self.console else "Not set up"

    def send(self, to, subject, text, html=None):
        if not self.configured:
            if self.console:
                print("\n[email to %s] %s\n%s\n" % (to, subject, text), file=sys.stderr, flush=True)
                return
            raise MailError("Email isn't set up on this site")
        msg = EmailMessage()
        msg["From"] = formataddr((SENDER_NAME, self.sender))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=False, usegmt=True)
        msg["Message-ID"] = make_msgid(domain=self.sender.rpartition("@")[2] or None)
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        ctx = ssl.create_default_context()
        try:
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=TIMEOUT)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=TIMEOUT)
            with server:
                if self.port != 465:
                    server.starttls(context=ctx)
                server.login(self.user, self.password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise MailError("The mail server refused the sign-in. For Gmail, SMTP_PASSWORD must be an "
                            "app password and 2-Step Verification must be on for that account.")
        except (smtplib.SMTPException, OSError) as e:
            raise MailError("Couldn't send the email (%s)." % type(e).__name__)
