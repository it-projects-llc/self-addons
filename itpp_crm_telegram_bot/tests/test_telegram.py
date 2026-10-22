import base64
from datetime import timedelta
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import requests

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.telegram_bot import TelegramAPIError, telegram_language
from ..models.telegram_queue import telegram_html_chunks


@tagged("post_install", "-at_install")
class TestTelegramBot(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team = cls.env["crm.team"].create(
            {"name": "Telegram Sales", "company_id": cls.env.company.id}
        )
        cls.bot = cls.env["crm.telegram.bot"].create(
            {
                "name": "Test bot",
                "token": "123:test",
                "team_id": cls.team.id,
                "welcome_en": "<p>Hello <strong>there</strong></p>",
                "welcome_ru": "<p>Здравствуйте</p>",
                "thanks_en": "<p>Thank you</p>",
                "thanks_ru": "<p>Спасибо</p>",
            }
        )
        cls.Inbox = cls.env["crm.telegram.inbox"]
        cls.Outbox = cls.env["crm.telegram.outbox"]
        cls.Conversation = cls.env["crm.telegram.conversation"]

    def update(self, text="hello", user_id=1234567890123, language="en", **extra):
        self._update_id = getattr(self, "_update_id", 0) + 1
        message = {
            "message_id": self._update_id,
            "date": 1700000000,
            "from": {
                "id": user_id,
                "first_name": "First",
                "last_name": "Last",
                "language_code": language,
            },
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        }
        message.update(extra)
        return {"update_id": self._update_id, "message": message}

    def receive(self, text="hello", **kwargs):
        update = self.update(text, **kwargs)
        self.Inbox._accept(self.bot, update)
        inbox = self.Inbox.search(
            [("bot_id", "=", self.bot.id), ("update_id", "=", update["update_id"])]
        )
        inbox._process()
        return inbox

    def start_collecting(self, language="en"):
        self.receive("/start source", language=language)
        inbox = self.receive("one", language=language)
        return inbox.conversation_id

    def expire(self, conversation):
        conversation.deadline = fields.Datetime.now() - timedelta(seconds=1)

    def test_language_selection(self):
        for code in (
            "ru",
            "be",
            "kk",
            "ky",
            "uz",
            "tg",
            "tk",
            "hy",
            "az",
            "RU-RU",
            "kk-KZ",
        ):
            self.assertEqual(telegram_language(code), "ru")
        for code in (None, "", "en", "uk", "de-DE"):
            self.assertEqual(telegram_language(code), "en")

    def test_requires_start_and_ignores_groups(self):
        self.receive("before start")
        self.receive("/start", chat={"id": -123, "type": "group"})
        self.assertFalse(self.Conversation.search([("bot_id", "=", self.bot.id)]))
        self.assertFalse(self.Outbox.search([("bot_id", "=", self.bot.id)]))
        start = self.receive("/start@test_bot campaign", language="kk-KZ")
        self.assertEqual(start.conversation_id.language, "ru")
        self.assertFalse(start.conversation_id.deadline)
        self.assertIn(
            "Здравствуйте", self.Outbox.search([("bot_id", "=", self.bot.id)]).text
        )

    def test_bundle_contact_team_and_single_thank_you(self):
        conversation = self.start_collecting()
        deadline = conversation.deadline
        self.receive("two <unsafe>")
        self.receive("/start")
        self.assertEqual(conversation.deadline, deadline)
        self.expire(conversation)
        with patch.object(type(self.bot), "_api", return_value={}) as api:
            self.Inbox._cron_process()
        lead = conversation.lead_id
        self.assertTrue(lead)
        self.assertEqual(lead.team_id, self.team)
        self.assertEqual(lead.partner_id.telegram_id, "1234567890123")
        self.assertEqual(lead.partner_id.name, "First Last (TG id 1234567890123)")
        self.assertIn("one", lead.description)
        self.assertIn("two &lt;unsafe&gt;", lead.description)
        self.assertEqual(
            len(
                lead.message_ids.filtered(
                    lambda m: m.subtype_id == self.env.ref("mail.mt_comment")
                )
            ),
            1,
        )
        thank_you = self.Outbox.search(
            [("bot_id", "=", self.bot.id), ("text", "ilike", "Thank you")]
        )
        self.assertEqual(len(thank_you), 1)
        self.assertTrue(thank_you.sent)
        self.assertTrue(api.called)
        count = self.Outbox.search_count([("bot_id", "=", self.bot.id)])
        self.receive("follow up")
        self.receive("/start")
        self.assertEqual(conversation.lead_id, lead)
        self.assertEqual(
            self.Outbox.search_count([("bot_id", "=", self.bot.id)]), count
        )
        self.assertIn("follow up", "".join(lead.message_ids.mapped("body")))

    def test_reuse_contact_and_russian_thanks(self):
        partner = self.env["res.partner"].create(
            {"name": "Existing", "telegram_id": "1234567890123"}
        )
        conversation = self.start_collecting(language="az")
        self.expire(conversation)
        conversation._create_lead()
        self.assertEqual(conversation.lead_id.partner_id, partner)
        self.assertEqual(
            self.Outbox.search_count(
                [("text", "ilike", "Спасибо"), ("bot_id", "=", self.bot.id)]
            ),
            1,
        )

    def test_message_after_deadline_is_not_bundled_twice(self):
        conversation = self.start_collecting()
        self.expire(conversation)
        self.receive("after deadline")
        lead = conversation.lead_id
        self.assertNotIn("after deadline", lead.description)
        matches = lead.message_ids.filtered(lambda msg: "after deadline" in msg.body)
        self.assertEqual(len(matches), 1)

    def test_duplicate_updates_and_no_duplicate_lead(self):
        payload = self.update("/start")
        self.Inbox._accept(self.bot, payload)
        self.Inbox._accept(self.bot, payload)
        self.assertEqual(self.Inbox.search_count([("bot_id", "=", self.bot.id)]), 1)
        with patch.object(type(self.bot), "_api", return_value={}):
            self.Inbox._cron_process()
        conversation = self.receive("request").conversation_id
        self.expire(conversation)
        with patch.object(type(self.bot), "_api", return_value={}):
            self.Inbox._cron_process()
            lead = conversation.lead_id
            self.Inbox._cron_process()
        self.assertEqual(conversation.lead_id, lead)
        self.assertEqual(
            self.env["crm.lead"].search_count([("team_id", "=", self.team.id)]), 1
        )

    def test_chatter_public_only_team_gate_and_no_echo(self):
        conversation = self.start_collecting()
        self.expire(conversation)
        conversation._create_lead()
        lead = conversation.lead_id
        count = self.Outbox.search_count([])
        lead.message_post(
            body="Private", message_type="comment", subtype_xmlid="mail.mt_note"
        )
        self.assertEqual(self.Outbox.search_count([]), count)
        attachment = self.env["ir.attachment"].create(
            {
                "name": "reply.txt",
                "datas": base64.b64encode(b"reply"),
                "res_model": "crm.lead",
                "res_id": lead.id,
            }
        )
        lead.message_post(
            body="<p>Public reply</p>",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            attachment_ids=attachment.ids,
        )
        self.assertEqual(self.Outbox.search_count([]), count + 2)
        document = self.Outbox.search([("attachment_id", "=", attachment.id)])
        self.assertEqual(document.method, "sendDocument")
        self.receive("incoming reply")
        self.assertEqual(self.Outbox.search_count([]), count + 2)
        lead.team_id = self.env["crm.team"].create({"name": "Another team"})
        lead.message_post(
            body="Other team", message_type="comment", subtype_xmlid="mail.mt_comment"
        )
        self.assertEqual(self.Outbox.search_count([]), count + 2)

    def test_retry_failure_retains_lead_and_order(self):
        conversation = self.start_collecting()
        self.expire(conversation)
        with patch.object(
            type(self.bot), "_api", side_effect=TelegramAPIError("Unavailable")
        ) as api:
            self.Inbox._cron_process()
        self.assertTrue(conversation.lead_id)
        self.assertEqual(api.call_count, 1)
        items = self.Outbox.search([("bot_id", "=", self.bot.id)])
        self.assertFalse(any(items.mapped("sent")))
        self.assertEqual(items[0].attempts, 1)
        items[0].next_attempt = False
        with patch.object(type(self.bot), "_api", return_value={}):
            self.Inbox._cron_process()
        self.assertTrue(all(items.mapped("sent")))

    def test_media_incoming_and_large_file_marker(self):
        conversation = self.start_collecting()
        with patch.object(type(self.bot), "_download", return_value=b"photo content"):
            self.receive(
                "",
                photo=[{"file_id": "photo", "file_size": 10}],
                caption="Photo caption",
            )
            self.receive(
                "",
                document={
                    "file_id": "large",
                    "file_name": "huge.pdf",
                    "file_size": 21 * 1024 * 1024,
                },
            )
            self.expire(conversation)
            conversation._create_lead()
        lead = conversation.lead_id
        self.assertIn("Photo caption", lead.description)
        self.assertIn("20 MB", lead.description)
        attachment = self.env["ir.attachment"].search(
            [("res_model", "=", "crm.lead"), ("res_id", "=", lead.id)]
        )
        self.assertEqual(len(attachment), 1)
        self.assertEqual(base64.b64decode(attachment.datas), b"photo content")

    def test_pending_inbox_defers_finalisation(self):
        conversation = self.start_collecting()
        self.expire(conversation)
        self.Inbox._accept(self.bot, self.update("queued"))
        with patch.object(
            type(self.Inbox), "_process", side_effect=ValueError("broken")
        ):
            with patch.object(type(self.bot), "_api", return_value={}):
                self.Inbox._cron_process()
        self.assertFalse(conversation.lead_id)
        self.assertTrue(self.Inbox.search([("processed", "=", False)]).error)
        with patch.object(type(self.bot), "_api", return_value={}):
            self.Inbox._cron_process()
        self.assertTrue(conversation.lead_id)

    def test_rich_text_and_long_messages(self):
        chunks = telegram_html_chunks(
            "<p><strong>Hello &amp; welcome</strong><br/>"
            '<a href="https://example.com">Link</a><img src="x"/></p>'
        )
        self.assertIn("<b>Hello &amp; welcome</b>", chunks[0])
        self.assertNotIn("<p>", chunks[0])
        self.assertIn('<a href="https://example.com">Link</a>', chunks[0])
        long = "Hello & 😀 " * 1500
        chunks = telegram_html_chunks(
            "<p><strong>%s</strong></p>" % long.replace("&", "&amp;")
        )
        self.assertGreater(len(chunks), 1)
        restored = ""
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 3500)
            restored += "".join(
                ElementTree.fromstring("<root>" + chunk + "</root>").itertext()
            )
        self.assertEqual(restored.strip(), long.strip())
        self.assertFalse(telegram_html_chunks("<p><br/></p>"))

    def test_configuration_and_access(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.bot.collection_minutes = 0
        partner = self.env["res.partner"].create({"name": "Contact"})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            partner.telegram_id = "@username"
        user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Salesperson",
                    "login": "telegram_sales_test",
                    "groups_id": [
                        (6, 0, [self.env.ref("sales_team.group_sale_salesman").id])
                    ],
                }
            )
        )
        with self.assertRaises(AccessError):
            self.bot.with_user(user).read(["token"])
        with self.assertRaises(AccessError):
            self.Inbox.with_user(user).create(
                {"bot_id": self.bot.id, "update_id": 500, "payload": "{}"}
            )

    def test_api_errors_do_not_leak_token(self):
        target = "odoo.addons.itpp_crm_telegram_bot.models.telegram_bot.requests.post"
        with patch(target, side_effect=requests.Timeout("secret token in URL")):
            with self.assertRaises(TelegramAPIError) as caught:
                self.bot._api("sendMessage", {})
        self.assertNotIn("secret", str(caught.exception))
        response = Mock(status_code=429)
        response.json.return_value = {
            "ok": False,
            "error_code": 429,
            "description": self.bot.token,
        }
        with patch(target, return_value=response):
            with self.assertRaises(TelegramAPIError) as caught:
                self.bot._api("sendMessage", {})
        self.assertNotIn(self.bot.token, str(caught.exception))
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"message_id": 4}}
        with patch(target, return_value=response) as post:
            self.assertEqual(
                self.bot._api("sendMessage", {"text": "Hi"}), {"message_id": 4}
            )
        self.assertEqual(post.call_args.kwargs["json"], {"text": "Hi"})

    def test_blocked_chat_backlog_does_not_starve_another_user(self):
        self.Outbox.create(
            [
                {
                    "bot_id": self.bot.id,
                    "chat_id": "blocked",
                    "method": "sendMessage",
                    "text": "Waiting",
                    "next_attempt": fields.Datetime.now() + timedelta(hours=1),
                }
                for number in range(101)
            ]
        )
        other = self.Outbox.create(
            {
                "bot_id": self.bot.id,
                "chat_id": "other",
                "method": "sendMessage",
                "text": "Hi",
            }
        )
        with patch.object(type(self.bot), "_api", return_value={}) as api:
            self.Outbox._deliver()
        self.assertTrue(other.sent)
        self.assertEqual(api.call_count, 1)

    def test_salesperson_can_reply_without_access_to_bot_models(self):
        conversation = self.start_collecting()
        self.expire(conversation)
        conversation._create_lead()
        user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Telegram salesperson",
                    "login": "telegram_reply_salesperson",
                    "email": "salesperson@example.invalid",
                    "groups_id": [
                        (6, 0, [self.env.ref("sales_team.group_sale_salesman").id])
                    ],
                }
            )
        )
        lead = conversation.lead_id
        lead.write({"user_id": user.id, "team_id": self.team.id})
        before = self.Outbox.search_count([])
        lead.with_user(user).message_post(
            body="Sales reply",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        self.assertEqual(self.Outbox.search_count([]), before + 1)
        with self.assertRaises(AccessError):
            self.bot.with_user(user).read(["token"])

    def test_contact_matching_is_company_scoped(self):
        company = self.env["res.company"].create({"name": "Other Telegram company"})
        other_partner = self.env["res.partner"].create(
            {
                "name": "Other company contact",
                "telegram_id": "1234567890123",
                "company_id": company.id,
            }
        )
        conversation = self.start_collecting()
        self.expire(conversation)
        conversation._create_lead()
        self.assertNotEqual(conversation.partner_id, other_partner)
        self.assertEqual(conversation.partner_id.company_id, self.bot.company_id)
