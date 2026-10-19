from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine

zw_phone = PatternRecognizer(
    supported_entity="ZW_PHONE",
    patterns=[Pattern("zw_phone", r"(?:\+263|0)\s?7[1-9]\s?\d{3}\s?\d{4}", 0.6)],
)
zw_id = PatternRecognizer(
    supported_entity="ZW_NATIONAL_ID",
    patterns=[Pattern("zw_id", r"\b\d{2}-?\d{6,7}\s?[A-Za-z]\s?\d{2}\b", 0.6)],
)

analyzer = AnalyzerEngine()
analyzer.registry.add_recognizer(zw_phone)
analyzer.registry.add_recognizer(zw_id)
anonymizer = AnonymizerEngine()

# Structured PII only. Names are left out on output: spaCy NER tags invented
# bundle names as PERSON, and the bot has no business emitting names anyway.
# PHONE_NUMBER stays because it catches 9-digit numbers ZW_PHONE misses.
OUTPUT_ENTITIES = ["ZW_PHONE", "ZW_NATIONAL_ID", "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IBAN_CODE"]
# Input adds names, but not LOCATION/DATE_TIME/NRP: spaCy tags the "GB" in
# "5GB" as a LOCATION, which breaks retrieval, and they aren't identifying here.
INPUT_ENTITIES = OUTPUT_ENTITIES + ["PERSON"]

def redact(text: str, entities: list[str] | None = None) -> str:
    results = analyzer.analyze(text=text, language="en", entities=entities)
    return anonymizer.anonymize(text=text, analyzer_results=results).text

if __name__ == "__main__":
    print(redact("Hi, I'm Tendai Moyo, call 0772 123 456, ID 63-123456 K 42, t.moyo@example.com"))
