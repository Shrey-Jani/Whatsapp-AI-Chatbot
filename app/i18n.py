"""Multilingual layer - detect the user's language + script once, then speak it back.

Supported languages: English and Punjabi only (Hindi is NOT supported - a Hindi or any other
message is served in English). English passes through untouched (no LLM call, no drift, no cost).
Punjabi prompts are translated and cached: the question set is fixed, so each prompt is translated
once ever.

Mandated verbatim legal text (slip safeguard, GST warning, no-refund policy) is NEVER sent
through here - those stay in English until a human translator signs off the Punjabi.
"""
from functools import lru_cache

from . import llm

DEFAULT = "English"
VALID = {"English", "Punjabi/Latin", "Punjabi/Gurmukhi"}

_SCRIPT_RULE = ("Write using ordinary English/Latin letters (romanised) - do NOT use "
                "Devanagari or Gurmukhi characters.")


def _parts(lang: str) -> tuple[str, str]:
    language, _, script = lang.partition("/")
    return language, (script or "Latin")


def detect(text: str) -> str:
    """English only (client decision) - the bot no longer detects or replies in other languages."""
    return DEFAULT
    try:                                          # ponytail: kept, unreachable - flip back if multilingual returns
        out = llm.complete(
            "Identify the language and script of the message below.\n"
            "Answer with EXACTLY one of these labels and nothing else:\n"
            "English | Punjabi/Latin | Punjabi/Gurmukhi\n\n"
            "Rules: Punjabi typed with English letters -> Punjabi/Latin; Punjabi in Gurmukhi script "
            "-> Punjabi/Gurmukhi. Punjabi markers: paji, tuhada, ki haal, vadhiya, sat sri akal, "
            "kiddan. If the message is English, Hindi, or ANY other language, pick English.\n\n"
            f'Message: "{text}"').strip().splitlines()[0].strip().strip(".")
        return out if out in VALID else DEFAULT
    except Exception as e:
        print(f"[i18n] detect failed: {e}")
        return DEFAULT


@lru_cache(maxsize=2048)
def localize(text: str, lang: str) -> str:
    """Translate a bot message into the user's language+script. Cached - fixed prompt set."""
    if lang == DEFAULT or not llm.configured() or not (text or "").strip():
        return text
    language, script = _parts(lang)
    try:
        return llm.complete(
            f"Translate the message below into {language}.\n"
            f"{_SCRIPT_RULE if script == 'Latin' else f'Write in {script} script.'}\n"
            f"Use natural, everyday {language} - not Hindi if the target is Punjabi.\n"
            "Keep every number, amount, date format, URL and numbered list item EXACTLY as-is.\n"
            "Output ONLY the translation, no preamble.\n\n"
            f"{text}").strip()
    except Exception as e:
        print(f"[i18n] localize failed: {e}")
        return text


GREETING = "Hello!"


def greet_and_ask(user_text: str, question: str, lang: str) -> str:
    """First turn: greet them, then ask the first question - in their language.

    English uses the canned greeting (no LLM call, no drift). Other languages get a natural
    acknowledgement of whatever they actually said.
    """
    if lang == DEFAULT or not llm.configured():
        return f"{GREETING}\n\n{question}"
    language, script = _parts(lang)
    try:
        return llm.complete(
            f'A user opened a chat by saying: "{user_text}"\n\n'
            f"Reply in {language}. {_SCRIPT_RULE if script == 'Latin' else f'Write in {script} script.'}\n"
            f"Use natural, everyday {language} - if the target is Punjabi use Punjabi words "
            "(e.g. 'vadhiya', 'tuhada', 'sat sri akal'), NEVER Hindi words like 'kya', 'matlab', "
            "'aap'.\n"
            "Start with a SHORT warm greeting (e.g. 'Sat sri akal ji!') - do NOT repeat or quote "
            "the user's message back to them. Then immediately ask the question below, translated. "
            "Keep any numbered options exactly as numbered.\n"
            "Output ONLY the reply.\n\n"
            f"Question: {question}").strip()
    except Exception as e:
        print(f"[i18n] greet failed: {e}")
        return question


def map_choice(user_text: str, options: tuple, lang: str) -> str | None:
    """Map a non-English menu answer onto one of the English options, or None."""
    if lang == DEFAULT or not llm.configured():
        return None
    try:
        out = llm.complete(
            "Which option does the user's answer correspond to? Reply with the option text "
            "EXACTLY as written below, or the word NONE.\n\n"
            f'Options: {" | ".join(options)}\n'
            f'User answer: "{user_text}"').strip()
        return out if out in options else None
    except Exception as e:
        print(f"[i18n] map_choice failed: {e}")
        return None
