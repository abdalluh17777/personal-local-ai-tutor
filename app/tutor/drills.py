"""Pronunciation drills and sound tips for the pronunciation check (American English, CMU phone symbols).

The drills follow the sounds that are known to be hard for Arabic speakers (Swan and Smith, "Learner English",
the chapter on Arabic speakers): /p/ and /b/, /f/ and /v/, the short and long vowels, /tʃ/ and /ʃ/, /θ/ and /ð/,
the ending of "-ing", vowels added inside consonant groups, and the -s and -ed endings taught in Stage 1.
"""

# How to make each sound, in simple English (shown when the learner misses that sound)
HOW = {
    "P": "Press your lips together, then open them with a strong puff of air. Your voice is off. "
         "Hold a paper in front of your mouth: it should move.",
    "B": "Press your lips together and open them with your voice on, without a strong puff of air.",
    "T": "Touch the ridge behind your top teeth with the tip of your tongue, then release it with a small puff of air.",
    "D": "Touch the ridge behind your top teeth with the tip of your tongue and release it with your voice on.",
    "K": "Press the back of your tongue against the roof of your mouth, then release it with a puff of air.",
    "G": "Press the back of your tongue against the roof of your mouth and release it with your voice on.",
    "F": "Touch your bottom lip with your top teeth and blow. Your voice is off.",
    "V": "Touch your bottom lip with your top teeth, as for /f/, and turn your voice on: you should feel a buzz.",
    "TH": "Put the tip of your tongue lightly between your teeth and blow, voice off. It is not /s/ and not /t/.",
    "DH": "Put the tip of your tongue lightly between your teeth with your voice on, as in \"this\". "
          "It is not /z/ and not /d/.",
    "S": "Keep the tip of your tongue close to the ridge behind your top teeth and hiss. Your voice is off.",
    "Z": "Make /s/ with your voice on: a buzzing sound.",
    "SH": "Round your lips a little and push air over your tongue without stopping it: a long \"shh\".",
    "ZH": "Make \"sh\" with your voice on, as in \"television\".",
    "CH": "Start with /t/ and let it go into \"sh\": t + sh, one quick sound, as in \"chair\".",
    "JH": "Start with /d/ and let it go into the voiced \"zh\": one quick sound, as in \"job\".",
    "HH": "A light breath before the vowel, like the h of Arabic هو (not ح or خ), as in \"house\".",
    "M": "Close your lips and hum through your nose.",
    "N": "Touch the ridge behind your top teeth with your tongue and hum through your nose.",
    "NG": "Lift the back of your tongue to the roof of your mouth and hum through your nose. In \"sing\" and \"long\", "
          "do not add a /g/ at the end.",
    "L": "Touch the ridge behind your top teeth with the tip of your tongue and let the air flow around its sides.",
    "R": "Curl the tip of your tongue up and back without touching anything. Do not tap or roll it.",
    "W": "Round your lips tightly, then open them into the vowel, as in \"we\".",
    "Y": "Start with your tongue high and forward, as at the start of \"yes\".",
    "IY": "A long, tense vowel: spread your lips a little and keep your tongue high and forward, as in \"sheep\".",
    "IH": "A short, relaxed vowel: your tongue a little lower than for \"sheep\" and your lips relaxed, as in \"ship\".",
    "EH": "Open your mouth a little more than for \"ship\", as in \"bed\".",
    "AE": "Open your mouth wide and spread your lips, with your tongue low and forward, as in \"cat\".",
    "AH": "A short, relaxed vowel with your mouth half open, as in \"cup\". When it is not stressed, it is a very "
          "short, weak \"uh\" (\"about\").",
    "AA": "Open your mouth wide with your tongue low and back, as in \"hot\" (American English).",
    "AO": "Round your lips a little, with your tongue low and back, as in \"law\".",
    "UH": "A short vowel with relaxed, slightly rounded lips, as in \"book\".",
    "UW": "A long vowel with tightly rounded lips, as in \"food\".",
    "ER": "Curl your tongue up and back without touching anything while you say the vowel, as in \"bird\".",
    "EY": "Glide from /e/ to /ɪ/, as in \"day\".",
    "AY": "Glide from /a/ to /ɪ/, as in \"my\".",
    "OY": "Glide from /ɔ/ to /ɪ/, as in \"boy\".",
    "AW": "Glide from /a/ to /ʊ/, as in \"now\".",
    "OW": "Glide from /o/ to /ʊ/, rounding your lips more as you go, as in \"go\".",
}

# Clear contrasts for the most common confusions: (expected, heard) -> what to change
CONTRAST = {
    ("P", "B"): "You said /b/. For /p/, keep your voice off and let out a puff of air.",
    ("F", "V"): "You said /v/. For /f/, keep your voice off.",
    ("V", "F"): "You said /f/. For /v/, keep your top teeth on your lip and turn your voice on.",
    ("V", "B"): "You said /b/. For /v/, do not close your lips: touch your bottom lip with your top teeth.",
    ("IH", "IY"): "You said a long /iː/ (as in \"sheep\"). Make it short and relaxed (as in \"ship\").",
    ("IY", "IH"): "You said a short /ɪ/ (as in \"ship\"). Make it longer and spread your lips (as in \"sheep\").",
    ("IH", "EH"): "You said /e/ (as in \"pen\"). Close your mouth a little more (as in \"pin\").",
    ("EH", "IH"): "You said /ɪ/ (as in \"pin\"). Open your mouth a little more (as in \"pen\").",
    ("AE", "EH"): "You said /e/ (as in \"bed\"). Open your mouth wider (as in \"bad\").",
    ("EH", "AE"): "You said /æ/ (as in \"bad\"). Do not open your mouth so wide (as in \"bed\").",
    ("AH", "AA"): "You said /ɑ/ (as in \"hot\"). Keep your mouth half open and relaxed (as in \"cup\").",
    ("AA", "AH"): "You said /ʌ/ (as in \"cup\"). Open your mouth wider (as in \"hot\").",
    ("UH", "UW"): "You said a long /uː/ (as in \"pool\"). Make it short and relaxed (as in \"pull\").",
    ("UW", "UH"): "You said a short /ʊ/ (as in \"pull\"). Make it longer with rounder lips (as in \"pool\").",
    ("OW", "AO"): "You said /ɔ/ (as in \"law\"). Glide to /ʊ/ at the end (as in \"low\").",
    ("AO", "OW"): "You said /oʊ/ (as in \"low\"). Keep the vowel steady, without the glide (as in \"law\").",
    ("CH", "SH"): "You said \"sh\". Start with /t/: t + sh (as in \"chair\", not \"share\").",
    ("SH", "CH"): "You said \"ch\". Do not start with /t/: a smooth \"shh\" (as in \"share\").",
    ("JH", "ZH"): "You said the soft \"zh\". Start with /d/: d + zh (as in \"job\").",
    ("TH", "S"): "You said /s/. Put your tongue between your teeth for /θ/ (\"think\", not \"sink\").",
    ("TH", "T"): "You said /t/. Do not stop the air: tongue between your teeth and blow (\"three\", not \"tree\").",
    ("DH", "Z"): "You said /z/. Put your tongue between your teeth for /ð/ (\"then\", not \"zen\").",
    ("DH", "D"): "You said /d/. Do not stop the air: tongue between your teeth with your voice on (\"they\", not \"day\").",
    ("ER", "AO"): "You said /ɔ/. Curl your tongue up for the r-sound (\"work\", not \"walk\").",
    ("ER", "AA"): "You said /ɑ/. Curl your tongue up for the r-sound (\"bird\").",
}

INSERTED_VOWEL = ("You added a vowel. English often puts two or three consonants together, and an -ed or -s ending "
                  "is often just one sound: say them without a vowel (\"street\" and \"played\" are one syllable).")
ADDED_G = "You added /g/ after /ŋ/. End with the nose sound only: \"sing\", not \"sing-g\"."


def advice(expected: str | None, heard: str | None) -> str:
    """One tip for an error: expected None = an extra sound, heard None = a sound left out."""
    if expected is None:
        if heard == "G":
            return ADDED_G
        if heard in VOWELS:
            return INSERTED_VOWEL
        return f"You added an extra sound /{IPA.get(heard, heard)}/. Leave it out."
    if heard is None:
        return f"You left out /{IPA.get(expected, expected)}/. " + HOW.get(expected, "")
    return CONTRAST.get((expected, heard)) or (f"You said /{IPA.get(heard, heard)}/. " + HOW.get(expected, ""))


def sounded_like(drill: dict, focus: set, word: dict) -> str | None:
    """The other word of a drill pair ("ship" / "sheep") when one swap between the drill's sounds turns the checked
    word into it in the dictionary. word: the one checked word, as the pronunciation check returns it."""
    from .assessment import pronunciations
    text = word["text"].lower()
    pair = next((p for p in drill["pairs"] if text in (w.lower() for w in p)), None)
    if not pair:
        return None
    phones = [p["phone"] for p in word["phones"]]
    for i, p in enumerate(word["phones"]):
        if p["status"] == "good" or p["phone"] not in focus or p.get("heard") not in focus:
            continue
        said = phones[:i] + ([p["heard"]] if p["heard"] else []) + phones[i + 1:]
        for other in pair:
            if other.lower() != text and any([ph for ph, _ in pron] == said for pron in pronunciations(other)):
                return other
    return None


VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
# The symbols of the course's phonemic chart (see pronunciation.py); AH is /ʌ/ (schwa when unstressed)
IPA = {"AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ", "EH": "e", "ER": "ɜr", "EY": "eɪ",
       "IH": "ɪ", "IY": "i", "OW": "oʊ", "OY": "ɔɪ", "UH": "ʊ", "UW": "u", "B": "b", "CH": "tʃ", "D": "d",
       "DH": "ð", "F": "f", "G": "g", "HH": "h", "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ",
       "P": "p", "R": "r", "S": "s", "SH": "ʃ", "T": "t", "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z",
       "ZH": "ʒ"}

# "focus": the sounds the drill's errors are swaps between, judged more strictly in the drill (the th drill's
# errors are /θ/ said as /s/ or /t/ and /ð/ as /z/ or /d/); None: a left-out -ed or -s ending is one of them
DRILLS = [
    {"id": "p-b", "title": "/p/ and /b/", "sounds": ["P", "B"], "focus": ["P", "B"],
     "why": "Arabic has /b/ but no /p/, so \"park\" can sound like \"bark\".",
     "pairs": [["pin", "bin"], ["pack", "back"], ["pear", "bear"], ["pull", "bull"], ["cap", "cab"], ["rope", "robe"]],
     "sentences": ["Please put the paper in the bin.", "Peter bought a big pack of pencils.",
                   "The people on the bus paid with paper money."]},
    {"id": "f-v", "title": "/f/ and /v/", "sounds": ["F", "V"], "focus": ["F", "V"],
     "why": "Arabic has /f/ but no /v/, so \"very\" can sound like \"ferry\".",
     "pairs": [["fan", "van"], ["fine", "vine"], ["ferry", "very"], ["few", "view"], ["safe", "save"], ["leaf", "leave"]],
     "sentences": ["I have a very good view from my window.", "Victor gave five of his favourite videos away.",
                   "Every evening we visit a different village."]},
    {"id": "ih-iy", "title": "Short /ɪ/ and long /i/", "sounds": ["IH", "IY"], "focus": ["IH", "IY"],
     "why": "Arabic has short and long vowels, but English /ɪ/ is also more relaxed than /i/: \"ship\" and \"sheep\".",
     "pairs": [["ship", "sheep"], ["live", "leave"], ["sit", "seat"], ["fill", "feel"], ["bit", "beat"], ["chip", "cheap"]],
     "sentences": ["Please sit in this seat.", "We need to leave this city in six weeks.",
                   "His little sister eats a lot of cheese."]},
    {"id": "ih-eh", "title": "/ɪ/ and /e/", "sounds": ["IH", "EH"], "focus": ["IH", "EH"],
     "why": "Arabic has three short vowels and English has more, so \"pin\" and \"pen\" can sound the same.",
     "pairs": [["pin", "pen"], ["bit", "bet"], ["sit", "set"], ["will", "well"], ["lift", "left"], ["did", "dead"]],
     "sentences": ["Did you get a pen from Ben?", "The bed is next to the window in his room.",
                   "Ten men went to the river."]},
    {"id": "eh-ae", "title": "/e/ and /æ/", "sounds": ["EH", "AE"], "focus": ["EH", "AE"],
     "why": "\"Bed\" and \"bad\" differ only in how wide the mouth opens.",
     "pairs": [["bed", "bad"], ["men", "man"], ["pen", "pan"], ["said", "sad"], ["set", "sat"], ["left", "laughed"]],
     "sentences": ["The man had a black cat.", "That bag is bad, but this bed is better.",
                   "Ann sat at the back of the class."]},
    {"id": "ah-aa", "title": "/ʌ/ and /ɑ/", "sounds": ["AH", "AA"], "focus": ["AH", "AA"],
     "why": "\"Cut\" and \"cot\" have different vowels in English.",
     "pairs": [["cut", "cot"], ["luck", "lock"], ["hut", "hot"], ["cup", "cop"], ["duck", "dock"], ["nut", "not"]],
     "sentences": ["My mother loves her cup of coffee.", "Lunch is not hot on Sunday.",
                   "The bus stopped at the top of the hill."]},
    {"id": "uh-uw", "title": "Short /ʊ/ and long /u/", "sounds": ["UH", "UW"], "focus": ["UH", "UW"],
     "why": "\"Full\" and \"fool\" differ in length and in how round the lips are.",
     "pairs": [["full", "fool"], ["pull", "pool"], ["look", "Luke"], ["book", "boot"], ["foot", "food"]],
     "sentences": ["Look at the moon.", "You should choose a good book.", "The pool is full of cool water."]},
    {"id": "ch-sh", "title": "/tʃ/ and /ʃ/", "sounds": ["CH", "SH"], "focus": ["CH", "SH"],
     "why": "Many Arabic dialects use /ʃ/ for both, so \"chair\" can sound like \"share\".",
     "pairs": [["chair", "share"], ["chip", "ship"], ["cheap", "sheep"], ["watch", "wash"], ["catch", "cash"], ["match", "mash"]],
     "sentences": ["The children watched a short film.", "Which chair should I choose?",
                   "She checked the chicken in the kitchen."]},
    {"id": "th", "title": "/θ/ and /ð/", "sounds": ["TH", "DH"], "focus": ["TH", "DH", "S", "Z", "T", "D"],
     "why": "Some Arabic dialects say /s/ or /t/ for /θ/, and /z/ or /d/ for /ð/.",
     "pairs": [["think", "sink"], ["thank", "tank"], ["three", "tree"], ["mouth", "mouse"], ["they", "day"], ["then", "zen"]],
     "sentences": ["I think there are three things in the bath.", "Thank you for this, Thomas.",
                   "They thought the weather was better than that."]},
    {"id": "ng", "title": "The -ng ending", "sounds": ["NG"], "focus": ["NG", "N", "G"],
     "why": "Arabic speakers often add /g/ after /ŋ/: \"sing-g\".",
     "pairs": [["sing", "sin"], ["thing", "thin"], ["wing", "win"], ["rang", "ran"], ["singer", "finger"]],
     "sentences": ["The singer is singing a long song.", "Bring something to drink in the morning.",
                   "We are going shopping this evening."]},
    {"id": "clusters", "title": "Consonant groups", "sounds": [], "focus": [],
     "why": "Arabic does not start a syllable with two consonants, so learners often add a vowel: \"istreet\", \"sipring\".",
     "pairs": [["street", "spring"], ["sport", "school"], ["three", "splash"], ["texts", "months"], ["asked", "sixth"]],
     "sentences": ["Students study in the school library.", "The street is strong and straight.",
                   "She asked for six texts in the first months of spring."]},
    {"id": "endings-ed", "title": "The -ed ending: /t/, /d/, /ɪd/", "sounds": ["T", "D"], "focus": ["T", "D", None],
     "why": "Adding a vowel to every -ed ending (\"walk-ed\") is a common mistake: the ending is often just /t/ or /d/.",
     "pairs": [["walked", "played"], ["wanted", "needed"], ["watched", "cleaned"], ["stopped", "started"]],
     "sentences": ["She walked home and cleaned her room.", "We wanted to visit, but it rained.",
                   "He watched the film and laughed."]},
    {"id": "endings-s", "title": "The -s ending: /s/, /z/, /ɪz/", "sounds": ["S", "Z"], "focus": ["S", "Z", None],
     "why": "The -s ending has three sounds, and leaving it out changes the meaning.",
     "pairs": [["cats", "dogs"], ["books", "pens"], ["watches", "boxes"], ["likes", "plays"]],
     "sentences": ["She likes books, but he plays games.", "The bus stops near the houses.",
                   "My friend washes the dishes."]},
]
DRILL_BY_ID = {d["id"]: d for d in DRILLS}
