import base64
import uuid
from urllib.parse import quote

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext

RU_LANGUAGES = {"ru", "be", "kk", "ky", "uz", "tg", "tk", "hy", "az"}


def telegram_language(code):
    return "ru" if (code or "").lower().split("-")[0] in RU_LANGUAGES else "en"


class TelegramAPIError(Exception):
    """Deliberately exclude the token-bearing request URL from error messages."""


class TelegramBot(models.Model):
    _name = "crm.telegram.bot"
    _description = "CRM Telegram Bot"
    _check_company_auto = True

    name = fields.Char(required=True, default="Telegram")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    team_id = fields.Many2one("crm.team", required=True, check_company=True)
    token = fields.Char(required=True, copy=False, groups="base.group_system")
    webhook_key = fields.Char(
        required=True,
        default=lambda self: str(uuid.uuid4()),
        copy=False,
        groups="base.group_system",
    )
    webhook_secret = fields.Char(
        required=True,
        default=lambda self: uuid.uuid4().hex,
        copy=False,
        groups="base.group_system",
    )
    webhook_url = fields.Char(
        compute="_compute_webhook_url", groups="base.group_system"
    )
    collection_minutes = fields.Integer(required=True, default=3)
    welcome_en = fields.Html(
        required=True, default="<p>Hello! Please describe your request.</p>"
    )
    welcome_ru = fields.Html(
        required=True, default="<p>Здравствуйте! Пожалуйста, опишите ваш запрос.</p>"
    )
    thanks_en = fields.Html(
        required=True,
        default="<p>Thank you for contacting us! We will get back to you shortly.</p>",
    )
    thanks_ru = fields.Html(
        required=True, default="<p>Спасибо за обращение! Мы скоро с вами свяжемся.</p>"
    )

    _sql_constraints = [
        ("webhook_key_unique", "unique(webhook_key)", "Webhook URLs must be unique."),
        ("token_unique", "unique(token)", "This bot token is already configured."),
    ]

    @api.depends("webhook_key")
    def _compute_webhook_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for bot in self:
            bot.webhook_url = "%s/telegram/webhook/%s" % (
                base.rstrip("/"),
                bot.webhook_key,
            )

    @api.constrains(
        "collection_minutes", "welcome_en", "welcome_ru", "thanks_en", "thanks_ru"
    )
    def _check_configuration(self):
        for bot in self:
            if bot.collection_minutes < 1:
                raise ValidationError(
                    _("The collection interval must be at least one minute.")
                )
            for name in ("welcome_en", "welcome_ru", "thanks_en", "thanks_ru"):
                if not html2plaintext(bot[name] or "").strip():
                    raise ValidationError(
                        _("Welcome and thank-you messages cannot be empty.")
                    )

    def action_register_webhook(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only administrators can register Telegram webhooks."))
        if not self.webhook_url.startswith("https://"):
            raise UserError(_("Set web.base.url to your public HTTPS address first."))
        try:
            self._api(
                "setWebhook",
                {
                    "url": self.webhook_url,
                    "secret_token": self.webhook_secret,
                    "allowed_updates": ["message"],
                },
            )
        except TelegramAPIError as error:
            raise UserError(str(error)) from error
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Telegram"),
                "message": _("Webhook registered."),
                "type": "success",
            },
        }

    def _api(self, method, payload, attachment=None):
        self.ensure_one()
        # The host is fixed; a Telegram payload can never choose a download host.
        url = "https://api.telegram.org/bot%s/%s" % (self.token, method)
        try:
            if attachment:
                response = requests.post(
                    url,
                    data=payload,
                    files={
                        "document": (
                            attachment.name,
                            base64.b64decode(attachment.datas or b""),
                            attachment.mimetype or "application/octet-stream",
                        )
                    },
                    timeout=20,
                )
            else:
                response = requests.post(url, json=payload, timeout=20)
            result = response.json()
        except (requests.RequestException, ValueError) as error:
            raise TelegramAPIError("Telegram connection or response error.") from error
        if not isinstance(result, dict):
            raise TelegramAPIError("Telegram returned an invalid response.")
        if response.status_code != 200 or not result.get("ok"):
            # Do not store arbitrary upstream text: it could contain credentials.
            raise TelegramAPIError(
                "Telegram API rejected the request (code %s)."
                % result.get("error_code", response.status_code)
            )
        return result.get("result")

    def _download(self, file_id):
        self.ensure_one()
        info = self._api("getFile", {"file_id": file_id})
        path = info.get("file_path")
        if not path or info.get("file_size", 0) > 20 * 1024 * 1024:
            return None
        url = "https://api.telegram.org/file/bot%s/%s" % (
            self.token,
            quote(path, safe="/"),
        )
        try:
            with requests.get(url, timeout=20, stream=True) as response:
                response.raise_for_status()
                content = bytearray()
                for block in response.iter_content(65536):
                    content.extend(block)
                    if len(content) > 20 * 1024 * 1024:
                        return None
                return bytes(content)
        except requests.RequestException as error:
            raise TelegramAPIError("Telegram file download failed.") from error

    def _queue_text(self, chat_id, html):
        from .telegram_queue import telegram_html_chunks

        for chunk in telegram_html_chunks(html):
            self.env["crm.telegram.outbox"].sudo().create(
                {
                    "bot_id": self.id,
                    "chat_id": str(chat_id),
                    "text": chunk,
                    "method": "sendMessage",
                }
            )
