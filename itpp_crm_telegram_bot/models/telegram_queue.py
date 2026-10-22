import json
import time
from datetime import timedelta
from html import escape
from html.parser import HTMLParser

from psycopg2 import IntegrityError

from odoo import api, fields, models
from odoo.tools import mute_logger

from .telegram_bot import TelegramAPIError


class TelegramHTML(HTMLParser):
    """Convert Odoo rich text into supported, independently balanced HTML chunks."""

    tags = {
        "b": "b",
        "strong": "b",
        "i": "i",
        "em": "i",
        "u": "u",
        "s": "s",
        "strike": "s",
        "del": "s",
        "code": "code",
        "pre": "pre",
        "a": "a",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.buffer = ""
        self.has_text = False
        self.ignored = 0

    def _append(self, token, text=False):
        closing = "".join("</%s>" % tag for tag, opening in reversed(self.stack))
        if len(self.buffer) + len(token) + len(closing) > 3500 and self.has_text:
            self.parts.append(self.buffer + closing)
            self.buffer = "".join(opening for tag, opening in self.stack)
            self.has_text = False
        self.buffer += token
        self.has_text |= text

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
        if self.ignored:
            return
        if tag == "br" or tag in ("p", "div", "li"):
            self.handle_data("\n")
        target = self.tags.get(tag)
        if not target or len(self.stack) >= 8:
            return
        opening = "<%s>" % target
        if target == "a":
            href = dict(attrs).get("href", "")
            if not href.startswith(("https://", "http://", "tg://")) or len(href) > 500:
                return
            opening = '<a href="%s">' % escape(href, quote=True)
        self._append(opening)
        self.stack.append((target, opening))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        target = self.tags.get(tag)
        if target and any(item[0] == target for item in self.stack):
            while self.stack:
                current, opening = self.stack.pop()
                self._append("</%s>" % current)
                if current == target:
                    break
        if tag in ("p", "div", "li"):
            self.handle_data("\n")

    def handle_data(self, data):
        if not self.ignored:
            for char in data:
                self._append(escape(char, quote=False), text=not char.isspace())

    def finish(self):
        if self.has_text:
            self.parts.append(
                self.buffer
                + "".join("</%s>" % tag for tag, opening in reversed(self.stack))
            )
        return self.parts


def telegram_html_chunks(html):
    parser = TelegramHTML()
    parser.feed(str(html or ""))
    parser.close()
    return parser.finish()


class TelegramInbox(models.Model):
    _name = "crm.telegram.inbox"
    _description = "Telegram Incoming Update"
    _order = "update_id, id"

    bot_id = fields.Many2one(
        "crm.telegram.bot", required=True, ondelete="cascade", index=True
    )
    update_id = fields.Integer(required=True, index=True)
    payload = fields.Text(required=True)
    processed = fields.Boolean(index=True)
    bundled = fields.Boolean()
    conversation_id = fields.Many2one(
        "crm.telegram.conversation", ondelete="set null", index=True
    )
    error = fields.Char(readonly=True)

    _sql_constraints = [
        (
            "update_unique",
            "unique(bot_id, update_id)",
            "This Telegram update has already been received.",
        ),
    ]

    @api.model
    def _accept(self, bot, payload):
        try:
            with mute_logger("odoo.sql_db"), self.env.cr.savepoint():
                self.create(
                    {
                        "bot_id": bot.id,
                        "update_id": payload["update_id"],
                        "payload": json.dumps(payload),
                    }
                )
        except IntegrityError as error:
            if error.pgcode != "23505":
                raise
        return True

    def _process(self):
        self.ensure_one()
        message = json.loads(self.payload).get("message") or {}
        user = message.get("from") or {}
        chat = message.get("chat") or {}
        if (
            chat.get("type") != "private"
            or not user.get("id")
            or user.get("is_bot")
            or chat.get("id") != user["id"]
        ):
            self.processed = True
            return
        conversations = self.env["crm.telegram.conversation"]
        conversation = conversations.search(
            [("bot_id", "=", self.bot_id.id), ("chat_id", "=", str(chat["id"]))],
            limit=1,
        )
        if not conversation and conversations._is_start(message.get("text")):
            conversation = conversations.create(
                {
                    "bot_id": self.bot_id.id,
                    "chat_id": str(chat["id"]),
                    "user_id": str(user["id"]),
                }
            )
        if conversation:
            conversation._receive(self, message)
        self.write({"processed": True, "error": False})

    @api.model
    def _cron_process(self):
        # One transaction owns state transitions and delivery, across all workers.
        self.env.cr.execute("SELECT pg_try_advisory_xact_lock(153017301)")
        if not self.env.cr.fetchone()[0]:
            return
        failed_bots = set()
        for _unused in range(200):
            inbox = self.search(
                [
                    ("processed", "=", False),
                    ("bot_id.active", "=", True),
                    ("bot_id", "not in", list(failed_bots)),
                ],
                limit=1,
            )
            if not inbox:
                break
            try:
                with self.env.cr.savepoint():
                    inbox._process()
            except Exception:
                # Keep order within a bot; don't expose tokens or personal data in logs.
                inbox.error = "Processing failed; the update will be retried."
                failed_bots.add(inbox.bot_id.id)
        # Never finalise a conversation while an older queued message is unprocessed.
        busy_bots = (
            self.search([("processed", "=", False), ("bot_id.active", "=", True)])
            .mapped("bot_id")
            .ids
        )
        due = self.env["crm.telegram.conversation"].search(
            [
                ("lead_id", "=", False),
                ("deadline", "<=", fields.Datetime.now()),
                ("bot_id.active", "=", True),
                ("bot_id", "not in", busy_bots),
            ],
            limit=100,
        )
        for conversation in due:
            try:
                with self.env.cr.savepoint():
                    conversation._create_lead()
            except Exception:
                # The saved inbox remains intact for retry on the next run.
                conversation.message_ids.filtered(lambda item: not item.bundled).write(
                    {
                        "error": "Lead creation failed; it will be retried.",
                    }
                )
        self.env["crm.telegram.outbox"]._deliver()


class TelegramOutbox(models.Model):
    _name = "crm.telegram.outbox"
    _description = "Telegram Outgoing Message"
    _order = "id"

    bot_id = fields.Many2one(
        "crm.telegram.bot", required=True, ondelete="cascade", index=True
    )
    chat_id = fields.Char(required=True, index=True)
    method = fields.Selection(
        [("sendMessage", "Text"), ("sendDocument", "Attachment")], required=True
    )
    text = fields.Text()
    attachment_id = fields.Many2one("ir.attachment", ondelete="restrict")
    sent = fields.Boolean(index=True)
    attempts = fields.Integer(readonly=True)
    next_attempt = fields.Datetime(index=True)
    error = fields.Char(readonly=True)

    @api.model
    def _deliver(self):
        until = time.monotonic() + 40
        for _unused in range(100):
            if time.monotonic() >= until:
                break
            self.flush()
            self.env["crm.telegram.bot"].flush(["active"])
            # Only select the oldest unsent item in each chat. A failed chat's
            # backlog must never fill the batch and starve other users.
            self.env.cr.execute(
                """
                SELECT item.id
                  FROM crm_telegram_outbox item
                  JOIN crm_telegram_bot bot ON bot.id = item.bot_id
                 WHERE NOT COALESCE(item.sent, FALSE) AND bot.active
                   AND (item.next_attempt IS NULL OR item.next_attempt <= %s)
                   AND NOT EXISTS (
                       SELECT 1 FROM crm_telegram_outbox previous
                        WHERE previous.bot_id = item.bot_id
                          AND previous.chat_id = item.chat_id
                          AND previous.id < item.id
                          AND NOT COALESCE(previous.sent, FALSE)
                   )
                 ORDER BY item.id LIMIT 1
            """,
                [fields.Datetime.now()],
            )
            row = self.env.cr.fetchone()
            if not row:
                break
            item = self.browse(row[0])
            payload = {"chat_id": item.chat_id}
            if item.method == "sendMessage":
                payload.update(text=item.text, parse_mode="HTML")
            try:
                item.bot_id._api(item.method, payload, attachment=item.attachment_id)
            except TelegramAPIError as error:
                item.write(
                    {
                        "attempts": item.attempts + 1,
                        "error": str(error),
                        "next_attempt": fields.Datetime.now()
                        + timedelta(minutes=min(60, 2 ** min(item.attempts, 6))),
                    }
                )
            else:
                item.write({"sent": True, "error": False, "next_attempt": False})
