#!/usr/bin/env python3
"""
Channel Request Bot
Telegram bot for processing channel join requests with age verification
Version: 1.0.0
"""

import os
import yaml
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from pathlib import Path
from enum import Enum

import telebot
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatJoinRequest
from telebot.async_telebot import AsyncTeleBot
from telebot.asyncio_storage import StateMemoryStorage

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class RequestStatus(Enum):
    """Статусы заявок"""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    APPROVED = "approved"
    DECLINED = "declined"
    BANNED = "banned"


@dataclass
class ChannelRequest:
    """Данные о заявке в канал"""
    user_id: int
    chat_id: int
    user_name: str
    user_username: str
    user_first_name: str
    user_last_name: str
    status: RequestStatus
    confirmation_message_id: Optional[int] = None
    channel_request_date: Optional[datetime] = None
    confirmation_date: Optional[datetime] = None
    decision_date: Optional[datetime] = None
    
    def to_dict(self):
        return {
            **asdict(self),
            'status': self.status.value,
            'channel_request_date': self.channel_request_date.isoformat() if self.channel_request_date else None,
            'confirmation_date': self.confirmation_date.isoformat() if self.confirmation_date else None,
            'decision_date': self.decision_date.isoformat() if self.decision_date else None,
        }
    
    @classmethod
    def from_dict(cls, data):
        data['status'] = RequestStatus(data['status'])
        for date_field in ['channel_request_date', 'confirmation_date', 'decision_date']:
            if data[date_field]:
                data[date_field] = datetime.fromisoformat(data[date_field])
        return cls(**data)


class ChannelRequestBot:
    """Основной класс бота"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self.load_config(config_path)
        
        # Инициализация бота
        self.bot = AsyncTeleBot(
            self.config['bot']['token'], 
            state_storage=StateMemoryStorage()
        )
        
        # Хранилище данных
        self.active_requests: Dict[int, ChannelRequest] = {}
        self.banned_users: set[int] = set()
        
        # Загрузка данных
        self.load_data()
        
        # Регистрация обработчиков
        self.register_handlers()
        
        logger.info("Channel Request Bot инициализирован")
        logger.info(f"Канал: {self.config['channel']['title']}")
    
    def load_config(self, config_path: str) -> Dict[str, Any]:
        """Загрузка конфигурации"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def save_data(self):
        """Сохранение данных"""
        data = {
            'active_requests': {
                str(user_id): req.to_dict()
                for user_id, req in self.active_requests.items()
            },
            'banned_users': list(self.banned_users)
        }
        
        with open('channel_requests_data.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def load_data(self):
        """Загрузка данных"""
        data_path = Path('channel_requests_data.json')
        if data_path.exists():
            try:
                with open(data_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Загрузка активных заявок
                active_requests = data.get('active_requests', {})
                for user_id_str, req_data in active_requests.items():
                    try:
                        request = ChannelRequest.from_dict(req_data)
                        # Проверка на устаревание
                        if request.channel_request_date:
                            days_old = (datetime.now() - request.channel_request_date).days
                            if days_old <= self.config['settings']['retention_days']:
                                self.active_requests[request.user_id] = request
                    except Exception as e:
                        logger.error(f"Ошибка загрузки заявки {user_id_str}: {e}")
                
                # Загрузка забаненных пользователей
                self.banned_users = set(data.get('banned_users', []))
                
                logger.info(f"Загружено {len(self.active_requests)} активных заявок")
                logger.info(f"Загружено {len(self.banned_users)} забаненных пользователей")
                
            except Exception as e:
                logger.error(f"Ошибка загрузки данных: {e}")
    
    def create_confirmation_keyboard(self) -> InlineKeyboardMarkup:
        """Создание клавиатуры подтверждения"""
        keyboard = InlineKeyboardMarkup(row_width=1)
        
        for row in self.config['keyboards']['confirmation_keyboard']:
            for btn in row:
                keyboard.add(
                    InlineKeyboardButton(
                        text=btn['text'],
                        callback_data=btn['callback_data']
                    )
                )
        
        return keyboard
    
    def create_adapter_keyboard(self) -> InlineKeyboardMarkup:
        """Клавиатура с кнопкой переходника"""
        keyboard = InlineKeyboardMarkup()
        
        if 'adapter_channel' in self.config['channel'] and self.config['channel']['adapter_channel']:
            adapter_url = f"https://t.me/{self.config['channel']['adapter_channel'].replace('@', '')}"
            keyboard.add(
                InlineKeyboardButton(
                    text="📢 ПОДПИСАТЬСЯ НА ПЕРЕХОДНИК",
                    url=adapter_url
                )
            )
        
        return keyboard
    
    def create_admin_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        """Клавиатура администратора"""
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        if 'admin_keyboard' in self.config['keyboards']:
            for row in self.config['keyboards']['admin_keyboard']:
                row_buttons = []
                for btn in row:
                    callback_data = btn['callback_data'].replace('{user_id}', str(user_id))
                    row_buttons.append(
                        InlineKeyboardButton(
                            text=btn['text'],
                            callback_data=callback_data
                        )
                    )
                keyboard.row(*row_buttons)
        
        return keyboard
    
    async def handle_chat_join_request(self, chat_join_request: ChatJoinRequest):
        """Обработка заявок на вступление"""
        try:
            user = chat_join_request.from_user
            user_id = user.id
            chat_id = chat_join_request.chat.id
            
            logger.info(f"Получена заявка от {user_id} (@{user.username})")
            
            # Проверка канала
            if chat_id != self.config['channel']['chat_id']:
                logger.warning(f"Заявка не в наш канал: {chat_id}")
                return
            
            # Проверка бана
            if user_id in self.banned_users:
                await self.bot.decline_chat_join_request(chat_id, user_id)
                logger.info(f"Забаненный пользователь {user_id} попытался подать заявку")
                return
            
            # Проверка активной заявки
            if user_id in self.active_requests:
                existing_request = self.active_requests[user_id]
                if existing_request.status == RequestStatus.CONFIRMED:
                    if self.config['settings']['auto_approve']:
                        await self.approve_channel_request(user_id)
                    return
            
            # Создание новой заявки
            request = ChannelRequest(
                user_id=user_id,
                chat_id=chat_id,
                user_name=user.full_name,
                user_username=user.username or "",
                user_first_name=user.first_name or "",
                user_last_name=user.last_name or "",
                status=RequestStatus.PENDING,
                channel_request_date=datetime.now()
            )
            
            self.active_requests[user_id] = request
            
            # Отправка сообщения с подтверждением
            await self.send_confirmation_message(user_id)
            
            # Уведомление администраторов
            if self.config['settings']['notify_admins']:
                await self.notify_admins_new_request(request)
            
            # Сохранение данных
            self.save_data()
            
            logger.info(f"Новая заявка от {user_id} обработана")
            
        except Exception as e:
            logger.error(f"Ошибка обработки заявки: {e}", exc_info=True)
    
    async def send_confirmation_message(self, user_id: int):
        """Отправка сообщения с подтверждением"""
        try:
            channel_title = self.config['channel']['title']
            age_requirement = self.config['channel'].get('age_requirement', 18)
            
            message_text = self.config['messages'].get('welcome', '').format(
                channel_title=channel_title,
                age_requirement=age_requirement
            )
            
            keyboard = self.create_confirmation_keyboard()
            
            sent_message = await self.bot.send_message(
                chat_id=user_id,
                text=message_text,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            
            if user_id in self.active_requests:
                self.active_requests[user_id].confirmation_message_id = sent_message.message_id
            
            logger.info(f"Подтверждение отправлено пользователю {user_id}")
            
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"API ошибка для пользователя {user_id}: {e}")
            
            if e.error_code == 403:
                logger.warning(f"Пользователь {user_id} заблокировал бота")
                await self.decline_channel_request(user_id, auto_decline=True)
            else:
                logger.error(f"Неизвестная API ошибка: {e}")
        except Exception as e:
            logger.error(f"Ошибка отправки подтверждения: {e}", exc_info=True)
    
    async def handle_confirmation(self, user_id: int):
        """Обработка подтверждения пользователем"""
        try:
            if user_id not in self.active_requests:
                logger.warning(f"Попытка подтвердить несуществующую заявку {user_id}")
                return
            
            request = self.active_requests[user_id]
            request.status = RequestStatus.CONFIRMED
            request.confirmation_date = datetime.now()
            
            # Обновление сообщения
            if request.confirmation_message_id:
                try:
                    channel_title = self.config['channel']['title']
                    adapter_channel = self.config['channel'].get('adapter_channel', '')
                    
                    approved_message = self.config['messages'].get('approved', '').format(
                        channel_title=channel_title,
                        adapter_channel=adapter_channel
                    )
                    
                    adapter_keyboard = self.create_adapter_keyboard()
                    
                    await self.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=request.confirmation_message_id,
                        text=approved_message,
                        reply_markup=adapter_keyboard,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Ошибка обновления сообщения: {e}")
            
            # Автоматическое одобрение
            if self.config['settings']['auto_approve']:
                await self.approve_channel_request(user_id)
            
            # Уведомление администраторов
            if self.config['settings']['notify_admins']:
                await self.notify_admins_confirmation(request)
            
            # Сохранение данных
            self.save_data()
            
            logger.info(f"Пользователь {user_id} подтвердил правила")
            
        except Exception as e:
            logger.error(f"Ошибка обработки подтверждения: {e}", exc_info=True)
    
    async def handle_decline(self, user_id: int):
        """Обработка отказа пользователя"""
        try:
            if user_id not in self.active_requests:
                logger.warning(f"Попытка отклонить несуществующую заявку {user_id}")
                return
            
            request = self.active_requests[user_id]
            request.status = RequestStatus.DECLINED
            request.decision_date = datetime.now()
            
            # Обновление сообщения
            if request.confirmation_message_id:
                try:
                    declined_message = self.config['messages'].get('declined', '❌ Заявка отклонена')
                    await self.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=request.confirmation_message_id,
                        text=declined_message,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Ошибка обновления сообщения отказа: {e}")
            
            # Отклонение заявки
            await self.decline_channel_request(user_id)
            
            # Бан при необходимости
            if self.config['settings'].get('ban_on_decline', False):
                await self.ban_user(user_id)
            
            # Уведомление администраторов
            if self.config['settings']['notify_admins']:
                await self.notify_admins_decline(request)
            
            # Сохранение данных
            self.save_data()
            
            logger.info(f"Пользователь {user_id} отказался")
            
        except Exception as e:
            logger.error(f"Ошибка обработки отказа: {e}", exc_info=True)
    
    async def approve_channel_request(self, user_id: int):
        """Одобрение заявки в канале"""
        try:
            if user_id not in self.active_requests:
                return
            
            request = self.active_requests[user_id]
            
            await self.bot.approve_chat_join_request(
                chat_id=request.chat_id,
                user_id=user_id
            )
            
            request.status = RequestStatus.APPROVED
            request.decision_date = datetime.now()
            
            logger.info(f"Заявка пользователя {user_id} одобрена")
            
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"API ошибка при одобрении {user_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка одобрения заявки: {e}", exc_info=True)
    
    async def decline_channel_request(self, user_id: int, auto_decline: bool = False):
        """Отклонение заявки в канале"""
        try:
            if user_id not in self.active_requests:
                return
            
            request = self.active_requests[user_id]
            
            await self.bot.decline_chat_join_request(
                chat_id=request.chat_id,
                user_id=user_id
            )
            
            if not auto_decline:
                request.status = RequestStatus.DECLINED
                request.decision_date = datetime.now()
            
            logger.info(f"Заявка пользователя {user_id} отклонена")
            
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"API ошибка при отклонении {user_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка отклонения заявки: {e}", exc_info=True)
    
    async def ban_user(self, user_id: int):
        """Бан пользователя"""
        try:
            self.banned_users.add(user_id)
            
            # Отправка уведомления
            try:
                banned_message = self.config['messages'].get('banned', '⛔ Вы забанены')
                await self.bot.send_message(user_id, banned_message, parse_mode='Markdown')
            except:
                pass
            
            logger.info(f"Пользователь {user_id} забанен")
            
        except Exception as e:
            logger.error(f"Ошибка бана пользователя: {e}", exc_info=True)
    
    async def notify_admins_new_request(self, request: ChannelRequest):
        """Уведомление администраторов о новой заявке"""
        message_template = self.config['messages'].get('admin_new', '🔔 Новая заявка')
        
        message = message_template.format(
            user_name=f"{request.user_first_name} {request.user_last_name}",
            username=request.user_username or "без username",
            user_id=request.user_id,
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        
        await self.send_to_admins(message, request.user_id)
    
    async def notify_admins_confirmation(self, request: ChannelRequest):
        """Уведомление о подтверждении"""
        message_template = self.config['messages'].get('admin_approved', '✅ Заявка подтверждена')
        
        message = message_template.format(
            username=request.user_username or "без username",
            user_id=request.user_id,
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        
        await self.send_to_admins(message, request.user_id)
    
    async def notify_admins_decline(self, request: ChannelRequest):
        """Уведомление об отказе"""
        message_template = self.config['messages'].get('admin_declined', '❌ Заявка отклонена')
        
        message = message_template.format(
            username=request.user_username or "без username",
            user_id=request.user_id,
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        
        await self.send_to_admins(message, request.user_id)
    
    async def send_to_admins(self, message: str, user_id: int = None):
        """Отправка сообщения администраторам"""
        keyboard = None
        if user_id and 'admin_keyboard' in self.config['keyboards']:
            keyboard = self.create_admin_keyboard(user_id)
        
        for admin_id in self.config['bot']['admin_ids']:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка отправки админу {admin_id}: {e}")
    
    def register_handlers(self):
        """Регистрация обработчиков"""
        
        @self.bot.chat_join_request_handler()
        async def chat_join_request_handler(chat_join_request: ChatJoinRequest):
            await self.handle_chat_join_request(chat_join_request)
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'confirm')
        async def confirm_request_handler(call):
            user_id = call.from_user.id
            
            if user_id not in self.active_requests:
                await self.bot.answer_callback_query(call.id, "Заявка не найдена", show_alert=True)
                return
            
            await self.handle_confirmation(user_id)
            await self.bot.answer_callback_query(call.id, "✅ Заявка подтверждена!")
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'decline')
        async def decline_request_handler(call):
            user_id = call.from_user.id
            
            await self.handle_decline(user_id)
            await self.bot.answer_callback_query(call.id, "❌ Заявка отклонена")
        
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
        async def admin_action_handler(call):
            admin_id = call.from_user.id
            
            if admin_id not in self.config['bot']['admin_ids']:
                await self.bot.answer_callback_query(call.id, "Нет прав", show_alert=True)
                return
            
            callback_data = call.data
            
            if callback_data.startswith('admin_approve_'):
                try:
                    user_id = int(callback_data.replace('admin_approve_', ''))
                    await self.approve_channel_request(user_id)
                    await self.bot.answer_callback_query(call.id, "✅ Одобрено")
                except ValueError:
                    await self.bot.answer_callback_query(call.id, "❌ Ошибка ID")
            
            elif callback_data.startswith('admin_decline_'):
                try:
                    user_id = int(callback_data.replace('admin_decline_', ''))
                    await self.decline_channel_request(user_id)
                    await self.bot.answer_callback_query(call.id, "❌ Отклонено")
                except ValueError:
                    await self.bot.answer_callback_query(call.id, "❌ Ошибка ID")
            
            elif callback_data.startswith('admin_ban_'):
                try:
                    user_id = int(callback_data.replace('admin_ban_', ''))
                    await self.ban_user(user_id)
                    await self.bot.answer_callback_query(call.id, "⛔ Забанено")
                except ValueError:
                    await self.bot.answer_callback_query(call.id, "❌ Ошибка ID")
            
            elif callback_data.startswith('view_request_'):
                try:
                    user_id = int(callback_data.replace('view_request_', ''))
                    if user_id in self.active_requests:
                        request = self.active_requests[user_id]
                        info = f"""
👤 Информация:
ID: {request.user_id}
Имя: {request.user_first_name} {request.user_last_name}
Username: @{request.user_username}
Статус: {request.status.value}
Дата: {request.channel_request_date}
                        """
                        await self.bot.send_message(admin_id, info)
                        await self.bot.answer_callback_query(call.id, "ℹ️ Информация отправлена")
                    else:
                        await self.bot.answer_callback_query(call.id, "❌ Заявка не найдена")
                except ValueError:
                    await self.bot.answer_callback_query(call.id, "❌ Ошибка ID")
        
        @self.bot.message_handler(commands=['start', 'help'])
        async def start_command(message):
            help_text = """
🤖 Channel Request Bot

Автоматическая обработка заявок на вступление в канал.

Для подачи заявки:
1. Перейдите в канал
2. Нажмите "Вступить"
3. Подтвердите правила в боте

Команды для админов:
/stats - статистика
/cleanup - очистка старых данных
/test - проверка работы
            """
            await self.bot.send_message(message.chat.id, help_text, parse_mode='Markdown')
        
        @self.bot.message_handler(commands=['stats'])
        async def stats_command(message):
            if message.from_user.id not in self.config['bot']['admin_ids']:
                await self.bot.send_message(message.chat.id, "❌ Нет прав администратора")
                return
            
            total = len(self.active_requests)
            pending = len([r for r in self.active_requests.values() if r.status == RequestStatus.PENDING])
            confirmed = len([r for r in self.active_requests.values() if r.status == RequestStatus.CONFIRMED])
            approved = len([r for r in self.active_requests.values() if r.status == RequestStatus.APPROVED])
            declined = len([r for r in self.active_requests.values() if r.status == RequestStatus.DECLINED])
            banned = len(self.banned_users)
            
            stats_text = f"""
📊 Статистика:

• Всего заявок: {total}
• Ожидают: {pending}
• Подтверждены: {confirmed}
• Одобрены: {approved}
• Отклонены: {declined}
• Забанено: {banned}

Канал: {self.config['channel']['title']}
            """
            
            await self.bot.send_message(message.chat.id, stats_text, parse_mode='Markdown')
        
        @self.bot.message_handler(commands=['cleanup'])
        async def cleanup_command(message):
            if message.from_user.id not in self.config['bot']['admin_ids']:
                await self.bot.send_message(message.chat.id, "❌ Нет прав администратора")
                return
            
            days = self.config['settings']['retention_days']
            removed = 0
            
            current_time = datetime.now()
            to_remove = []
            
            for user_id, request in self.active_requests.items():
                if request.channel_request_date:
                    days_old = (current_time - request.channel_request_date).days
                    if days_old > days:
                        to_remove.append(user_id)
            
            for user_id in to_remove:
                del self.active_requests[user_id]
                removed += 1
            
            self.save_data()
            
            await self.bot.send_message(message.chat.id, f"🗑️ Удалено {removed} старых заявок", parse_mode='Markdown')
        
        @self.bot.message_handler(commands=['test'])
        async def test_command(message):
            await self.bot.send_message(
                message.chat.id,
                f"✅ Бот работает!\nКанал: {self.config['channel']['title']}",
                parse_mode='Markdown'
            )
        
        @self.bot.message_handler(commands=['users'])
        async def users_command(message):
            if message.from_user.id not in self.config['bot']['admin_ids']:
                return
            
            recent_users = list(self.active_requests.values())[-10:]  # Последние 10
            
            if not recent_users:
                await self.bot.send_message(message.chat.id, "Нет заявок")
                return
            
            users_text = "👥 Последние пользователи:\n\n"
            for req in recent_users:
                users_text += f"• {req.user_first_name} (@{req.user_username}) - {req.status.value}\n"
            
            await self.bot.send_message(message.chat.id, users_text, parse_mode='Markdown')
    
    async def run(self):
        """Запуск бота"""
        logger.info("Бот запущен...")
        
        try:
            me = await self.bot.get_me()
            logger.info(f"Бот: @{me.username} (ID: {me.id})")
            
            await self.bot.polling(non_stop=True, timeout=60)
        except Exception as e:
            logger.error(f"Ошибка при запуске: {e}", exc_info=True)
            raise


async def main():
    """Основная функция"""
    try:
        bot = ChannelRequestBot()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise


if __name__ == '__main__':
    asyncio.run(main())
