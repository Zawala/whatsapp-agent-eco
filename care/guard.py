import re
from pathlib import Path

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult
from presidio_anonymizer import AnonymizerEngine

zw_phone = PatternRecognizer(
    supported_entity="ZW_PHONE",
    patterns=[Pattern("zw_phone", r"(?:\+263|0)\s?7[1-9]\s?\d{3}\s?\d{4}", 0.6)],
)
zw_id = PatternRecognizer(
    supported_entity="ZW_NATIONAL_ID",
    patterns=[Pattern("zw_id", r"\b\d{2}-?\d{6,7}\s?[A-Za-z]\s?\d{2}\b", 0.6)],
)



class IntroducedNameRecognizer(EntityRecognizer):
    """PERSON for the name after an introduction: "I'm Tendai Moyo", "this is Farai",
    "my name is rutendo". The English spaCy model misses many Zimbabwean names."""

    # "my name is" takes a name in any case; the shorter openers need a capital letter.
    _ANY_CASE = re.compile(r"\bmy name is\s+([a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,2})", re.IGNORECASE)
    _CAPITALISED = re.compile(r"(?i:\b(?:i'm|i am|im|this is|it's|its)\s+)([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,2})")
    # First words that follow "I'm"/"this is" but aren't names.
    _NOT_NAMES = {"not", "unable", "having", "trying", "looking", "getting", "still", "so", "very", "sorry",
                  "here", "there", "just", "really", "also", "calling", "writing", "asking", "fine", "ok",
                  "okay", "good", "sure", "urgent", "the", "a", "an", "my", "your", "about", "from", "in",
                  "on", "at", "with", "for", "no", "now", "again", "back", "new", "out", "stuck"}
    _STOP_AT = {"and", "or", "but", "from", "here", "with", "on", "my", "i", "please", "is", "the"}

    def __init__(self):
        super().__init__(supported_entities=["PERSON"], name="IntroducedNameRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        results = []
        for pattern in (self._ANY_CASE, self._CAPITALISED):
            for m in pattern.finditer(text):
                words = m.group(1).split()
                if words[0].lower() in self._NOT_NAMES:
                    continue
                keep = []
                for w in words:                 # "my name is tendai and my line is down" -> "tendai"
                    if w.lower() in self._STOP_AT:
                        break
                    keep.append(w)
                start = m.start(1)
                end = start + len(" ".join(keep)) if keep else start
                if end > start:
                    results.append(RecognizerResult("PERSON", start, end, 0.85))
        return results


# Common Shona and Ndebele names, from care/zw_names.txt (a starter list; see its header).
zw_names = PatternRecognizer(
    supported_entity="PERSON",
    deny_list=[l.strip() for l in Path(__file__).with_name("zw_names.txt").read_text().splitlines()
               if l.strip() and not l.startswith("#")],
    name="ZwNameRecognizer",
)

analyzer = AnalyzerEngine()
analyzer.registry.add_recognizer(zw_phone)
analyzer.registry.add_recognizer(zw_id)
analyzer.registry.add_recognizer(IntroducedNameRecognizer())
analyzer.registry.add_recognizer(zw_names)
anonymizer = AnonymizerEngine()

# Structured PII only. Names are left out on output: spaCy NER tags invented
# bundle names as PERSON, and the bot has no business emitting names anyway.
# PHONE_NUMBER stays because it catches 9-digit numbers ZW_PHONE misses.
OUTPUT_ENTITIES = ["ZW_PHONE", "ZW_NATIONAL_ID", "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IBAN_CODE"]
# Input adds names (spaCy, plus the two recognizers above), but not
# LOCATION/DATE_TIME/NRP: spaCy tags the "GB" in "5GB" as a LOCATION, which
# breaks retrieval, and they aren't identifying here.
INPUT_ENTITIES = OUTPUT_ENTITIES + ["PERSON"]

def redact(text: str, entities: list[str] | None = None) -> str:
    results = analyzer.analyze(text=text, language="en", entities=entities)
    return anonymizer.anonymize(text=text, analyzer_results=results).text

if __name__ == "__main__":
    print(redact("Hi, I'm Tendai Moyo, call 0772 123 456, ID 63-123456 K 42, t.moyo@example.com"))
