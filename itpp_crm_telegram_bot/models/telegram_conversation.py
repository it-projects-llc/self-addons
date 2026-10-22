import json
from datetime import timedelta
from html import escape

from odoo import _, fields, models

from .telegram_bot import telegram_language


class TelegramConversation(models.Model):
    _name = "crm.telegram.conversation"
    _description = "Telegram Conversation"
    _order = "id"

    bot_id = fields.Many2one(
        "crm.telegram.bot", required=True, ondelete="cascade", index=True
    )
    chat_id = fields.Char(required=True, index=True)
    user_id = fields.Char(required=True)
    first_name = fields.Char()
    last_name = fields.Char()
    language = fields.Selection(
        [("en", "English"), ("ru", "Russian")], default="en", required=True
    )
    deadline = fields.Datetime(index=True)
    lead_id = fields.Many2one("crm.lead", ondelete="restrict", index=True)
    partner_id = fields.Many2one("res.partner", ondelete="restrict")
    message_ids = fields.One2many("crm.telegram.inbox", "conversation_id")

    _sql_constraints = [
        (
            "chat_unique",
            "unique(bot_id, chat_id)",
            "A chat can have only one conversation per bot.",
        ),
    ]

    def _receive(self, inbox, message):
        self.ensure_one()
        user = message["from"]
        self.write(
            {
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
                "language": telegram_language(user.get("language_code")),
            }
        )
        inbox.conversation_id = self
        if self.lead_id:
            self._post_incoming(inbox)
            return
        text = message.get("text", "")
        if self._is_start(text):
            # Repeated /start never extends the collection deadline.
            self.bot_id._queue_text(
                self.chat_id, self.bot_id["welcome_" + self.language]
            )
            inbox.bundled = True
            return
        if not self.deadline:
            self.deadline = inbox.create_date + timedelta(
                minutes=self.bot_id.collection_minutes
            )
        elif inbox.create_date >= self.deadline:
            self._create_lead()
            self._post_incoming(inbox)

    @staticmethod
    def _is_start(text):
        command = (text or "").split(maxsplit=1)[0] if (text or "").strip() else ""
        return command.split("@")[0] == "/start"

    def _incoming_values(self, inbox):
        message = json.loads(inbox.payload)["message"]
        text = message.get("text") or message.get("caption") or ""
        attachments = []
        for kind in (
            "photo",
            "document",
            "voice",
            "audio",
            "video",
            "video_note",
            "sticker",
            "animation",
        ):
            media = message.get(kind)
            if not media:
                continue
            if kind == "photo":
                media = media[-1]
            name = media.get("file_name") or "%s_%s" % (kind, message["message_id"])
            # The public Bot API cannot download files larger than 20 MB.
            if media.get("file_size", 0) > 20 * 1024 * 1024:
                text += "\n[%s: %s; exceeds Telegram's 20 MB download limit]" % (
                    kind,
                    name,
                )
                continue
            content = self.bot_id._download(media["file_id"])
            if content is not None:
                attachments.append((name, content))
            else:
                text += "\n[%s: %s; unavailable for download]" % (kind, name)
        if message.get("location"):
            loc = message["location"]
            text += "\n[Location: %s, %s]" % (loc["latitude"], loc["longitude"])
        if message.get("contact"):
            contact = message["contact"]
            text += "\n[Contact: %s %s; %s]" % (
                contact.get("first_name", ""),
                contact.get("last_name", ""),
                contact.get("phone_number", ""),
            )
        if not text and not attachments:
            text = "[Telegram message: %s]" % ", ".join(
                sorted(set(message) - {"from", "chat", "date", "message_id"})
            )
        return text, attachments

    def _post_incoming(self, inbox):
        self.ensure_one()
        text, attachments = self._incoming_values(inbox)
        self.lead_id.with_context(
            telegram_incoming=True, mail_create_nosubscribe=True
        ).message_post(
            body="<p>%s</p>" % escape(text).replace("\n", "<br/>"),
            author_id=self.partner_id.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            attachments=attachments,
        )
        inbox.bundled = True

    def _create_lead(self):
        self.ensure_one()
        if self.lead_id or not self.deadline:
            return
        bot = self.bot_id
        partner = (
            self.env["res.partner"]
            .with_context(active_test=False)
            .search(
                [
                    ("telegram_id", "=", self.user_id),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", bot.company_id.id),
                ],
                order="id",
                limit=1,
            )
        )
        if not partner:
            name = " ".join(filter(None, [self.first_name, self.last_name]))
            partner = self.env["res.partner"].create(
                {
                    "name": "%s (TG id %s)" % (name or "Telegram", self.user_id),
                    "telegram_id": self.user_id,
                    "company_id": bot.company_id.id,
                }
            )
        pending = self.message_ids.filtered(
            lambda item: item.processed and not item.bundled
        ).sorted("update_id")
        texts, attachments = [], []
        for inbox in pending:
            text, media = self._incoming_values(inbox)
            texts.append(text)
            attachments.extend(media)
        body = "<p>%s</p>" % escape("\n".join(texts)).replace("\n", "<br/>")
        lead = (
            self.env["crm.lead"]
            .with_company(bot.company_id)
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": _("Telegram enquiry: %s") % partner.name,
                    "type": "lead",
                    "partner_id": partner.id,
                    "team_id": bot.team_id.id,
                    "company_id": bot.company_id.id,
                    "description": body,
                    "user_id": False,
                }
            )
        )
        self.write({"lead_id": lead.id, "partner_id": partner.id})
        lead.with_context(
            telegram_incoming=True, mail_create_nosubscribe=True
        ).message_post(
            body=body,
            author_id=partner.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            attachments=attachments,
        )
        pending.write({"bundled": True, "error": False})
        bot._queue_text(self.chat_id, bot["thanks_" + self.language])
