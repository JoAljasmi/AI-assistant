"""Discord transport for the assistant.

The whole trick: discord.py runs an asyncio event loop, but our agent is
blocking (HTTP, subprocess, and approval that waits on a queue). So we NEVER
run a turn on the event loop. We hand each turn to a worker thread via
run_in_executor, which leaves the loop free to keep receiving messages —
including the y/n approval answer that the turn is blocked waiting for.

Routing in on_message:
  1. If this channel has an approval waiting, the message IS the answer.
  2. If a turn is already running here, tell the user to wait.
  3. Otherwise it's a new task -> run it on a worker thread.

One Conversation per channel (keyed by channel id) so each chat keeps its own
memory. Sending to Discord from the worker thread hops back onto the loop via
asyncio.run_coroutine_threadsafe.

Setup (once):
  - Create an application + bot at https://discord.com/developers
  - Enable the "Message Content Intent" (Bot tab) — without it message.content
    is empty and nothing works.
  - Put the bot token in your .env as DISCORD_TOKEN=...
  - Invite the bot to a server (or just DM it).
"""
import asyncio
import os
import queue
import threading
from pathlib import Path

import discord
from dotenv import load_dotenv

from agent import Conversation
from budget import Budget
from config import MAX_TOKENS_DEFAULT, MAX_REQUESTS_PER_MINUTE_DEFAULT
import approval

load_dotenv(Path(__file__).parent.parent / ".env")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# One shared budget across all channels — your hard cap protects your wallet
# no matter how many chats are open.
budget = Budget(MAX_TOKENS_DEFAULT, MAX_REQUESTS_PER_MINUTE_DEFAULT)

# Per-channel state, guarded by a lock because the event loop thread and the
# worker threads both touch it.
conversations = {}        # channel_id -> Conversation
busy = set()              # channel_ids with a turn currently running
pending_answers = {}      # channel_id -> queue.Queue waiting for an approval answer
_state_lock = threading.Lock()

intents = discord.Intents.default()
intents.message_content = True    # privileged — enable it in the dev portal too
client = discord.Client(intents=intents)


def _get_conversation(channel_id):
    with _state_lock:
        convo = conversations.get(channel_id)
        if convo is None:
            convo = Conversation(budget=budget)
            conversations[channel_id] = convo
        return convo


def _run_turn_blocking(channel, content, loop):
    """Runs on a WORKER thread. Binds Discord approval I/O for this channel,
    then runs one conversation turn."""
    channel_id = channel.id

    def send(text):
        # Discord caps messages near 2000 chars; chunk to be safe (code replies
        # get long). Each send hops onto the event loop and we wait for it.
        if not text:
            return
        for i in range(0, len(text), 1900):
            fut = asyncio.run_coroutine_threadsafe(channel.send(text[i:i + 1900]), loop)
            fut.result()

    def prompt(worker_id, command):
        # Arm an answer queue for this channel, then show the request.
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

    approval.bind_approval_io(prompt, wait)

    convo = _get_conversation(channel_id)
    convo.run_turn(content, deliver=send)


@client.event
async def on_ready():
    print(f"[discord] logged in as {client.user}")


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    content = message.content.strip()
    if not content:
        return

    channel_id = message.channel.id
    loop = asyncio.get_running_loop()

    # 1. Pending approval here? This message is the answer.
    with _state_lock:
        answer_q = pending_answers.get(channel_id)
    if answer_q is not None:
        answer_q.put(content)
        return

    # 2. Already running a turn here? Don't stack another on top.
    with _state_lock:
        if channel_id in busy:
            await message.channel.send("hang on — still working on the last thing.")
            return
        busy.add(channel_id)

    # 3. New task -> worker thread, so the event loop stays free for approvals.
    def work():
        try:
            _run_turn_blocking(message.channel, content, loop)
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