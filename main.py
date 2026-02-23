#!/usr/bin/env python3
"""
Bot Telegram de Prediction - v10.0
Prédictions basées sur une base chargée par l'administrateur via /pre
Pas de système de pause automatique — arrêt manuel via /stop
"""
import os
import sys
import asyncio
import logging
import re
import random
import json
from datetime import datetime, timedelta
from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from config import (
    API_ID, API_HASH, BOT_TOKEN,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, ADMIN_ID,
    PORT, PREDICTION_TIMEOUT, TRIGGER_DISTANCE, JOKE_INTERVAL_SECONDS
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================================
# VARIABLES GLOBALES
# ============================================================

bot_client = None

# Base de données de prédiction: { numero: suit }
prediction_db = {}

DB_FILE = 'prediction_db.json'


def save_prediction_db():
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in prediction_db.items()}, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Base sauvegardée: {len(prediction_db)} numéros → {DB_FILE}")
    except Exception as e:
        logger.error(f"❌ Erreur sauvegarde DB: {e}")


def load_prediction_db():
    global prediction_db
    if not os.path.exists(DB_FILE):
        logger.info(f"📭 Aucun fichier de base trouvé ({DB_FILE}), démarrage avec DB vide")
        return
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        prediction_db = {int(k): v for k, v in raw.items()}
        logger.info(f"✅ Base chargée depuis {DB_FILE}: {len(prediction_db)} numéros")
    except Exception as e:
        logger.error(f"❌ Erreur chargement DB: {e}")


bot_state = {
    'last_source_number': 0,
    'last_prediction_number': None,
    'predictions_history': [],
    'is_stopped': False,
    'stop_end': None,
    'joke_task': None,
    'waiting_for_predictions': False,
}

verification_state = {
    'predicted_number': None,
    'predicted_suit': None,
    'current_check': 0,
    'message_id': None,
    'channel_id': None,
    'status': None,
    'base_game': None,
    'timestamp': None
}

stats_bilan = {
    'total': 0, 'wins': 0, 'losses': 0,
    'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0, '✅3️⃣': 0},
}

# ============================================================
# SYSTÈME DE BLAGUES
# ============================================================

DEFAULT_JOKES = [
    "🎰 Pourquoi les cartes ne jouent-elles jamais au football ? Parce qu'elles ont peur des tacles ! ⚽",
    "🃏 Quelle est la carte la plus drôle ? Le joker, bien sûr ! Il a toujours un as dans sa manche... ou pas ! 😄",
    "♠️ Pourquoi le cœur a-t-il perdu au poker ? Parce qu'il montrait toujours ses sentiments ! 💔",
    "🎲 Qu'est-ce qu'un dé dit à un autre dé ? 'On se retrouve au casino ce soir ?' 🎰",
    "♦️ Pourquoi les diamants sont-ils si chers ? Parce qu'ils ont beaucoup de carats... et de caractère ! 💎",
    "🍀 Quelle est la différence entre un joueur de poker et un magicien ? Le magicien perd son chapeau, le joueur perd sa chemise ! 🎩",
    "♣️ Pourquoi les trèfles portent-ils bonheur ? Parce qu'ils n'ont pas besoin de travailler, ils sont déjà dans les cartes ! 🍀",
    "🎰 Que fait une carte quand elle est fatiguée ? Elle se couche... sur le tapis vert ! 😴",
    "❤️ Pourquoi le roi de cœur est-il toujours amoureux ? Parce qu'il a toujours un cœur sur la main ! 👑",
    "🃏 Qu'est-ce qu'un as qui ment ? Un as... du bluff ! 😎"
]

JOKES_LIST = DEFAULT_JOKES.copy()

# ============================================================
# PARSING DE LA BASE DE PRÉDICTION
# ============================================================

def parse_prediction_text(text):
    db = {}
    errors = []
    suit_map = {
        '❤️': '❤️', '❤': '❤️',
        '♦️': '♦️', '♦': '♦️',
        '♣️': '♣️', '♣': '♣️',
        '♠️': '♠️', '♠': '♠️',
    }

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        match = re.match(r'^(\d+)\s*[\[\(]?\s*([❤♦♣♠️]+)\s*[\]\)]?', line)
        if not match:
            continue

        num_str = match.group(1)
        suit_raw = match.group(2).strip()

        suit = None
        for key, val in suit_map.items():
            if suit_raw.startswith(key):
                suit = val
                break

        if suit is None:
            errors.append(f"Costume inconnu: '{suit_raw}' (ligne: {line[:30]})")
            continue

        db[int(num_str)] = suit

    return db, errors


# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================

def extract_game_number(message):
    match = re.search(r"#N\s*(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))

    for pattern in [r"^#(\d+)", r"N\s*(\d+)", r"Numéro\s*(\d+)", r"Game\s*(\d+)"]:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def extract_suits_from_first_group(message_text):
    matches = re.findall(r"\(([^)]+)\)", message_text)
    if not matches:
        return []

    first_group = matches[0]
    normalized = first_group.replace('❤️', '♥️').replace('❤', '♥️')

    suits = []
    for suit in ['♥️', '♠️', '♦️', '♣️']:
        if suit in normalized:
            suits.append(suit)
    return suits


def is_message_editing(message_text):
    return message_text.strip().startswith('⏰')


def is_message_finalized(message_text):
    return '✅' in message_text or '🔰' in message_text


def reset_verification_state():
    global verification_state
    verification_state = {
        'predicted_number': None,
        'predicted_suit': None,
        'current_check': 0,
        'message_id': None,
        'channel_id': None,
        'status': None,
        'base_game': None,
        'timestamp': None
    }


def find_next_prediction(source_number):
    for offset in range(1, TRIGGER_DISTANCE + 1):
        candidate = source_number + offset
        if candidate in prediction_db:
            return candidate, prediction_db[candidate]
    return None, None


# ============================================================
# FORMATAGE DES PRÉDICTIONS
# ============================================================

WIN_LABELS = ['✅0️⃣', '✅1️⃣', '✅2️⃣', '✅3️⃣']


def format_prediction(number, suit, status=None):
    base = (
        f"🤖 Bot Prédiction\n"
        f"🎰 Prédiction #{number}\n"
        f"🎯 Costume : {suit}\n"
        f"📊 Statut : "
    )

    if status == "pending" or status is None:
        return base + "⏳ En attente"
    elif status in WIN_LABELS:
        return base + f"{status} GAGNÉ"
    elif status == '❌':
        return base + "❌ PERDU"
    elif status == '⏹️':
        return base + "⏹️ Expiré"
    else:
        return base + status


# ============================================================
# SERVEUR WEB
# ============================================================

async def handle_health(request):
    status = "STOPPED" if bot_state['is_stopped'] else "RUNNING"
    last = bot_state['last_source_number']
    pred = verification_state['predicted_number'] or 'Libre'
    db_size = len(prediction_db)
    return web.Response(
        text=f"Bot {status} | Source: #{last} | Pred: #{pred} | DB: {db_size} numéros",
        status=200
    )


async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health)
    app.router.add_get('/health', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Serveur web port {PORT}")
    return runner


# ============================================================
# SYSTÈME D'ARRÊT TEMPORAIRE + BLAGUES
# ============================================================

async def send_jokes_during_stop():
    used_jokes = []

    while bot_state['is_stopped']:
        if bot_state['stop_end'] and datetime.now() >= bot_state['stop_end']:
            logger.info("⏰ Fin de l'arrêt temporaire programmée")
            await stop_temporary_stop()
            break

        available = [j for j in JOKES_LIST if j not in used_jokes]
        if not available:
            used_jokes = []
            available = JOKES_LIST

        if not available:
            await asyncio.sleep(JOKE_INTERVAL_SECONDS)
            continue

        joke = random.choice(available)
        used_jokes.append(joke)

        try:
            await bot_client.send_message(
                PREDICTION_CHANNEL_ID,
                f"😄 **Blague du moment**\n\n{joke}"
            )
            logger.info("😄 Blague envoyée")
        except Exception as e:
            logger.error(f"❌ Erreur envoi blague: {e}")

        # Attendre l'intervalle en petits morceaux pour pouvoir s'arrêter
        elapsed = 0
        while elapsed < JOKE_INTERVAL_SECONDS and bot_state['is_stopped']:
            await asyncio.sleep(10)
            elapsed += 10


async def start_temporary_stop(minutes):
    if bot_state['is_stopped']:
        await bot_client.send_message(ADMIN_ID, "⚠️ Arrêt temporaire déjà en cours!")
        return False

    bot_state['is_stopped'] = True
    bot_state['stop_end'] = datetime.now() + timedelta(minutes=minutes) if minutes > 0 else None

    if verification_state['predicted_number'] is not None:
        reset_verification_state()

    duree_txt = f"{minutes} minutes" if minutes > 0 else "indéfinie"
    msg = (
        f"🛑 **ARRÊT TEMPORAIRE ACTIVÉ**\n\n"
        f"⏱️ Durée : {duree_txt}\n"
        f"😄 Blagues toutes les 5 min\n"
        f"🎰 Prédictions : ARRÊTÉES\n\n"
        f"Utilisez /resume pour reprendre"
    )

    await bot_client.send_message(PREDICTION_CHANNEL_ID, msg)
    await bot_client.send_message(ADMIN_ID, f"🛑 Arrêt temporaire démarré ({duree_txt})")

    bot_state['joke_task'] = asyncio.create_task(send_jokes_during_stop())
    logger.info(f"🛑 Arrêt temporaire démarré: {duree_txt}")
    return True


async def stop_temporary_stop():
    if not bot_state['is_stopped']:
        return False

    bot_state['is_stopped'] = False
    bot_state['stop_end'] = None

    if bot_state['joke_task']:
        bot_state['joke_task'].cancel()
        try:
            await bot_state['joke_task']
        except asyncio.CancelledError:
            pass
        bot_state['joke_task'] = None

    msg = (
        "✅ **ARRÊT TERMINÉ**\n\n"
        "🤖 Les prédictions reprennent!\n"
        "🎰 Bonne chance à tous! 🍀"
    )

    await bot_client.send_message(PREDICTION_CHANNEL_ID, msg)
    await bot_client.send_message(ADMIN_ID, "✅ Arrêt terminé — Prédictions relancées")
    logger.info("✅ Arrêt temporaire terminé")
    return True


# ============================================================
# SYSTÈME DE PRÉDICTION
# ============================================================

async def send_prediction(target_game, predicted_suit, base_game):
    if bot_state['is_stopped']:
        logger.info("🛑 Prédiction bloquée: arrêt temporaire en cours")
        return False

    if verification_state['predicted_number'] is not None:
        logger.error(
            f"⛔ BLOQUÉ: Prédiction #{verification_state['predicted_number']} "
            f"en cours de vérification!"
        )
        return False

    try:
        prediction_text = format_prediction(target_game, predicted_suit, "pending")
        sent_msg = await bot_client.send_message(PREDICTION_CHANNEL_ID, prediction_text)

        verification_state.update({
            'predicted_number': target_game,
            'predicted_suit': predicted_suit,
            'current_check': 0,
            'message_id': sent_msg.id,
            'channel_id': PREDICTION_CHANNEL_ID,
            'status': 'pending',
            'base_game': base_game,
            'timestamp': datetime.now()
        })

        bot_state['last_prediction_number'] = target_game
        bot_state['predictions_history'].append({
            'number': target_game,
            'suit': predicted_suit,
            'trigger': base_game,
            'timestamp': datetime.now().strftime('%H:%M:%S')
        })

        logger.info(
            f"🚀 PRÉDICTION #{target_game} ({predicted_suit}) lancée "
            f"[déclencheur #{base_game}]"
        )
        return True

    except Exception as e:
        logger.error(f"❌ Erreur envoi prédiction: {e}")
        return False


async def update_prediction_status(status):
    global stats_bilan

    if verification_state['predicted_number'] is None:
        return False

    try:
        predicted_num = verification_state['predicted_number']
        predicted_suit = verification_state['predicted_suit']

        updated_text = format_prediction(predicted_num, predicted_suit, status)
        await bot_client.edit_message(
            verification_state['channel_id'],
            verification_state['message_id'],
            updated_text
        )

        if status in WIN_LABELS:
            stats_bilan['total'] += 1
            stats_bilan['wins'] += 1
            stats_bilan['win_details'][status] = stats_bilan['win_details'].get(status, 0) + 1
            logger.info(f"🎉 #{predicted_num} GAGNÉ ({status})")
        elif status == '❌':
            stats_bilan['total'] += 1
            stats_bilan['losses'] += 1
            logger.info(f"💔 #{predicted_num} PERDU")
        elif status == '⏹️':
            logger.info(f"⏹️ #{predicted_num} EXPIRÉ")

        logger.info("🔓 SYSTÈME LIBÉRÉ")
        reset_verification_state()
        return True

    except Exception as e:
        logger.error(f"❌ Erreur mise à jour statut: {e}")
        return False


async def process_verification_step(game_number, message_text):
    if verification_state['predicted_number'] is None:
        return

    predicted_num = verification_state['predicted_number']
    predicted_suit = verification_state['predicted_suit']
    current_check = verification_state['current_check']

    expected_number = predicted_num + current_check
    if game_number != expected_number:
        logger.warning(f"⚠️ Reçu #{game_number} != attendu #{expected_number}")
        return

    suits = extract_suits_from_first_group(message_text)
    logger.info(
        f"🔍 Vérification #{game_number}: groupes={suits}, attendu={predicted_suit}"
    )

    predicted_normalized = predicted_suit.replace('❤️', '♥️').replace('❤', '♥️')

    if predicted_normalized in suits:
        win_label = WIN_LABELS[current_check]
        logger.info(f"🎉 GAGNÉ! {predicted_suit} trouvé au check {current_check} → {win_label}")
        await update_prediction_status(win_label)
        return

    if current_check < 3:
        verification_state['current_check'] += 1
        next_num = predicted_num + verification_state['current_check']
        logger.info(f"❌ Check {current_check} échoué sur #{game_number}, prochain: #{next_num}")
    else:
        logger.info(f"💔 PERDU après 4 vérifications")
        await update_prediction_status("❌")


async def check_prediction_timeout(current_game):
    if verification_state['predicted_number'] is None:
        return False

    predicted_num = verification_state['predicted_number']

    if current_game > predicted_num + PREDICTION_TIMEOUT:
        logger.warning(f"⏰ PRÉDICTION #{predicted_num} EXPIRÉE (actuel: #{current_game})")

        try:
            updated_text = format_prediction(
                predicted_num, verification_state['predicted_suit'], "⏹️"
            )
            await bot_client.edit_message(
                verification_state['channel_id'],
                verification_state['message_id'],
                updated_text
            )
            await bot_client.send_message(
                ADMIN_ID,
                f"⚠️ Prédiction #{predicted_num} expirée. Système libéré."
            )
        except Exception as e:
            logger.error(f"Erreur mise à jour expiration: {e}")

        reset_verification_state()
        return True

    return False


async def check_and_launch_prediction(game_number):
    if bot_state['is_stopped']:
        return

    await check_prediction_timeout(game_number)

    if verification_state['predicted_number'] is not None:
        return

    if not prediction_db:
        logger.debug("📭 Base de prédiction vide")
        return

    target_num, suit = find_next_prediction(game_number)

    if target_num is None:
        return

    logger.info(
        f"🎯 Cible DB: #{target_num} ({suit}) [source #{game_number}]"
    )
    await send_prediction(target_num, suit, game_number)


# ============================================================
# TRAITEMENT DES MESSAGES SOURCE
# ============================================================

async def process_source_message(event, is_edit=False):
    try:
        message_text = event.message.message
        game_number = extract_game_number(message_text)

        if game_number is None:
            return

        is_editing = is_message_editing(message_text)
        is_finalized = is_message_finalized(message_text)

        log_type = "ÉDITÉ" if is_edit else "NOUVEAU"
        log_status = "⏰" if is_editing else ("✅" if is_finalized else "📝")
        logger.info(f"📩 {log_status} {log_type}: #{game_number}")

        bot_state['last_source_number'] = game_number

        if verification_state['predicted_number'] is not None:
            predicted_num = verification_state['predicted_number']
            current_check = verification_state['current_check']
            expected_number = predicted_num + current_check

            if game_number > predicted_num + PREDICTION_TIMEOUT:
                await check_prediction_timeout(game_number)
                if verification_state['predicted_number'] is None:
                    await check_and_launch_prediction(game_number)

            elif game_number == expected_number:
                if is_editing and not is_finalized:
                    logger.info(f"⏳ #{game_number} en édition, attente...")
                    return

                if is_finalized or not is_editing:
                    logger.info(f"✅ Vérification #{game_number}...")
                    await process_verification_step(game_number, message_text)

                    if verification_state['predicted_number'] is None:
                        await asyncio.sleep(1)
                        await check_and_launch_prediction(bot_state['last_source_number'])
                    return
                else:
                    logger.info(f"⏳ Attente finalisation #{game_number}")
                    return
            else:
                logger.info(f"⏭️ Attente #{expected_number}, reçu #{game_number}")

            return

        await check_and_launch_prediction(game_number)

    except Exception as e:
        logger.error(f"❌ Erreur traitement message: {e}")
        import traceback
        logger.error(traceback.format_exc())


# ============================================================
# COMMANDES ADMIN
# ============================================================

async def handle_admin_commands(event):
    global JOKES_LIST, prediction_db

    if event.sender_id != ADMIN_ID:
        return

    text = (event.message.text or '').strip()
    parts = text.split()
    if not parts:
        return

    cmd = parts[0].lower()

    try:
        # ---- AIDE ----
        if cmd == '/start':
            await event.respond(
                "🤖 **Bot Prédiction v10.0**\n\n"
                "**Base de prédiction:**\n"
                "/pre — Charger/remplacer la base\n"
                "/showdb — Afficher la base\n"
                "/cleardb — Vider la base\n\n"
                "**Contrôle:**\n"
                "/stop [min] — Arrêt temporaire + blagues (0 = indéfini)\n"
                "/resume — Reprendre les prédictions\n"
                "/status — État du système\n"
                "/bilan — Statistiques\n"
                "/reset — Réinitialiser\n"
                "/forceunlock — Débloquer prédiction bloquée\n\n"
                "**Blagues:**\n"
                "/jokes — Gérer les blagues"
            )

        # ---- BASE DE PRÉDICTION ----
        elif cmd == '/pre':
            bot_state['waiting_for_predictions'] = True
            await event.respond(
                "📋 **Charger la base de prédiction**\n\n"
                "Envoyez le texte ou un fichier .txt avec le format :\n"
                "`6 [❤️]`\n"
                "`12 [♣️]`\n"
                "`18 [❤️]`\n"
                "...\n\n"
                "⚠️ L'ancienne base sera entièrement remplacée."
            )

        elif cmd == '/showdb':
            if not prediction_db:
                await event.respond(
                    "📭 Base vide. Utilisez /pre pour charger des données."
                )
                return

            sorted_nums = sorted(prediction_db.keys())
            lines = [f"{n} [{prediction_db[n]}]" for n in sorted_nums]

            chunks = [f"📊 **Base ({len(prediction_db)} numéros)**\n\n"]
            for line in lines:
                if len(chunks[-1]) + len(line) + 1 > 3800:
                    chunks.append("")
                chunks[-1] += line + "\n"

            for c in chunks:
                if c.strip():
                    await event.respond(c)

        elif cmd == '/cleardb':
            count = len(prediction_db)
            prediction_db.clear()
            save_prediction_db()
            await event.respond(f"🗑️ Base vidée ({count} numéros supprimés).")

        # ---- CONTRÔLE ----
        elif cmd == '/stop':
            minutes = 0
            if len(parts) >= 2:
                try:
                    minutes = int(parts[1])
                    if minutes < 0:
                        minutes = 0
                except ValueError:
                    await event.respond("❌ Usage: /stop [minutes] (ex: /stop 30 ou /stop 0)")
                    return

            success = await start_temporary_stop(minutes)
            if success:
                duree = f"{minutes} min" if minutes > 0 else "indéfinie"
                await event.respond(f"✅ Arrêt démarré — durée: {duree}")

        elif cmd == '/resume':
            if not bot_state['is_stopped']:
                await event.respond("ℹ️ Le bot n'est pas en arrêt.")
                return
            await stop_temporary_stop()
            await event.respond("▶️ Prédictions reprises!")

        elif cmd == '/status':
            current_pred = verification_state['predicted_number']
            last_src = bot_state['last_source_number']

            lock = '🔴 OCCUPÉ' if current_pred else '🟢 LIBRE'
            stopped = '🔴 OUI' if bot_state['is_stopped'] else '🟢 NON'

            msg = (
                f"📊 **ÉTAT DU SYSTÈME**\n\n"
                f"🔒 **Verrou:** {lock}\n"
            )
            if current_pred:
                msg += (
                    f"   └ Prédiction #{current_pred} en cours\n"
                    f"   └ Check: {verification_state['current_check']}/3\n"
                    f"   └ Déclencheur: #{verification_state['base_game']}\n"
                    f"   └ Costume: {verification_state['predicted_suit']}\n"
                    f"   └ Attend: #{current_pred + verification_state['current_check']}\n"
                )

            if bot_state['is_stopped'] and bot_state['stop_end']:
                remaining = bot_state['stop_end'] - datetime.now()
                mins = max(0, int(remaining.total_seconds() // 60))
                stopped += f" (encore {mins} min)"

            msg += (
                f"🛑 **Arrêt temp.:** {stopped}\n"
                f"📩 **Dernier source:** #{last_src}\n"
                f"📋 **Base DB:** {len(prediction_db)} numéros\n"
                f"📏 **Distance déclenchement:** source + {TRIGGER_DISTANCE}\n"
            )

            if prediction_db and last_src > 0:
                upcoming = sorted([n for n in prediction_db if n > last_src])[:5]
                if upcoming:
                    lines = [
                        f"#{n} {prediction_db[n]}  (déclenche à #{n - TRIGGER_DISTANCE})"
                        for n in upcoming
                    ]
                    msg += "\n🎯 **Prochaines prédictions:**\n" + "\n".join(lines)
                else:
                    msg += f"\n🎯 **Prochaines:** Aucune dans la DB après #{last_src}"
            elif prediction_db:
                upcoming = sorted(prediction_db.keys())[:5]
                lines = [
                    f"#{n} {prediction_db[n]}  (déclenche à #{n - TRIGGER_DISTANCE})"
                    for n in upcoming
                ]
                msg += "\n🎯 **Prochaines prédictions (début DB):**\n" + "\n".join(lines)
            else:
                msg += "\n🎯 **Prochaines:** Base vide — utilisez /pre"

            await event.respond(msg)

        elif cmd == '/bilan':
            if stats_bilan['total'] == 0:
                await event.respond("📊 Aucune prédiction effectuée")
                return

            win_rate = (stats_bilan['wins'] / stats_bilan['total']) * 100
            wd = stats_bilan['win_details']
            await event.respond(
                f"📊 **BILAN**\n\n"
                f"🎯 Total: {stats_bilan['total']}\n"
                f"✅ Victoires: {stats_bilan['wins']} ({win_rate:.1f}%)\n"
                f"❌ Défaites: {stats_bilan['losses']}\n\n"
                f"**Détails victoires:**\n"
                f"• ✅0️⃣ (N)   : {wd.get('✅0️⃣', 0)}\n"
                f"• ✅1️⃣ (N+1) : {wd.get('✅1️⃣', 0)}\n"
                f"• ✅2️⃣ (N+2) : {wd.get('✅2️⃣', 0)}\n"
                f"• ✅3️⃣ (N+3) : {wd.get('✅3️⃣', 0)}"
            )

        elif cmd == '/reset':
            old_pred = verification_state['predicted_number']
            bot_state['waiting_for_predictions'] = False
            reset_verification_state()

            msg = "🔄 RESET! Système libéré."
            if old_pred:
                msg += f" (prédiction #{old_pred} annulée)"
            await event.respond(msg)

        elif cmd == '/forceunlock':
            old_pred = verification_state['predicted_number']
            reset_verification_state()
            await event.respond(
                f"🔓 Débloqué! #{old_pred} annulée. Système libre."
                if old_pred else "ℹ️ Aucune prédiction en cours."
            )

        # ---- BLAGUES ----
        elif cmd == '/jokes':
            if len(parts) < 2:
                preview = "\n".join([f"{i+1}. {j[:60]}..." for i, j in enumerate(JOKES_LIST[:5])])
                if len(JOKES_LIST) > 5:
                    preview += f"\n... et {len(JOKES_LIST)-5} autres"
                await event.respond(
                    f"😄 **Blagues** ({len(JOKES_LIST)} enregistrées)\n\n"
                    f"Sous-commandes:\n"
                    f"`/jokes list` — Voir toutes\n"
                    f"`/jokes add <texte>` — Ajouter\n"
                    f"`/jokes del <numéro>` — Supprimer\n"
                    f"`/jokes edit <num> <texte>` — Modifier\n"
                    f"`/jokes reset` — Réinitialiser par défaut\n\n"
                    f"**Aperçu:**\n{preview}"
                )
                return

            subcmd = parts[1].lower()

            if subcmd == 'list':
                if not JOKES_LIST:
                    await event.respond("📭 Aucune blague")
                    return
                chunk = ""
                for i, joke in enumerate(JOKES_LIST, 1):
                    line = f"**{i}.** {joke}\n\n"
                    if len(chunk) + len(line) > 3800:
                        await event.respond(chunk)
                        chunk = ""
                    chunk += line
                if chunk:
                    await event.respond(chunk)

            elif subcmd == 'add':
                if len(parts) < 3:
                    await event.respond("📋 Usage: `/jokes add <texte>`")
                    return
                new_joke = ' '.join(parts[2:])
                JOKES_LIST.append(new_joke)
                await event.respond(
                    f"✅ Blague ajoutée! (Total: {len(JOKES_LIST)})\n\n{new_joke}"
                )

            elif subcmd == 'del':
                if len(parts) < 3:
                    await event.respond("📋 Usage: `/jokes del <numéro>`")
                    return
                try:
                    idx = int(parts[2]) - 1
                    if idx < 0 or idx >= len(JOKES_LIST):
                        await event.respond(f"❌ Numéro invalide (1-{len(JOKES_LIST)})")
                        return
                    deleted = JOKES_LIST.pop(idx)
                    await event.respond(f"🗑️ Blague #{idx+1} supprimée!\n\n{deleted[:100]}")
                except ValueError:
                    await event.respond("❌ Entrez un numéro valide")

            elif subcmd == 'edit':
                if len(parts) < 4:
                    await event.respond("📋 Usage: `/jokes edit <numéro> <texte>`")
                    return
                try:
                    idx = int(parts[2]) - 1
                    if idx < 0 or idx >= len(JOKES_LIST):
                        await event.respond(f"❌ Numéro invalide (1-{len(JOKES_LIST)})")
                        return
                    old = JOKES_LIST[idx]
                    JOKES_LIST[idx] = ' '.join(parts[3:])
                    await event.respond(
                        f"✏️ Blague #{idx+1} modifiée!\n\n"
                        f"**Avant:** {old[:80]}\n\n"
                        f"**Après:** {JOKES_LIST[idx]}"
                    )
                except ValueError:
                    await event.respond("❌ Entrez un numéro valide")

            elif subcmd == 'reset':
                JOKES_LIST.clear()
                JOKES_LIST.extend(DEFAULT_JOKES)
                await event.respond(f"🔄 Blagues réinitialisées ({len(JOKES_LIST)} par défaut)")

            else:
                await event.respond("❓ Sous-commande inconnue. Tapez /jokes pour la liste")

        else:
            await event.respond("❓ Commande inconnue. /start pour la liste.")

    except Exception as e:
        logger.error(f"Erreur commande: {e}")
        await event.respond(f"❌ Erreur: {str(e)}")


# ============================================================
# RÉCEPTION DES DONNÉES DE PRÉDICTION DE L'ADMIN
# ============================================================

async def handle_prediction_data_message(event):
    global prediction_db

    if event.sender_id != ADMIN_ID:
        return
    if not bot_state['waiting_for_predictions']:
        return

    text_content = None

    if event.message.file:
        try:
            file_bytes = await event.message.download_media(bytes)
            text_content = file_bytes.decode('utf-8', errors='replace')
            logger.info(f"📂 Fichier reçu ({len(file_bytes)} octets)")
        except Exception as e:
            await event.respond(f"❌ Erreur lecture fichier: {e}")
            return
    elif event.message.text:
        text_content = event.message.text

    if not text_content:
        await event.respond("❌ Aucun contenu détecté. Envoyez un texte ou un fichier .txt")
        return

    bot_state['waiting_for_predictions'] = False

    new_db, errors = parse_prediction_text(text_content)

    if not new_db:
        await event.respond(
            "❌ Aucune prédiction valide trouvée.\n\n"
            "Format attendu:\n`6 [❤️]`\n`12 [♣️]`\n..."
            + (f"\n\n⚠️ Erreurs:\n" + "\n".join(errors[:10]) if errors else "")
        )
        return

    prediction_db.clear()
    prediction_db.update(new_db)
    save_prediction_db()

    sorted_nums = sorted(prediction_db.keys())
    sample = ", ".join([f"#{n} {prediction_db[n]}" for n in sorted_nums[:8]])
    if len(sorted_nums) > 8:
        sample += f" ... +{len(sorted_nums)-8} autres"

    reply = (
        f"✅ **Base remplacée et sauvegardée!**\n\n"
        f"📋 Numéros chargés: {len(prediction_db)}\n"
        f"📝 Plage: #{sorted_nums[0]} → #{sorted_nums[-1]}\n"
        f"💾 Persistante (survit aux redémarrages)\n\n"
        f"**Aperçu:** {sample}"
    )
    if errors:
        reply += f"\n\n⚠️ {len(errors)} ligne(s) ignorée(s)"

    await event.respond(reply)
    logger.info(f"✅ Base remplacée: {len(prediction_db)} numéros")


# ============================================================
# DÉMARRAGE
# ============================================================

async def start_bot():
    global bot_client

    session = os.getenv('TELEGRAM_SESSION', '')
    bot_client = TelegramClient(StringSession(session), API_ID, API_HASH)

    try:
        await bot_client.start(bot_token=BOT_TOKEN)
        logger.info("✅ Bot connecté")

        @bot_client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
        async def source_handler(event):
            await process_source_message(event, is_edit=False)

        @bot_client.on(events.MessageEdited(chats=SOURCE_CHANNEL_ID))
        async def edit_handler(event):
            await process_source_message(event, is_edit=True)

        @bot_client.on(events.NewMessage(pattern=r'^/', from_users=ADMIN_ID))
        async def admin_cmd_handler(event):
            await handle_admin_commands(event)

        @bot_client.on(events.NewMessage(from_users=ADMIN_ID))
        async def admin_data_handler(event):
            msg_text = event.message.text or ''
            if msg_text.strip().startswith('/'):
                return
            await handle_prediction_data_message(event)

        db_info = f"{len(prediction_db)} numéros chargés" if prediction_db else "vide (utilisez /pre)"

        startup = (
            f"🤖 **BOT PRÉDICTION DÉMARRÉ (v10.0)**\n\n"
            f"📋 Base de prédiction: {db_info}\n"
            f"📏 Distance déclenchement: source + {TRIGGER_DISTANCE}\n"
            f"😄 Blagues: {len(JOKES_LIST)} disponibles\n\n"
            f"Canal source: {SOURCE_CHANNEL_ID}\n"
            f"Canal prédictions: {PREDICTION_CHANNEL_ID}\n\n"
            f"/start pour les commandes"
        )
        await bot_client.send_message(ADMIN_ID, startup)
        return bot_client

    except Exception as e:
        logger.error(f"Erreur démarrage bot: {e}")
        return None


async def main():
    logger.info("🚀 Démarrage...")

    load_prediction_db()

    web_runner = await start_web_server()
    client = await start_bot()

    if not client:
        return

    logger.info("✅ Bot opérationnel")

    try:
        while True:
            if bot_state['is_stopped'] and bot_state['stop_end']:
                if datetime.now() >= bot_state['stop_end']:
                    logger.info("⏰ Fin programmée de l'arrêt temporaire")
                    await stop_temporary_stop()
            await asyncio.sleep(30)
    except KeyboardInterrupt:
        logger.info("👋 Arrêt")
    finally:
        if bot_state['joke_task']:
            bot_state['joke_task'].cancel()
        await client.disconnect()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal: {e}")
        sys.exit(1)
