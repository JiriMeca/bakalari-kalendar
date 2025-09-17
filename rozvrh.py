# ====== INSTRUKCE ======

#zde přidej svou doménu která vede na login page
LOGIN_URL = "https://bakalari.example.cz/bakaweb/login" 

#zde přidej url která vede na stránku "rozvrh školy" s url pro "tento týden"
URL_THIS_WEEK = f"https://bakalari.example.cz/bakaweb/Timetable/Public/Actual/Class/3F"

#zde přidej url která vede na stránku "rozvrh školy" s url pro "příští týden"
URL_NEXT_WEEK = f"https://bakalari.example.cz/bakaweb/Timetable/Public/Next/Class/3F"

#do tohoto listu vlož jakékoliv hodiny které se ti zobrazují v rozvrhu přestože je nemáš
FILTER_WORDS = ["Molekulární biologie", "Přírodovědná cvičení", "Seminář z hudební výchovy", "Konverzace v německém jazyce", "Seminář z matematiky"]

#do tohoto listu vlož všechny skupiny ve kterých nejsi - když máš třeba půlenou hodinu....
FILTER_GROUPS = ["JAZ1","SPJ2"]

#zde vlož id kalendáře podle návodu
CALENDAR_ID = "EXAMPLEa3f1755ba4631550a61c0f004cb906297e34@group.calendar.google.com"
#---------------------------------------------------------------------------------------------
import datetime
import time
import requests
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import os.path
import json
import re
import os

SCOPES = ["https://www.googleapis.com/auth/"]
BAKALARI_USER = os.environ["BAKALARI_USER"]
BAKALARI_PASS = os.environ["BAKALARI_PASS"]
EVENT_TAG_KEY = "tag"
EVENT_TAG_VALUE = "rozvrh"
DELETE_SLEEP = 0.25
DELETE_WORDS = ["None", "ODPADLÁ HODINA", "VÝUKA ZRUŠENA"]

# ====== Google autorizace ======
def google_auth():
    creds = None
    if os.path.exists("token.pkl"):
        with open("token.pkl", "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # nahraď názvem svého souboru client secret
            flow = InstalledAppFlow.from_client_secrets_file("client_secret_xxx.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.pkl", "wb") as token:
            pickle.dump(creds, token)
    return creds

# ====== Login na Bakaláře ======
def login_bakalari():
    session = requests.Session()
    r = session.get(LOGIN_URL)
    soup = BeautifulSoup(r.text, "html.parser")
    token_tag = soup.find("input", {"name": "__RequestVerificationToken"})
    token = token_tag["value"] if token_tag else ""
    payload = {
        "Username": BAKALARI_USER,
        "Password": BAKALARI_PASS,
        "__RequestVerificationToken": token
    }
    headers = {"Referer": LOGIN_URL}
    r = session.post(LOGIN_URL, data=payload, headers=headers)
    if "Rozvrh" not in r.text and "Timetable" not in r.text:
        raise Exception("❌ Login na Bakaláře selhal. Zkontroluj login/heslo.")
    print("✅ Přihlášení na Bakaláře OK")
    return session

# ====== Parsování data a času ======
def parse_date_time(date_str, time_str):
    date_match = re.search(r'(\d{1,2})\D+(\d{1,2})(?:\D+(\d{2,4}))?', date_str)
    if not date_match:
        print(f"❌ Neznámý formát data: {date_str}")
        return None, None
    day, month, year = date_match.groups()
    if not year:
        year = datetime.datetime.today().year
    date_iso = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    times = re.findall(r'(\d{1,2}:\d{2})', time_str)
    if len(times) != 2:
        print(f"❌ Neznámý formát času: {time_str}")
        return None, None

    # Doplníme nulu před jednobodové hodiny
    start_hour, start_minute = map(int, times[0].split(":"))
    end_hour, end_minute = map(int, times[1].split(":"))
    start_iso = f"{date_iso}T{start_hour:02d}:{start_minute:02d}:00+02:00"
    end_iso   = f"{date_iso}T{end_hour:02d}:{end_minute:02d}:00+02:00"

    return start_iso, end_iso

# ====== Normální UTC datetime pro přesné porovnání ======
def to_utc_dt(iso_str):
    if not iso_str:
        return None
    s = iso_str.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        # předpokládejme +02:00 pokud neexistuje (tak jak se vytváří v parse_date_time)
        dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    return dt.astimezone(datetime.timezone.utc).replace(microsecond=0)

# ====== Parsování rozvrhu ======
def fetch_timetable(session, url):
    r = session.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    lessons = []
    for div in soup.find_all("div", class_="day-item-hover"):
        detail = div.get("data-detail")
        if not detail:
            continue
        clean = detail.replace("null", '"None"')
        try:
            data = json.loads(clean)
        except:
            continue

        teacher = data.get("teacher", "")
        room = data.get("room", "")
        group = data.get("group", "")

        # Priorita: InfoAbsentName (třídnické hodiny atd.)
        if "InfoAbsentName" in data and data["InfoAbsentName"]:
            subject_name = data["InfoAbsentName"]
            date_time_part = data.get("subjecttext", "")
        else:
            subject_text = data.get("subjecttext", "")
            if "|" not in subject_text:
                continue
            spl = subject_text.split(" |")
            subject_name = spl[0].strip()
            date_time_part = " |".join(spl[1:])

        # Extrahování data a času
        date_match = re.search(r'(\d{1,2}\.\d{1,2}\.)', date_time_part)
        time_match = re.search(r'(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})', date_time_part)
        if not date_match or not time_match:
            continue
        date = date_match.group(1)
        time = time_match.group(1)

        start_iso, end_iso = parse_date_time(date, time)
        if not start_iso or not end_iso:
            continue
        lessons.append({
            "subject": subject_name,
            "teacher": teacher,
            "room": room,
            "group": group,
            "start": start_iso,
            "end": end_iso
        })
    return lessons

# ====== Filtrování ======
def filter_lessons(lessons, filters):
    result = []
    for l in lessons:
        if any(word.lower() in l["subject"].lower() for word in filters):
            print(f"⏭ Přeskočeno: {l['subject']} ({l['start']})")
            continue
        result.append(l)
    return result

def filter_groups(lessons, filter_groups):
    result = []
    for l in lessons:
        g = l.get("group","") or ""
        if any(fg.lower() in g.lower() for fg in filter_groups):
            print(f"⏭ Přeskočeno (skupina): {l['subject']} [{g}] ({l['start']})")
            continue
        result.append(l)
    return result

# ====== Přidání / aktualizace eventů s tagem ======
def add_or_update_events(lessons, creds):
    service = build("", "v3", credentials=creds)

    for l in lessons:
        if not l["subject"] or not l["start"] or not l["end"]:
            print(f"❌ Přeskočeno prázdné pole: {l}")
            continue

        location = f"Učebna {l['room']}" if l["room"] else ""
        description = f"Učitel: {l.get('teacher','')}"

        try:
            events_result = service.events().list(
                calendarId=CALENDAR_ID,
                timeMin=l["start"],
                timeMax=l["end"],
                q=l["subject"],
                singleEvents=True,
                orderBy="startTime"
            ).execute()
        except Exception as e:
            print(f"⚠️ Chyba při načítání existujících událostí: {e}")
            events_result = {"items": []}

        events = events_result.get("items", [])

        # Najdeme událost se správným tagem (pokud existuje)
        tagged_event = None
        exact_untagged_event = None
        lesson_start_utc = to_utc_dt(l["start"])
        lesson_end_utc = to_utc_dt(l["end"])

        for ev in events:
            ext = ev.get("extendedProperties", {}).get("private", {})
            tag = ext.get(EVENT_TAG_KEY)
            ev_start = to_utc_dt(ev["start"].get("dateTime") or ev["start"].get("date"))
            ev_end = to_utc_dt(ev["end"].get("dateTime") or ev["end"].get("date"))
            if tag == EVENT_TAG_VALUE:
                tagged_event = ev
                break
            if ev_start == lesson_start_utc and ev_end == lesson_end_utc and (ev.get("summary","").strip().lower() == l["subject"].strip().lower()):
                exact_untagged_event = ev

        chosen_event = tagged_event or exact_untagged_event

        if chosen_event:
            if chosen_event.get("extendedProperties", {}).get("private", {}).get(EVENT_TAG_KEY) != EVENT_TAG_VALUE:
                chosen_event.setdefault("extendedProperties", {}).setdefault("private", {})[EVENT_TAG_KEY] = EVENT_TAG_VALUE

            changes_made = False
            if chosen_event.get("location", "") != location:
                chosen_event["location"] = location
                changes_made = True
            if chosen_event.get("description", "") != description:
                chosen_event["description"] = description
                changes_made = True

            ev_start = to_utc_dt(chosen_event["start"].get("dateTime") or chosen_event["start"].get("date"))
            ev_end = to_utc_dt(chosen_event["end"].get("dateTime") or chosen_event["end"].get("date"))
            if ev_start != lesson_start_utc or ev_end != lesson_end_utc:
                chosen_event["start"] = {"dateTime": l["start"], "timeZone": "Europe/Prague"}
                chosen_event["end"] = {"dateTime": l["end"], "timeZone": "Europe/Prague"}
                changes_made = True

            if changes_made:
                try:
                    service.events().update(calendarId=CALENDAR_ID, eventId=chosen_event["id"], body=chosen_event).execute()
                    print(f"🔄 Aktualizováno / označeno: {l['subject']} ({location}) {l['start']}")
                except Exception as e:
                    print(f"⚠️ Chyba při aktualizaci: {l['subject']}: {e}")
        else:
            event = {
                "summary": l["subject"],
                "location": location,
                "description": description,
                "start": {"dateTime": l["start"], "timeZone": "Europe/Prague"},
                "end": {"dateTime": l["end"], "timeZone": "Europe/Prague"},
                "extendedProperties": {"private": {EVENT_TAG_KEY: EVENT_TAG_VALUE}},
                "reminders": {"useDefault": False}
            }
            try:
                service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
                print(f"✅ Přidáno: {l['subject']} ({location}) {l['start']}")
            except Exception as e:
                print(f"❌ Nepodařilo se přidat: {l['subject']}, chyba: {e}")
                print(f"Raw data: {l}")

# ====== Odstranění zrušených lekcí (pouze tagované eventy) ======
def remove_cancelled_lessons(lessons, creds):
    service = build("calendar", "v3", credentials=creds)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    two_weeks_iso = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=14)).isoformat()

    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=now_iso,
            timeMax=two_weeks_iso,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
    except Exception as e:
        print(f"⚠️ Chyba při načítání událostí z kalendáře: {e}")
        return

    events = events_result.get("items", [])

    # mapujeme lekce podle start/end -> subject
    lessons_by_slot = {}
    for l in lessons:
        start = to_utc_dt(l["start"])
        end = to_utc_dt(l["end"])
        if start and end:
            lessons_by_slot[(start, end)] = l["subject"].strip().lower()

    for event in events:
        ext = event.get("extendedProperties", {}).get("private", {})
        if ext.get(EVENT_TAG_KEY) != EVENT_TAG_VALUE:
            # smažeme pouze tagované události
            continue

        summary = (event.get("summary") or "").strip().lower()
        ev_start = to_utc_dt(event["start"].get("dateTime") or event["start"].get("date"))
        ev_end = to_utc_dt(event["end"].get("dateTime") or event["end"].get("date"))

        if ev_start is None or ev_end is None:
            continue

        slot_key = (ev_start, ev_end)
        lesson_subject = lessons_by_slot.get(slot_key)

        # Podmínky ke smazání:
        # 1) pokud v rozvrhu NENÍ nic ve stejném přesném slotu -> smazat
        # 2) nebo pokud v rozvrhu JE matching slot ale jeho předmět obsahuje některé položky z DELETE_WORDS -> smazat
        should_delete = False

        if lesson_subject is None:
            should_delete = True
            reason = "slot prázdný v rozvrhu"
        else:
            # zkontrolujeme, zda lesson_subject obsahuje některé z delete slov
            if any(word.lower() in lesson_subject for word in DELETE_WORDS):
                should_delete = True
                reason = f"slot obsahuje delete slovo ({lesson_subject})"
            else:
                should_delete = False

        if should_delete:
            try:
                service.events().delete(calendarId=CALENDAR_ID, eventId=event["id"]).execute()
                print(f"🗑 Smazáno (tag={EVENT_TAG_VALUE}, {reason}): {event.get('summary','?')} ({ev_start.isoformat()})")
                time.sleep(DELETE_SLEEP)
            except Exception as e:
                print(f"⚠️ Nepodařilo se smazat {event.get('summary','?')}: {e}")
                time.sleep(1)

# ====== Main ======
if __name__ == "__main__":
    creds = google_auth()
    session = login_bakalari()

    print("\n--- Tento týden ---")
    this_week = fetch_timetable(session, URL_THIS_WEEK)
    this_week = filter_lessons(this_week, FILTER_WORDS)
    this_week = filter_groups(this_week, FILTER_GROUPS)

    print("\n--- Příští týden ---")
    next_week = fetch_timetable(session, URL_NEXT_WEEK)
    next_week = filter_lessons(next_week, FILTER_WORDS)
    next_week = filter_groups(next_week, FILTER_GROUPS)

    # 1) přidáme/aktualizujeme všechny události (označené tagem)
    add_or_update_events(this_week + next_week, creds)

    # 2) pak odstraníme přesně ty eventy S TAGEM, které už v rozvrhu nejsou nebo mají delete slovo
    remove_cancelled_lessons(this_week + next_week, creds)

    print("Hotovo.")
