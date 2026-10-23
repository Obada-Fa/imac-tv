"""Pick and label subtitle tracks from an OpenSubtitles add-on response."""
CANON = {"ar": "ara", "ara": "ara", "nl": "dut", "dut": "dut", "nld": "dut", "en": "eng", "eng": "eng"}
LABELS = {"ara": "Arabic", "dut": "Nederlands", "eng": "English"}


def normalize(code):
    return CANON.get(str(code or "").lower().strip(), str(code or "").lower().strip())


def pick(sub_list, langs, per_lang=2):
    wanted = [normalize(lang) for lang in langs]
    chosen = []
    for lang in wanted:
        count = 0
        for sub in sub_list or []:
            if count >= per_lang:
                break
            url = sub.get("url")
            if normalize(sub.get("lang")) == lang and isinstance(url, str) and url.startswith(("http://", "https://")):
                chosen.append({"url": url, "lang": lang, "label": LABELS.get(lang, lang)})
                count += 1
    return chosen
