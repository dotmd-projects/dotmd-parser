You score an auto-extracted domain ontology against its source corpus.

Given the ontology summary and a corpus summary, rate:
- coverage: fraction of important domain concepts/relations captured (0..1)
- faithfulness: fraction of ontology elements actually supported by the corpus (0..1)

## Ontology
{{ontology_summary}}

## Corpus summary
{{corpus_summary}}

## Output (JSON only)
```json
{"coverage": 0.0, "faithfulness": 0.0, "notes": "one sentence"}
```
