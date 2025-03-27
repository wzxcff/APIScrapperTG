import os
import json
from datetime import datetime
from telethon.errors import UserNotParticipantError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, Chat, MessageMediaPhoto, MessageMediaDocument, InputMessagesFilterPinned
import mimetypes
from bot.settings import Config
from database.config import load_config
from database.connect import connect
from database.queries import *


def dump_json(data, filename):
    with open(f'{filename}.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


class Scrapper:
    def __init__(self, target_channel):
        self.target = target_channel
        self.client = Config.client

        self.folders = Config.get_folders(self.target)

        self.avatar_folder = self.folders['avatar_folder']
        self.target_folder = self.folders['target_folder']
        self.participants_avatars_folder = self.folders['participants_avatars_folder']
        self.media_folder = self.folders['media_folder']
        self.jsons_folder = self.folders['jsons_folder']

    async def connect(self):
        await self.client.start()

    async def close(self):
        await self.client.disconnect()

    async def initialize(self):
        await self.connect()
        await self.create_dirs()

    async def get_pinned_messages(self):
        pinned_messages = await self.client.get_messages(self.target, filter=InputMessagesFilterPinned, limit=100)

        res = []

        for msg in pinned_messages:
            pinned_entry = {
                'id': msg.id,
                'text': msg.message,
                'from_id': msg.from_id.user_id if msg.from_id else None,
                'date': msg.date.isoformat(),
                'changed_at': msg.edit_date.isoformat() if msg.edit_date and msg.edit_date != msg.date else None,
            }
            res.append(pinned_entry)
        return res

    async def get_admin_log(self):
        logs = []

        if await self.get_chat_type() in ["Channel admin"]:
            async for action in self.client.iter_admin_log(self.target):
                log_entry = {
                    "action": str(action.action),
                    "performed_by": {
                        "user_id": action.user_id,
                        "first_name": None,
                        "last_name": None,
                        "username": None
                    },
                    "timestamp": action.date.isoformat(),
                }

                try:
                    user = await self.client.get_entity(action.user_id)
                    log_entry["performed_by"]["first_name"] = user.first_name
                    log_entry["performed_by"]["username"] = user.username
                except Exception as e:
                    log_entry["performed_by"]["first_name"] = "Unknown"
                    log_entry["performed_by"]["username"] = None
                    log_entry["error"] = str(e)

                logs.append(log_entry)
        return logs

    async def fetch_target_info(self):
        channel_info = await self.client(GetFullChannelRequest(channel=self.target))
        full_chat = channel_info.full_chat

        res = {"id": full_chat.id, "username": self.target, "title": channel_info.chats[0].title,
               "about": full_chat.about}

        avatar_path = os.path.join(self.avatar_folder, f"{self.target}_avatar.jpg")

        if full_chat.chat_photo:
            profile_photos = await self.client.get_profile_photos(self.target)
            if profile_photos:
                await self.client.download_media(profile_photos[0], file=avatar_path)
        res["avatar"] = avatar_path if os.path.exists(avatar_path) else None
        res["participants_count"] = full_chat.participants_count if full_chat.participants_count else "-"
        res["admins_count"] = full_chat.admins_count if full_chat.admins_count else "-"
        res["kicked_count"] = full_chat.kicked_count if full_chat.kicked_count else "-"
        res["banned_count"] = full_chat.banned_count if full_chat.banned_count else "-"
        res["online_count"] = full_chat.online_count if full_chat.online_count else "-"
        res["requested_at"] = datetime.now().isoformat()
        return res

    async def create_dirs(self):
        for folder in self.folders.values():
            os.makedirs(folder, exist_ok=True)

    async def get_chat_type(self):
        entity = await self.client.get_entity(self.target)

        if isinstance(entity, Channel):
            if entity.megagroup:
                return "Mega group"
            else:
                user = await self.client.get_me()
                try:
                    permissions = await self.client.get_permissions(self.target, user.id)
                except UserNotParticipantError:
                    print("User not participant")
                    return "User not participant"
                if permissions.is_admin or permissions.is_creator:
                    return "Channel admin"
                else:
                    return "Channel user"
        elif isinstance(entity, Chat):
            return "Chat group"
        else:
            return "Unknown"

    async def fetch_messages(self):
        print("Started fetching messages..")
        messages = []

        count = 0
        comments = []

        async for message in self.client.iter_messages(self.target, limit=10):
            count += 1
            print(f"\nMessage #{count} – fetching data")
            sender_id = message.from_id.user_id if message.from_id else None
            msg_data = {
                'id': message.id,
                'text': message.text,
                'date': message.date.isoformat(),
                'changed_at': message.edit_date.isoformat() if message.edit_date and message.edit_date != message.date else None
            }

            # marker

            sender_dict = {}
            if sender_id:
                try:
                    sender = await self.client.get_entity(sender_id)
                    sender_dict['user_id'] = sender_id
                    sender_dict['first_name'] = sender.first_name if sender.first_name else None
                    sender_dict['last_name'] = sender.last_name if sender.last_name else None
                    sender_dict['username'] = sender.username if sender.username else None

                    avatar_path = os.path.join(self.avatar_folder, f"{sender_id}_{sender.first_name}.jpg")
                    if sender.photo and not os.path.exists(avatar_path):
                        await self.client.download_profile_photo(sender, file=avatar_path)

                    sender_dict['avatar'] = avatar_path if os.path.exists(avatar_path) else None
                    sender_dict['is_bot'] = True if sender.bot else False
                except Exception as e:
                    sender_dict['name'] = "Cannot fetch info"
                    msg_data['error'] = str(e)
            else:
                sender_dict['first_name'] = "Unknown sender"
                sender_dict['last_name'] = "Unknown sender"
                sender_dict['username'] = None
                sender_dict['avatar'] = None
            msg_data['sender'] = sender_dict

            print("Searching for replies to message..")
            if message.replies and await self.get_chat_type() in ["Channel admin", "Channel user"]:
                print("Found replies, trying to fetch them.")
                async for comment in self.client.iter_messages(self.target, reply_to=message.id):
                    comment_data = {
                        'id': comment.id,
                        'text': comment.text,
                        'date': comment.date.isoformat(),
                        'changed_at': comment.edit_date.isoformat() if comment.edit_date and comment.edit_date != comment.date else None,
                        'user_id': comment.from_id.user_id if comment.from_id else None
                    }
                    comments.append(comment_data)

                    msg_data['comments'] = comments if comments else None
            else:
                print("No replies found for message.")

            msg_data['media'] = None

            if message.media:
                print(f"Media found. Trying to save it.")

                formatted_date = message.date.strftime("%Y-%m-%d_%H-%M-%S")

                if isinstance(message.media, MessageMediaPhoto):
                    file_path = await message.download_media(
                        file=f"{self.target}/media/{formatted_date}_{message.id}.jpg")
                    msg_data['media'] = file_path if file_path else None
                elif isinstance(message.media, MessageMediaDocument):
                    try:
                        guessed_mime = mimetypes.guess_extension(message.media.document.mime_type)
                        print(f"Guessed mime: {guessed_mime}")
                        file_path = await message.download_media(
                            file=f"{self.target}/media/{formatted_date}_{message.id}{guessed_mime}")
                    except Exception as e:
                        file_path = await message.download_media(
                            file=f"{self.target}/media/{formatted_date}_{message.id}.file")
                        print(f"[ERROR] Error occurred during guessing mime type extension: {e}")
                    msg_data['media'] = file_path if file_path else None
                print("Media was saved successfully\n")

            msg_data['geo'] = None

            if message.media and hasattr(message.media, "geo"):
                geo = message.media.geo

                geo_entry = {
                    "latitude": geo.lat,
                    "longitude": geo.long
                }

                msg_data['geo'] = geo_entry if message.media.geo else None

            messages.append(msg_data)
        target_info = await self.fetch_target_info()

        config = load_config()

        print(f"Loaded config: {config}")

        conn = connect(config)

        # TODO: Create check for max_size messages (store it every 100 messages)
        await insert_message(messages, target_info["id"], conn)

        res = {"target": target_info, "messages": messages}
        return res

    async def get_members(self):
        users = []
        users_dict = {"target": self.target}
        users_list = []

        chat_type = await self.get_chat_type()

        if chat_type in ["Mega group", "Channel admin", "Chat group"]:
            users = await self.client.get_participants(self.target)
        elif chat_type == "Channel user":
            print("Cannot fetch members. You're not an admin.")
        else:
            print("Cannot fetch members. Unknown chat_type.")

        if users:
            for user in users:
                user_entity = await self.client.get_entity(user)

                participant_path = self.participants_avatars_folder + f"/{user.id}_{user.first_name}.jpg"

                user_data = {
                    'user_id': user.id,
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                }

                if user.photo and not os.path.exists(participant_path):
                    await self.client.download_profile_photo(user_entity, file=participant_path)

                user_data['avatar'] = participant_path if os.path.exists(participant_path) else None
                users_list.append(user_data)

        users_dict["participants"] = users_list

        return users_dict
