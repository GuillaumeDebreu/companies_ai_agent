import json
import sys
import time
from deep_translator import GoogleTranslator

INPUT_FILE = "startups.json"
BATCH_SIZE = 10
SLEEP_BETWEEN_BATCHES = 1


def log(msg):
    print(msg, flush=True)


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    startups = data["startups"]

    # Collect indices of startups that need translation
    to_translate = []
    for i, s in enumerate(startups):
        desc = s.get("description", "").strip()
        desc_fr = s.get("description_fr", "").strip()
        if desc and not desc_fr:
            to_translate.append(i)

    log(f"Total startups: {len(startups)}")
    log(f"Need translation: {len(to_translate)}")

    translator = GoogleTranslator(source="en", target="fr")
    translated_count = 0
    error_count = 0

    for batch_start in range(0, len(to_translate), BATCH_SIZE):
        batch_indices = to_translate[batch_start : batch_start + BATCH_SIZE]
        batch_texts = [startups[i]["description"] for i in batch_indices]

        try:
            results = translator.translate_batch(batch_texts)
            for idx, translation in zip(batch_indices, results):
                if translation:
                    startups[idx]["description_fr"] = translation
                    translated_count += 1
                else:
                    error_count += 1
        except Exception as e:
            log(f"  Error on batch starting at {batch_start}: {e}")
            # Fall back to one-by-one for this batch
            for idx in batch_indices:
                try:
                    result = translator.translate(startups[idx]["description"])
                    if result:
                        startups[idx]["description_fr"] = result
                        translated_count += 1
                    else:
                        error_count += 1
                except Exception as e2:
                    log(f"    Skipping '{startups[idx]['name']}': {e2}")
                    error_count += 1

        if translated_count > 0 and translated_count % 50 < BATCH_SIZE:
            log(f"  Progress: {translated_count} translated so far...")

        if batch_start + BATCH_SIZE < len(to_translate):
            time.sleep(SLEEP_BETWEEN_BATCHES)

    data["startups"] = startups
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"\nDone! Total translated: {translated_count}, errors: {error_count}")


if __name__ == "__main__":
    main()
