CRM Telegram Bot
================

Odoo 15 addon for turning a private Telegram conversation into one CRM lead,
then replying through that lead's public chatter. Internal notes stay private.

Configuration
-------------

* Install ``itpp_crm_telegram_bot`` with its CRM dependency.
* As an administrator, open **CRM > Configuration > Telegram > Bots**.
* Create a bot with its BotFather token, company, sales team, collection interval
  in minutes, and English/Russian welcome and thank-you messages.
* Set Odoo's ``web.base.url`` to its public HTTPS address. For multiple databases,
  configure ``dbfilter`` so this hostname selects exactly the intended database.
  Configure the reverse proxy and ``proxy_mode`` as appropriate for your server.
* Save the bot, then click **Register webhook**. This calls Telegram's
  ``setWebhook`` with the generated
  ``https://your-odoo.example/telegram/webhook/<uuid>`` endpoint and a separate
  secret header. No Telegram token appears in the webhook URL.
* Keep **CRM Telegram: process messages and create leads** enabled under
  Settings > Technical > Automation > Scheduled Actions.

The Telegram section is administrator-only because it contains credentials and
raw messages. Salespeople use ordinary CRM leads and chatter. Contacts have a
**Telegram ID** field containing the numeric user ID, rather than @username.
Tokens are stored in the Odoo database; restrict database and backup access.

Messages use Odoo's HTML editor. Bold, italic, underline, strikethrough, links,
code and preformatted text are converted to Telegram HTML. Paragraphs and line
breaks are retained; unsupported formatting is stripped. Long messages are split
into independently balanced HTML messages within Telegram's text limit.

Workflow
--------

1. A private-chat user must send ``/start`` (including Telegram deep-link start
   parameters). The welcome language uses the base client language code:
   ``ru, be, kk, ky, uz, tg, tk, hy, az`` select Russian; everything else selects
   English. Groups, bot senders and updates other than messages are ignored.
2. Their first subsequent message starts a fixed collection window. Further
   messages do not extend it. Repeated ``/start`` before lead creation repeats
   the welcome without starting or extending the timer.
3. When the window ends, the scheduler creates one lead with all collected text,
   captions and supported attachments, and assigns the configured sales team.
   The interval starts at webhook receipt, independent of scheduler delays.
4. An existing contact with the same Telegram ID in this company, or a shared
   contact, is reused (including archived contacts). If several match, the
   oldest is selected. Otherwise a contact named
   ``First_name Last_name (TG id 123456789)`` is created.
5. The thank-you message is queued after successful lead creation. Its language
   uses the most recently received client language. Later messages, including
   ``/start``, go straight to the same lead's chatter. There is no 24-hour reset
   and no further welcome or thank-you message for that conversation.
6. Public chatter comments and their attachments are queued for Telegram while
   the lead belongs to the bot's configured company and sales team. Internal
   notes and automatic tracking notifications are never sent. Incoming messages
   appear as the contact and are never echoed back to Telegram.

A conversation is permanent per bot and user. Leads/contacts linked to a
conversation cannot be deleted; archive them instead. Multiple bots are
supported, including company-specific configuration. Archiving a bot stops its
webhook and queues locally; it does not unregister the webhook at Telegram.

Delivery and supported media
----------------------------

The scheduler runs every minute, so a configured three-minute window normally
creates the lead and queues the thank-you after three to four minutes. Welcome
messages and chatter replies also use the scheduler and normally arrive within
a minute. Busy queues or API failures can add delay.

Incoming updates are committed before the HTTP webhook responds. Repeated
Telegram update IDs are ignored. A PostgreSQL transaction lock serializes worker
processing. Lead creation, linking the conversation and queuing the thank-you
are atomic. Failed updates remain available for retry; later updates for that
bot wait to preserve order. Failures can be inspected under **Incoming Updates**.

Outgoing messages are retained in **Deliveries**, with errors and exponential
retry delays capped at one hour. Message order is preserved per chat. A blocked
or unavailable user does not prevent other chats from receiving replies.
Delivery is at least once: if Telegram accepts a message but the connection or
Odoo transaction fails before recording success, retry can duplicate that
message. Telegram's send API provides no idempotency key. The application queues
only one thank-you per conversation.

Photos, documents, voice/audio, videos, video notes, animations and stickers are
downloaded into Odoo attachments. Location and contact messages become readable
text. Other message types receive a descriptive marker. Files exceeding the
public Telegram API's 20 MB download limit are represented by a marker and
caption; their binary content cannot be imported through this API. Outgoing
chatter attachments are sent as documents, subject to Telegram's upload limits.
Edits, deletions, reactions and message read receipts are not synchronized.
Saved incoming/outgoing records contain personal data and are retained until an
administrator removes them through an appropriate data retention procedure.

Tests
-----

Run against an Odoo 15 installation with PostgreSQL::

    odoo --addons-path=/path/to/odoo/addons,/path/to/self-addons \
        -d telegram_test -i itpp_crm_telegram_bot --without-demo=all \
        --test-enable --test-tags=/itpp_crm_telegram_bot --stop-after-init \
        --max-cron-threads=0

The suite covers languages, /start, fixed collection windows, bundled content,
contact reuse/creation, sales teams, permanent conversations, public/private
chatter, attachments, late messages, update deduplication, retries, rich text,
configuration constraints, access control and HTTP webhook authentication.
Telegram requests are mocked; tests send no messages to a real bot.

References
----------

* Telegram Bot API: https://core.telegram.org/bots/api
* Odoo 15 mail thread API:
  https://github.com/odoo/odoo/blob/15.0/addons/mail/models/mail_thread.py

License: AGPL-3. Maintainer: IT-Projects LLC.
