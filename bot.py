import discord
import pronotepy
import asyncio
import json
import os
import datetime
import hashlib
import unicodedata

from pathlib import Path
from dotenv import load_dotenv
from discord.ext import commands, tasks


# =========================
# CONFIGURATION
# =========================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

SESSION_FILE = Path("data/session.json")
STATE_FILE = Path("data/last_check.json")
TIMETABLE_FILE = Path("data/last_timetable.json")

CHECK_INTERVAL = 5 * 60
TIMETABLE_INTERVAL = 60 * 60
TIMETABLE_DAYS = 14

# Seuls ces statuts sont suivis dans l'emploi du temps.
# La comparaison ignore les majuscules et les accents.
TIMETABLE_STATUS_KEYWORDS = (
    "changement de salle",
    "cours annulé",
    "exceptionnel",
)


# =========================
# ÉTAT DU BOT
# =========================

pronote_connected = None
last_scan_at = None
last_timetable_scan_at = None

last_grades_count = 0
last_homework_count = 0
last_timetable_count = 0

scan_lock = asyncio.Lock()
timetable_lock = asyncio.Lock()
pronote_access_lock = asyncio.Lock()


# =========================
# STOCKAGE
# =========================

def load_json(path):
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


def fingerprint(data):
    raw = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_text(value):
    """Normalise un texte pour comparer les statuts sans accents/majuscules."""
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def is_interesting_timetable_status(status):
    normalized = normalize_text(status)
    return any(
        normalize_text(keyword) in normalized
        for keyword in TIMETABLE_STATUS_KEYWORDS
    )


# =========================
# PRONOTE
# =========================

def get_client():
    credentials = load_json(SESSION_FILE)

    if not credentials:
        raise RuntimeError(
            "Session absente. Lance pronote_auth.py."
        )

    client = pronotepy.Client.token_login(**credentials)

    if not client.logged_in:
        raise RuntimeError("Connexion Pronote impossible.")

    # Actualisation des identifiants de session
    save_json(SESSION_FILE, client.export_credentials())

    return client


def fetch_data():
    client = get_client()

    grades = []
    homework = []

    # Récupération des notes
    for period in client.periods:
        for grade in period.grades:
            item = {
                "id": fingerprint({
                    "date": grade.date,
                    "subject": grade.subject.name,
                    "grade": grade.grade,
                    "out_of": grade.out_of,
                    "coefficient": grade.coefficient,
                    "comment": grade.comment
                }),
                "subject": grade.subject.name,
                "grade": grade.grade,
                "out_of": grade.out_of,
                "coefficient": grade.coefficient,
                "comment": grade.comment,
                "date": str(grade.date),
                "period": period.name
            }
            grades.append(item)

    # Récupération des devoirs
    for hw in client.homework(datetime.date.today()):
        item = {
            "id": fingerprint({
                "date": hw.date,
                "subject": hw.subject.name,
                "description": hw.description
            }),
            "subject": hw.subject.name,
            "description": hw.description,
            "date": str(hw.date),
            "done": hw.done
        }
        homework.append(item)

    return {
        "grades": grades,
        "homework": homework
    }


def fetch_timetable():
    """
    Récupère les cours des 14 prochains jours, mais ne conserve
    que ceux dont le statut correspond aux statuts surveillés.
    """
    client = get_client()
    today = datetime.date.today()
    end_date = today + datetime.timedelta(days=TIMETABLE_DAYS)

    timetable = []

    for lesson in client.lessons(today, end_date):
        subject_obj = getattr(lesson, "subject", None)
        subject = getattr(subject_obj, "name", None) if subject_obj else None
        start = getattr(lesson, "start", None)
        end = getattr(lesson, "end", None)

        if start is None:
            continue

        raw_status = getattr(lesson, "status", "")
        status = str(raw_status or "").strip()

        # FILTRE IMPORTANT : ignorer tous les cours sans statut ciblé.
        if not is_interesting_timetable_status(status):
            continue

        item = {
            "id": str(getattr(lesson, "id", "") or ""),
            "subject": str(subject or "Cours"),
            "start": start.isoformat() if hasattr(start, "isoformat") else str(start),
            "end": end.isoformat() if hasattr(end, "isoformat") else str(end or ""),
            "teacher": str(getattr(lesson, "teacher_name", None) or "Non précisé"),
            "classroom": str(getattr(lesson, "classroom", None) or "Non précisée"),
            "canceled": bool(getattr(lesson, "canceled", False)),
            "status": status,
        }

        # Identifiant de secours si Pronote ne fournit pas d'ID.
        if not item["id"]:
            item["id"] = fingerprint({
                "subject": item["subject"],
                "start": item["start"],
                "end": item["end"],
            })

        timetable.append(item)

    return timetable


# =========================
# DISCORD
# =========================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================
# EMBEDS NOTES / DEVOIRS
# =========================

def make_grade_embed(grade):
    embed = discord.Embed(
        title="Nouvelle note !",
        description=f"**{grade['subject']}**",
        color=discord.Color.green()
    )

    embed.add_field(
        name="Note",
        value=f"{grade['grade']}/{grade['out_of']}",
        inline=True
    )
    embed.add_field(
        name="Coefficient",
        value=grade["coefficient"] or "Non précisé",
        inline=True
    )
    embed.add_field(
        name="Date",
        value=grade["date"],
        inline=True
    )
    embed.add_field(
        name="Période",
        value=grade["period"],
        inline=True
    )

    if grade["comment"]:
        embed.add_field(
            name="Commentaire",
            value=str(grade["comment"])[:1024],
            inline=False
        )

    embed.set_footer(text="PronoteDiscord")
    return embed


def make_homework_embed(hw):
    embed = discord.Embed(
        title="Nouveau devoir !",
        description=f"**{hw['subject']}**",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="À faire pour le",
        value=hw["date"],
        inline=True
    )
    embed.add_field(
        name="Description",
        value=(str(hw["description"])[:1024] or "Aucune description"),
        inline=False
    )

    embed.set_footer(text="PronoteDiscord")
    return embed


# =========================
# SURVEILLANCE NOTES / DEVOIRS
# =========================

async def check_pronote():
    global pronote_connected, last_scan_at
    global last_grades_count, last_homework_count

    async with scan_lock:
        last_scan_at = datetime.datetime.now(datetime.timezone.utc)

        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)

        try:
            async with pronote_access_lock:
                data = await asyncio.to_thread(fetch_data)
            pronote_connected = True
        except Exception:
            pronote_connected = False
            raise

        last_grades_count = len(data["grades"])
        last_homework_count = len(data["homework"])

        current = {
            "grades": {item["id"]: item for item in data["grades"]},
            "homework": {item["id"]: item for item in data["homework"]}
        }

        previous = load_json(STATE_FILE)

        # Première synchronisation : envoi de toutes les notes et devoirs.
        if previous is None:
            await channel.send(
                "📚 **Première synchronisation Pronote !**\n"
                "Voici toutes les notes et tous les devoirs récupérés."
            )

            for grade in data["grades"]:
                await channel.send(embed=make_grade_embed(grade))

            for hw in data["homework"]:
                await channel.send(embed=make_homework_embed(hw))

            save_json(STATE_FILE, current)

            print(
                "Première synchronisation terminée : "
                f"{len(data['grades'])} notes et "
                f"{len(data['homework'])} devoirs."
            )
            return

        old_grades = previous.get("grades", {})
        for grade_id, grade in current["grades"].items():
            if grade_id not in old_grades:
                await channel.send(embed=make_grade_embed(grade))

        old_homework = previous.get("homework", {})
        for hw_id, hw in current["homework"].items():
            if hw_id not in old_homework:
                await channel.send(embed=make_homework_embed(hw))

        save_json(STATE_FILE, current)

        print(
            f"[{datetime.datetime.now():%H:%M:%S}] "
            f"Scan terminé : {last_grades_count} notes, "
            f"{last_homework_count} devoirs."
        )


# =========================
# SURVEILLANCE EMPLOI DU TEMPS
# =========================

def format_lesson_time(value):
    try:
        return datetime.datetime.fromisoformat(value).strftime("%d/%m/%Y à %H:%M")
    except (TypeError, ValueError):
        return value or "Non précisé"


def make_timetable_embed(lesson, old=None):
    status_normalized = normalize_text(lesson.get("status", ""))

    if "annul" in status_normalized:
        title = "❌ Cours annulé"
        color = discord.Color.red()
    elif "salle" in status_normalized:
        title = "🚪 Changement de salle"
        color = discord.Color.orange()
    else:
        title = "📌 Information exceptionnelle"
        color = discord.Color.blurple()

    embed = discord.Embed(
        title=title,
        description=f"**{lesson['subject']}**",
        color=color
    )

    embed.add_field(
        name="Horaire",
        value=(
            f"{format_lesson_time(lesson['start'])}\n"
            f"→ {format_lesson_time(lesson['end'])}"
        ),
        inline=False
    )
    embed.add_field(
        name="Salle",
        value=lesson["classroom"],
        inline=True
    )
    embed.add_field(
        name="Professeur",
        value=lesson["teacher"],
        inline=True
    )
    embed.add_field(
        name="Statut Pronote",
        value=lesson.get("status") or "Non précisé",
        inline=False
    )

    if old:
        changes = []
        labels = {
            "start": "Début",
            "end": "Fin",
            "teacher": "Professeur",
            "classroom": "Salle",
            "status": "Statut",
        }

        for key, label in labels.items():
            if old.get(key) != lesson.get(key):
                before = old.get(key, "Non précisé")
                after = lesson.get(key, "Non précisé")

                if key in ("start", "end"):
                    before = format_lesson_time(before)
                    after = format_lesson_time(after)

                changes.append(f"**{label} :** {before} → {after}")

        if changes:
            embed.add_field(
                name="Changements détectés",
                value="\n".join(changes)[:1024],
                inline=False
            )

    embed.set_footer(text="PronoteDiscord • Surveillance EDT")
    return embed


async def check_timetable():
    global last_timetable_scan_at, last_timetable_count, pronote_connected

    async with timetable_lock:
        last_timetable_scan_at = datetime.datetime.now(datetime.timezone.utc)

        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)

        try:
            async with pronote_access_lock:
                lessons = await asyncio.to_thread(fetch_timetable)
            pronote_connected = True
        except Exception:
            pronote_connected = False
            raise

        last_timetable_count = len(lessons)
        current = {lesson["id"]: lesson for lesson in lessons}
        previous = load_json(TIMETABLE_FILE)

        # Premier passage : mémorise uniquement les événements ciblés,
        # sans envoyer de notification initiale.
        if previous is None:
            save_json(TIMETABLE_FILE, {"lessons": current})
            print(
                "Première synchronisation EDT : "
                f"{len(current)} événement(s) ciblé(s) enregistré(s)."
            )
            return

        old_lessons = previous.get("lessons", {})
        now = datetime.datetime.now()

        def is_upcoming(lesson):
            try:
                start = datetime.datetime.fromisoformat(lesson["start"])
                reference = (
                    datetime.datetime.now(start.tzinfo)
                    if start.tzinfo
                    else now
                )
                return start >= reference
            except (KeyError, TypeError, ValueError):
                return True

        # Nouveaux événements ciblés ou modifications d'un événement ciblé.
        for lesson_id, lesson in current.items():
            old = old_lessons.get(lesson_id)

            if old is None:
                if is_upcoming(lesson):
                    await channel.send(embed=make_timetable_embed(lesson))
                continue

            if old != lesson and is_upcoming(lesson):
                await channel.send(
                    embed=make_timetable_embed(lesson, old)
                )

        # Si un événement ciblé disparaît de Pronote, on met simplement
        # à jour la référence : aucun message pour les cours ordinaires.
        save_json(TIMETABLE_FILE, {"lessons": current})

        print(
            f"[{datetime.datetime.now():%H:%M:%S}] "
            f"Scan EDT terminé : {last_timetable_count} événement(s) ciblé(s)."
        )


# =========================
# OUTILS DE STATUT
# =========================

def format_task_countdown(task):
    next_iteration = task.next_iteration
    if next_iteration is None:
        return "En attente"

    now = datetime.datetime.now(datetime.timezone.utc)
    remaining = max(0, int((next_iteration - now).total_seconds()))
    minutes, seconds = divmod(remaining, 60)

    return f"{minutes} min {seconds} sec" if minutes else f"{seconds} sec"


def format_relative_time(value):
    return f"<t:{int(value.timestamp())}:R>" if value else "Aucun scan effectué"


# =========================
# COMMANDE /STATUS
# =========================

@bot.tree.command(
    name="status",
    description="Affiche le statut de Pronote Bot."
)
async def status(interaction: discord.Interaction):
    if pronote_connected is True:
        connection = "🟢 Connecté"
        color = discord.Color.green()
    elif pronote_connected is False:
        connection = "🔴 Déconnecté"
        color = discord.Color.red()
    else:
        connection = "🟡 Pas encore testé"
        color = discord.Color.orange()

    embed = discord.Embed(
        title="📊 Statut de Pronote Bot",
        description=f"Connexion Pronote : {connection}",
        color=color
    )

    embed.add_field(
        name="Notes et devoirs",
        value=(
            f"Dernier scan : {format_relative_time(last_scan_at)}\n"
            f"Prochain scan : {format_task_countdown(pronote_loop)}\n"
            f"Notes : {last_grades_count}\n"
            f"Devoirs : {last_homework_count}"
        ),
        inline=False
    )

    embed.add_field(
        name="Emploi du temps",
        value=(
            f"Dernier scan : {format_relative_time(last_timetable_scan_at)}\n"
            f"Prochain scan : {format_task_countdown(timetable_loop)}\n"
            f"Événements ciblés : {last_timetable_count}\n"
            f"Période : {TIMETABLE_DAYS} jours"
        ),
        inline=False
    )

    embed.set_footer(text="PronoteDiscord")
    await interaction.response.send_message(embed=embed)


# =========================
# COMMANDE /SCAN
# =========================

@bot.tree.command(
    name="scan",
    description="Lance immédiatement un scan Pronote."
)
async def scan(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        await check_pronote()
        await interaction.followup.send(
            "✅ Scan terminé ! Les notes et devoirs ont été vérifiés.",
            ephemeral=True
        )
    except Exception as error:
        await interaction.followup.send(
            f"❌ Échec du scan : `{str(error)[:1500]}`",
            ephemeral=True
        )


# =========================
# COMMANDE /EDTSCAN
# =========================

@bot.tree.command(
    name="edtscan",
    description="Lance immédiatement un scan des changements importants de l'EDT."
)
async def edtscan(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        await check_timetable()
        await interaction.followup.send(
            "✅ Scan EDT terminé. Seuls les statuts ciblés sont surveillés.",
            ephemeral=True
        )
    except Exception as error:
        await interaction.followup.send(
            f"❌ Échec du scan EDT : `{str(error)[:1500]}`",
            ephemeral=True
        )


# =========================
# BOUCLE AUTOMATIQUE NOTES / DEVOIRS
# =========================

@tasks.loop(seconds=CHECK_INTERVAL)
async def pronote_loop():
    try:
        await check_pronote()
    except Exception as error:
        print(f"Erreur de surveillance Pronote : {error}")


@pronote_loop.before_loop
async def before_pronote_loop():
    await bot.wait_until_ready()


# =========================
# BOUCLE AUTOMATIQUE EDT
# =========================

@tasks.loop(seconds=TIMETABLE_INTERVAL)
async def timetable_loop():
    try:
        await check_timetable()
    except Exception as error:
        print(f"Erreur de surveillance EDT : {error}")


@timetable_loop.before_loop
async def before_timetable_loop():
    await bot.wait_until_ready()


# =========================
# DÉMARRAGE
# =========================

@bot.event
async def on_ready():
    print(f"Connecté à Discord : {bot.user}")

    await bot.tree.sync()
    print("Commandes slash synchronisées.")

    if not pronote_loop.is_running():
        pronote_loop.start()

    if not timetable_loop.is_running():
        timetable_loop.start()


# =========================
# LANCEMENT
# =========================

if __name__ == "__main__":
    if not TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "Configure DISCORD_TOKEN et DISCORD_CHANNEL_ID dans .env"
        )

    bot.run(TOKEN)
