"""Клиент для работы с Telegram Bot API."""

import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class TelegramBotAPIClient:
    """Клиент для взаимодействия с Telegram Bot API."""
    
    BASE_URL = "https://api.telegram.org/bot"
    
    def __init__(self, bot_token: str):
        """
        Инициализация клиента.
        
        Args:
            bot_token: Токен бота от @BotFather
        """
        if not bot_token:
            raise ValueError("Bot token не может быть пустым")
        
        self.bot_token = bot_token
        self.api_url = f"{self.BASE_URL}{bot_token}"
        logger.info("TelegramBotAPIClient инициализирован")
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        files: Optional[Dict] = None,
        json_data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Выполнить запрос к Telegram Bot API.
        
        Args:
            method: HTTP метод (GET, POST)
            endpoint: Endpoint API (например, "getMe")
            params: Параметры запроса
            files: Файлы для загрузки
            json_data: JSON данные для отправки
        
        Returns:
            Ответ от API в виде словаря
        
        Raises:
            requests.RequestException: При ошибке запроса
        """
        url = f"{self.api_url}/{endpoint}"
        
        try:
            if method.upper() == "GET":
                response = requests.get(url, params=params, timeout=10)
            else:
                response = requests.post(
                    url,
                    params=params,
                    files=files,
                    json=json_data,
                    timeout=30
                )
            
            response.raise_for_status()
            result = response.json()
            
            if not result.get("ok"):
                error = result.get("description", "Unknown error")
                logger.error(f"Telegram API error: {error}")
                raise requests.RequestException(f"Telegram API error: {error}")
            
            return result.get("result", {})
        
        except requests.RequestException as e:
            logger.error(f"Ошибка при запросе к Telegram API: {e}")
            raise
    
    def get_me(self) -> Dict[str, Any]:
        """
        Получить информацию о боте.
        
        Returns:
            Информация о боте
        """
        return self._make_request("GET", "getMe")
    
    def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = None,
        reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Отправить текстовое сообщение.
        
        Args:
            chat_id: ID чата
            text: Текст сообщения
            parse_mode: Режим парсинга (HTML, Markdown)
            reply_to_message_id: ID сообщения для ответа
        
        Returns:
            Отправленное сообщение
        """
        params = {
            "chat_id": chat_id,
            "text": text
        }
        
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
        
        return self._make_request("POST", "sendMessage", params=params)
    
    def send_document(
        self,
        chat_id: int,
        document: Path,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Отправить документ.
        
        Args:
            chat_id: ID чата
            document: Путь к файлу
            caption: Подпись к документу
            reply_to_message_id: ID сообщения для ответа
        
        Returns:
            Отправленный документ
        """
        if not document.exists():
            raise FileNotFoundError(f"Файл не найден: {document}")
        
        params = {"chat_id": chat_id}
        if caption:
            params["caption"] = caption
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
        
        with open(document, "rb") as f:
            files = {"document": (document.name, f, "application/octet-stream")}
            return self._make_request("POST", "sendDocument", params=params, files=files)
    
    def send_photo(
        self,
        chat_id: int,
        photo: Path,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Отправить фото.
        
        Args:
            chat_id: ID чата
            photo: Путь к файлу изображения
            caption: Подпись к фото
            reply_to_message_id: ID сообщения для ответа
        
        Returns:
            Отправленное фото
        """
        if not photo.exists():
            raise FileNotFoundError(f"Файл не найден: {photo}")
        
        params = {"chat_id": chat_id}
        if caption:
            params["caption"] = caption
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
        
        with open(photo, "rb") as f:
            files = {"photo": (photo.name, f, "image/jpeg")}
            return self._make_request("POST", "sendPhoto", params=params, files=files)
    
    def get_updates(
        self,
        offset: Optional[int] = None,
        limit: Optional[int] = 100,
        timeout: Optional[int] = 0
    ) -> List[Dict[str, Any]]:
        """
        Получить обновления (long polling).
        
        Args:
            offset: Идентификатор первого обновления для получения
            limit: Максимальное количество обновлений
            timeout: Таймаут в секундах для long polling
        
        Returns:
            Список обновлений
        """
        params = {}
        if offset is not None:
            params["offset"] = offset
        if limit:
            params["limit"] = limit
        if timeout:
            params["timeout"] = timeout
        
        return self._make_request("GET", "getUpdates", params=params)
    
    def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False
    ) -> bool:
        """
        Ответить на callback query.
        
        Args:
            callback_query_id: ID callback query
            text: Текст ответа
            show_alert: Показать alert вместо уведомления
        
        Returns:
            True если успешно
        """
        params = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        if show_alert:
            params["show_alert"] = "true"
        
        return self._make_request("POST", "answerCallbackQuery", params=params) is not None

