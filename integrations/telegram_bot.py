"""Telegram-бот (минимальный каркас).

Команда:
- /start
- /help
- документ (фото/файл) -> распознать и вернуть JSON

Зависимость: python-telegram-bot (v20+)
"""

import logging
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from core.document_processor import DocumentProcessor

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self, token: str, processor: DocumentProcessor):
        self.token = token
        self.processor = processor

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Привет! Пришли фото/файл документа (JPG/PNG/PDF) — верну JSON.")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Отправь документ. Команды: /start, /help")

    async def on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg:
            return
        # Получаем файл
        file = None
        suffix = "bin"
        if msg.document:
            file = await msg.document.get_file()
            suffix = (msg.document.file_name or "file").split(".")[-1].lower()
        elif msg.photo:
            file = await msg.photo[-1].get_file()
            suffix = "jpg"

        if not file:
            await msg.reply_text("Не понял сообщение. Отправь документ/фото.")
            return

        data = await file.download_as_bytearray()
        # Сохраняем во временный файл
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=f".{suffix}")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(bytes(data))

        try:
            result = self.processor.process_file(path)
            import json
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if len(text) > 3900:
                await msg.reply_text("JSON слишком большой — отправляю файлом.")
                await msg.reply_document(document=text.encode("utf-8"), filename="result.json")
            else:
                await msg.reply_text(f"```json\n{text}\n```", parse_mode="Markdown")
        except Exception as e:
            logger.error("Ошибка обработки: %s", e, exc_info=True)
            await msg.reply_text(f"Ошибка: {e}")
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    def run(self):
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("help", self.help))
        app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, self.on_document))
        logger.info("Telegram bot started")
        app.run_polling()
