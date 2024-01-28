from __future__ import annotations

import asyncio
import logging
import os
import io

from uuid import uuid4
from telegram import BotCommandScopeAllGroupChats, Update, constants
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle
from telegram import InputTextMessageContent, BotCommand
from telegram.error import RetryAfter, TimedOut, BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, \
    filters, InlineQueryHandler, CallbackQueryHandler, Application, ContextTypes, CallbackContext,ConversationHandler
from telegram import error

from pydub import AudioSegment
from PIL import Image

from utils import is_group_chat, get_thread_id, message_text, wrap_with_indicator, split_into_chunks, \
    edit_message_with_retry, get_stream_cutoff_values, is_allowed, get_remaining_budget, is_admin, is_within_budget, \
    get_reply_to_message_id, add_chat_request_to_usage_tracker, error_handler, is_direct_result, handle_direct_result, \
    cleanup_intermediate_files
from openai_helper import OpenAIHelper, localized_text
from usage_tracker import UsageTracker
ACCENTS = {  
 
"American": "./audio/american.mp3",  
"Australian": "./audio/australian.mp3",  
"British": "./audio/british.mp3", 
"Indian": "./audio/indian.mp3",  
"Welsh": "./audio/welsh.mp3",  
"Italian": "./audio/italian.mp3",  
} 
class ChatGPTTelegramBot:
    """
    Class representing a ChatGPT Telegram Bot.
    """

    def __init__(self, config: dict, openai: OpenAIHelper):
        """
        Initializes the bot with the given configuration and GPT bot object.
        :param config: A dictionary containing the bot configuration
        :param openai: OpenAIHelper object
        """
        self.config = config
        self.openai = openai
        bot_language = self.config['bot_language']
        self.commands = [
            BotCommand(command='help', description=localized_text('help_description', bot_language)),
            BotCommand(command='reset', description=localized_text('reset_description', bot_language)),
            BotCommand(command='stats', description=localized_text('stats_description', bot_language)),
            BotCommand(command='resend', description=localized_text('resend_description', bot_language)),
            BotCommand(command='chatmode', description=localized_text('chatmode_description', bot_language)),
            BotCommand(command='setting', description=localized_text('setting_description', bot_language)),
        ]
        
        self.disallowed_message = localized_text('disallowed', bot_language)
        self.budget_limit_message = localized_text('budget_limit', bot_language)
        self.usage = {}
        self.last_message = {}
        self.inline_queries_cache = {}
        self.voice_enable=False
        # self.tts_voices=list(self.config['tts_voice'])
        self.tts_voice=list(ACCENTS.keys())[0]
        

    async def help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Shows the help menu.
        """
        commands = self.group_commands if is_group_chat(update) else self.commands
        commands_description = [f'/{command.command} - {command.description}' for command in commands]
        bot_language = self.config['bot_language']
        help_text = (
                localized_text('help_text', bot_language)[0] +
                '\n\n' +
                '\n'.join(commands_description) +
                '\n\n' +
                localized_text('help_text', bot_language)[1] +
                '\n\n' +
                localized_text('help_text', bot_language)[2]
        )
        await update.message.reply_text(help_text, disable_web_page_preview=True)

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Returns token usage statistics for current day and month.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            f'is not allowed to request their usage statistics')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                     f'requested their usage statistics')

        user_id = update.message.from_user.id
        if user_id not in self.usage:
            self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

        tokens_today, tokens_month = self.usage[user_id].get_current_token_usage()
        images_today, images_month = self.usage[user_id].get_current_image_count()
        (transcribe_minutes_today, transcribe_seconds_today, transcribe_minutes_month,
         transcribe_seconds_month) = self.usage[user_id].get_current_transcription_duration()
        vision_today, vision_month = self.usage[user_id].get_current_vision_tokens()
        characters_today, characters_month = self.usage[user_id].get_current_tts_usage()
        current_cost = self.usage[user_id].get_current_cost()

        chat_id = update.effective_chat.id
        chat_messages, chat_token_length = self.openai.get_conversation_stats(chat_id)
        remaining_budget = get_remaining_budget(self.config, self.usage, update)
        bot_language = self.config['bot_language']
        
        text_current_conversation = (
            f"*{localized_text('stats_conversation', bot_language)[0]}*:\n"
            f"{chat_messages} {localized_text('stats_conversation', bot_language)[1]}\n"
            f"{chat_token_length} {localized_text('stats_conversation', bot_language)[2]}\n"
            f"----------------------------\n"
        )
        
        # Check if image generation is enabled and, if so, generate the image statistics for today
        text_today_images = ""
        if self.config.get('enable_image_generation', False):
            text_today_images = f"{images_today} {localized_text('stats_images', bot_language)}\n"

        text_today_vision = ""
        if self.config.get('enable_vision', False):
            text_today_vision = f"{vision_today} {localized_text('stats_vision', bot_language)}\n"

        text_today_tts = ""
        if self.config.get('enable_tts_generation', False):
            text_today_tts = f"{characters_today} {localized_text('stats_tts', bot_language)}\n"
        
        text_today = (
            f"*{localized_text('usage_today', bot_language)}:*\n"
            f"{tokens_today} {localized_text('stats_tokens', bot_language)}\n"
            f"{text_today_images}"  # Include the image statistics for today if applicable
            f"{text_today_vision}"
            f"{text_today_tts}"
            f"{transcribe_minutes_today} {localized_text('stats_transcribe', bot_language)[0]} "
            f"{transcribe_seconds_today} {localized_text('stats_transcribe', bot_language)[1]}\n"
            f"{localized_text('stats_total', bot_language)}{current_cost['cost_today']:.2f}\n"
            f"----------------------------\n"
        )
        
        text_month_images = ""
        if self.config.get('enable_image_generation', False):
            text_month_images = f"{images_month} {localized_text('stats_images', bot_language)}\n"

        text_month_vision = ""
        if self.config.get('enable_vision', False):
            text_month_vision = f"{vision_month} {localized_text('stats_vision', bot_language)}\n"

        text_month_tts = ""
        if self.config.get('enable_tts_generation', False):
            text_month_tts = f"{characters_month} {localized_text('stats_tts', bot_language)}\n"
        
        # Check if image generation is enabled and, if so, generate the image statistics for the month
        text_month = (
            f"*{localized_text('usage_month', bot_language)}:*\n"
            f"{tokens_month} {localized_text('stats_tokens', bot_language)}\n"
            f"{text_month_images}"  # Include the image statistics for the month if applicable
            f"{text_month_vision}"
            f"{text_month_tts}"
            f"{transcribe_minutes_month} {localized_text('stats_transcribe', bot_language)[0]} "
            f"{transcribe_seconds_month} {localized_text('stats_transcribe', bot_language)[1]}\n"
            f"{localized_text('stats_total', bot_language)}{current_cost['cost_month']:.2f}"
        )

        # text_budget filled with conditional content
        text_budget = "\n\n"
        budget_period = self.config['budget_period']
        if remaining_budget < float('inf'):
            text_budget += (
                f"{localized_text('stats_budget', bot_language)}"
                f"{localized_text(budget_period, bot_language)}: "
                f"${remaining_budget:.2f}.\n"
            )
        # No longer works as of July 21st 2023, as OpenAI has removed the billing API
        # add OpenAI account information for admin request
        # if is_admin(self.config, user_id):
        #     text_budget += (
        #         f"{localized_text('stats_openai', bot_language)}"
        #         f"{self.openai.get_billing_current_month():.2f}"
        #     )

        usage_text = text_current_conversation + text_today + text_month + text_budget
        await update.message.reply_text(usage_text, parse_mode=constants.ParseMode.MARKDOWN)

    async def resend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resend the last request
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name}  (id: {update.message.from_user.id})'
                            f' is not allowed to resend the message')
            await self.send_disallowed_message(update, context)
            return

        chat_id = update.effective_chat.id
        if chat_id not in self.last_message:
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id})'
                            f' does not have anything to resend')
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('resend_failed', self.config['bot_language'])
            )
            return

        # Update message text, clear self.last_message and send the request to prompt
        logging.info(f'Resending the last prompt from user: {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')
        with update.message._unfrozen() as message:
            message.text = self.last_message.pop(chat_id)

        await self.prompt(update=update, context=context)

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resets the conversation.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            f'is not allowed to reset the conversation')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'Resetting the conversation for user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})...')

        chat_id = update.effective_chat.id
        reset_content = message_text(update.message)
        self.openai.reset_chat_history(chat_id=chat_id, content=reset_content)
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=localized_text('reset_done', self.config['bot_language'])
        )

    async def stt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logging.info(
            f'New voice message received from user {update.message.from_user.name} (id: {update.message.from_user.id})')
        filename = update.message.effective_attachment.file_unique_id
        filename_mp3 = f'{filename}.mp3'
        bot_language = self.config['bot_language']
        try:
            media_file = await context.bot.get_file(update.message.effective_attachment.file_id)
            await media_file.download_to_drive(filename)
        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=(
                    f"{localized_text('media_download_fail', bot_language)[0]}: "
                    f"{str(e)}. {localized_text('media_download_fail', bot_language)[1]}"
                ),
                parse_mode=constants.ParseMode.MARKDOWN
            )
            return

        try:
            audio_track = AudioSegment.from_file(filename)
            audio_track.export(filename_mp3, format="mp3")
            logging.info(f'New transcribe request received from user {update.message.from_user.name} '
                            f'(id: {update.message.from_user.id})')

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=localized_text('media_type_fail', bot_language)
            )
            if os.path.exists(filename):
                os.remove(filename)
            return

        user_id = update.message.from_user.id
        if user_id not in self.usage:
            self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

        try:
            transcript = await self.openai.transcribe(filename_mp3)

            transcription_price = self.config['transcription_price']
            self.usage[user_id].add_transcription_seconds(audio_track.duration_seconds, transcription_price)

            allowed_user_ids = self.config['allowed_user_ids'].split(',')
            if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                self.usage["guests"].add_transcription_seconds(audio_track.duration_seconds, transcription_price)

            # check if transcript starts with any of the prefixes
            response_to_transcription = any(transcript.lower().startswith(prefix.lower()) if prefix else False
                                            for prefix in self.config['voice_reply_prompts'])

            if  not response_to_transcription:

                self.last_message[update.effective_chat.id] = transcript
        

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=f"{localized_text('transcribe_fail', bot_language)}: {str(e)}",
                parse_mode=constants.ParseMode.MARKDOWN
            )
        finally:
            if os.path.exists(filename_mp3):
                os.remove(filename_mp3)
            if os.path.exists(filename):
                os.remove(filename)
    async def tts(self,update:Update,context:ContextTypes.DEFAULT_TYPE,response:str):
        logging.info(f'New speech generation request received from user {update.message.from_user.name} '
                    f'(id: {update.message.from_user.id})')

        async def _generate():
            try:
                speech_file, text_length = await self.openai.generate_speech(text=response,tts_voice=self.tts_voice)

                await update.effective_message.reply_voice(
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    voice=speech_file
                )
                speech_file.close()
                # add image request to users usage tracker
                user_id = update.message.from_user.id
                self.usage[user_id].add_tts_request(text_length, self.config['tts_model'], self.config['tts_prices'])
                # add guest chat request to guest usage tracker
                if str(user_id) not in self.config['allowed_user_ids'].split(',') and 'guests' in self.usage:
                    self.usage["guests"].add_tts_request(text_length, self.config['tts_model'], self.config['tts_prices'])

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('tts_fail', self.config['bot_language'])}: {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )

        await wrap_with_indicator(update, context, _generate, constants.ChatAction.UPLOAD_VOICE)
        


    async def transcribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Transcribe audio messages.
        """
        if not  await self.check_allowed_and_within_budget(update, context):
            return
        if update.edited_message or not update.message or update.message.via_bot:
            return

        chat_id = update.effective_chat.id
        user_id = update.message.from_user.id

        if update.message.voice:
            try:
                await self.stt(update, context)
            except Exception as e:
                logging.exception(e)
        elif update.message.text:
            logging.info(
            f'New text message received from user {update.message.from_user.name} (id: {update.message.from_user.id})')
            prompt = message_text(update.message)
            self.last_message[chat_id] = prompt

        try:
            total_tokens = 0
            response, total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=self.last_message[chat_id])
            if is_direct_result(response):
                return await handle_direct_result(self.config, update, response)
            if not self.config["enable_tts_generation"]:
                async def _reply():
                    # Split into chunks of 4096 characters (Telegram's message limit)
                    chunks = split_into_chunks(response)

                    for index, chunk in enumerate(chunks):
                        try:
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config,
                                                                            update) if index == 0 else None,
                                text=chunk,
                                parse_mode=constants.ParseMode.MARKDOWN
                            )
                        except Exception:
                            try:
                                await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    reply_to_message_id=get_reply_to_message_id(self.config,
                                                                                update) if index == 0 else None,
                                    text=chunk
                                )
                            except Exception as exception:
                                raise exception

                await wrap_with_indicator(update, context, _reply, constants.ChatAction.TYPING)

                add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)
            else:
                try:
                    await self.tts(update=update,context=context,response=response)
                except Exception as e:
                    logging.exception(e)
                

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=f"{localized_text('chat_fail', self.config['bot_language'])} {str(e)}",
                parse_mode=constants.ParseMode.MARKDOWN
            )

    

    async def check_allowed_and_within_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        Checks if the user is allowed to use the bot and if they are within their budget
        :param update: Telegram update object
        :param context: Telegram context object
        :return: Boolean indicating if the user is allowed to use the bot
        """
        name = update.message.from_user.name
        user_id = update.message.from_user.id

        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {name} (id: {user_id}) is not allowed to use the bot')
            await self.send_disallowed_message(update, context)
            return False
        if not is_within_budget(self.config, self.usage, update):
            await self.send_budget_reached_message(update, context)
            return False

        return True

    async def send_disallowed_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        """
        Sends the disallowed message to the user.
        """
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=self.disallowed_message,
            disable_web_page_preview=True
        )
        
    async def send_budget_reached_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        """
        Sends the budget reached message to the user.
        """
        
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=self.budget_limit_message
        )
       

    async def post_init(self, application: Application) -> None:
        """
        Post initialization hook for the bot.
        """
        
        await application.bot.set_my_commands(self.commands)
   
    def voice_off_keyboard(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅"+"TEXT",callback_data="text_mode")],
            [InlineKeyboardButton("VOICE OFF",callback_data="voice_off")],
            [InlineKeyboardButton("🔙"+"CANCEL",callback_data="cancel")]
        ])
    def voice_on_keyboard(self) -> InlineKeyboardMarkup:
        #当用户选择在单个窗口打开多个音色选择的内联键盘时，需要删除上个内联键盘留下的语音信息 
                  
       
        # 用户选择音色后，进行clear_history操作，重新选择音色前清空context.user_data中的数据
        
            

        return InlineKeyboardMarkup([
            [InlineKeyboardButton("TEXT",callback_data="text_mode")],
            [InlineKeyboardButton("✅"+"VOICE ON",callback_data="voice_on")],
            [InlineKeyboardButton("VOICE OPTIONS",callback_data="select_voice")],
            [InlineKeyboardButton("🔙"+"CANCEL",callback_data="cancel")]
        ]
        )
    def accents_keyboard(self):
        keyboard=[]
        row=[]
        for accent in ACCENTS.keys():      
            if(self.tts_voice==accent):
                row.append(InlineKeyboardButton("✅"+accent,callback_data=accent))
            else:
                row.append(InlineKeyboardButton(accent,callback_data=accent))
            if len(row)==2:
                keyboard.append(row)
                row=[]
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("BACK", callback_data="back")])  
        return InlineKeyboardMarkup(keyboard)
    async def handle_accent_selection(self,update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query  
        # 用户做出选择后，我们编辑原有的消息，删除上一次的语音  
        await query.answer() 

        if 'voice_message_id' in context.user_data: 
            try:      
                await context.bot.delete_message(chat_id=query.message.chat_id,  
            message_id=context.user_data['voice_message_id'])
                context.user_data.clear()
            
            except error.TelegramError as e:
                    # 处理可能发生的错误，例如消息已经被删除，或者bot没有权限删除消息等
                logging.error(f"Error occurred: {e.message}")
                context.user_data.clear()
        
        accent_chosen = query.data
        #点开音色选择，发送用户默认的语音
        if(accent_chosen=="select_voice"):
            file_id = ACCENTS[self.tts_voice]
             # 发送新的语音消息  
            new_voice_message = await context.bot.send_voice(chat_id=query.message.chat_id, voice=file_id)  
            
            # 保存这个语音消息的ID，以便后面可能删除  
            context.user_data['voice_message_id'] = new_voice_message.message_id
        else:
            if accent_chosen!="back":
                await query.edit_message_text("SELECT VOICE:",reply_markup=self.accents_keyboard())  
                file_id = ACCENTS[accent_chosen]
                # 发送新的语音消息  
                new_voice_message = await context.bot.send_voice(chat_id=query.message.chat_id, voice=file_id)  
                
                # 保存这个语音消息的ID，以便后面可能删除  
                context.user_data['voice_message_id'] = new_voice_message.message_id
        # 命令处理函数用于展示选择口音的内联键盘  
    async def reply_mode(self,update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
        keyboard=[
            [InlineKeyboardButton("✅"+"TEXT",callback_data="text_mode")],
            [InlineKeyboardButton("VOICE OFF",callback_data="voice_off")],
            [InlineKeyboardButton("🔙"+"CANCEL",callback_data="cancel")]
        ]
        reply_markup=InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("TEXT ON:",reply_markup=reply_markup)
    async def reply_button(self,update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  
        query=update.callback_query
        data=query.data
        if data=="text_mode":
            # await query.answer("文本模式已选择",show_alert=True)
            await query.edit_message_text("TEXT ON",reply_markup=self.voice_off_keyboard())
        elif data=="voice_on":
            await query.edit_message_text("TEXT ON",reply_markup=self.voice_off_keyboard())
        elif data=="voice_off":
            await query.edit_message_text("VOICE ON:",reply_markup= self.voice_on_keyboard())
        elif data=="select_voice":
            await query.edit_message_text("SELECT VOICE:",reply_markup=self.accents_keyboard())
            await self.handle_accent_selection(update,context)
        elif data=="cancel":
            await query.message.delete()
        elif data in ACCENTS:
            self.tts_voice=data
            await self.handle_accent_selection(update,context)
        elif data=="back":
            await self.handle_accent_selection(update,context)
            await query.edit_message_text("VOICE ON:",reply_markup= self.voice_on_keyboard())
           
        await query.answer()
            
        

    def run(self):
        """
        Runs the bot indefinitely until the user presses Ctrl+C
        """
        application = ApplicationBuilder() \
            .token(self.config['token']) \
            .proxy_url(self.config['proxy']) \
            .get_updates_proxy_url(self.config['proxy']) \
            .post_init(self.post_init) \
            .concurrent_updates(True) \
            .build()

        application.add_handler(CommandHandler('reset', self.reset))
        application.add_handler(CommandHandler('help', self.help))
        application.add_handler(CommandHandler('start', self.help))
        application.add_handler(CommandHandler('stats', self.stats))
        application.add_handler(CommandHandler('resend', self.resend))
        application.add_handler(CommandHandler("setting", self.reply_mode,filters=filters.COMMAND))
        application.add_handler(CallbackQueryHandler(self.reply_button))
        application.add_handler(MessageHandler((filters.TEXT|filters.VOICE)  & (~filters.COMMAND), self.transcribe))
      
        

        application.add_error_handler(error_handler)

        application.run_polling()
