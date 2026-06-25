"""Discord transport for the assistant.

The agent is blocking (HTTP, subprocess, approval-waits), so we never run a turn
on the asyncio event loop — each turn goes to a worker thread via
run_in_executor, leaving the loop free to receive messages (including approval
answers).

Reminders: a background scheduler thread (scheduler.py) wakes periodically and
asks tasks.due_reminders() what's due. To message you *first* — with no incoming
message to reply to — it hops onto the event loop with run_coroutine_threadsafe,
the same trick approvals use. It sends to the last channel you spoke in, which we
remember on disk so reminders still work right after a restart.

Setup (once):
  - Create an app + bot at https://discord.com/developers
  - Enable the "Message Content Intent" (Bot tab)
  - Put the token in .env as DISCORD_TOKEN=...
  - Invite the bot to a server (or DM it)
"""
import asyncio
import os
import queue
import threading
from pathlib import Path

import discord
from dotenv import load_dotenv

from ..config import MAX_TOKENS_DEFAULT, MAX_REQUESTS_PER_MINUTE_DEFAULT
from ..core import approval
from ..core import filesend
from ..core.agent import Conversation
from ..core.budget import Budget
from ..runtime.scheduler import run_scheduler
from ..runtime.settings import get_setting

load_dotenv(Path(__file__).parent.parent / ".env")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

budget = Budget(MAX_TOKENS_DEFAULT, MAX_REQUESTS_PER_MINUTE_DEFAULT)

# Per-channel state, guarded because the event loop and worker threads share it.
conversations = {}        # channel_id -> Conversation
busy = set()              # channel_ids with a turn currently running
pending_answers = {}      # channel_id -> queue.Queue awaiting an approval answer
_state_lock = threading.Lock()

# Where to send reminders: the last channel the user spoke in. Persisted to disk
# so a reminder due right after a restart still has somewhere to go (before the
# user has said anything in the new run).
from ..paths import REMINDER_CHANNEL_FILE


def _load_reminder_channel():
    try:
        return int(REMINDER_CHANNEL_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _save_reminder_channel(channel_id):
    try:
        REMINDER_CHANNEL_FILE.write_text(str(channel_id))
    except OSError:
        pass


reminder_channel_id = _load_reminder_channel()
scheduler_stop = threading.Event()
_scheduler_started = False

intents = discord.Intents.default()
intents.message_content = True    # privileged — enable it in the dev portal too
client = discord.Client(intents=intents)


def _get_conversation(channel_id):
    with _state_lock:
        convo = conversations.get(channel_id)
        if convo is None:
            convo = Conversation(budget=budget, conversation_id=channel_id)
            conversations[channel_id] = convo
        return convo


async def _send_to_channel(channel_id, text):
    """Send text to a channel by id, even if we have no message object for it
    (which is the case for a proactive reminder)."""
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)
    if not text:
        return
    for i in range(0, len(text), 1900):     # Discord caps near 2000 chars
        await channel.send(text[i:i + 1900])


def _run_turn_blocking(channel, content, loop, image_urls=None):
    """Runs on a WORKER thread: bind Discord approval I/O, run one turn."""
    channel_id = channel.id

    def send(text):
        if not text:
            return
        for i in range(0, len(text), 1900):
            asyncio.run_coroutine_threadsafe(channel.send(text[i:i + 1900]), loop).result()

    def prompt(worker_id, command):
        answer_q = queue.Queue()
        with _state_lock:
            pending_answers[channel_id] = answer_q
        send(
            "**Approval needed** — this will write or run something:\n"
            f"```\n{command}\n```\n"
            "Reply `y` to allow, anything else to deny."
        )

    def wait():
        with _state_lock:
            answer_q = pending_answers.get(channel_id)
        answer = answer_q.get() if answer_q is not None else "n"
        with _state_lock:
            pending_answers.pop(channel_id, None)
        return answer

    def send_file_fn(host_path, caption=None):
        asyncio.run_coroutine_threadsafe(
            channel.send(content=caption or None, file=discord.File(host_path)), loop
        ).result()

    approval.bind_approval_io(prompt, wait)
    filesend.bind_file_sender(send_file_fn)
    convo = _get_conversation(channel_id)
    convo.run_turn(content, deliver=send, image_urls=image_urls)


@client.event
async def on_ready():
    global _scheduler_started
    print(f"[discord] logged in as {client.user}")
    if _scheduler_started:
        return  # on_ready can fire again on reconnect; only start once
    _scheduler_started = True

    loop = asyncio.get_running_loop()

    def reminder_send(channel_id, text):
        # Called from the scheduler thread; hop onto the loop and wait.
        asyncio.run_coroutine_threadsafe(_send_to_channel(channel_id, text), loop).result()

    threading.Thread(
        target=run_scheduler,
        args=(reminder_send, lambda: reminder_channel_id, scheduler_stop),
        kwargs={"interval": 60},
        daemon=True,
    ).start()
    print("[discord] reminder scheduler started")


@client.event
async def on_message(message):
    global reminder_channel_id
    if message.author == client.user:
        return

    # In a server, honor mention-only mode if it's currently on (toggleable
    # at runtime via the set_mention_mode tool). DMs always get a reply.
    if message.guild is not None:
        if get_setting("mention_only", True) and client.user not in message.mentions:
            return

    # Strip the bot's own mention out of the text the model sees.
    content = message.content
    for tag in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
        content = content.replace(tag, "")
    content = content.strip()

    # Pull any image attachments — the model can look at these.
    image_urls = [
        a.url for a in message.attachments
        if (a.content_type or "").startswith("image/")
    ]
    if not content and not image_urls:
        return
        
    author = message.author.display_name      # who is speaking

    # --- your control commands (you, not the model): approvals + budget ---
    low = content.lower()
    if low in ("!auto", "!auto on", "!auto off", "!auto status", "!status"):
        from ..core.approval import is_auto_approve, toggle_auto_approve
        if low == "!status":
            state = "ON" if is_auto_approve() else "OFF"
            await message.channel.send(f"Budget: {budget.snapshot()} | auto-approve: {state}")
            return
        if low == "!auto status":
            state = "ON" if is_auto_approve() else "OFF"
        elif low == "!auto on":
            if not is_auto_approve():
                toggle_auto_approve()
            state = "ON"
        elif low == "!auto off":
            if is_auto_approve():
                toggle_auto_approve()
            state = "OFF"
        else:  # bare "!auto" toggles
            state = "ON" if toggle_auto_approve() else "OFF"
        note = (" Writes and commands run without asking — the danger filter still blocks the worst."
                if state == "ON" else " I'll ask before writes and commands again.")
        await message.channel.send(f"Auto-approve {state}.{note}")
        return

    channel_id = message.channel.id

    # Remember this channel for reminders (and persist if it changed).
    if reminder_channel_id != channel_id:
        reminder_channel_id = channel_id
        _save_reminder_channel(channel_id)

    loop = asyncio.get_running_loop()

    # 1. Pending approval here? This message is the answer.
    with _state_lock:
        answer_q = pending_answers.get(channel_id)
    if answer_q is not None:
        answer_q.put(content)
        return

    # 2. Already running a turn here? Don't stack another.
    with _state_lock:
        if channel_id in busy:
            await message.channel.send("hang on — still working on the last thing.")
            return
        busy.add(channel_id)

    # 3. New task -> worker thread, so the loop stays free for approvals.
    def work():
        try:
            turn_text = f"{author}: {content}" if content else f"{author} sent an image."
            _run_turn_blocking(message.channel, turn_text, loop, image_urls)
        except RuntimeError as e:
            asyncio.run_coroutine_threadsafe(
                message.channel.send(f"[halted: {e}]"), loop
            )
        finally:
            with _state_lock:
                busy.discard(channel_id)

    await loop.run_in_executor(None, work)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env first.")
    client.run(DISCORD_TOKEN)