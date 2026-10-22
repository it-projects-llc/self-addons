from odoo import models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    def _message_post_after_hook(self, message, msg_vals):
        result = super()._message_post_after_hook(message, msg_vals)
        if self.env.context.get("telegram_incoming"):
            return result
        if (
            message.message_type != "comment"
            or not message.subtype_id
            or message.subtype_id.internal
        ):
            return result
        # Creating the Odoo message already checked the user's access to the lead.
        conversations = (
            self.env["crm.telegram.conversation"]
            .sudo()
            .search(
                [
                    ("lead_id", "=", self.id),
                    ("bot_id.active", "=", True),
                ]
            )
        )
        for conversation in conversations:
            bot = conversation.bot_id
            if self.team_id != bot.team_id or self.company_id != bot.company_id:
                continue
            bot._queue_text(conversation.chat_id, message.body)
            for attachment in message.attachment_ids:
                self.env["crm.telegram.outbox"].sudo().create(
                    {
                        "bot_id": bot.id,
                        "chat_id": conversation.chat_id,
                        "method": "sendDocument",
                        "attachment_id": attachment.id,
                    }
                )
        return result
