from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu inline keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔑 Generate API Key", callback_data="btn_generate"),
                InlineKeyboardButton(text="📋 My API Keys", callback_data="btn_my_keys")
            ],
            [
                InlineKeyboardButton(text="📊 API Usage", callback_data="btn_usage"),
                InlineKeyboardButton(text="📖 Documentation", callback_data="btn_docs")
            ],
            [
                InlineKeyboardButton(text="🟢 API Status", callback_data="btn_status")
            ]
        ]
    )

def get_back_keyboard() -> InlineKeyboardMarkup:
    """Back to main menu keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Back to Main Menu", callback_data="btn_main_menu")]
        ]
    )

def get_revoke_confirm_keyboard(key_prefix: str) -> InlineKeyboardMarkup:
    """Revocation confirmation keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚠️ Confirm Revoke", callback_data=f"confirm_revoke:{key_prefix}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="btn_my_keys")
            ]
        ]
    )

