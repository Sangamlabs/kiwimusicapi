import logging
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from backend_client import backend_client
from config import bot_settings
from keyboards import get_back_keyboard, get_main_menu_keyboard, get_revoke_confirm_keyboard

logger = logging.getLogger(__name__)
router = Router()

START_TEXT = """
✨ <b>Welcome to Kiwi Music API Key Manager!</b>

<b>Kiwi Music API</b> is a centralized, high-speed music infrastructure service providing:
• 🔍 Fast music search & metadata
• ⚡ Yukisbox audio caching (upload once, reuse forever)
• 🎧 High-speed streaming & direct download links
• 🤖 Multi-bot & client support (Python & Go)

<b>API Key Policy:</b>
Each API key receives <b>200 lifetime successful requests</b>.
Failed requests do <i>not</i> consume quota.

Select an option below to get started:
"""

DOCS_TEXT = """
📖 <b>Kiwi Music API Documentation</b>

<b>Base URL:</b> <code>{backend_url}</code>

⚙️ <b>Environment Setup (.env):</b>
<code>KIWIAPI=YOUR_API_KEY</code>

<b>Authentication:</b>
You can authenticate using any of these supported methods:
• Header: <code>KIWIAPI: YOUR_API_KEY</code> (matches your .env!)
• Header: <code>X-API-Key: YOUR_API_KEY</code>
• Header: <code>Authorization: Bearer YOUR_API_KEY</code>
• Query param: <code>?kiwiapi=YOUR_API_KEY</code>

<b>Public Endpoints:</b>
• <code>GET /v1/search?q=song_name</code> (Free)
• <code>GET /v1/track/{track_id}</code> (Free)
• <code>GET /v1/resolve/{track_id}</code> (Billable - 1 quota unit)
• <code>GET /v1/stream/{track_id}</code> (Billable - 302 Redirect)
• <code>GET /v1/usage</code> (Free)
• <code>GET /v1/health</code> (Free)

<b>Python Client Example:</b>
<pre><code class="language-python">import os
import requests

API_URL = "{backend_url}"
API_KEY = os.getenv("KIWIAPI", "YOUR_API_KEY")

headers = {"KIWIAPI": API_KEY}  # or "X-API-Key": API_KEY

# Search
r = requests.get(
    f"{API_URL}/v1/search",
    params={"q": "Believer"},
    headers=headers
)
print(r.json())
</code></pre>

<b>Go Client Example:</b>
<pre><code class="language-go">apiKey := os.Getenv("KIWIAPI")
req, _ := http.NewRequest("GET", "{backend_url}/v1/search?q=Believer", nil)
req.Header.Set("KIWIAPI", apiKey)
resp, _ := (&http.Client{}).Do(req)
</code></pre>
"""

@router.message(CommandStart())
async def handle_start(message: Message):
    await message.answer(START_TEXT, reply_markup=get_main_menu_keyboard())

@router.callback_query(F.data == "btn_main_menu")
async def handle_main_menu_callback(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(START_TEXT, reply_markup=get_main_menu_keyboard())

@router.message(Command("generate"))
@router.callback_query(F.data == "btn_generate")
async def handle_generate(event: Message | CallbackQuery):
    user_id = event.from_user.id
    if isinstance(event, CallbackQuery):
        await event.answer("Checking key status...")

    try:
        data = await backend_client.generate_key(user_id)
        prefix = data.get("key_prefix")
        already_exists = data.get("already_exists", False)

        if already_exists:
            # Case B: User already has an active, valid key
            used = data.get("used_requests", 0)
            total = data.get("quota_limit", 200)
            left = data.get("remaining_requests", total - used)
            api_key = data.get("api_key")

            if api_key:
                key_display = f"""🔑 <b>Your Active API Key:</b>
<code>{api_key}</code>
<i>(👆 Tap key to copy)</i>

⚙️ <b>Add to your project's .env:</b>
<code>KIWIAPI={api_key}</code>
<i>(👆 Tap line to copy)</i>"""
            else:
                key_display = f"• <b>Key Identifier:</b> <code>{prefix}</code>"

            response_text = f"""
⚠️ <b>Active API Key Already Exists!</b>

{key_display}

<b>Quota:</b> {used} / {total} used (<b>{left} remaining</b>)
<b>Status:</b> 🟢 <b>ACTIVE</b>

ℹ️ <i>Policy: Only ONE active API key per user is allowed.</i>
If you wish to obtain a new key with a fresh 200 quota, you can revoke this key below:
"""
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"🗑️ Revoke {prefix}", callback_data=f"req_revoke:{prefix}")],
                    [InlineKeyboardButton(text="« Back to Main Menu", callback_data="btn_main_menu")]
                ]
            )
        else:
            api_key = data.get("api_key")
            quota = data.get("quota_limit", 200)

            response_text = f"""
🎉 <b>New API Key Generated Successfully!</b>

🔑 <b>Your API Key:</b>
<code>{api_key}</code>
<i>(👆 Tap key to copy)</i>

⚙️ <b>Add to your project's .env:</b>
<code>KIWIAPI={api_key}</code>
<i>(👆 Tap line to copy)</i>

<b>Details:</b>
• Key Identifier: <code>{prefix}</code>
• Lifetime Quota: <b>{quota} successful requests</b>
• Status: 🟢 <b>ACTIVE</b>
"""
            kb = get_back_keyboard()

    except Exception as e:
        logger.error("Failed to generate key for %d: %s", user_id, e)
        response_text = "❌ <b>Error:</b> Unable to process API key request. The backend service may be temporarily unavailable."
        kb = get_back_keyboard()

    if isinstance(event, CallbackQuery) and event.message:
        await event.message.edit_text(response_text, reply_markup=kb)
    elif isinstance(event, Message):
        await event.answer(response_text, reply_markup=kb)

@router.message(Command("myapi"))
@router.callback_query(F.data == "btn_my_keys")
async def handle_my_keys(event: Message | CallbackQuery):
    user_id = event.from_user.id
    if isinstance(event, CallbackQuery):
        await event.answer()

    try:
        keys = await backend_client.list_keys(user_id)
        if not keys:
            text = "📋 <b>You have not generated any API keys yet.</b>\nClick below to generate your first key!"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔑 Generate API Key", callback_data="btn_generate")],
                    [InlineKeyboardButton(text="« Back", callback_data="btn_main_menu")]
                ]
            )
        else:
            text = f"📋 <b>Your API Keys ({len(keys)} total):</b>\n\n"
            keyboard_buttons = []
            for idx, k in enumerate(keys, 1):
                if k["status"] == "active":
                    full_key = k.get("api_key")
                    if full_key:
                        key_display = (
                            f"<code>{full_key}</code>\n"
                            f"   <i>(👆 Tap key to copy)</i>\n"
                            f"   ⚙️ <code>KIWIAPI={full_key}</code>\n"
                            f"   <i>(👆 Tap line to copy for .env)</i>"
                        )
                    else:
                        key_display = f"<code>{k['key_prefix']}</code>"

                    text += (
                        f"<b>{idx}. Active Key:</b> 🟢\n"
                        f"   🔑 {key_display}\n"
                        f"   • Quota: {k['used_requests']} / {k['total_quota']} used "
                        f"({k['remaining_requests']} left)\n"
                        f"   • Status: <b>ACTIVE</b>\n\n"
                    )
                    keyboard_buttons.append([
                        InlineKeyboardButton(
                            text=f"🗑️ Revoke Active Key ({k['key_prefix']})",
                            callback_data=f"req_revoke:{k['key_prefix']}"
                        )
                    ])
                else:
                    text += (
                        f"<b>{idx}. {k['key_prefix']}</b> 🔴\n"
                        f"   • Quota: {k['used_requests']} / {k['total_quota']} used "
                        f"({k['remaining_requests']} left)\n"
                        f"   • Status: <b>{k['status'].upper()}</b>\n\n"
                    )

            keyboard_buttons.append([InlineKeyboardButton(text="« Back", callback_data="btn_main_menu")])
            kb = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    except Exception as e:
        logger.error("Error fetching keys for %d: %s", user_id, e)
        text = "❌ <b>Error:</b> Could not retrieve your keys. Backend service unavailable."
        kb = get_back_keyboard()

    if isinstance(event, CallbackQuery) and event.message:
        await event.message.edit_text(text, reply_markup=kb)
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=kb)

@router.message(Command("usage"))
@router.callback_query(F.data == "btn_usage")
async def handle_usage(event: Message | CallbackQuery):
    user_id = event.from_user.id
    if isinstance(event, CallbackQuery):
        await event.answer()

    try:
        data = await backend_client.get_usage(user_id)
        text = f"""
📊 <b>Kiwi Music API Usage Summary</b>

• <b>Key:</b> <code>{data.get('key_prefix')}</code>
• <b>Total Lifetime Quota:</b> {data.get('total_quota')}
• <b>Successful Requests:</b> {data.get('used_requests')}
• <b>Failed Requests:</b> {data.get('failed_requests')} (no quota cost)
• <b>Remaining Requests:</b> <b>{data.get('remaining_requests')}</b>
• <b>Status:</b> <b>{data.get('status', 'ACTIVE').upper()}</b>
"""
    except Exception as e:
        logger.warning("Usage error for %d: %s", user_id, e)
        text = "📊 <b>No active API keys found.</b>\nPlease generate an API key first to track usage."

    if isinstance(event, CallbackQuery) and event.message:
        await event.message.edit_text(text, reply_markup=get_back_keyboard())
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=get_back_keyboard())

@router.message(Command("revoke"))
async def handle_revoke_command(message: Message):
    user_id = message.from_user.id
    try:
        keys = await backend_client.list_keys(user_id)
        active_keys = [k for k in keys if k["status"] == "active"]
        if not active_keys:
            await message.answer(
                "ℹ️ <b>You do not have an active API key to revoke.</b>\nClick below to generate an API key.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🔑 Generate API Key", callback_data="btn_generate")],
                        [InlineKeyboardButton(text="« Main Menu", callback_data="btn_main_menu")]
                    ]
                )
            )
            return

        prefix = active_keys[0]["key_prefix"]
        text = f"⚠️ <b>Are you sure you want to revoke key <code>{prefix}</code>?</b>\n\nThis action cannot be undone. Once revoked, you can generate a fresh replacement key."
        await message.answer(text, reply_markup=get_revoke_confirm_keyboard(prefix))
    except Exception as e:
        logger.error("Error in revoke command: %s", e)
        await message.answer("❌ Error checking keys. Backend service unavailable.")

@router.callback_query(F.data.startswith("req_revoke:"))
async def handle_req_revoke(callback: CallbackQuery):
    await callback.answer()
    prefix = callback.data.split("req_revoke:")[1]
    text = f"⚠️ <b>Are you sure you want to revoke key <code>{prefix}</code>?</b>\n\nThis action cannot be undone. Any applications using this key will immediately lose access. Once revoked, you can generate a new replacement key."
    if callback.message:
        await callback.message.edit_text(text, reply_markup=get_revoke_confirm_keyboard(prefix))

@router.callback_query(F.data.startswith("confirm_revoke:"))
async def handle_confirm_revoke(callback: CallbackQuery):
    await callback.answer()
    prefix = callback.data.split("confirm_revoke:")[1]
    user_id = callback.from_user.id
    try:
        res = await backend_client.revoke_key(user_id, prefix)
        msg = f"✅ <b>Key <code>{prefix}</code> has been permanently revoked.</b>\n\nYou can now generate a new API key with a fresh quota of 200 requests."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔑 Generate New API Key", callback_data="btn_generate")],
                [InlineKeyboardButton(text="« Back to Main Menu", callback_data="btn_main_menu")]
            ]
        )
    except Exception as e:
        logger.error("Revoke failed for %s: %s", prefix, e)
        msg = f"❌ <b>Error:</b> Failed to revoke key {prefix}."
        kb = get_back_keyboard()

    if callback.message:
        await callback.message.edit_text(msg, reply_markup=kb)

@router.message(Command("docs"))
@router.callback_query(F.data == "btn_docs")
async def handle_docs(event: Message | CallbackQuery):
    if isinstance(event, CallbackQuery):
        await event.answer()
    text = DOCS_TEXT.replace("{backend_url}", bot_settings.BACKEND_API_URL)
    if isinstance(event, CallbackQuery) and event.message:
        await event.message.edit_text(text, reply_markup=get_back_keyboard())
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=get_back_keyboard())

@router.message(Command("status"))
@router.callback_query(F.data == "btn_status")
async def handle_status(event: Message | CallbackQuery):
    if isinstance(event, CallbackQuery):
        await event.answer("Checking status...")
    try:
        health = await backend_client.get_health()
        text = f"""
🟢 <b>Kiwi Music API Status: HEALTHY</b>

• <b>Backend API:</b> Operational
• <b>Database:</b> {health.get('database', 'operational').capitalize()}
• <b>Yukisbox Storage:</b> {health.get('storage', 'operational').capitalize()}
• <b>Music Provider:</b> {health.get('jiosaavn', 'operational').capitalize()}
• <b>Version:</b> {health.get('version', '1.0.0')}
"""
    except Exception as e:
        logger.error("Health check failed: %s", e)
        text = "🔴 <b>Kiwi Music API Status: OFFLINE or UNREACHABLE</b>\nThe backend server is currently not responding."

    if isinstance(event, CallbackQuery) and event.message:
        await event.message.edit_text(text, reply_markup=get_back_keyboard())
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=get_back_keyboard())

@router.message(Command("stats", "admin"))
async def handle_admin_stats(message: Message):
    if message.from_user.id != bot_settings.OWNER_ID:
        return

    try:
        stats = await backend_client.get_stats()
        text = f"""
👑 <b>Admin Operational Statistics</b>

• <b>Total Registered Users:</b> {stats.get('total_users', 0)}
• <b>Total API Keys Issued:</b> {stats.get('total_keys', 0)}
• <b>Active Keys:</b> {stats.get('active_keys', 0)}
• <b>Cached Tracks in DB:</b> {stats.get('total_tracks', 0)}
• <b>Total API Usage Events:</b> {stats.get('total_usage_events', 0)}
"""
        await message.answer(text)
    except Exception as e:
        await message.answer(f"❌ Error fetching admin stats: {e}")

