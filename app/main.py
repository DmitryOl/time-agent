import os
from pathlib import Path

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes

from storage import Store

DB_PATH = Path('/app/data/agent.sqlite3')
TOKEN = os.environ['TG_BOT_TOKEN']
OWNER_ID = int(os.environ.get('TG_ALLOWED_USER_ID', '0'))
KINDS = {'wish': 'желание', 'goal': 'цель', 'task': 'задача'}
store = Store(DB_PATH, OWNER_ID)


def positive_id(raw):
    if not raw.isdecimal() or int(raw) <= 0:
        raise ValueError('Укажите положительный числовой Telegram ID.')
    return int(raw)


def page_arg(args, at=0):
    if len(args) <= at:
        return 1
    page = positive_id(args[at])
    if page > 1000000:
        raise ValueError('Слишком большой номер страницы.')
    return page


async def send_lines(message, lines):
    if not lines:
        await message.reply_text('Записей нет.')
        return
    chunk = ''
    for line in lines:
        # Telegram messages have a finite length; long entries go separately.
        if len(chunk) + len(line) + 2 > 3900:
            if chunk:
                await message.reply_text(chunk)
            chunk = ''
        if len(line) > 3900:
            for index in range(0, len(line), 3900):
                await message.reply_text(line[index:index + 3900])
        else:
            chunk = (chunk + '\n\n' if chunk else '') + line
    if chunk:
        await message.reply_text(chunk)


async def private(update):
    if not update.effective_message or not update.effective_user:
        return False
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text('Напишите боту в личном чате.')
        return False
    return True


async def identify(update):
    if not await private(update):
        return None
    u = update.effective_user
    return store.touch(u.id, u.full_name or '', u.username or '')


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await private(update):
        return
    await update.effective_message.reply_text(f'Ваш Telegram user ID: {update.effective_user.id}')


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    if user['status'] == 'pending':
        reply = 'Заявка записана. Администратор должен разрешить доступ. Ваш ID: ' + str(user['user_id'])
    elif user['status'] == 'revoked':
        reply = 'Доступ отозван. Повторное обращение не восстанавливает доступ автоматически.'
    elif user['needs_choice']:
        reply = 'Доступ восстановлен. Выберите /continue (продолжить) либо /newstart CONFIRM (архивировать старые записи). До выбора можно посмотреть /history.'
    else:
        reply = ('Команды: /wish текст, /goal текст, /task текст, /list [страница], /archive [страница].\n'
                 'Администратору: /users pending|active|revoked [страница], /grant ID, /revoke ID причина, '
                 '/userentries ID [active|archive|all] [страница], /accesslog ID [страница].')
    await update.effective_message.reply_text(reply)


async def add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    command = update.effective_message.text.split()[0].split('@')[0][1:].lower()
    text = ' '.join(context.args).strip()
    try:
        eid = store.add(user['user_id'], command, text)
        await update.effective_message.reply_text(f'Сохранено: {KINDS[command]} №{eid}.')
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


async def list_entries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    mode = 'archive' if update.effective_message.text.split()[0].split('@')[0].lower() == '/archive' else 'active'
    try:
        page = page_arg(context.args)
        rows = store.entries(user['user_id'], mode=mode, page=page)
        await send_lines(update.effective_message, [f"№{r['id']} · {KINDS.get(r['kind'], r['kind'])} · {r['created_at']}: {r['text']}" for r in rows])
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


async def choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    fresh = update.effective_message.text.split()[0].split('@')[0].lower() == '/newstart'
    if fresh and context.args != ['CONFIRM']:
        await update.effective_message.reply_text('Для архивации подтвердите: /newstart CONFIRM. Старые записи останутся доступны через /archive.')
        return
    try:
        store.choose(user['user_id'], fresh)
        await update.effective_message.reply_text('Выбор сохранён. ' + ('Старые записи доступны в /archive.' if fresh else 'Старые записи доступны в /list.'))
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    try:
        page = page_arg(context.args)
        rows = store.entries(user['user_id'], mode='all', page=page)
        await send_lines(update.effective_message, [f"№{r['id']} · {KINDS.get(r['kind'], r['kind'])} · {r['created_at']} · {'архив' if r['archived_at'] else 'активная'}: {r['text']}" for r in rows])
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    try:
        if not context.args:
            raise ValueError('Формат: /users pending|active|revoked [страница]')
        status = context.args[0].lower()
        page = page_arg(context.args, 1)
        rows = store.users(user['user_id'], status, page)
        await send_lines(update.effective_message, [f"{r['user_id']} · {r['name']} (@{r['username'] or '-'}) · записей: {r['active_count']} активных, {r['archive_count']} в архиве" + (f" · причина: {r['reason']}" if status == 'revoked' else '') for r in rows])
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    try:
        if len(context.args) != 1:
            raise ValueError('Формат: /grant ID')
        target = positive_id(context.args[0])
        changed = store.grant(user['user_id'], target)
        await update.effective_message.reply_text(('Доступ разрешён.' if changed else 'Доступ уже разрешён.') + f' ID: {target}')
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    try:
        if len(context.args) < 2:
            raise ValueError('Формат: /revoke ID причина')
        target = positive_id(context.args[0])
        reason = ' '.join(context.args[1:]).strip()
        store.revoke(user['user_id'], target, reason)
        await update.effective_message.reply_text(f'Доступ отозван: {target}. Причина сохранена.')
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


async def userentries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    try:
        if not context.args or len(context.args) > 3:
            raise ValueError('Формат: /userentries ID [active|archive|all] [страница]')
        target = positive_id(context.args[0])
        mode = context.args[1].lower() if len(context.args) > 1 else 'all'
        page = page_arg(context.args, 2)
        rows = store.entries(user['user_id'], target=target, mode=mode, page=page)
        await send_lines(update.effective_message, [f"№{r['id']} · {KINDS.get(r['kind'], r['kind'])} · {r['created_at']} · {'архив' if r['archived_at'] else 'активная'}: {r['text']}" for r in rows])
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


async def accesslog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await identify(update)
    if user is None:
        return
    try:
        if not context.args or len(context.args) > 2:
            raise ValueError('Формат: /accesslog ID [страница]')
        target = positive_id(context.args[0])
        page = page_arg(context.args, 1)
        rows = store.events(user['user_id'], target, page)
        await send_lines(update.effective_message, [f"{r['created_at']} · {r['action']} · админ {r['actor_id']} · причина: {r['reason'] or '-'}" for r in rows])
    except (PermissionError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))


def main():
    if OWNER_ID <= 0:
        raise SystemExit('TG_ALLOWED_USER_ID должен быть положительным числовым ID администратора.')
    store.init()
    app = Application.builder().token(TOKEN).build()
    handlers = {'id': show_id, 'start': start, 'list': list_entries, 'archive': list_entries,
                'history': history, 'continue': choice, 'newstart': choice, 'users': users,
                'grant': grant, 'revoke': revoke, 'userentries': userentries, 'accesslog': accesslog}
    for command in KINDS:
        handlers[command] = add_entry
    for command, callback in handlers.items():
        app.add_handler(CommandHandler(command, callback))
    app.run_polling(drop_pending_updates=False)


if __name__ == '__main__':
    main()
