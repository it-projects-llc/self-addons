/** @odoo-module **/

import {registerInstancePatchModel} from "@mail/model/model_core";

registerInstancePatchModel(
    "mail.messaging_notification_handler",
    "itpp_mail/static/src/models/messaging_notification_handler/messaging_notification_handler.js",
    {
        _handleNotificationNeedaction(data) {
            this._super.apply(this, arguments);
            const message = this.messaging.models["mail.message"].insert(
                this.messaging.models["mail.message"].convertData(data)
            );
            if (message.originThread) {
                this._notifyNewChannelMessageWhileOutOfFocus({
                    channel: message.originThread,
                    message: message,
                });
            }
        },
    }
);
