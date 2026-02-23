"""
Configuration du Bot Prédiction
Modifiez ce fichier pour personnaliser le bot.
"""
import os

# ============================================================
# CREDENTIALS TELEGRAM API
# Obtenez API_ID et API_HASH sur https://my.telegram.org
# ============================================================
API_ID = 29177661
API_HASH = "a8639172fa8d35dbfd8ea46286d349ab"

# Token du bot (depuis @BotFather sur Telegram)
# La variable d'environnement TELEGRAM_BOT_TOKEN prend priorité si définie
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '7573497633:AAHk9K15yTCiJP-zruJrc9v8eK8I9XhjyH4')

# ============================================================
# CANAUX ET ADMIN
# ============================================================

# Canal source : canal que le bot surveille pour les numéros de jeu
SOURCE_CHANNEL_ID = -1002682552255

# Canal prédictions : canal où le bot envoie ses prédictions
PREDICTION_CHANNEL_ID = -1003814088712

# ID Telegram de l'administrateur (obtenez-le via @userinfobot)
ADMIN_ID = 1190237801

# ============================================================
# SERVEUR WEB (pour Render.com)
# ============================================================

# Port d'écoute du serveur de santé (health check)
PORT = int(os.getenv('PORT', 10000))

# ============================================================
# PARAMÈTRES DE PRÉDICTION
# ============================================================

# Nombre de jeux après la prédiction avant expiration
# Ex: si PREDICTION_TIMEOUT = 10, la prédiction expire si le canal
# dépasse numéro_prédit + 10 sans résultat
PREDICTION_TIMEOUT = 10

# Distance de déclenchement :
# Le bot prédit le numéro X quand le canal source est à X - TRIGGER_DISTANCE
# Ex: TRIGGER_DISTANCE = 2 → canal source à #4 déclenche prédiction #6
TRIGGER_DISTANCE = 2

# Intervalle entre les blagues pendant un arrêt temporaire (en secondes)
# 300 = 5 minutes
JOKE_INTERVAL_SECONDS = 300
