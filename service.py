#!/usr/bin/env python
# pylint: disable=unused-argument

"""
Simple Bot to send timed Telegram messages.

This Bot uses the Application class to handle the bot and the JobQueue to send
timed messages.

First, a few handler functions are defined. Then, those functions are passed to
the Application and registered at their respective places.
Then, the bot is started and runs until we press Ctrl-C on the command line.

Usage:
Basic Alarm Bot example, sends a message after a set time.
Press Ctrl-C on the command line or send a signal to the process to stop the
bot.

Note:
To use the JobQueue, you must install PTB via
`pip install "python-telegram-bot[job-queue]"`
"""

import json
import logging
import subprocess
import traceback
import time
import asyncio

from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, ConversationHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
log = logging.getLogger("service")

TELEGRAM_BOT_TOKEN_KEY = "telegram_bot_token"
TELEGRAM_LOGINS_ALLOWLIST = "telegram_logins_allowlist"

WAITING_FOR_URL = range(1)

telegram_logins = []


def launch(args: list) -> dict:
    process = subprocess.run(
        args, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        raise RuntimeError("Process returned code {}.\nstdout:\n{}\nstderr:\n{}\n".format(
            process.returncode, process.stdout.decode(), process.stderr.decode()))
    return json.loads(process.stdout)


def list_labels(pr_id: str) -> dict:
    api = "/repos/ydb-platform/nbs/issues/{}/labels".format(pr_id)
    return launch(["gh", "api",
                   "-H", "Accept: application/vnd.github+json",
                   "-H" "X-GitHub-Api-Version: 2022-11-28",
                   api])


def add_labels(pr_id: str, labels: list) -> dict:
    if len(labels) == 0:
        raise RuntimeError("Labels is an empty list")

    api = "/repos/ydb-platform/nbs/issues/{}/labels".format(pr_id)

    args = []
    for label in labels:
        args.append("-f")
        args.append("labels[]={}".format(label))

    return launch(["gh", "api",
                   "--method", "POST",
                   "-H", "Accept: application/vnd.github+json",
                   "-H" "X-GitHub-Api-Version: 2022-11-28",
                   api,
                   ] + args)


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hi! Use /run_tests <url> to run tests in PR")


def parse_url(url: str) -> str:
    pr_url = urlparse(url)
    if pr_url.netloc != "github.com":
        raise RuntimeError("Weird url")

    PATH_PREFIX = "/ydb-platform/nbs/pull/"
    if not pr_url.path.startswith(PATH_PREFIX):
        raise RuntimeError("Not a PR url")

    pr_id = pr_url.path.removeprefix(PATH_PREFIX)
    if not pr_id.isdigit() or len(pr_id) > 5:
        raise RuntimeError("Can't parse pr id")

    return pr_id


async def run(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> int:
    pr_id = parse_url(message)
    labels = list_labels(pr_id)
    if type(labels) is not list:
        raise RuntimeError("Weird result: {}".format(labels))

    neccessary_labels = ["blockstore", "large-tests"]
    missing_neccesary_labels = False

    log.info("current labels: %s", json.dumps(labels))

    for neccessary_label in neccessary_labels:
        result = filter(
            lambda label: label["name"] == neccessary_label, labels)
        if (len(list(result))) == 0:
            missing_neccesary_labels = True
            break

    if not missing_neccesary_labels:
        await update.message.reply_text("Setting ok-to-test label...")
        add_labels(pr_id, ["ok-to-test"])
        log.info("Added ok-to-test label")
    else:
        await update.message.reply_text("Setting neccessary labels...")
        add_labels(pr_id, neccessary_labels)
        log.info("Added labels: %s", json.dumps(neccessary_labels, indent=2))

        await asyncio.sleep(5)

        await update.message.reply_text("Setting ok-to-test label...")
        add_labels(pr_id, neccessary_labels + ["ok-to-test"])
        log.info("Added ok-to-test label")

    await update.message.reply_text("Success!")


async def url_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        log.warning(
            "There is no message in the update: %s", update.to_json())
        return ConversationHandler.END

    log.info("Request to run tests on url. User: %s\nText: %s",
             update.message.from_user.to_json(), update.message.text)
    if update.message.from_user.username not in telegram_logins:
        log.warning("Unknown user %s. Users: %s",
                    update.message.from_user.username, json.dumps(telegram_logins))
        await update.message.reply_text("I don't know you.")
        return ConversationHandler.END

    try:
        await run(update, context, update.message.text)

    except (IndexError, ValueError, RuntimeError) as ex:
        log.error("Exception occurred during 'url_message':\n%s",
                  traceback.format_exc())
        await update.message.reply_text("Usage: /run_tests <url>")

    return ConversationHandler.END


async def run_tests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        log.warning(
            "There is no message in the update: %s", update.to_json())
        return ConversationHandler.END

    log.info("Request to run tests. User: %s",
             update.message.from_user.to_json())
    if update.message.from_user.username not in telegram_logins:
        log.warning("Unknown user %s. Users: %s",
                    update.message.from_user.username, json.dumps(telegram_logins))
        await update.message.reply_text("I don't know you.")
        return ConversationHandler.END

    try:
        if (len(context.args) == 0):
            await update.message.reply_text("Send url to the PR, e.g. https://github.com/ydb-platform/nbs/pull/xxx")
            return WAITING_FOR_URL

        await run(update, context, context.args[0])

    except (IndexError, ValueError, RuntimeError):
        log.error("Exception occurred during 'run_tests':\n%s",
                  traceback.format_exc())
        await update.message.reply_text("Usage: /run_tests <url>")

    return ConversationHandler.END


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    log.info("done")
    return ConversationHandler.END


def main() -> None:
    """Run bot."""

    with open('private.json') as file:
        private = json.load(file)
        global telegram_logins
        telegram_logins = private[TELEGRAM_LOGINS_ALLOWLIST]

    application = Application.builder().token(
        private[TELEGRAM_BOT_TOKEN_KEY]).build()

    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("run_tests", run_tests)],
        states={
            WAITING_FOR_URL: [
                MessageHandler(filters.TEXT, url_message)
            ],
        },
        fallbacks=[MessageHandler(filters.Regex("^Done$"), done)],
        name="run_tests_conversation",
        persistent=False,
    ))

    logging.getLogger('httpx').setLevel(logging.WARNING)
    log.info("Starting to poll...")

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=[Update.MESSAGE])


if __name__ == "__main__":
    main()
