import discord
import pronotepy
import asyncio
import json
import os
import datetime
import hashlib
import unicodedata
import time
from zoneinfo import ZoneInfo

from pathlib import Path
from dotenv import load_dotenv
from discord.ext import commands, tasks

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

SESSION_FILE = Path("data/session.json")
STATE_FILE = Path("data/last_check.json")
TIMETABLE_FILE = Path("data/last_timetable.json")

CHECK_INTERVAL = 5 * 60
TIMETABLE_INTERVAL = 60 * 60
TIMETABLE_DAYS = 14

TIMETABLE_STATUS_KEYWORDS = (
    "changement de salle",
    "cours annulé",
    "exceptionnel",
)

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
# STOCKAGE ET IDENTIFIANTS
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
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_text(value):
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def is_interesting_timetable_status(status):
    normalized = normalize_text(status)
    return any(normalize_text(k) in normalized for k in TIMETABLE_STATUS_KEYWORDS)


def timetable_lesson_id(subject, start, end):
    """Identifiant stable : ne dépend pas de l'ID temporaire de Pronote."""
    return fingerprint({
        "subject": str(subject or "Cours").strip().lower(),
        "start": start.isoformat() if hasattr(start, "isoformat") else str(start),
        "end": end.isoformat() if hasattr(end, "isoformat") else str(end or ""),
    })


# =========================
# CONNEXION ET RÉCUPÉRATION PRONOTE
# =========================

def get_client():
    credentials = load_json(SESSION_FILE)
    if not credentials:
        raise RuntimeError("Session absente. Lance pronote_auth.py.")

    client = pronotepy.Client.token_login(**credentials)
    if not client.logged_in:
        raise RuntimeError("Connexion Pronote impossible.")

    save_json(SESSION_FILE, client.export_credentials())
    return client

def fetch_menu():
    client = get_client()

    today = datetime.datetime.now(
        ZoneInfo("Europe/Paris")
    ).date()

    menus = client.menus(today, today)

    if not menus:
        return None

    # On privilégie le menu du midi
    menu = next(
        (m for m in menus if m.is_lunch),
        menus[0]
    )

    def format_foods(foods):
        if not foods:
            return None

        names = [
            getattr(food, "name", str(food))
            for food in foods
        ]

        return "\n".join(f"• {name}" for name in names)

    return {
        "date": today,
        "entree": format_foods(menu.first_meal),
        "plat": format_foods(menu.main_meal),
        "accompagnement": format_foods(menu.side_meal),
        "autre": format_foods(menu.other_meal),
        "fromage": format_foods(menu.cheese),
        "dessert": format_foods(menu.dessert),
    }

def make_menu_embed(menu):
    embed = discord.Embed(
        title="🍽️ Menu de la cantine",
        description=menu["date"].strftime("%d/%m/%Y"),
        color=discord.Color.green()
    )

    sections = [
        ("🥗 Entrée", "entree"),
        ("🍛 Plat", "plat"),
        ("🥔 Accompagnement", "accompagnement"),
        ("🍽️ Autre", "autre"),
        ("🧀 Fromage", "fromage"),
        ("🍰 Dessert", "dessert"),
    ]

    for title, key in sections:
        value = menu[key]

        if value:
            embed.add_field(
                name=title,
                value=value[:1024],
                inline=False
            )

    embed.set_footer(text="PronoteDiscord • Source : Pronote")

    return embed

def fetch_data():
    client = get_client()
    grades = []
    homework = []
    periods_data = []

    current_period = getattr(client, "current_period", None)
    current_period_id = getattr(current_period, "id", None)

    for period in client.periods:
        for grade in period.grades:
            item = {
                "id": fingerprint({
                    "date": grade.date,
                    "subject": grade.subject.name,
                    "grade": grade.grade,
                    "out_of": grade.out_of,
                    "coefficient": grade.coefficient,
                    "comment": grade.comment,
                }),
                "subject": grade.subject.name,
                "grade": grade.grade,
                "out_of": grade.out_of,
                "coefficient": grade.coefficient,
                "comment": grade.comment,
                "date": str(grade.date),
                "period": period.name,
            }
            grades.append(item)

        subject_averages = []
        for average in period.averages:
            subject_obj = getattr(average, "subject", None)
            subject_name = getattr(subject_obj, "name", None)
            subject_averages.append({
                "subject": str(subject_name or "Matière"),
                "student": getattr(average, "student", None),
                "out_of": getattr(average, "out_of", None),
            })

        periods_data.append({
            "name": period.name,
            "is_current": (
                getattr(period, "id", None) == current_period_id
                if current_period_id is not None else False
            ),
            "overall_average": getattr(period, "overall_average", None),
            "subject_averages": subject_averages,
        })

    for hw in client.homework(datetime.date.today()):
        item = {
            "id": fingerprint({
                "date": hw.date,
                "subject": hw.subject.name,
                "description": hw.description,
            }),
            "subject": hw.subject.name,
            "description": hw.description,
            "date": str(hw.date),
            "done": hw.done,
        }
        homework.append(item)

    return {
        "grades": grades,
        "homework": homework,
        "periods": periods_data,
    }


def to_number(value):
    """Convertit une valeur Pronote en nombre."""
    if value is None:
        return None

    try:
        return float(str(value).replace(",", ".").replace("/20", "").strip())
    except (TypeError, ValueError):
        return None


def get_current_period(data):
    """Sélectionne la période actuelle, ou la dernière avec une moyenne."""
    periods = data.get("periods", [])

    for period in periods:
        if period.get("is_current"):
            return period

    for period in reversed(periods):
        if to_number(period.get("overall_average")) is not None:
            return period

    return periods[-1] if periods else None


def find_subject_average(period, subject):
    """Récupère la moyenne par matière calculée par Pronote."""
    if not period:
        return None

    for average in period.get("subject_averages", []):
        if normalize_text(average.get("subject")) == normalize_text(subject):
            value = to_number(average.get("student"))
            out_of = to_number(average.get("out_of"))

            if value is None:
                return None
            if out_of is not None and out_of > 0:
                value = value * 20 / out_of

            return value

    return None


async def moyenne_autocomplete(
    interaction: discord.Interaction,
    current: str
):
    """Propose les matières du dernier scan, sans requête réseau."""
    previous = load_json(STATE_FILE) or {}
    grades = previous.get("grades", {})

    subjects = sorted({
        grade.get("subject")
        for grade in grades.values()
        if grade.get("subject")
    })

    choices = [
        discord.app_commands.Choice(
            name="Toutes les matières",
            value="Toutes"
        )
    ]

    for subject in subjects:
        if normalize_text(current) in normalize_text(subject):
            choices.append(
                discord.app_commands.Choice(
                    name=subject[:100],
                    value=subject[:100]
                )
            )

    return choices[:25]


def fetch_timetable():
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

        status = str(getattr(lesson, "status", "") or "").strip()
        if not is_interesting_timetable_status(status):
            continue

        item = {
            # On ignore lesson.id, qui peut changer entre deux sessions.
            "id": timetable_lesson_id(subject, start, end),
            "subject": str(subject or "Cours"),
            "start": start.isoformat() if hasattr(start, "isoformat") else str(start),
            "end": end.isoformat() if hasattr(end, "isoformat") else str(end or ""),
            "teacher": str(getattr(lesson, "teacher_name", None) or "Non précisé"),
            "classroom": str(getattr(lesson, "classroom", None) or "Non précisée"),
            "canceled": bool(getattr(lesson, "canceled", False)),
            "status": status,
        }
        timetable.append(item)

    return timetable


def fetch_next_lesson():
    client = get_client()

    paris_tz = ZoneInfo("Europe/Paris")
    now = datetime.datetime.now(paris_tz)
    today = now.date()
    end_date = today + datetime.timedelta(days=TIMETABLE_DAYS)

    upcoming = []

    for lesson in client.lessons(today, end_date):
        start = getattr(lesson, "start", None)
        end = getattr(lesson, "end", None)

        if start is None:
            continue

        # Convertit les dates en heure française.
        if isinstance(start, str):
            start = datetime.datetime.fromisoformat(start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=paris_tz)
        else:
            start = start.astimezone(paris_tz)

        if end is not None:
            if isinstance(end, str):
                end = datetime.datetime.fromisoformat(end)
            if end.tzinfo is None:
                end = end.replace(tzinfo=paris_tz)
            else:
                end = end.astimezone(paris_tz)

        # Ignore les cours déjà commencés ou terminés.
        if start < now:
            continue

        status = str(getattr(lesson, "status", "") or "").strip()
        normalized_status = normalize_text(status)

        # Ignore les cours annulés.
        canceled = bool(getattr(lesson, "canceled", False))
        if canceled or "annul" in normalized_status:
            continue

        subject_obj = getattr(lesson, "subject", None)
        subject = getattr(subject_obj, "name", None) if subject_obj else None

        teacher = getattr(lesson, "teacher_name", None) or "Non précisé"
        classroom = getattr(lesson, "classroom", None) or "Non précisée"

        upcoming.append({
            "subject": str(subject or "Cours"),
            "start": start,
            "end": end,
            "teacher": str(teacher),
            "classroom": str(classroom),
            "status": status,
        })

    upcoming.sort(key=lambda lesson: lesson["start"])

    return upcoming[0] if upcoming else None


# =========================
# BOT DISCORD ET EMBEDS
# =========================

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def make_grade_embed(grade):
    embed = discord.Embed(
        title="Nouvelle note !",
        description=f"**{grade['subject']}**",
        color=discord.Color.green(),
    )
    embed.add_field(name="Note", value=f"{grade['grade']}/{grade['out_of']}", inline=True)
    embed.add_field(name="Coefficient", value=grade["coefficient"] or "Non précisé", inline=True)
    embed.add_field(name="Date", value=grade["date"], inline=True)
    embed.add_field(name="Période", value=grade["period"], inline=True)
    if grade["comment"]:
        embed.add_field(name="Commentaire", value=str(grade["comment"])[:1024], inline=False)
    embed.set_footer(text="PronoteDiscord")
    return embed


def make_homework_embed(hw):
    embed = discord.Embed(
        title="Nouveau devoir !",
        description=f"**{hw['subject']}**",
        color=discord.Color.blue(),
    )
    embed.add_field(name="À faire pour le", value=hw["date"], inline=True)
    embed.add_field(
        name="Description",
        value=str(hw["description"])[:1024] or "Aucune description",
        inline=False,
    )
    embed.set_footer(text="PronoteDiscord")
    return embed


def format_lesson_time(value):
    try:
        return datetime.datetime.fromisoformat(value).strftime("%d/%m/%Y à %H:%M")
    except (TypeError, ValueError):
        return value or "Non précisé"


def make_timetable_embed(lesson, old=None):
    status = normalize_text(lesson.get("status", ""))
    if "annul" in status:
        title, color = "❌ Cours annulé", discord.Color.red()
    elif "salle" in status:
        title, color = "🚪 Changement de salle", discord.Color.orange()
    else:
        title, color = "📌 Information exceptionnelle", discord.Color.blurple()

    embed = discord.Embed(
        title=title,
        description=f"**{lesson['subject']}**",
        color=color,
    )
    embed.add_field(
        name="Horaire",
        value=f"{format_lesson_time(lesson['start'])}\n→ {format_lesson_time(lesson['end'])}",
        inline=False,
    )
    embed.add_field(name="Salle", value=lesson["classroom"], inline=True)
    embed.add_field(name="Professeur", value=lesson["teacher"], inline=True)
    embed.add_field(
        name="Statut Pronote",
        value=lesson.get("status") or "Non précisé",
        inline=False,
    )

    if old:
        labels = {
            "start": "Début",
            "end": "Fin",
            "teacher": "Professeur",
            "classroom": "Salle",
            "status": "Statut",
        }
        changes = []
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
                inline=False,
            )

    embed.set_footer(text="PronoteDiscord • Surveillance EDT")
    return embed


def make_next_lesson_embed(lesson):
    start = lesson["start"]
    end = lesson["end"]

    embed = discord.Embed(
        title="📚 Ton prochain cours",
        description=f"**{lesson['subject']}**",
        color=discord.Color.blurple(),
    )

    if end:
        horaire = (
            f"{start.strftime('%d/%m/%Y à %Hh%M')}\n"
            f"→ {end.strftime('%Hh%M')}"
        )
    else:
        horaire = start.strftime("%d/%m/%Y à %Hh%M")

    embed.add_field(name="🕒 Horaire", value=horaire, inline=False)
    embed.add_field(name="🚪 Salle", value=lesson["classroom"], inline=True)
    embed.add_field(name="👨‍🏫 Professeur", value=lesson["teacher"], inline=True)

    remaining = start - datetime.datetime.now(ZoneInfo("Europe/Paris"))
    minutes = max(0, int(remaining.total_seconds() // 60))

    if minutes < 60:
        countdown = f"Dans {minutes} minute(s)"
    else:
        hours, mins = divmod(minutes, 60)
        countdown = f"Dans {hours}h{mins:02d}"

    embed.add_field(name="⏳ Début du cours", value=countdown, inline=False)
    embed.set_footer(text="PronoteDiscord")

    return embed

# =========================
# SCAN NOTES ET DEVOIRS
# =========================

async def check_pronote(scan_type="all"):
    """Scanne les notes, les devoirs, ou les deux (mode automatique)."""
    global pronote_connected, last_scan_at
    global last_grades_count, last_homework_count

    if scan_type not in ("all", "notes", "devoirs"):
        raise ValueError("Type de scan Pronote invalide.")

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
            "grades": {x["id"]: x for x in data["grades"]},
            "homework": {x["id"]: x for x in data["homework"]},
        }
        previous = load_json(STATE_FILE)
        first_sync = previous is None
        if previous is None:
            previous = {"grades": {}, "homework": {}}

        if first_sync and scan_type == "all":
            await channel.send(
                "📚 **Première synchronisation Pronote !**\\n"
                "Voici toutes les notes et tous les devoirs récupérés."
            )

        if scan_type == "all":
            sections = ("grades", "homework")
        elif scan_type == "notes":
            sections = ("grades",)
        else:
            sections = ("homework",)

        for section in sections:
            old_items = previous.get(section, {})
            for item_id, item in current[section].items():
                if item_id not in old_items:
                    if section == "grades":
                        await channel.send(embed=make_grade_embed(item))
                    else:
                        await channel.send(embed=make_homework_embed(item))
            previous[section] = current[section]

        save_json(STATE_FILE, previous)
        print(
            f"[{datetime.datetime.now():%H:%M:%S}] Scan {scan_type} terminé : "
            f"{last_grades_count} notes, {last_homework_count} devoirs."
        )


# =========================
# SCAN EMPLOI DU TEMPS
# =========================

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

        # Première synchronisation EDT : enregistre sans notifier.
        if previous is None:
            save_json(TIMETABLE_FILE, {"lessons": current})
            print(
                "Première synchronisation EDT : "
                f"{len(current)} événement(s) enregistré(s)."
            )
            return

        old_lessons_raw = previous.get("lessons", {})

        # Convertit les anciens enregistrements utilisant les IDs Pronote
        # vers les nouveaux identifiants stables.
        old_lessons = {}
        for old in old_lessons_raw.values():
            stable_id = timetable_lesson_id(
                old.get("subject", "Cours"),
                old.get("start", ""),
                old.get("end", ""),
            )
            old_lessons[stable_id] = old

        now = datetime.datetime.now()

        def is_upcoming(lesson):
            try:
                start = datetime.datetime.fromisoformat(lesson["start"])
                reference = datetime.datetime.now(start.tzinfo) if start.tzinfo else now
                return start >= reference
            except (KeyError, TypeError, ValueError):
                return True

        for lesson_id, lesson in current.items():
            old = old_lessons.get(lesson_id)

            if old is None:
                if is_upcoming(lesson):
                    await channel.send(embed=make_timetable_embed(lesson))
                continue

            if old != lesson and is_upcoming(lesson):
                await channel.send(embed=make_timetable_embed(lesson, old))

        # Sauvegarde les données courantes pour éviter les notifications répétées.
        save_json(TIMETABLE_FILE, {"lessons": current})
        print(
            f"[{datetime.datetime.now():%H:%M:%S}] Scan EDT terminé : "
            f"{last_timetable_count} événement(s) ciblé(s)."
        )


# =========================
# COMMANDES ET STATUT
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
def format_datetime_fr(timestamp):
    if timestamp is None:
        return "Jamais"

    paris_tz = ZoneInfo("Europe/Paris")

    # Si la date n'a pas de fuseau, on considère qu'elle est en UTC.
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.timezone.utc)

    dt = timestamp.astimezone(paris_tz)

    mois = [
        "janvier", "février", "mars", "avril",
        "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"
    ]

    return (
        f"{dt.day} {mois[dt.month - 1]} {dt.year} "
        f"à {dt.hour:02d}h{dt.minute:02d}"
    )

@bot.tree.command(name="status", description="Teste la connexion Pronote et affiche le statut du bot.")
async def status(interaction: discord.Interaction):
    # Le test effectue une vraie connexion authentifiée à Pronote.
    await interaction.response.defer(thinking=True)

    started_at = time.perf_counter()
    try:
        async with pronote_access_lock:
            await asyncio.to_thread(get_client)

        latency_ms = (time.perf_counter() - started_at) * 1000
        connection = f"🟢 Accessible • {latency_ms:.0f} ms"
        color = discord.Color.green()
        test_result = f"✅ Connexion réussie en **{latency_ms:.0f} ms**."

        global pronote_connected
        pronote_connected = True

    except Exception as error:
        connection = "🔴 Inaccessible"
        color = discord.Color.red()
        test_result = f"❌ Échec du test (`{type(error).__name__}`)."
        pronote_connected = False

    embed = discord.Embed(
        title="📊 Statut de Pronote Bot",
        description=f"Connexion Pronote : {connection}",
        color=color,
    )

    embed.add_field(
        name="Test de connexion Pronote",
        value=test_result,
        inline=False,
    )

    embed.add_field(
        name="Notes et devoirs",
        value=(
            f"Dernier scan : {format_datetime_fr(last_scan_at)}\n"
            f"Prochain scan : {format_datetime_fr(pronote_loop.next_iteration)}\n"
            f"Notes : {last_grades_count}\n"
            f"Devoirs : {last_homework_count}"
        ),
        inline=False,
    )

    embed.add_field(
        name="Emploi du temps",
        value=(
            f"Dernier scan : {format_datetime_fr(last_timetable_scan_at)}\n"
            f"Prochain scan : {format_datetime_fr(timetable_loop.next_iteration)}\n"
            f"Événements ciblés : {last_timetable_count}\n"
            f"Période : {TIMETABLE_DAYS} jours"
        ),
        inline=False,
    )

    embed.set_footer(text="PronoteDiscord")
    await interaction.followup.send(embed=embed)


# Commande groupée : /scan devoirs, /scan notes ou /scan edt
scan_group = discord.app_commands.Group(
    name="scan",
    description="Lance un scan Pronote ciblé.",
)


@bot.tree.command(
    name="menu",
    description="Affiche le menu de la cantine du jour."
)
async def menu_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        async with pronote_access_lock:
            menu = await asyncio.to_thread(fetch_menu)

        if menu is None:
            await interaction.followup.send(
                "📭 Aucun menu disponible sur Pronote aujourd'hui."
            )
            return

        embed = make_menu_embed(menu)
        await interaction.followup.send(embed=embed)

    except Exception as error:
        await interaction.followup.send(
            f"❌ Impossible de récupérer le menu : "
            f"`{type(error).__name__}: {str(error)[:1000]}`"
        )

@scan_group.command(name="devoirs", description="Vérifie immédiatement les devoirs.")
async def scan_devoirs(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await check_pronote("devoirs")
        await interaction.followup.send("✅ Scan des devoirs terminé.", ephemeral=True)
    except Exception as error:
        await interaction.followup.send(
            f"❌ Échec du scan des devoirs : `{str(error)[:1500]}`",
            ephemeral=True,
        )


@scan_group.command(name="notes", description="Vérifie immédiatement les notes.")
async def scan_notes(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await check_pronote("notes")
        await interaction.followup.send("✅ Scan des notes terminé.", ephemeral=True)
    except Exception as error:
        await interaction.followup.send(
            f"❌ Échec du scan des notes : `{str(error)[:1500]}`",
            ephemeral=True,
        )


@scan_group.command(name="edt", description="Vérifie les changements importants de l'emploi du temps.")
async def scan_edt(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await check_timetable()
        await interaction.followup.send(
            "✅ Scan EDT terminé. Seuls les statuts ciblés sont surveillés.",
            ephemeral=True,
        )
    except Exception as error:
        await interaction.followup.send(
            f"❌ Échec du scan EDT : `{str(error)[:1500]}`",
            ephemeral=True,
        )


@bot.tree.command(
    name="prochaincours",
    description="Affiche ton prochain cours non annulé."
)
async def prochaincours(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        async with pronote_access_lock:
            lesson = await asyncio.to_thread(fetch_next_lesson)

        if lesson is None:
            await interaction.followup.send(
                f"📭 Aucun cours à venir trouvé dans les "
                f"{TIMETABLE_DAYS} prochains jours."
            )
            return

        embed = make_next_lesson_embed(lesson)
        await interaction.followup.send(embed=embed)

    except Exception as error:
        await interaction.followup.send(
            f"❌ Impossible de récupérer le prochain cours : "
            f"`{type(error).__name__}: {str(error)[:1000]}`"
        )


@bot.tree.command(
    name="moyenne",
    description="Affiche la moyenne Pronote générale ou par matière."
)
@discord.app_commands.describe(
    matiere="Choisis une matière ou toutes les matières."
)
async def moyenne(
    interaction: discord.Interaction,
    matiere: str
):
    await interaction.response.defer(thinking=True)

    try:
        async with pronote_access_lock:
            data = await asyncio.to_thread(fetch_data)

        period = get_current_period(data)
        if period is None:
            await interaction.followup.send(
                "📭 Pronote ne fournit aucune moyenne pour le moment."
            )
            return

        if matiere == "Toutes":
            average = to_number(period.get("overall_average"))
            display_name = "Moyenne générale"
        else:
            average = find_subject_average(period, matiere)
            display_name = matiere

        if average is None:
            await interaction.followup.send(
                f"📭 Aucune moyenne Pronote disponible pour **{display_name}** "
                f"dans la période **{period.get('name', 'actuelle')}**."
            )
            return

        if average >= 16:
            color = discord.Color.green()
        elif average >= 10:
            color = discord.Color.gold()
        else:
            color = discord.Color.red()

        embed = discord.Embed(
            title="📊 Moyenne Pronote",
            description=f"**{display_name}**",
            color=color
        )
        embed.add_field(
            name="Moyenne",
            value=f"**{average:.2f}/20**",
            inline=True
        )
        embed.add_field(
            name="Période",
            value=period.get("name") or "Période actuelle",
            inline=True
        )
        embed.set_footer(
            text="PronoteDiscord • Moyenne fournie par Pronote"
        )

        await interaction.followup.send(embed=embed)

    except Exception as error:
        await interaction.followup.send(
            f"❌ Impossible de récupérer la moyenne : "
            f"`{type(error).__name__}: {str(error)[:1000]}`"
        )


moyenne.autocomplete("matiere")(moyenne_autocomplete)


@bot.tree.command(
    name="devoirs",
    description="Affiche les devoirs à venir pour les 7 prochains jours."
)
async def devoirs(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        async with pronote_access_lock:
            data = await asyncio.to_thread(fetch_data)

        today = datetime.date.today()
        limit_date = today + datetime.timedelta(days=7)
        upcoming = []

        for hw in data.get("homework", []):
            try:
                due_date = datetime.date.fromisoformat(str(hw.get("date"))[:10])
            except (TypeError, ValueError):
                continue

            if today <= due_date <= limit_date:
                upcoming.append((due_date, hw))

        upcoming.sort(key=lambda item: (item[0], item[1].get("subject", "")))

        if not upcoming:
            await interaction.followup.send(
                "🎉 Aucun devoir prévu dans les 7 prochains jours !"
            )
            return

        embed = discord.Embed(
            title="📚 Devoirs à venir",
            description=(
                f"Du **{today.strftime('%d/%m')}** "
                f"au **{limit_date.strftime('%d/%m')}**"
            ),
            color=discord.Color.blue()
        )

        for due_date, hw in upcoming[:15]:
            is_done = bool(hw.get("done"))
            status = "✅ Fait" if is_done else "📝 À faire"
            description = str(hw.get("description") or "Aucune description").strip()
            if len(description) > 900:
                description = description[:897] + "..."

            embed.add_field(
                name=(
                    f"{'✅' if is_done else '📌'} "
                    f"{hw.get('subject') or 'Matière inconnue'} "
                    f"— {due_date.strftime('%d/%m')}"
                )[:256],
                value=f"{status}\n{description}",
                inline=False
            )

        embed.set_footer(
            text=(
                f"15 devoirs affichés sur {len(upcoming)}"
                if len(upcoming) > 15
                else f"{len(upcoming)} devoir(s) • Source : Pronote"
            )
        )
        await interaction.followup.send(embed=embed)

    except Exception as error:
        await interaction.followup.send(
            f"❌ Impossible de récupérer les devoirs : "
            f"`{type(error).__name__}: {str(error)[:1000]}`"
        )


bot.tree.add_command(scan_group)


# =========================
# BOUCLES AUTOMATIQUES
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


if __name__ == "__main__":
    if not TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "Configure DISCORD_TOKEN et DISCORD_CHANNEL_ID dans .env"
        )
    bot.run(TOKEN)
