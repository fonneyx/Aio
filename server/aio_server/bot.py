"""Telegram bot: send a link, tap a format, get the file in the chat. Run with: python -m aio_server.bot"""

import asyncio
import html
import json
import logging
import re
import secrets
import subprocess
from contextlib import ExitStack
from pathlib import Path

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    ReplyParameters,
    Update,
)
from telegram.constants import ChatAction, ParseMode, ReactionEmoji
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from aio_grab import Engine, EngineError, Request

from .api import engine_from
from .config import Settings
from .jobs import JobStore, new_job_id
from .storage import Storage

log = logging.getLogger(__name__)

# Files bots can send through Telegram's cloud Bot API. A self-hosted Bot API server raises this to 2 GB.
UPLOAD_LIMIT = 50 * 1024 * 1024
# Telegram compresses photos and rejects ones over 10 MB; bigger images go out as files.
PHOTO_LIMIT = 10 * 1024 * 1024
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac"}
MAX_VIDEO_BUTTONS = 4
PROGRESS_EDIT_SECONDS = 3  # Telegram rate-limits message edits
URL_PATTERN = re.compile(r"https?://\S+")

WELCOME = (
    "<b>Aio</b> saves videos and music from YouTube, Instagram, SoundCloud and over a thousand other sites.\n\n"
    "Send me a link, pick a quality, and the file arrives right here. "
    "Posts with several videos come back as an album."
)
# Shown on the bot's empty chat screen ("What can this bot do?") and in its profile.
DESCRIPTION = (
    "Send a link from YouTube, Instagram, SoundCloud or 1,000+ other sites and get the video or MP3 "
    "right here in the chat. Pick the quality, see the size first, no ads."
)
SHORT_DESCRIPTION = "Videos and music from 1,000+ sites, straight into the chat."
COMMANDS = [BotCommand("start", "What Aio does"), BotCommand("help", "How to use it")]


class AioBot:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self.engine = engine
        self.allowed_users = settings.telegram_users
        # Separate from the web server's staging folder, whose sweeper owns the job folders it finds.
        self.storage = Storage(settings.storage_dir / "telegram", settings.file_ttl_seconds)
        self.storage.sweep(JobStore())  # leftovers from a previous run
        self.slots = asyncio.Semaphore(settings.workers)
        self.choices: dict[str, tuple[str, list[tuple[str, Request]]]] = {}

    def build(self, token: str) -> Application:
        app = (Application.builder().token(token).media_write_timeout(600).read_timeout(30)
               .post_init(self.set_profile).build())
        app.add_handler(CommandHandler(["start", "help"], self.on_start))
        app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, self.on_link))
        app.add_handler(CallbackQueryHandler(self.on_choice, pattern=r"^dl:"))
        return app

    async def set_profile(self, app: Application) -> None:
        """Command menu and descriptions. The profile photo can only be set through @BotFather (/setuserpic)."""
        try:
            await app.bot.set_my_commands(COMMANDS)
            await app.bot.set_my_description(DESCRIPTION)
            await app.bot.set_my_short_description(SHORT_DESCRIPTION)
        except TelegramError as e:
            log.warning("Couldn't update the bot profile: %s", e)

    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(WELCOME, parse_mode=ParseMode.HTML)

    def allowed(self, update: Update) -> bool:
        user = update.effective_user
        return not self.allowed_users or (user is not None and user.id in self.allowed_users)

    async def on_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not self.allowed(update):
            user = update.effective_user
            await message.reply_text(f"This is a private Aio bot. Your Telegram ID is {user.id if user else 'unknown'}.")
            return
        match = URL_PATTERN.search(message.text or message.caption or "")
        if not match:
            await message.reply_text("Send me a link to a video or track.")
            return
        url = match.group(0)
        log.info("Link from chat %s: %s", message.chat_id, url)
        await _react(message, ReactionEmoji.EYES)
        status = await message.reply_text("Looking up the link…")
        try:
            summary = await asyncio.to_thread(self.engine.probe, url)
        except EngineError as e:
            log.info("Lookup failed for %s: %s", url, e)
            await status.edit_text(f"Couldn't use this link. {e}")
            return

        options = build_choices(summary)
        if not options:
            await status.edit_text("Everything available here is over Telegram's 50 MB limit for bots.")
            return
        token = secrets.token_urlsafe(6)
        self.choices[token] = (url, [(label, Request(url, **request)) for label, request in options])
        while len(self.choices) > 500:
            self.choices.pop(next(iter(self.choices)))

        keyboard = _keyboard(token, [label for label, _ in options])
        caption = _caption(summary)
        await status.delete()
        thumbnail = _jpeg_thumbnail(summary.get("thumbnail"))
        try:
            if not thumbnail:
                raise BadRequest("no thumbnail")
            await message.reply_photo(thumbnail, caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except BadRequest:
            await message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def on_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not self.allowed(update):
            await query.answer("This is a private Aio bot.", show_alert=True)
            return
        _, token, index = query.data.split(":")
        entry = self.choices.get(token)
        if entry is None:
            await query.answer("This menu expired. Send the link again.", show_alert=True)
            return
        if context.user_data.get("busy"):
            await query.answer("One download at a time. Wait for the current one to finish.", show_alert=True)
            return
        label, request = entry[1][int(index)]
        await query.answer()
        context.user_data["busy"] = True
        try:
            await self._deliver(query.message, label, request)
        finally:
            context.user_data["busy"] = False

    async def _deliver(self, menu: Message, label: str, request: Request) -> None:
        status = _StatusMessage(menu, label)
        await status.show("Waiting for a free slot…")
        job_id = new_job_id()
        try:
            async with self.slots:
                state: dict = {}
                out_dir = self.storage.job_dir(job_id)
                task = asyncio.create_task(asyncio.to_thread(self.engine.download, request, out_dir, state.update))
                while not task.done():
                    await status.show(_progress_text(state))
                    await asyncio.wait({task}, timeout=PROGRESS_EDIT_SECONDS)
                files = task.result()

            sendable = [f for f in files if f.stat().st_size <= UPLOAD_LIMIT]
            if not sendable:
                size = max(f.stat().st_size for f in files)
                await status.show(f"The file came out at {_mb(size)}, over Telegram's 50 MB limit for bots. Pick a lower quality.", done=True)
                return
            await status.show("Sending…")
            if len(sendable) == 1:
                await self._send(menu, sendable[0], state)
            else:
                await self._send_album(menu, sendable, request)
            await status.remove()
            if menu.reply_to_message:
                await _react(menu.reply_to_message, ReactionEmoji.THUMBS_UP)
            if skipped := len(files) - len(sendable):
                await menu.reply_text(f"{skipped} of {len(files)} items were over Telegram's 50 MB limit and were left out.")
            log.info("Sent %d file(s) to chat %s", len(sendable), menu.chat_id)
        except EngineError as e:
            await status.show(f"Failed: {e}", done=True)
        except Exception:
            log.exception("Telegram delivery failed")
            await status.show("Something went wrong. Try again.", done=True)
        finally:
            self.storage.delete(job_id)

    async def _send(self, menu: Message, path: Path, state: dict) -> None:
        chat = menu.chat
        reply = ReplyParameters(message_id=menu.message_id)
        duration = int(state["duration"]) if state.get("duration") else None
        kind = media_type(path)
        with path.open("rb") as file:
            if kind == "video":
                await chat.send_action(ChatAction.UPLOAD_VIDEO)
                # Without width/height Telegram shows every video as a square; without a thumbnail, a black frame.
                width, height, thumbnail = await asyncio.to_thread(video_details, path)
                await chat.send_video(file, filename=path.name, duration=duration, width=width, height=height,
                                      thumbnail=thumbnail.read_bytes() if thumbnail else None,
                                      supports_streaming=True, reply_parameters=reply)
            elif kind == "audio":
                await chat.send_action(ChatAction.UPLOAD_VOICE)
                await chat.send_audio(file, filename=path.name, duration=duration, title=state.get("title"),
                                      reply_parameters=reply)
            elif kind == "photo" and path.stat().st_size <= PHOTO_LIMIT:
                await chat.send_action(ChatAction.UPLOAD_PHOTO)
                await chat.send_photo(file, reply_parameters=reply)
            else:
                await chat.send_action(ChatAction.UPLOAD_DOCUMENT)
                await chat.send_document(file, filename=path.name, reply_parameters=reply)

    async def _send_album(self, menu: Message, files: list[Path], request: Request) -> None:
        """A multi-item post as Telegram albums (at most 10 items each). Audio can't mix with photos or videos."""
        reply = ReplyParameters(message_id=menu.message_id)
        audio = [f for f in files if media_type(f) == "audio"]
        visual = [f for f in files if media_type(f) == "video"
                  or (media_type(f) == "photo" and f.stat().st_size <= PHOTO_LIMIT)]
        rest = [f for f in files if f not in audio and f not in visual]
        for group in (visual, audio):
            for start in range(0, len(group), 10):
                await self._send_group(menu, group[start:start + 10], reply)
        for path in rest:
            await self._send(menu, path, {})

    async def _send_group(self, menu: Message, chunk: list[Path], reply: ReplyParameters) -> None:
        if len(chunk) == 1:
            await self._send(menu, chunk[0], {})
            return
        with ExitStack() as stack:
            media = []
            for path in chunk:
                file = stack.enter_context(path.open("rb"))
                kind = media_type(path)
                if kind == "video":
                    width, height, thumbnail = await asyncio.to_thread(video_details, path)
                    media.append(InputMediaVideo(file, filename=path.name, width=width, height=height,
                                                 thumbnail=thumbnail.read_bytes() if thumbnail else None,
                                                 supports_streaming=True))
                elif kind == "photo":
                    media.append(InputMediaPhoto(file, filename=path.name))
                else:
                    media.append(InputMediaAudio(file, filename=path.name))
            action = ChatAction.UPLOAD_VOICE if media_type(chunk[0]) == "audio" else ChatAction.UPLOAD_PHOTO
            await menu.chat.send_action(action)
            await menu.chat.send_media_group(media, reply_parameters=reply)


class _StatusMessage:
    """One message per download that is edited in place as the job moves along."""

    def __init__(self, menu: Message, label: str) -> None:
        self.menu = menu
        self.header = f"<b>{html.escape(label)}</b>"
        self.message: Message | None = None
        self.last = ""

    async def show(self, text: str, done: bool = False) -> None:
        body = f"{self.header}\n{html.escape(text)}"
        if body == self.last:
            return
        self.last = body
        try:
            if self.message is None:
                self.message = await self.menu.reply_text(body, parse_mode=ParseMode.HTML)
            else:
                await self.message.edit_text(body, parse_mode=ParseMode.HTML)
        except BadRequest as e:  # "message is not modified" and friends
            log.debug("Status edit skipped: %s", e)

    async def remove(self) -> None:
        if self.message is not None:
            try:
                await self.message.delete()
            except BadRequest:
                pass


def media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in PHOTO_EXTENSIONS:
        return "photo"
    return "document"


def video_details(path: Path) -> tuple[int | None, int | None, Path | None]:
    """Display width and height of a video (rotation applied) and a small JPEG thumbnail next to it."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_side_data=rotation", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    streams = json.loads(probe.stdout or "{}").get("streams") or [{}]
    width, height = streams[0].get("width"), streams[0].get("height")
    rotation = next((d.get("rotation") for d in streams[0].get("side_data_list", []) if "rotation" in d), 0)
    if width and height and abs(int(rotation)) % 180 == 90:  # phone videos stored sideways
        width, height = height, width

    # Telegram wants a JPEG thumbnail no larger than 320 px and 200 KB.
    thumbnail = path.with_suffix(".thumb.jpg")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-frames:v", "1",
         "-vf", "thumbnail,scale='if(gt(iw,ih),320,-2)':'if(gt(iw,ih),-2,320)'", "-q:v", "4", str(thumbnail)],
        capture_output=True,
    )
    return width, height, thumbnail if thumbnail.is_file() else None


def build_choices(summary: dict) -> list[tuple[str, dict]]:
    """Buttons for a probed link: (label, Request fields). Only what fits Telegram's upload limit."""
    if summary.get("kind") == "images":
        return [(f"🖼 All {summary['items']} images" if summary["items"] > 1 else "🖼 Image", {"kind": "video"})]
    if items := summary.get("items"):  # a multi-item post: everything at once, sent back as an album
        return [
            (f"🎬 All {items} videos", {"kind": "video", "quality": "720", "codec": "compatible"}),
            (f"🎵 All {items} as MP3", {"kind": "audio", "audio_format": "mp3", "audio_bitrate": "192"}),
        ]
    options = []
    for row in summary["video"]:
        if len(options) == MAX_VIDEO_BUTTONS:
            break
        # H.264 plays inline in every Telegram client; AV1/VP9 often don't.
        size = row["size_compatible"]
        if size is None and "H.264" not in row["codecs"] and summary["platform"] == "Youtube":
            continue
        if size is not None and size > UPLOAD_LIMIT:
            continue
        label = f"🎬 {row['label']}" + (f" · {_mb(size)}" if size else "")
        options.append((label, {"kind": "video", "quality": str(row["res"]), "codec": "compatible"}))

    if not summary["video"] and summary["audio"] is None:
        options.append(("🎬 Video", {"kind": "video", "codec": "compatible"}))

    duration = summary.get("duration")
    for bitrate in ("320", "128"):
        estimate = int(duration * int(bitrate) * 1000 / 8) if duration else None
        if estimate is None or estimate <= UPLOAD_LIMIT:
            label = f"🎵 MP3 {bitrate} kbps" + (f" · {_mb(estimate)}" if estimate else "")
            options.append((label, {"kind": "audio", "audio_format": "mp3", "audio_bitrate": bitrate}))
    return options


def _keyboard(token: str, labels: list[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(label, callback_data=f"dl:{token}:{i}") for i, label in enumerate(labels)]
    video = [b for b in buttons if not b.text.startswith("🎵")]
    audio = [b for b in buttons if b.text.startswith("🎵")]
    rows = [video[i:i + 2] for i in range(0, len(video), 2)] + ([audio] if audio else [])
    return InlineKeyboardMarkup(rows)


def _caption(summary: dict) -> str:
    title = html.escape(summary.get("title") or "Untitled")
    items = f"{summary['items']} items" if summary.get("items") else None
    details = [summary.get("uploader"), summary.get("platform"), items, _clock(summary.get("duration"))]
    return f"<b>{title}</b>\n{html.escape(' · '.join(d for d in details if d))}\n\nChoose a format:"


def _progress_text(state: dict) -> str:
    stage = state.get("stage") or "Starting"
    if state.get("progress") is None:
        return f"{stage}…"
    filled = round(state["progress"] / 10)
    parts = [f"{state['progress']:.0f}%"]
    if state.get("speed"):
        parts.append(f"{state['speed'] / 1e6:.1f} MB/s")
    return f"{stage}\n{'▰' * filled}{'▱' * (10 - filled)}  {' · '.join(parts)}"


async def _react(message: Message, emoji: ReactionEmoji) -> None:
    try:
        await message.set_reaction(emoji)
    except TelegramError:  # reactions can be disabled in a chat; never worth failing over
        pass


def _jpeg_thumbnail(url: str | None) -> str | None:
    # Telegram won't take WebP by URL; YouTube serves the same image as JPEG under /vi/.
    if url and "/vi_webp/" in url:
        return url.replace("/vi_webp/", "/vi/").replace(".webp", ".jpg")
    return url


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.0f} MB" if size >= 1024 * 1024 else "<1 MB"


def _clock(seconds: float | None) -> str | None:
    if not seconds:
        return None
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per poll otherwise
    settings = Settings.from_env()
    if not settings.telegram_token:
        raise SystemExit("Set AIO_TELEGRAM_TOKEN (in server/.env) to the token from @BotFather.")
    engine = engine_from(settings)
    engine.ensure_ffmpeg()
    bot = AioBot(settings, engine)
    app = bot.build(settings.telegram_token)
    log.info("Aio bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
