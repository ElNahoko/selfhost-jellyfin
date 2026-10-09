"""Sends the sign-in codes. Any SMTP provider works (Brevo, Resend, Mailjet, ...): set these in uploads-meta.env
   SMTP_HOST, SMTP_PORT (465 = SSL, 587 = STARTTLS), SMTP_USER, SMTP_PASS, MAIL_FROM ("Name <address>"), BRAND."""
import os, smtplib, ssl
from email.message import EmailMessage
from email.utils import make_msgid

HOST = os.environ.get("SMTP_HOST", "")
PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
USER = os.environ.get("SMTP_USER", "")
PASS = os.environ.get("SMTP_PASS", "")
FROM = os.environ.get("MAIL_FROM", "")
BRAND = os.environ.get("BRAND", "LUMIO")

def configured():
    return bool(HOST and FROM)

def send_code(to, code, site):
    msg = EmailMessage()
    msg["Subject"] = "%s is your %s sign-in code" % (code, BRAND)
    msg["From"] = FROM; msg["To"] = to; msg["Message-ID"] = make_msgid()
    msg.set_content("Your sign-in code for %s: %s\n\nIt works once, for 15 minutes.\nIf you did not ask for it, ignore this email.\n\n%s\n" % (BRAND, code, site))
    msg.add_alternative("""<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:420px;margin:auto;padding:24px;color:#222">
<p style="font-size:15px">Your sign-in code for <b>%s</b>:</p>
<p style="font-size:34px;font-weight:800;letter-spacing:6px;margin:12px 0">%s</p>
<p style="font-size:13px;color:#666">It works once, for 15 minutes. If you did not ask for it, ignore this email.</p>
<p style="font-size:13px"><a href="%s" style="color:#e50914">%s</a></p></div>""" % (BRAND, code, site, site), subtype="html")
    ctx = ssl.create_default_context()
    if PORT == 465:
        with smtplib.SMTP_SSL(HOST, PORT, context=ctx, timeout=20) as s:
            if USER: s.login(USER, PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP(HOST, PORT, timeout=20) as s:
            s.starttls(context=ctx)
            if USER: s.login(USER, PASS)
            s.send_message(msg)
