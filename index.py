import logging, re, asyncio
from utils import temp
from info import ADMINS
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.errors.exceptions.bad_request_400 import ChannelInvalid, ChatAdminRequired, UsernameInvalid, UsernameNotModified
from info import INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import time
import pytz
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
lock = asyncio.Lock()

@Client.on_callback_query(filters.regex(r'^index'))
async def index_files(bot, query):
    if query.data.startswith('index_cancel'):
        temp.CANCEL = True
        return await query.answer("Cancelling Indexing")
    _, raju, chat, lst_msg_id, from_user = query.data.split("#")
    if raju == 'reject':
        await query.message.delete()
        await bot.send_message(
            int(from_user),
            f'Your Submission for indexing {chat} has been decliened by our moderators.',
            reply_to_message_id=int(lst_msg_id)
        )
        return

    if lock.locked():
        return await query.answer('Wait until previous process complete.', show_alert=True)
    msg = query.message

    await query.answer('Processing...⏳', show_alert=True)
    if int(from_user) not in ADMINS:
        await bot.send_message(
            int(from_user),
            f'Your Submission for indexing {chat} has been accepted by our moderators and will be added soon.',
            reply_to_message_id=int(lst_msg_id)
        )
    await msg.edit(
        "Starting Indexing",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton('Cancel', callback_data='index_cancel')]]
        )
    )
    try:
        chat = int(chat)
    except:
        chat = chat
    await index_files_to_db(int(lst_msg_id), chat, msg, bot)

@Client.on_message(filters.private & filters.command('index'))
async def send_for_index(bot, message):
    vj = await bot.ask(message.chat.id, "**Now Send Me Your Channel Last Post Link Or Forward A Last Message From Your Index Channel.\n\nAnd You Can Set Skip Number By - /setskip yourskipnumber**")
    if vj.forward_from_chat and vj.forward_from_chat.type == enums.ChatType.CHANNEL:
        last_msg_id = vj.forward_from_message_id
        chat_id = vj.forward_from_chat.username or vj.forward_from_chat.id
    elif vj.text:
        regex = re.compile("(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")
        match = regex.match(vj.text)
        if not match:
            return await vj.reply('Invalid link\n\nTry again by /index')
        chat_id = match.group(4)
        last_msg_id = int(match.group(5))
        if chat_id.isnumeric():
            chat_id  = int(("-100" + chat_id))
    else:
        return
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await vj.reply('This may be a private channel / group. Make me an admin over there to index the files.')
    except (UsernameInvalid, UsernameNotModified):
        return await vj.reply('Invalid Link specified.')
    except Exception as e:
        logger.exception(e)
        return await vj.reply(f'Errors - {e}')
    try:
        k = await bot.get_messages(chat_id, last_msg_id)
    except:
        return await message.reply('Make Sure That Iam An Admin In The Channel, if channel is private')
    if k.empty:
        return await message.reply('This may be group and iam not a admin of the group.')

    if message.from_user.id in ADMINS:
        buttons = [[
            InlineKeyboardButton('Yes', callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')
        ],[
            InlineKeyboardButton('close', callback_data='close_data')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        return await message.reply(
            f'Do you Want To Index This Channel/ Group ?\n\nChat ID/ Username: <code>{chat_id}</code>\nLast Message ID: <code>{last_msg_id}</code>',
            reply_markup=reply_markup
        )

    if type(chat_id) is int:
        try:
            link = (await bot.create_chat_invite_link(chat_id)).invite_link
        except ChatAdminRequired:
            return await message.reply('Make sure iam an admin in the chat and have permission to invite users.')
    else:
        link = f"@{message.forward_from_chat.username}"
    buttons = [[
        InlineKeyboardButton('Accept Index', callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')
    ],[
        InlineKeyboardButton('Reject Index', callback_data=f'index#reject#{chat_id}#{message.id}#{message.from_user.id}'),
    ]]
    reply_markup = InlineKeyboardMarkup(buttons)
    await bot.send_message(
        LOG_CHANNEL,
        f'#IndexRequest\n\nBy : {message.from_user.mention} (<code>{message.from_user.id}</code>)\nChat ID/ Username - <code> {chat_id}</code>\nLast Message ID - <code>{last_msg_id}</code>\nInviteLink - {link}',
        reply_markup=reply_markup
    )
    await message.reply('ThankYou For the Contribution, Wait For My Moderators to verify the files.')

@Client.on_message(filters.command('setskip') & filters.user(ADMINS))
async def set_skip_number(bot, message):
    if ' ' in message.text:
        _, skip = message.text.split(" ")
        try:
            skip = int(skip)
        except:
            return await message.reply("Skip number should be an integer.")
        await message.reply(f"Successfully set SKIP number as {skip}")
        temp.CURRENT = int(skip)
    else:
        await message.reply("Give me a skip number")

async def index_files_to_db(lst_msg_id, chat, msg, bot):
    total_files = 0
    duplicate = 0
    errors = 0
    deleted = 0
    no_media = 0
    unsupported = 0

    async with lock:
        try:
            current = temp.CURRENT
            temp.CANCEL = False
            start_time = time.time()  # Start time for performance metrics

            async for message in bot.iter_messages(chat, lst_msg_id, temp.CURRENT):
                # Check for cancellation
                if temp.CANCEL:
                    await msg.edit(
                        f"Successfully Cancelled!!\n\n"
                        f"Saved <code>{total_files}</code> files to database!\n"
                        f"Duplicate Files Skipped: <code>{duplicate}</code>\n"
                        f"Deleted Messages Skipped: <code>{deleted}</code>\n"
                        f"Non-Media messages skipped: <code>{no_media + unsupported}</code> "
                        f"(Unsupported Media - `{unsupported}` )\n"
                        f"Errors Occurred: <code>{errors}</code>"
                    )
                    break

                # Increment message counter
                current += 1

                # Pause processing every 200 messages
                if current % 200 == 0:
                    await asyncio.sleep(10)

                # Update progress every 20 messages
                if current % 20 == 0:
                    can = [[InlineKeyboardButton('Cancel', callback_data='index_cancel')]]
                    reply = InlineKeyboardMarkup(can)

                    # Calculate elapsed time
                    elapsed_time = time.time() - start_time

                    # Avoid division by zero and calculate speed
                    files_per_min = total_files / (elapsed_time / 60) if elapsed_time > 0 else 0
                    files_per_day = files_per_min * 1440  # 1440 minutes in a day

                    # Estimate time remaining based on files
                    files_remaining = temp.CURRENT - total_files
                    time_per_file = elapsed_time / total_files if total_files > 0 else float('inf')
                    total_time_remaining = time_per_file * files_remaining

                    # Format remaining time
                    eta_remaining = timedelta(seconds=int(total_time_remaining))
                    days_remaining = eta_remaining.days
                    hours_remaining, remainder = divmod(eta_remaining.seconds, 3600)
                    minutes_remaining, seconds_remaining = divmod(remainder, 60)

                    # Update progress message
                    try:
                        await msg.edit_text(
                            text=(
                                f"**Processed**\n"
                                f"Total Messages: <code>{current}</code>\n"
                                f"Saved Files: <code>{total_files}</code>\n"
                                f"Duplicate Files Skipped: <code>{duplicate}</code>\n"
                                f"Deleted Skipped: <code>{deleted}</code>\n"
                                f"Non-Media Skipped: <code>{no_media}</code>\n"
                                f"Unsupported Media: <code>{unsupported}</code>\n"
                                f"Errors: <code>{errors}</code>\n\n"
                                f"**Speed:** <code>{files_per_min:.2f} files/min, {files_per_day:.2f} files/day</code>\n"
                                f"**ETA:** <code>{days_remaining} days, {hours_remaining} hours, {minutes_remaining} minutes, {seconds_remaining} seconds</code>"
                            ),
                            reply_markup=reply
                        )
                    except MessageNotModified:
                        pass

                # Process the message
                if message.empty:
                    deleted += 1
                    continue
                elif not message.media:
                    no_media += 1
                    continue
                elif message.media not in [enums.MessageMediaType.DOCUMENT]:
                    unsupported += 1
                    continue

                # Save the file
                media = getattr(message, message.media.value, None)
                if not media:
                    unsupported += 1
                    continue

                media.caption = message.caption
                success, error_code = await save_file(media)
                if success:
                    total_files += 1
                elif error_code == 0:
                    duplicate += 1
                elif error_code == 2:
                    errors += 1

        except Exception as e:
            logger.exception(e)
            k = await msg.edit(
                f"Error: {e}\n\n"
                f"Successfully saved <code>{total_files}</code> to database!\n"
                f"Duplicate Files Skipped: <code>{duplicate}</code>\n"
                f"Deleted Messages Skipped: <code>{deleted}</code>\n"
                f"Non-Media messages skipped: <code>{no_media + unsupported}</code> "
                f"(Unsupported Media - `{unsupported}` )\n"
                f"Errors Occurred: <code>{errors}</code>"
            )
            await k.reply_text(
                "**If You Get Message Not Modified Error, Skip Your Saved File and Index Again**"
            )
        else:
            await msg.edit(
                f"Successfully saved <code>{total_files}</code> to database!\n"
                f"Duplicate Files Skipped: <code>{duplicate}</code>\n"
                f"Deleted Messages Skipped: <code>{deleted}</code>\n"
                f"Non-Media messages skipped: <code>{no_media + unsupported}</code> "
                f"(Unsupported Media - `{unsupported}` )\n"
                f"Errors Occurred: <code>{errors}</code>"
            )
