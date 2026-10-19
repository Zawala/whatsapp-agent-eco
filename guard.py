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

def redact(text: str) -> str:
    results = analyzer.analyze(text=text, language="en")
    return anonymizer.anonymize(text=text, analyzer_results=results).text

if __name__ == "__main__":
    print(redact("Hi, I'm Tendai Moyo, call 0772 123 456, ID 63-123456 K 42, t.moyo@example.com"))