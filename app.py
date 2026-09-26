
import streamlit as st
import streamlit.components.v1 as components
from groq import Groq
import httpx
import json
import os
import io
import datetime

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="EnglishSaathi", page_icon="🗣️", layout="centered")
 
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", os.environ.get("GROQ_API_KEY", ""))
 
if not GROQ_API_KEY:
    st.error(
        "⚠️ GROQ_API_KEY not found.\n\n"
        "Add it in `.streamlit/secrets.toml` (local) or in your Streamlit Cloud "
        "app's **Settings → Secrets** as:\n\n`GROQ_API_KEY = \"your_key_here\"`"
    )
    st.stop()
 
http_client = httpx.Client(trust_env=False)
client = Groq(
    api_key=GROQ_API_KEY,
    http_client=http_client
)
CHAT_MODEL = "openai/gpt-oss-120b"
STT_MODEL = "whisper-large-v3"
 
# ─────────────────────────────────────────────────────────────
# LIGHT UI POLISH (custom look)
# ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
.es-banner {
    background: linear-gradient(90deg, #0e639c 0%, #2ea3a3 100%);
    padding: 18px 22px; border-radius: 14px; margin-bottom: 14px;
}
.es-banner h1 { color: white; margin: 0; font-size: 26px; }
.es-banner p { color: #eaf6f6; margin: 4px 0 0 0; font-size: 14px; }
.stChatMessage { border-radius: 16px; }
div[data-testid="stChatInput"] textarea { border-radius: 12px !important; }
</style>
<div class="es-banner">
  <h1>🗣️ EnglishSaathi</h1>
  <p>Your personal AI buddy for learning English speaking</p>
</div>
""", unsafe_allow_html=True)
 
# ─────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []          # list of {role, ...fields}
if "last_audio_id" not in st.session_state:
    st.session_state.last_audio_id = None
if "daily_word" not in st.session_state:
    st.session_state.daily_word = None       # {"date": "...", "data": {...}}
if "dict_result" not in st.session_state:
    st.session_state.dict_result = None
 
# ─────────────────────────────────────────────────────────────
# SIDEBAR — SETTINGS
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    level = st.selectbox("Your English level", ["Beginner", "Intermediate", "Advanced"], index=0)
    native_lang = st.selectbox(
        "Explain my mistakes in", ["Hindi", "Marathi", "Hinglish (Hindi+English mix)"], index=0
    )
    auto_speak = st.checkbox("🔊 Auto-read replies aloud", value=True)
    st.divider()
    if st.button("🗑️ Clear conversation"):
        st.session_state.messages = []
        st.session_state.last_audio_id = None
        st.rerun()
    st.caption(
        "Talk in Hindi, Marathi, or English (type or use the mic). "
        "I'll translate, correct your English, and reply like a friend "
        "so you can practice speaking."
    )
 
# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT — the "brain" that does translation + correction + teaching
# ─────────────────────────────────────────────────────────────
def build_system_prompt():
    return f"""You are "EnglishSaathi", a warm, patient personal English-speaking coach for a
Hindi/Marathi speaking student in India whose English level is: {level}.
 
The student will write to you in Hindi, Marathi, English, or a mix (Hinglish). Your job every
single turn is to help them practice REAL spoken English while feeling supported.
 
For every message the student sends, do ALL of the following:
 
1. If any part of their message is in Hindi or Marathi, translate that part into natural,
   everyday spoken English (not textbook English).
2. If any part of their message is an English attempt, check it for grammar, word choice,
   pronunciation-spelling, or unnatural phrasing mistakes.
   - If there ARE mistakes: give the corrected version, and explain WHY in simple
     {native_lang}, in 1-3 short lines (mention the grammar rule/word in an easy way).
   - If there are NO mistakes: briefly say so encouragingly.
   - If they wrote no English at all, has_correction is false.
3. Continue the conversation naturally like a friendly buddy chatting in English — respond to
   what they said, keep it appropriate for a {level} learner, and ask ONE simple follow-up
   question to keep them talking. Keep this reply short (1-3 sentences).
4. Give a short meaning of your own reply in {native_lang} so they always understand you.
 
Respond ONLY with a single valid JSON object (no markdown, no extra text) with EXACTLY these keys:
{{
  "translation": "<English translation of any Hindi/Marathi part, or empty string if none>",
  "has_correction": true or false,
  "original_attempt": "<the student's original English attempt, or empty string>",
  "corrected_english": "<corrected sentence, or empty string if nothing to correct>",
  "explanation": "<short explanation in {native_lang}, or empty string>",
  "reply_english": "<your natural, friendly English reply + one follow-up question>",
  "reply_meaning": "<short meaning of reply_english, in {native_lang}>"
}}
"""
 
def get_coach_response(user_text: str) -> dict:
    history = []
    for m in st.session_state.messages[-8:]:
        if m["role"] == "user":
            history.append({"role": "user", "content": m["text"]})
        else:
            history.append({"role": "assistant", "content": m["reply_english"]})
 
    messages = [{"role": "system", "content": build_system_prompt()}] + history
    messages.append({"role": "user", "content": user_text})
 
    completion = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    raw = completion.choices[0].message.content
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "translation": "", "has_correction": False, "original_attempt": "",
            "corrected_english": "", "explanation": "", "reply_english": raw, "reply_meaning": "",
        }
    return data
 
# ─────────────────────────────────────────────────────────────
# WORD / DICTIONARY ENGINE — shared JSON schema for both features
# ─────────────────────────────────────────────────────────────
WORD_SCHEMA = """Respond ONLY with a single valid JSON object (no markdown, no extra text) with
EXACTLY these keys:
{
  "word": "<the English word or phrase>",
  "part_of_speech": "<verb / noun / adjective / adverb / phrasal verb / idiom>",
  "meaning_english": "<simple one-line English meaning>",
  "meaning_hindi": "<meaning in Hindi>",
  "meaning_marathi": "<meaning in Marathi>",
  "is_verb": true or false,
  "verb_forms": {
    "base": "", "past": "", "past_participle": "", "ing_form": "", "third_person_singular": ""
  },
  "example_sentences": ["<simple example 1>", "<simple example 2>"],
  "synonyms": ["<synonym 1>", "<synonym 2>", "<synonym 3>"]
}
Leave verb_forms values as empty strings if is_verb is false."""
 
def get_daily_word() -> dict:
    today_str = datetime.date.today().isoformat()
    cached = st.session_state.daily_word
    if cached and cached.get("date") == today_str:
        return cached["data"]
 
    prompt = f"""Pick ONE useful English word or common phrasal verb that is genuinely helpful
for daily spoken conversation, suitable for a {level} Indian English learner. Prefer verbs and
phrasal verbs most days since they help speaking the most. Use today's date ({today_str}) as
a seed so the word is different from other days and not a trivially easy word like "go" or "is".
{WORD_SCHEMA}"""
    try:
        completion = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            response_format={"type": "json_object"},
        )
        data = json.loads(completion.choices[0].message.content)
    except Exception:
        data = None
 
    if data:
        st.session_state.daily_word = {"date": today_str, "data": data}
    return data
 
def lookup_word(word: str) -> dict:
    prompt = f"""Explain the English word/phrase "{word}" for a {level} Indian English learner.
{WORD_SCHEMA}"""
    completion = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    return json.loads(completion.choices[0].message.content)
 
def render_word_card(data: dict):
    pos_emoji = {
        "verb": "🏃", "noun": "🏷️", "adjective": "🎨", "adverb": "⚡",
        "phrasal verb": "🔗", "idiom": "💡",
    }.get(data.get("part_of_speech", "").lower(), "📝")
 
    with st.container(border=True):
        st.markdown(f"### {pos_emoji} {data.get('word', '')}  \n*{data.get('part_of_speech', '')}*")
        st.write(data.get("meaning_english", ""))
 
        col1, col2 = st.columns(2)
        with col1:
            st.caption("🇮🇳 Hindi")
            st.write(data.get("meaning_hindi", ""))
        with col2:
            st.caption("🇮🇳 Marathi")
            st.write(data.get("meaning_marathi", ""))
 
        if data.get("is_verb") and data.get("verb_forms"):
            st.caption("Verb forms")
            vf = data["verb_forms"]
            st.table({
                "Base": [vf.get("base", "")],
                "Past": [vf.get("past", "")],
                "Past participle": [vf.get("past_participle", "")],
                "-ing form": [vf.get("ing_form", "")],
                "He/She/It": [vf.get("third_person_singular", "")],
            })
 
        if data.get("example_sentences"):
            st.caption("Examples")
            for ex in data["example_sentences"]:
                st.markdown(f"- {ex}")
 
        if data.get("synonyms"):
            st.caption("Similar words: " + ", ".join(data["synonyms"]))
 
        speak_button(data.get("word", ""), key=f"word_{data.get('word','')}")
 
# ─────────────────────────────────────────────────────────────
# SPEECH-TO-TEXT (Groq Whisper)
# ─────────────────────────────────────────────────────────────
def transcribe_audio(audio_bytes: bytes) -> str:
    file_tuple = ("speech.wav", io.BytesIO(audio_bytes).read())
    transcript = client.audio.transcriptions.create(
        file=file_tuple, model=STT_MODEL, response_format="json",
    )
    return transcript.text.strip()
 
# ─────────────────────────────────────────────────────────────
# TEXT-TO-SPEECH — browser's own voice, free
# ─────────────────────────────────────────────────────────────
def speak_button(text: str, key: str):
    safe_text = json.dumps(text)
    html = f"""
    <button id="btn_{key}" style="
        background:#0e639c;color:white;border:none;border-radius:6px;
        padding:6px 12px;font-size:13px;cursor:pointer;margin-top:4px;">
        🔊 Listen
    </button>
    <script>
    const btn_{key} = document.getElementById("btn_{key}");
    btn_{key}.onclick = function() {{
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance({safe_text});
        utter.lang = "en-US"; utter.rate = 0.9;
        window.speechSynthesis.speak(utter);
    }};
    </script>
    """
    components.html(html, height=45)
 
def auto_speak_js(text: str, key: str):
    safe_text = json.dumps(text)
    html = f"""
    <script>
    const u_{key} = new SpeechSynthesisUtterance({safe_text});
    u_{key}.lang = "en-US"; u_{key}.rate = 0.9;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u_{key});
    </script>
    """
    components.html(html, height=0)
 
# ─────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────
chat_tab, word_tab, dict_tab = st.tabs(["💬 Chat Practice", "📅 Word of the Day", "📖 Dictionary"])
 
# ───────────────────── CHAT TAB ─────────────────────
with chat_tab:
    for i, m in enumerate(st.session_state.messages):
        if m["role"] == "user":
            with st.chat_message("user", avatar="🧑"):
                st.markdown(m["text"])
        else:
            with st.chat_message("assistant", avatar="🗣️"):
                if m.get("translation"):
                    st.markdown(f"**🌐 Translation:** {m['translation']}")
                if m.get("has_correction"):
                    st.markdown(f"**✏️ You said:** {m['original_attempt']}")
                    st.markdown(f"**✅ Correct way:** {m['corrected_english']}")
                    st.info(f"**Why:** {m['explanation']}")
                st.markdown(f"**💬 {m['reply_english']}**")
                if m.get("reply_meaning"):
                    st.caption(f"({m['reply_meaning']})")
                speak_button(m["reply_english"], key=f"msg_{i}")
 
    if auto_speak and st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
        if not st.session_state.get("_spoken_last", False):
            auto_speak_js(st.session_state.messages[-1]["reply_english"], key="latest")
            st.session_state["_spoken_last"] = True
 
    st.divider()
 
    try:
        from streamlit_mic_recorder import mic_recorder
        mic_available = True
    except ImportError:
        mic_available = False
 
    col1, col2 = st.columns([1, 4])
    transcribed_text = None
 
    with col1:
        if mic_available:
            audio = mic_recorder(
                start_prompt="🎤 Speak", stop_prompt="⏹️ Stop",
                just_once=True, use_container_width=True, key="recorder",
            )
            if audio and audio["id"] != st.session_state.last_audio_id:
                st.session_state.last_audio_id = audio["id"]
                with st.spinner("Listening..."):
                    transcribed_text = transcribe_audio(audio["bytes"])
        else:
            st.caption("Mic unavailable — install streamlit-mic-recorder")
 
    with col2:
        typed_text = st.chat_input("Type in Hindi, Marathi, or English...")
 
    user_input = transcribed_text or typed_text
 
    if user_input:
        st.session_state["_spoken_last"] = False
        st.session_state.messages.append({"role": "user", "text": user_input})
        with st.spinner("EnglishSaathi is thinking..."):
            result = get_coach_response(user_input)
        st.session_state.messages.append({
            "role": "assistant",
            "translation": result.get("translation", ""),
            "has_correction": result.get("has_correction", False),
            "original_attempt": result.get("original_attempt", ""),
            "corrected_english": result.get("corrected_english", ""),
            "explanation": result.get("explanation", ""),
            "reply_english": result.get("reply_english", ""),
            "reply_meaning": result.get("reply_meaning", ""),
        })
        st.rerun()
 
# ───────────────────── WORD OF THE DAY TAB ─────────────────────
with word_tab:
    st.subheader("📅 Today's word")
    st.caption("A new word or phrasal verb every day — learn it, then try using it in the Chat tab.")
    with st.spinner("Fetching today's word..."):
        daily_data = get_daily_word()
    if daily_data:
        render_word_card(daily_data)
    else:
        st.warning("Couldn't fetch today's word right now — try refreshing the page.")
    if st.button("🔄 Get a different word for today"):
        st.session_state.daily_word = None
        st.rerun()
 
# ───────────────────── DICTIONARY TAB ─────────────────────
with dict_tab:
    st.subheader("📖 Look up any English word")
    st.caption("Get its meaning in Hindi/Marathi, verb forms (if it's a verb), and example sentences.")
    query = st.text_input("Type a word or phrase", placeholder="e.g. procrastinate, look forward to")
    if st.button("🔍 Search", use_container_width=True) and query.strip():
        with st.spinner(f"Looking up '{query}'..."):
            st.session_state.dict_result = lookup_word(query.strip())
    if st.session_state.dict_result:
        render_word_card(st.session_state.dict_result)